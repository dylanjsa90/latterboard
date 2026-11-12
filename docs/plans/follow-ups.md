# Follow-ups from the 2026-11 repo review

Last updated: 2026-11-12

Known issues the polish pass (startup and logging fixes, email privacy, repo hygiene,
Alembic, the `app/` restructure, tests, docs) found but deliberately left alone. Each is
small enough to be its own change. Delete an item when it ships; delete this file when it's
empty.

## Needs a person, not code

- **Adopt migrations on production** before deploying anything from `7bead82` on: drift
  check, create `ix_user_is_bot`, `alembic stamp 0001`
  (`docs/guides/database-migrations.md`). Until then the new container exits at
  `alembic upgrade head`.
- **The deploy's environment moved.** The image no longer contains `app/.env`; the VM needs
  the root `.env` that `docker-compose.yml` reads (`SECRET_KEY`, `DB_*`, …).
- **Remove `first.user@test.com` from production.** Older startups seeded it everywhere with
  the password `password`; deployments no longer do, but an existing row stays.

## Security

- **Reset tokens are reusable.** A password-reset token works for its whole 48 hours, even
  after it has been used and even after a later password change. Fix: put a hash of the
  current password (or a `password_changed_at`) in the token and check it on reset.
- **A non-numeric token subject probably 500s on Postgres.** `deps.get_current_user` passes the
  token's `sub` straight to `db.get(User, …)`. A reset token used as a bearer token (its
  `sub` is an email) gives 404 on SQLite but most likely a `DataError` (500) on Postgres. Make
  `TokenPayload.sub` an `int`, or reject non-digits, so it's a 401. Separately, give reset and
  access tokens a `purpose` claim.
- **No minimum `SECRET_KEY` length.** Outside `local`, only empty and `changethis` are refused;
  HS256 wants at least 32 bytes (pyjwt warns below that).
- **Legacy usernames are emails.** Accounts made before handles still have their email in
  `username`, which `UserPublic` and `PlayerPublic` send to other players. Migrate them to a
  generated handle (and prompt for a real one), or mask them on output.

## Bugs and gaps

- **The recovery email links to a page webcade doesn't have.** It points at
  `{FRONTEND_HOST}/reset-password?token=…`, which webcade serves as a 404. Either build the
  page in webcade (`POST /reset-password/` is ready) or hide recovery until it exists.
- **`POST /puzzles/sudoku/move` trusts the client's board.** It writes the cell into the
  board the request sends and calls the puzzle solved when that board matches the solution.
  Keep the board server-side, like word and cipher attempts.
- **Word guesses aren't checked with `wordle.is_valid_word`**, and `puzzle_attempt` has no
  unique `(user_id, puzzle_id)`. Both are also listed under Puzzles in `AGENTS.md`. The
  constraint is the first real migration: remove duplicate rows first.

## Performance

- **N+1 user lookups in the match presenters.** `crud/match.py`'s `to_public`, `to_detail`,
  `to_pending_invite` and the `/me/history` items call `db.get(User, …)` per player and per
  guess; `/me/active` was
  already fixed to load in bulk (`tests/test_active_matches.py` guards it). Do the same here.
  While there, move the presenters out of `crud/` (into `services/`).
- **`async def` routes on a sync `Session`.** Most of `routes/matches.py`, `matchmaking.py`'s
  `join_queue` and `invite_links.py`'s `claim_invite_link` are `async def` for the broadcast,
  so their database calls block the event loop. Either make them plain `def` and broadcast
  through a sync hook, or run the database work in `run_in_threadpool`.

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
