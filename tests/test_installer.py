import importlib.util
import stat
import zipfile
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "installer", Path(__file__).parents[1] / "scripts" / "install_sc2.py"
)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def test_installer_requires_explicit_license_acceptance_before_network(monkeypatch):
    def unexpected_download(*args):
        pytest.fail("Download attempted before license acceptance")

    monkeypatch.setattr(installer, "download", unexpected_download)
    with pytest.raises(SystemExit) as error:
        installer.main([])
    assert error.value.code == 2


def test_archive_extracts_inside_destination(tmp_path):
    archive = tmp_path / "test.zip"
    with zipfile.ZipFile(archive, "w") as file:
        file.writestr("StarCraftII/Versions/Base90000/SC2_x64", b"synthetic test file")
    installer.extract(archive, tmp_path / "install")
    assert (tmp_path / "install/StarCraftII/Versions/Base90000/SC2_x64").exists()


def test_archive_extracts_without_native_unzip(tmp_path, monkeypatch):
    monkeypatch.setattr(installer.shutil, "which", lambda name: None)
    archive = tmp_path / "test.zip"
    with zipfile.ZipFile(archive, "w") as file:
        file.writestr("StarCraftII/Versions/Base90000/SC2_x64", b"portable extraction")
    installer.extract(archive, tmp_path / "install")
    assert (
        tmp_path / "install/StarCraftII/Versions/Base90000/SC2_x64"
    ).read_bytes() == b"portable extraction"


@pytest.mark.parametrize("entry", ["../escape", "/tmp/sc2mind-escape"])
def test_archive_cannot_escape_destination(tmp_path, entry):
    archive = tmp_path / "test.zip"
    with zipfile.ZipFile(archive, "w") as file:
        file.writestr(entry, b"bad")
    with pytest.raises(ValueError, match="Unsafe archive entry"):
        installer.extract(archive, tmp_path / "install")


def test_archive_symlinks_are_rejected(tmp_path):
    archive = tmp_path / "test.zip"
    entry = zipfile.ZipInfo("escape")
    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "w") as file:
        file.writestr(entry, "/tmp")
    with pytest.raises(ValueError, match="Unsafe archive entry"):
        installer.extract(archive, tmp_path / "install")
