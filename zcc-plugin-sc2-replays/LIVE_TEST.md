# Live verification: SC2 Replays

Keep `ZCC_SERVER_URL` and `ZCC_DATA_DIR` pointed at the same running Zana instance. Do not change session credentials or installed-plugin records.

1. Run `npm test`, `npm run typecheck`, `zcc plugin install .`, then `zcc plugin dev --once` from this plugin directory.
2. Open **SC2 Replays** in the running Zana app with the computer-use tool. Verify the panel fills its space, shows recorded matches, and loads actual replay units/economy.
3. Seek to a known game time. Confirm recorded Jev/OpenAI annotations and economy change with the timeline. Test play/pause and search.
4. Create a uniquely named temporary bookmark and note. Make a visible source edit, run `zcc plugin dev --once`, and verify the open panel updates while saved annotations survive. Click the bookmark and verify the replay seeks to its saved time. Remove only the temporary data you created.
5. Prepare an existing short unexported replay through the panel. Confirm progress reaches a usable player, the result/controller labels are correct, and `uv run sc2mind --memory-info` has unchanged learning counts.
6. Leave the requested plugin installed and the first victory selected. Inspect `zcc plugin logs sc2-replays -n 20` if any check fails.

## Observed on 2026-10-11

- Path installation and repeated `zcc plugin dev --once` succeeded. The panel appeared in the sidebar and opened in Zana.
- First victory playback loaded actual SC2 replay positions and synchronized metrics/decisions. Seeking to 6:00 showed 38 workers, 32 army supply, 72/86 supply, and the plan received at 5:39.
- Searching for a nonexistent map produced the correct empty search state.
- A 6:00 bookmark and notes survived a live source reload; the changed header appeared without restarting Zana. The saved bookmark sought back to 6:00. Temporary verification annotations were removed afterward.
- The existing ten-second offline diagnostic replay prepared through the UI and became playable, labelled Tie and Rule-based controller. Learning storage still had exactly one completed episode.
- Automated verification: eight plugin tests and TypeScript compile checks; 122 Python tests. These counts are separate from actual game outcomes.
