---
name: sc2-replays
description: Browse SC2 recordings, prepare tactical playback, and review saved moments in the Zana SC2 Replays plugin.
---

Use `zcc sc2-replays list` to discover match IDs and metadata from the plugin's configured local SC2 project. Use `zcc sc2-replays prepare <id>` only when the user requests replay playback/preparation and an export is missing or needs rebuilding. Preparation uses a compatible SC2 engine without model calls, new matches, or bot-learning writes.

The **SC2 Replays** sidebar panel contains the map player, search/filters, favorites, timestamped bookmarks, notes, and original `.SC2Replay` downloads. Match data is in the SC2 project's `runs/<group>/<match>/`; exports are `viewer/data.json` and `viewer/metadata.json`. Saved review annotations are in the plugin's Zana storage and are separate from bot episodes in `memory/matches.sqlite3`.

Playback shows full-map 2D spectator data sampled approximately every two game seconds. Do not claim exact micro, full 3D graphics, that the bot knew unseen enemy positions, general win-rate improvement, or that model weights were retrained.

Configure the source project and docker/native/disabled preparation mode in plugin settings. Remote SSH projects are not supported. For problems use `zcc plugin logs sc2-replays -n 30`. Never expose `.env`, API keys, private desktop tokens, or serve the repository root.
