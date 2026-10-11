# Implementation review — 2026-10-11

The architecture is consistent with the project constraints: Jev chooses typed
legal tactical options, OpenAI returns a bounded Pydantic plan, and deterministic
code owns legality, thresholds, reservations, and SC2 orders. Model calls remain
background tasks. Core state/actions/coordinator/memory modules run without SC2.

## Findings resolved

| Finding | Resolution |
|---|---|
| Continuously producing barracks could never become idle for an add-on | Count all completed bare barracks as eligible; reserve one producer and let its existing queue finish. Other producers continue. |
| Unavailable initial Jev answers could leave investments empty indefinitely | Supply the legal baseline before a confident investment and after prolonged absence of a usable investment. Retain accepted choices while valid. |
| Zero local enemy supply was replaced with the distant remembered army | Distinguish an unknown local estimate from a measured zero. |
| A random opponent stayed labelled Random after scouting | Retain the observed enemy race across loss of vision. |
| Offline replanning erased a selected opening and could retain a temporary defensive posture | Replan from the selected opening, applying current-state adjustments each time. |
| Outcomes could contaminate later benchmark matches | Disable persistent experience in benchmarks by default; explicit `--learn` runs are labelled. |
| Game launch arguments allowed invalid negative/oversized seeds | Validate the SC2 unsigned 32-bit range before launch. |
| A partially initialized provider could leak its HTTP client | Close initialized clients even when the coordinator was never constructed. |
| An unexpected SDK/callback exception could abort an entire benchmark | Report the failed attempt as an error with a nonzero exit code; do not create completed-match evidence. |

## Added memory and learning

Completed observed matches are saved once in a local SQLite database. The evidence
includes match result, profiles and plans, measured economy/army/base peaks, first
base threat, supply-block observations, issued orders, provider failures, and a
bounded timeline. Gaps in observations are not counted as measured time. Episodes
come from game callbacks; scripted scenarios and failed/unobserved launches create
no training records. Interrupted processes cannot invent a completed match result.

Both models receive comparable experience. Jev receives short flat text; OpenAI
receives structured summaries and current-game observations. Database operations
run outside `on_step`. Missing/corrupt/unsupported memory leaves the game playable;
future schema versions are rejected without overwriting them.

The selector explores three bounded profiles before favoring their smoothed result
scores and continues occasional exploration. Historical filtering separates map,
race, difficulty, engine, controller, speed, time limit, and policy. The policy
version in `learning.py` must change when gameplay/learning semantics change enough
to make old results incomparable. Provider aliases can change behind a model name;
use explicit model versions when reproducibility matters.

## Verification and limits

120 tests pass, along with Ruff checks, formatting checks, package builds, and ten
scripted scenarios on both macOS and the Linux container. A temporary Docker-volume
fixture retained nine synthetic episodes across a container restart and selected
the profile with the stronger observed result score. That volume was removed;
the bot's real history remains empty. Compose configuration and Git whitespace
checks also pass.

Automated tests cover persistence/reload, duplicate finishes, context isolation,
opening exploration and preference from saved results, bounded evidence, thread
offloading, memory failures, both provider payloads, game-free core imports,
add-on reservation/draining, resource and supply accounting, timeouts, stale/illegal
responses, benchmark isolation, CLI validation, and installer archive checks.
Scripted scenarios check ten decision states. Packaging and container smoke checks
exercise the distributable runtime without a game engine or API keys.

Authenticated connection checks with the supplied keys returned a valid Jev
tactical decision and an OpenAI structured Plan. SC2 4.10 completed 10-second and
60-second offline engine checks and saved replays on this Apple Silicon Mac using
an explicit QEMU runner; Docker's default emulation crashed during map loading.
Those checks disabled memory and ended at their time limits, so they provide no
training records or win-rate evidence. Jev has returned valid in-game decisions
in approximately 250 ms. Two initial OpenAI calls exceeded the default 30-second
wall budget; the interrupted attempt saved no memory. This host now uses a
90-second System 2 background-call budget. After restarting, the live game collected
Jev decisions and accepted an OpenAI plan about 30 seconds into the match, with
no provider errors at that checkpoint. The game is continuing; a completed live
outcome and its stored episode remain to be verified. Replay tests
are still needed for build timing, placement, movement, combat, latency, and the
effectiveness of learning. The bot currently has Marine/Marauder combat only,
with no healing, detection, drops, or squad control. Match-result associations
are not causal proof and are not model weight training.
