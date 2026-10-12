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

## Post-game analysis update, 2026-10-11

- The saved victory received a structured OpenAI review with evidence references constrained to actual recorded IDs. Early free-text references were rejected; the final implementation uses a dynamic evidence enum in the response schema.
- The installed plugin was rebuilt/reloaded. Its `analysis` command read the actual saved report successfully, returning complete status and five provisional findings without making another model call. Learning storage remained at one completed episode.
- An actual ten-second offline Docker game saved `result.json`, its replay, `analysis.json`, and `analysis.md` automatically after engine shutdown. It used `--no-memory`; short diagnostics remain excluded from general gameplay signals.
- Automated verification: ten plugin tests and TypeScript checks; 140 Python tests. Tests cover the new report UI, read confinement, post-game callbacks, failed/oversized model reviews, evidence validation, idempotent reuse, and cross-map evaluation.
- Desktop interaction of the new analysis disclosure remains unverified: the native computer-use request timed out, and the computer-use in-app browser surface reported unavailable. The core browser could capture a newly loaded Zana shell but did not establish a rendered analysis panel. Do not describe the new controls as live visually verified based on unit tests or backend success.

## Parallel squad control update, 2026-10-12

- Added recorded main/scout/harassment modes and ownership rings to the tactical player. Roster events include empty groups on recall, so a released scout does not keep its old visual role. Older replays remain compatible.
- Twelve plugin tests and TypeScript checks pass, including DOM rendering of the actual viewer functions, recall ownership, and safe mission text. `zcc plugin dev --once` rebuilt and reloaded the installed plugin successfully.
- Three separate live Jev squad clients returned typed decisions concurrently in a synthetic integration probe (~0.38s total). This is provider integration evidence, not a live SC2 performance result.
- A six-minute offline MediumHard Protoss diagnostic on AcropolisLE completed with all three roles, 20 scout visits, a Tech Lab, three Reactors, and completed Stim. A complete offline MediumHard Zerg game on TritonLE won at ~539 game seconds with all three roles and 29 scout visits. Both used --no-memory and produced post-game reports. These are execution checks, not a controlled improvement comparison.
- A six-minute offline MediumHard Terran diagnostic on AcropolisLE also completed without runtime/provider errors: 201 squad decisions, 14 scout visits, and a time-limit tie. The saved Zerg victory exported successfully: 276 replay frames, 367 squad decisions, 309 squad orders, 153 roster events, and 29 scouting visits. Replay preparation kept learning storage at exactly two episodes.
- Final Python verification passed 166 tests, Ruff checks, the ten-scenario coordinator run, and package builds. Scout hold orders, exclusion of workers from the army movement estimate, unknown scouting metrics in historical traces, and assignment-versus-age discard classification have regression coverage. Reliability findings cite the actual affected squad/provider; unclassified historic discards do not imply latency failures. The Docker image was rebuilt with these final corrections.
- Desktop interaction remains unverified: the computer-use in-app browser returned `Browser is not available: iab`. Do not claim the new card or rings were live visually checked from unit or backend tests alone.

## Dedicated base/worker brain, 2026-10-12

- Added an independent Jev worker/economy client alongside macro and combat clients. Its four-second cadence/eight-second expiry, legal priority/mode choices, current-threat overrides and shutdown run in code; no model call blocks on_step. Economic proposals share the existing investment path and preserve production/tech commitments.
- Worker allocation protects scout/builders/other orders, preserves mineral income, rebalances safe bases/gas, bounds repairs and evacuates locally endangered SCVs. Current saturation and issued commands feed observed memory, OpenAI context, replay annotations and evidence-based reviews.
- 182 Python tests, Ruff, ten scripted scenarios and package builds pass. Twelve plugin tests plus TypeScript checks pass, including actual viewer-function rendering of the Base & workers pill when no combat events exist. The installed plugin was rebuilt/reloaded and the Docker image rebuilt.
- A real worker Jev integration probe returned typed `gas` / `refinery` choices at confidence 0.64; it used synthetic observations, separate from SC2 performance checks.
- An initial six-minute offline MediumHard Terran check on AcropolisLE, seed 105, completed without errors. It recorded 88 economy replies, 91 allocation states and 72 worker commands (36 mineral assignments, 35 gas assignments, one evacuation). Last sample: 42 workers, two bases plus one pending, 21 army supply. Result was a time-limit tie; memory was disabled. Final eligibility/mineral-floor guards have regression coverage.
- The final image completed a full offline MediumHard Zerg game on TritonLE, seed 106: victory at 525 game seconds. All four specialist roles operated; 128 economy replies, 136 allocation states, 832 issued worker commands (including 34 evacuation commands), 26 scout visits, no errors. Last sampled economy: 66 workers, three bases, 72 army supply. Automatic post-game analysis retained 525 seconds of dense observations; learning remained disabled. This checks execution, not a controlled improvement or a live paid-model match.
- The new economy pill's actual desktop appearance remains unverified because the in-app computer-use browser was unavailable. Automated DOM tests and replay annotation decoding do not establish live visual proof.
