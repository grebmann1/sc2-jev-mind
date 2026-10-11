"""Install Blizzard's official headless Linux engine and Acropolis ladder map pack.

The explicit license flag is required before any downloads or extraction.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
import subprocess
import urllib.request
import zipfile
from pathlib import Path

LICENSE = "https://blzdistsc2-a.akamaihd.net/AI_AND_MACHINE_LEARNING_LICENSE.html"
ENGINE = "https://blzdistsc2-a.akamaihd.net/Linux/SC2.4.10.zip"
MAPS = "https://blzdistsc2-a.akamaihd.net/MapPacks/Ladder2019Season3.zip"


def download(url: str, destination: Path) -> None:
    if destination.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(".part")
    print(f"Downloading {url}", flush=True)
    try:
        with urllib.request.urlopen(url, timeout=60) as response, partial.open("wb") as output:
            shutil.copyfileobj(response, output)
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)


def extract(archive_path: Path, destination: Path) -> None:
    root = destination.resolve()
    print(f"Extracting {archive_path.name}", flush=True)
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            target = (root / member.filename).resolve()
            if not target.is_relative_to(root) or stat.S_ISLNK(member.external_attr >> 16):
                raise ValueError(f"Unsafe archive entry: {member.filename}")
        # Python decrypts ZipCrypto byte by byte. The multi-GB official archive
        # extracts much faster with native unzip, after the same path checks.
        native_unzip = shutil.which("unzip")
        if native_unzip:
            root.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [
                    native_unzip,
                    "-q",
                    "-o",
                    "-P",
                    "iagreetotheeula",
                    str(archive_path.resolve()),
                    "-d",
                    str(root),
                ],
                check=True,
            )
        else:
            archive.extractall(root, pwd=b"iagreetotheeula")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--accept-license",
        action="store_true",
        help=f"Agree to Blizzard's AI and Machine Learning License: {LICENSE}",
    )
    parser.add_argument("--directory", type=Path, default=Path("runtime"))
    args = parser.parse_args(argv)
    if not args.accept_license:
        parser.error(f"Read {LICENSE}, then pass --accept-license if you agree to its terms.")
    root = args.directory.resolve()
    engine_zip, maps_zip = root / "downloads" / "SC2.4.10.zip", root / "downloads" / "maps.zip"
    download(ENGINE, engine_zip)
    download(MAPS, maps_zip)
    extract(engine_zip, root)
    install = root / "StarCraftII"
    extract(maps_zip, install / "Maps")
    binaries = list((install / "Versions").glob("Base*/SC2_x64"))
    if not binaries:
        raise RuntimeError("Engine archive did not contain a usable SC2_x64 binary")
    for binary in binaries:
        binary.chmod(binary.stat().st_mode | 0o111)
    digests = {}
    for path in (engine_zip, maps_zip):
        with path.open("rb") as file:
            digests[path.name] = hashlib.file_digest(file, "sha256").hexdigest()
    manifest = {
        "engine": ENGINE,
        "maps": MAPS,
        "license": LICENSE,
        "install": str(install),
        "sha256": digests,
    }
    (root / "installation.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Installed to {install}. Run: docker compose run --rm bot --offline --no-realtime")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
