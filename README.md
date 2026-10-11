# sc2mind — Jev × OpenAI StarCraft II bot

A Terran Marine/Marauder bot against the built-in SC2 computer. TypeSafe Jev makes
fast tactical decisions, OpenAI supplies a structured strategic plan, and Python
owns legality, confidence thresholds, resource reservations, and game orders.

The offline baseline runs the same coordinator and executor with deterministic
model stand-ins. You can exercise the decision loop without SC2 or any API keys.
Actual matches require the game engine; the snapshot scenarios do not measure win rate.
Completed matches now become persistent local experience for both models and the
opening selector. Learning starts automatically when actual matches are played.

## Try it now — no game or keys needed

```sh
uv sync --locked
uv run sc2mind --simulate
uv run pytest
```

`--simulate` checks ten scripted positions: the opening, gas and tech, expansion,
stim, defense, a timing attack, and a larger enemy army in fog of war. It writes
`runs/<timestamp>/result.json` and `trace.jsonl`. Use `--json` for machine-readable output.

Python 3.12 is selected by `.python-version`; uv installs it if needed.

## Play on macOS or Windows

1. Install StarCraft II through Battle.net. On macOS the default directory is
   `/Applications/StarCraft II`. Use `SC2PATH` for a different installation.
2. Place `AcropolisLE.SC2Map` from the
   [2019 Season 3 map pack](https://github.com/Blizzard/s2client-proto#map-packs)
   inside the game's `Maps` directory, at its root or one directory below it.
   Blizzard's download page specifies the license and archive password.
3. Check setup and play the offline baseline:

```sh
uv run sc2mind --doctor --offline
uv run sc2mind --offline --enemy zerg --difficulty Medium
```

No model provider is contacted in offline mode. The bot builds supply, grows
workers, upgrades command centers to orbitals, uses MULEs, expands, builds
barracks with room for add-ons, trains infantry, and researches stim and weapons.
An early SCV scouts the enemy start; the attacking army searches expansions when
there are no visible enemy structures. Depots lower for our army and raise near enemies.

`--map`, `--enemy`, `--difficulty`, `--seed`, `--runs-dir`, and
`--game-time-limit` configure a match. The time limit defaults to 1,200 game seconds.
`--no-realtime` runs as fast as SC2 permits; use realtime to evaluate model latency.

## Headless Linux engine through Docker

The included container can run the official Linux engine on Linux, Windows, or
macOS. On Apple Silicon, set this in `.env` before running the container:

```dotenv
SC2_ENGINE_RUNNER=/usr/local/bin/sc2-qemu
```

The image includes a QEMU runner built for the Docker host. Only the SC2 engine
uses that runner; Python and the model clients use Docker's normal execution.
On this Mac, Docker's default emulation crashed SC2 4.10 during map loading;
explicit QEMU completed short engine checks and saved replays. Emulated matches
may run slowly. Game files are mounted separately from the bot image.

Read [Blizzard's AI and Machine Learning License](https://blzdistsc2-a.akamaihd.net/AI_AND_MACHINE_LEARNING_LICENSE.html)
before installing the official engine. If you agree, this installer downloads
SC2 4.10 and its 2019 Season 3 map pack into the ignored `runtime/` directory:

```sh
uv run python scripts/install_sc2.py --accept-license
docker compose build
docker compose run --rm bot --doctor --offline
docker compose run --rm bot --offline --no-realtime --enemy zerg
```

You can instead point `SC2_INSTALL_DIR` at an existing extracted **Linux** engine.
A native macOS/Windows installation cannot be mounted into the Linux container.
Traces, result reports, and replays are saved in the host's `runs/` directory.
The host's `memory/` directory is mounted separately so experience survives containers.
The Linux engine uses its own older game balance, so report the engine version
when comparing results with a current native client.
Compose uses Docker's existing bridge network and a 1 GB shared-memory area.

## Connect the models later

```sh
cp .env.example .env  # only if .env doesn't already exist
# Fill TYPESAFE_API_KEY and OPENAI_API_KEY locally in .env.
uv run --env-file .env sc2mind --doctor
uv run --env-file .env sc2mind --enemy zerg --difficulty Medium
```

`.env` is ignored by Git and excluded from the Docker build context. Docker
Compose reads the API keys from `.env` when running a live match:

```sh
docker compose run --rm bot --enemy zerg
```

If the strategic model exceeds its default 30-second wall-clock budget, increase
`S2_TIMEOUT_S` in `.env` (this host uses `90` for `gpt-5`). Calls stay in the
background with at most one outstanding call per provider; SC2 keeps playing
with its current plan. Plans older than `S2_MAX_AGE_S` game seconds are discarded.

| Setting | Default | Meaning |
|---|---|---|
| `JEV_MODEL` | `jev-latest` | TypeSafe System One model |
| `OPENAI_MODEL` | `gpt-5` | OpenAI structured strategy model |
| `S1_INTERVAL_S` | `2` | Game seconds between tactical requests |
| `S1_MIN_CONFIDENCE` | `0.45` | Minimum confidence to accept a choice |
| `S1_REPLAN_THRESHOLD` | `0.7` | Divergence probability that requests an early plan |
| `S2_INTERVAL_S` | `45` | Game seconds between regular strategic requests |
| `S2_MIN_INTERVAL_S` | `15` | Minimum game seconds between strategic requests |
| `S1_TIMEOUT_S` / `S2_TIMEOUT_S` | `3` / `30` | Wall-clock request budgets |
| `S1_MAX_AGE_S` / `S2_MAX_AGE_S` | `8` / `120` | Game-time freshness limits for returned answers |
| `SC2MIND_MEMORY_DIR` | `memory` | Directory for the persistent match database |

Requests run in background tasks. Failures and low confidence retain previous
accepted decisions; code rechecks them against the current state on every step.
Before the first confident investment, code supplies the baseline build. It also
resumes that build if no investment remains and confident answers have gone stale.
Outdated replies are discarded. Faster-than-realtime live matches can discard
many replies because the game advances faster than the providers respond.

## Memory and learning

`memory/matches.sqlite3` stores completed observed matches across process restarts.
Each record includes the result, selected opening, initial/final plans, bounded
observation timeline, peak economy/army/base counts, observed supply-block time,
base threats, issued investments, and model error counts. Lessons are derived from
those observations in code. The database and SQLite sidecars are ignored by Git
and excluded from image builds. Back up the memory directory to retain experience.

OpenAI receives recent comparable episodes, opening result statistics, and the
current match's observations. Jev receives a flat summary limited to 650 characters.
Historical experience is filtered by map, opponent race, difficulty, controller/model
names and decision configuration, game engine/SDK, realtime mode, time limit, and
policy version. Random opponents
use pooled race history until scouting reveals their race. Retrieval runs in a
background thread; the game-step callback performs no database operations.

The opening selector starts with **balanced**, explores **pressure** and **macro**
for at least three comparable matches each, then chooses the highest smoothed
result score. Every fifth subsequent selection explores the least sampled opening.
Wins score 1, ties 0.5, losses 0, with a small prior for low sample counts. Openings
set initial attack timing, worker targets, unit mix, and strategic priorities;
the models may adapt the plan during play. Results measure associations with the
starting profile, not the isolated effect of that profile. Matches can be compared
across seeds; replaying the same seed does not create independent evidence. This is
retrieval and strategy selection; model weights are not retrained, and improvement
is not guaranteed. Legality, confidence thresholds, and defensive overrides stay in code.

```sh
uv run sc2mind --memory-info                # no game or API keys needed
uv run sc2mind --offline --enemy zerg       # remember and learn after each match
uv run sc2mind --offline --opening pressure # choose a profile explicitly
uv run sc2mind --offline --no-learning      # retain memory; use balanced opening
uv run sc2mind --offline --no-memory        # disable persistent storage/retrieval
```

Use `--memory-dir PATH` or `SC2MIND_MEMORY_DIR` for another database directory.
`--no-learning` disables automatic opening selection; reasoning still receives
past experience. `--opening` takes precedence over automatic selection.
Failed launches, interrupted games, and scripted scenarios do not train the selector.
Memory errors are logged and play continues with the code baseline and current state.

## Automatic post-game analysis

Every completed observed game now saves `analysis.json` and `analysis.md` beside
its replay. After SC2 exits, live games make one bounded OpenAI review request;
offline games produce a measured report without contacting a provider. Review
failure preserves the game result and the measured report. The review never
blocks `on_step`, changes gameplay code, or writes model suggestions into learning memory.

Reports distinguish recorded facts, provisional improvement hypotheses, proposed
experiments, and missing evidence. Structured output restricts evidence references
to actual recorded IDs. Supply interruptions, mineral-bank duration, observation
coverage, first expansion/stim milestones, issued investments, answer age, provider
failures, and recorded plan changes help identify general mechanisms to investigate.
Sparse historic traces retain explicit coverage limits; a plan's rationale is a
model claim, not resource or combat telemetry.

General signals use up to 50 recent completed matches with the same engine,
controller/settings, policy, difficulty, realtime mode, and time limit while
allowing different maps and races. Repeated map/race/seed conditions are counted
separately from match totals. Games shorter than 180 seconds are diagnostic reports
and do not support recurring gameplay signals. Missing old metrics remain unknown.
Signals remain provisional; diversity can qualify a candidate for a controlled test,
but never proves that a proposed change improves the bot.

Code-derived recurring observations are available to subsequent strategic
reasoning through memory. The existing opening selector keeps its stricter
map/race comparisons. Model-written suggestions stay in the review for evaluation.

```sh
# Review an existing completed game without launching SC2:
uv run --env-file .env sc2mind-analyze runs/<match>
uv run sc2mind-analyze runs/<match> --offline
# Existing reports are reused; explicitly replace/retry one:
uv run --env-file .env sc2mind-analyze runs/<match> --force
# During a live match, choose measured analysis only, or disable it:
uv run --env-file .env sc2mind --analysis-offline
uv run --env-file .env sc2mind --no-analysis
```

The model uses `OPENAI_MODEL`; `ANALYSIS_TIMEOUT_S` or `--analysis-timeout` sets its
wall-clock budget (default 120 seconds, no automatic retries). In Zana, open
**Post-game analysis** above the replay to read findings, cited evidence, uncertainty,
and validation plans. Viewing a saved review makes no model calls.

## Measure match results

Preview a reproducible schedule without launching games or making API calls:

```sh
uv run sc2mind-benchmark --offline --games 3 --dry-run
```

After installing SC2, run nine offline matches (three seeds against each race):

```sh
uv run sc2mind-benchmark --offline --games 3
uv run --env-file .env sc2mind-benchmark --games 3 --realtime
```

Use `--races zerg protoss` and `--difficulties Easy Medium` to choose a matrix.
Use `--maps` and a separate `--seed-start` range for broader, held-out evaluation:

```sh
uv run sc2mind-benchmark --offline --maps AcropolisLE TritonLE --seed-start 101 --games 3 --dry-run
```

That previews 18 games across two maps and three races without launching them.
Remove `--dry-run` after installing the required maps to run the matrix. Hold the
engine/difficulty/speed and memory settings equal, compare one change against an
unchanged baseline, and measure regressions alongside wins. `--opening` can fix an
opening profile. `--analysis-offline` avoids review API calls during live model
benchmarks; `--no-analysis` skips review reports.
Each match has its own trace, replay, and result report. `benchmark.json` records
attempts, completed games, wins, win rate, and launch errors. Failed launches are
excluded from the win-rate denominator. Games run sequentially.
Benchmarks disable cross-match memory by default to keep the controller fixed.
Pass `--learn --memory-dir memory` for an explicitly labelled training run that
shares experience between matches. Use the same context settings when comparing results.

## Architecture and development

| Component | Responsibility |
|---|---|
| `state.py` | Engine-independent snapshots; individual enemy sightings, expiry, and confirmed deaths |
| `actions.py` | Bounded investment/stance vocabulary, prerequisites, baseline build, safety override |
| `system1.py` | Jev `AsyncTypeSafeClient.system_one`: Choice investment, Choice stance, Noul replan |
| `system2.py` | Bounded Pydantic `Plan`; OpenAI `AsyncOpenAI().responses.parse` |
| `mind.py` | Non-blocking scheduling, validation, timeouts, freshness, confidence, and shutdown |
| `memory.py` | Persistent SQLite episodes, observed lessons, comparable retrieval, bounded match timeline |
| `learning.py` | Validated opening catalog and evidence-based exploration/selection |
| `executor.py` | Supply/workers before investment; reserved production; queued reactors; scouting/combat |
| `bot.py` | SC2 callbacks, worker distribution, enemy death events, periodic snapshots |
| `doctor.py` / `cli.py` | Startup checks and match/scenario entry points |
| `scenarios.py` / `benchmark.py` | Snapshot regression cases and real-match evaluation |
| `analysis.py` | Post-game measured evidence, constrained OpenAI reviews, and controlled-test hypotheses |

```sh
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run pytest
uv build
```

Tests use fake models and game objects. They require neither an SC2 installation
nor API keys; one test explicitly blocks all `sc2` imports while exercising the
core coordinator and scenario suite. CI runs these checks on every push and PR.

Traces contain plans, tactical confidence, request IDs when available, accepted
choices, failures, discarded replies, investments, snapshots, and game results.
Replays are saved beside the trace for actual matches. No win-rate claim is made
until the real-match harness has been run.
See [the implementation review](docs/review.md) for resolved findings and the remaining
runtime validation limits.

The first version has no medivacs, drops, detection, or squad control. Next gameplay
work should follow real replay evidence: defend early rushes, add healing and air
support, and tune the build and confidence policy against measured match results.

SDK references: [TypeSafe's Python SDK](https://github.com/typesafe-ai/typesafe-sdk-python),
[burnysc2 API](https://burnysc2.github.io/python-sc2/bot_ai/index.html), and
[Blizzard's SC2 API and engine packages](https://github.com/Blizzard/s2client-proto).

## Replay studio in Zana

The [SC2 Replays plugin](zcc-plugin-sc2-replays/README.md) keeps the tactical replay viewer available from Zana's sidebar. It includes a searchable match library, synchronized recorded Jev/OpenAI decisions, favorites, timestamped bookmarks, notes, and original replay downloads. It is installed locally as `sc2-replays`.

New recordings in `runs/` appear automatically. Click **Prepare replay** to export their actual unit movement through the existing engine, without playing a new match, calling models, or modifying bot learning. Playback is a full-map 2D view with roughly two-second samples and interpolated movement. Notes/bookmarks are stored separately in Zana's plugin storage.

For manual preparation with a compatible native engine:

```bash
uv run python -m sc2mind.replay runs/<match>/replay.SC2Replay
```

Docker preparation is available directly in the plugin and reuses this project's engine configuration. See the plugin's [feature ideas](zcc-plugin-sc2-replays/ROADMAP.md).
