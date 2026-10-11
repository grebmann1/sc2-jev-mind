# Replay Studio ideas

The first version keeps playback simple and local: replay library, actual unit movement, recorded strategy/tactical choices, favorites, bookmarks, notes, background preparation, and original replay downloads.

## Next: understand decisions

Add a clickable decision timeline with strategic changes, policy overrides, stale/rejected choices, confidence, and provider latency. Distinguish what a model proposed, what policy applied, and what the executor actually issued. Only show evidence captured by the trace. An explicit “Explain this moment” action could later request a bounded model analysis with visible cost/provider information.

## Then: compare and learn

Compare worker and army growth, expansion timing, measured supply blocks, and outcomes across compatible game settings. Align by game time or a shared milestone. A learning journal should show completed episodes, comparable opening trials, the selection reason, and uncertainty. One victory is not a reliable win rate, and opening selection is not model-weight training.

## Separate mode: live dashboard

Show the current accepted plan, economy, army, and provider health while a game runs. The existing live trace has no full unit positions; a live tactical map needs a bounded observation stream. Keep this as a separate mode so normal replay viewing stays calm.

## Useful additions

- Measured automatic highlights: first expansion, first threat, long supply blocks, large army losses.
- Match and bookmark @-mentions to seed agent reviews in Zana.
- Bot-perspective visibility captured during export; never treat full-map spectator knowledge as something the bot saw.
- Portable replay collections and exports, with explicit support for foreign replays and engine-version compatibility.

The fuller brainstorm is saved in the SC2 project's Zana Library, tagged `idea`, as “SC2 Replay Studio: persistent playback and bot insight.”
