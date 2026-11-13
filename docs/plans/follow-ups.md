# Follow-ups from the 2026-11 repo review

Last updated: 2026-11-12

Known issues the polish pass (startup and logging fixes, email privacy, repo hygiene,
Alembic, the `app/` restructure, tests, docs) found but deliberately left alone. Each is
small enough to be its own change. Delete an item when it ships; delete this file when it's
empty.

## Security

- **Require the `purpose` claim on access tokens.** Access tokens issued before it existed
  have none, and `deps._access_token_user_id` still accepts that so nobody was signed out.
  Once they have all expired (`ACCESS_TOKEN_EXPIRE_MINUTES`, 8 days after the deploy that
  added it), reject a missing `purpose` too.

## Bugs and gaps

- **The recovery email links to a page webcade doesn't have.** It points at
  `{FRONTEND_HOST}/reset-password?token=…`, which webcade serves as a 404. Either build the
  page in webcade (`POST /reset-password/` is ready) or hide recovery until it exists.
- **`POST /puzzles/sudoku/move` trusts the client's board.** It writes the cell into the
  board the request sends and calls the puzzle solved when that board matches the solution.
  Keep the board server-side, like word and cipher attempts.
- **The optional compose Postgres can't be reached.** `DATABASE_URL` always adds
  `sslmode=require`, and `postgres:16-alpine` has no SSL, so `--profile local-db` fails to
  connect. Add a `DB_SSLMODE` setting (default `require`). Production uses SQLite, so this
  only affects trying Postgres.
- **Word guesses aren't checked with `wordle.is_valid_word`**, and `puzzle_attempt` has no
  unique `(user_id, puzzle_id)`. Both are also listed under Puzzles in `AGENTS.md`. The
  constraint is the first real migration: remove duplicate rows first.

## Performance

- **N+1 user lookups in the match presenters.** `crud/match.py`'s `to_public`, `to_detail`,
  `to_pending_invite` and the `/me/history` items call `db.get(User, …)` per player and per
  guess; `/me/active` was
  already fixed to load in bulk (`tests/test_active_matches.py` guards it). Do the same here.
  While there, move the presenters out of `crud/` (into `services/`).

## Code health

- **Long functions:** `crud/match.py` `submit_guess` (63 lines) and `game/vocab.py`
  `apply_action` (67 lines).
- **Repeated win/loss and puzzle-id handling in `routes/puzzles.py`**; the word, sudoku and
  cipher routes each rebuild what `game.puzzles.dated_puzzle` could provide.
- **No constraint naming convention on `Base.metadata`.** Unnamed unique constraints can't be
  dropped by a SQLite batch migration. Adding a convention renames existing constraints, so it
  needs its own migration.
- **The tests' `TestClient` warns** that Starlette wants `httpx2` instead of `httpx`.
- **No error reporting.** `sentry-sdk` was removed as unused (it's still installed through
  `fastapi[standard]`); nothing reports exceptions beyond the container log.
