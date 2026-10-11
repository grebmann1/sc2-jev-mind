# SC2 Replays

A Zana plugin for browsing and reviewing our SC2 bot's recorded matches. It keeps the original dark tactical-map player, with a persistent match library, favorites, timestamped bookmarks, and notes.

Open **SC2 Replays** in Zana's sidebar. The plugin discovers `.SC2Replay` recordings in this project's `runs/` tree and refreshes every 20 seconds. Search by map, race, difficulty, opening, folder, or notes, and filter victories, defeats, or favorites.

Playback shows actual replay unit positions, terrain, health, economy, and recorded Jev/OpenAI choices. Use play/pause, speeds, scrubbing, chapter jumps, base labels, and unit hover. Space toggles playback; arrows seek ten seconds when a form control is not focused. Click a saved bookmark to return to that moment.

This is **2D tactical playback**, sampled about every two game seconds with interpolated movement. It shows both players, including units the bot could not see. It does not reproduce the full SC2 graphical client. Strategic summaries and tactical choices are recorded evidence; review notes are your observations.

Open **Post-game analysis** above the player to read a match's saved review. It shows measured scope, proposed improvements, exact evidence references, uncertainty, and controlled validation plans. New completed games produce this report automatically; `sc2mind-analyze` can review older recordings. The panel refreshes as a pending review completes. Reports remain provisional and never change bot policy automatically; reading them makes no model calls and leaves learning memory untouched.

## Replay preparation

An existing `viewer/data.json` export opens immediately. Otherwise, click **Prepare replay**. The plugin runs `python -m sc2mind.replay` in the background through the configured engine; it does not play a new match, call models, or change the bot's learning database. Only one preparation runs at a time, with a ten-minute timeout. A compatible SC2 engine and map/cache data are required. Unloading the plugin cancels preparation.

The default **docker** mode reuses this project's `compose.yaml`, existing bot image, mounted engine/maps, and QEMU launcher. It mounts the current `src/` read-only so exporter changes work without rebuilding the image. Docker must be running and reachable from Zana. No shell command is composed from a replay filename.

The **native** mode uses `uv run --offline python -m sc2mind.replay` in the selected local project. The installed engine, `SC2PATH`, and optional `SC2_ENGINE_RUNNER` must be available to the Zana process. **disabled** allows viewing previously prepared replays without launching an engine.

Use the plugin's configuration to choose the SC2 project and engine mode. Project settings refer to a registered local Zana project; remote SSH projects are not supported by this version. The first victory already has a prepared export.

CLI equivalents:

```sh
zcc sc2-replays list
zcc sc2-replays prepare <match-id>
zcc sc2-replays analysis <match-id>  # read an existing saved report; no model call
```

## Install and develop

From this directory:

```sh
npm ci --registry=https://registry.npmjs.org
npm test
npm run typecheck
zcc plugin install .
zcc plugin dev --once
```

The path install loads `server.ts`; Zana bundles the frontend with its shared React runtime. The nav slot appears under SC2 Replays. No temporary HTTP server or external browser is required. Backend routes serve only the selected replay export and original replay, confined to the configured project's `runs/` directory.

The official Zana SDK 0.1.0 is included as `vendor/zana-ai-zcc-plugin-sdk-0.1.0.tgz`, packed from the local Zana SDK distribution with lifecycle scripts disabled because this version is not published on npm. `package-lock.json` pins dependencies. `sdk-hooks.d.ts` fills two documented hook declarations omitted by that SDK's type bundle.

Favorites, bookmarks, and notes live in the plugin's Zana-managed SQLite database under its plugin data directory. They survive source reloads and app restarts, independently of the bot's `memory/matches.sqlite3`. Uninstalling/clearing plugin data can remove them. Replay files and exports remain in the project. Viewing/reviewing a replay needs no API keys.

See [LIVE_TEST.md](LIVE_TEST.md) for live verification and [ROADMAP.md](ROADMAP.md) for the feature brainstorm.
