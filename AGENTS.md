# Agent notes

- Python 3.12 + uv. Run `uv run pytest` after changes; tests must not need SC2 or API keys.
- System 1 = TypeSafe Jev (`typesafe-sdk`, `AsyncTypeSafeClient.system_one`). Keep Jev state short and flat; only offer legal options.
- System 2 = OpenAI (`AsyncOpenAI().responses.parse` with the `Plan` pydantic schema).
- Policy (thresholds, overrides, legality) lives in code (`actions.py`, `mind.py`), never in prompts.
- Never block `on_step` on a model call; use background tasks in `Mind`.
- `state.py` / `actions.py` / `mind.py` must stay importable without `sc2`.
