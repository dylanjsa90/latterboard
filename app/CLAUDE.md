# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

# Project notes (Latterboard API)

Commands: `uv run pytest`, `uv run ruff check app/ tests/`, `uv run mypy app/`. Dev server: `fastapi dev app/main.py` (auto-reloads, so it picks up edits while you work).

## Dates and times
- DB `DateTime` columns are **naive UTC** (SQLite returns them naive). Use `app.utils.utcnow()` for anything compared to or stored in them. Comparing against `datetime.now(timezone.utc)` raises `TypeError`.
- Calendar dates are UTC everywhere: routes, seeding, and tests. Use `app.game.puzzles.today()`, never `date.today()`, which is local time and runs a day off in US evenings.

## Redis
- Always use `settings.REDIS_URL`; never hardcode `localhost`. Docker compose sets `redis://redis:6379`.
- The pub/sub client in `ConnectionManager` needs `socket_timeout=None`. redis-py 8 defaults to 5s, so an idle channel times out, `_listen` exits, and broadcasts stop without any error. The `redis>=7.4.0` pin allows 8.x.
- `ConnectionManager` creates its Redis client in `start()` because `stop()` closes it. Don't share one client across restarts.
- fastapi-cache `@cache` doesn't work on endpoints that depend on `db`/`current_user`. The default key includes their reprs, which change every request, so it never hits and only piles up keys.

## Database / schema
- Tables come from `Base.metadata.create_all` (`app/init_db.py`). There are no Alembic migrations yet, so new columns and constraints won't reach existing databases. Call this out whenever you change a model.
- `PuzzleAttempt.puzzle_id` is a string key like `"word-2026-09-11"`, not a foreign key to `Puzzle.id`.

## Puzzles
- Future-dated puzzle ids must return 404. Otherwise the endpoints leak upcoming answers and create rows for arbitrary dates.
- `app/game/puzzle_seed_data.py`: use `shuffled_words()`, which returns a copy. Don't shuffle or mutate the shared word list; `wordle.py` uses it too.
- Known open issues (not fixed yet):
  - `attempt_count` comes from the client, so anyone can send 6 and get today's word answer.
  - Word guesses aren't checked with `wordle.is_valid_word`.
  - `(user_id, puzzle_id)` has no unique constraint.

## Tests
- `tests/conftest.py` sets env vars **before** any app import, because `Settings()` reads them at import time. Keep new overrides above the imports.
- Each test drops the DB, recreates it, and re-seeds it with `init_db`. `WS_HEARTBEAT_SECONDS=3600` stops heartbeat frames from interleaving with the websocket frames tests assert on.
- Tests use the same Redis as the dev server (default `REDIS_URL`), so test broadcasts and presence keys show up in dev Redis.