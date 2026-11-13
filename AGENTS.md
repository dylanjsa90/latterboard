# Latterboard

> FastAPI backend for webcade's browser games: daily puzzles, snake scores and leaderboards,
> head-to-head matches with matchmaking and a computer opponent, Vocab Challenger rooms and
> wordbooks, and a websocket for live play.

Last updated: 2026-11-12

Python 3.10+ with uv, SQLAlchemy, Redis, JWT auth. SQLite everywhere. Production is a Linux VM
running Uvicorn under systemd (`deploy/latterboard.service`), not Docker; the `Dockerfile` and
compose file are an optional way to run it. Setup, Docker, and deploying are in `README.md`.

## Commands

| Command                         | Does                                                   |
| ------------------------------- | ------------------------------------------------------ |
| `make check`                    | ruff check + ruff format --check + mypy + pytest, printing only errors and summary lines; fails if any step fails |
| `uv run pytest`                 | Tests (under a minute, needs the local Redis)          |
| `uv run ruff check app/ tests/` | Lint (logging instead of `print`: ruff enforces T201)  |
| `uv run mypy app/`              | Type check, strict mode                                |
| `fastapi dev app/main.py`       | Dev server; auto-reloads, so it picks up edits as you work |

`ruff check`, `ruff format`, and `mypy` are clean on `dev`; keep them that way (`uv run ruff
format app/ tests/` fixes formatting; the prek hook does it on commit). Run `git config
blame.ignoreRevsFile .git-blame-ignore-revs` once so blame skips the bulk-format commit.
For demos, export `DB_TYPE=sqlite SQLITE_DATABASE_URL=sqlite:///<scratch file>` so seeding
doesn't touch the real `sql_app.db`.

## Key Context Files

Paths are in backticks, not `@` imports, so they load only when read.

| When working on...                                            | Read first                                        |
| ------------------------------------------------------------- | ------------------------------------------------- |
| Finding a file, what a module exports                         | `docs/architecture/backend.md`                    |
| Matches, race/co-op modes, matchmaking, the computer opponent | `docs/architecture/matches.md`                    |
| Any REST call, websocket frame, or new endpoint               | `docs/reference/cross-repo-contract.md`           |
| Changing a model, writing or running a migration              | `docs/guides/database-migrations.md`              |
| Picking up known issues, security and performance follow-ups  | `docs/plans/follow-ups.md`                        |
| webcade, the React client                                     | `~/projects/webcade/AGENTS.md`                    |

## Conventions

### Code layout
- Routes stay thin. Logic shared by routes (or by a route and the bot) goes in `app/services/`, row access in `app/crud/`, rules with no database or I/O in `app/game/`. Imports flow `api` → `services` → `crud` → `game`, never back up; the layer map is in `docs/architecture/backend.md`.
- The database session is synchronous, so it never runs on the event loop: routes and services that touch it are plain `def` (FastAPI runs them in its threadpool) and broadcast with `manager.broadcast_from_thread` / `send_to_user_from_thread`. An `async def` that awaits Redis hands its database work to `run_in_threadpool`. Every test fails if a query runs on the loop (the guard in `tests/conftest.py`).
- Import the module you need (`from app.crud.user import user as crud_user`, `from app.schemas.user import UserPublic`); packages don't re-export from `__init__`, except `app.models`.

### Settings and database
- `Settings` (`app/core/config.py`) reads `app/.env.local` while `APP_ENV=local` (the default) and `app/.env` otherwise, unless `SKIP_ENV_FILE` is set (the tests set it).
- The database URL is computed from `DB_TYPE`; a `DATABASE_URL` env var is ignored. `sqlite` (the default) uses `SQLITE_DATABASE_URL`; any other value builds `postgresql+psycopg2://…?sslmode=require` from `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `DB_NAME`, falling back to the lowercase `user`, `password`, `host`, `port`, `dbname` that older deployments set. Only `psycopg2-binary` is installed.
- `APP_ENV` (`local`, the default, / `staging` / `production`) is the one environment flag. Outside `local`, a missing or `changethis` `SECRET_KEY` stops startup and the `first.user@test.com` sign-in isn't seeded. `APP_ENV` picks the env file, so it must come from the real environment: the systemd unit sets `APP_ENV=production`, which reads `app/.env` on the VM. The Docker image carries no env files: compose passes the root `.env`.
- Alembic owns the schema; the app never creates or alters tables (`init_db` only seeds rows). Every model change ships with a migration from `uv run alembic revision --autogenerate`, read and fixed by hand; `tests/test_migrations.py` fails until models and migrations agree. Deploys run `alembic upgrade head` before Uvicorn starts (the systemd unit's `ExecStartPre`; the Docker `CMD` does the same). How-to and pitfalls: `docs/guides/database-migrations.md`.
- Locally, run `uv run alembic upgrade head` after setup and after pulling a migration. Tests use `create_all`, not migrations.

### Dates and times
- DB `DateTime` columns are **naive UTC** (SQLite returns them naive). Use `app.utils.utcnow()` for anything compared to or stored in them. Comparing against `datetime.now(timezone.utc)` raises `TypeError`.
- Calendar days are **Pacific** (`America/Los_Angeles`) everywhere: puzzles, the word of the day, score caps and leaderboards, streaks, invite-link limits, birth years. Use `app.game.puzzles.today()`, never `date.today()` (the host's zone) or `utcnow().date()` (UTC, a day ahead every evening).
- To filter a timestamp column by day, use a range from `day_start(day)` to `day_start(next_day)`, not `func.date(column)`, which gives the UTC date. Use `day_of(ts)` for a stored timestamp's day. Days last 23 or 25 hours across DST changes, so never add 24 hours to a day start.

### Redis
- Always use `settings.REDIS_URL`; never hardcode `localhost`. Docker compose sets `redis://redis:6379`.
- The pub/sub client in `ConnectionManager` needs `socket_timeout=None`. redis-py 8 defaults to 5s, so an idle channel times out, `_listen` exits, and broadcasts stop without any error. The `redis>=7.4.0` pin allows 8.x.
- `ConnectionManager` creates its Redis client in `start()` because `stop()` closes it. Don't share one client across restarts.

### Users
- `username` is the public handle: `[a-z0-9_]{3,20}`, lowercased by `schemas/user.py`. Look it up case-insensitively (`crud_user.get_user_by_username`).
- `email` and `birth_year` are private. Only `UserPrivate` (test-token, `POST /users/`, `PATCH /users/me`, the owner-or-superuser `PUT /users/{id}`) carries them; `UserPublic` (returned to any signed-in user) and `PlayerPublic` must not. `GET /users/` is superuser-only.
- Google sign-in (`app/core/google.py` checks the ID token, `app/services/google_sign_in.py` holds what the two routes share) links accounts through `user_identity` (`provider`, `subject` = Google's `sub`, never the email). `GOOGLE_CLIENT_ID` (the public web client ID, the same as webcade's `VITE_GOOGLE_CLIENT_ID`) turns it on; tests replace `google.verify_google_id_token`.
- Photos live in `user_avatar` (a separate table, so loading a user never loads the bytes). `app/services/avatar.py` re-encodes every upload to a 256px WebP, which drops EXIF such as phone GPS. `user.avatar_version` versions the public URL, so it can be cached forever.

### Email
- `render_email_template` (`app/core/email.py`) uses a plain jinja `Template`, which does **not** autoescape. Escape anything a player wrote (usernames, invite messages) with `markupsafe.escape`, as `generate_match_invite_email` does.
- Templates: edit `app/email-templates/src/*.mjml` and keep `build/*.html` in step. There's no MJML toolchain in the repo, so `build/match_invite.html` is hand-written.
- `send_email` asserts `settings.emails_enabled` (SMTP host and from-address set). Check it before sending; invite links report `emailed: false` instead when it's off.

### Puzzles
- Future-dated puzzle ids must return 404. Otherwise the endpoints leak upcoming answers and create rows for arbitrary dates.
- `PuzzleAttempt.puzzle_id` is a string key like `"word-2026-09-11"`, not a foreign key to `Puzzle.id`.
- The server decides word, sudoku, and cipher outcomes and counts a signed-in player's moves itself; a client's `attempt_count` is trusted only for guests, who record nothing. `PuzzleAttempt.guesses` holds word guesses or cipher codes (`encode_cipher`). Why: webcade's `docs/decisions/004-daily-puzzle-outcomes.md`.
- `app/game/puzzle_seed_data.py`: use `shuffled_words()`, which returns a copy. Don't shuffle or mutate the shared word list; `wordle.py` uses it too.
- Known open issues (not fixed yet; more in `docs/plans/follow-ups.md`):
  - Word guesses aren't checked with `wordle.is_valid_word`.
  - `(user_id, puzzle_id)` has no unique constraint. Adding one is a migration that must first remove duplicate rows.
  - `POST /puzzles/sudoku/move` trusts the board the client sends.

### Tests
- `tests/conftest.py` sets env vars **before** any app import, because `Settings()` reads them at import time. Keep new overrides above the imports. `SKIP_ENV_FILE=1` keeps `app/.env.local` out, so tests see only what conftest sets (including their own `SECRET_KEY`).
- Each run gets a fresh SQLite file in a temp dir; each test drops it, recreates it with `create_all`, and re-seeds it with `init_db`. SQLite skips fsync and passwords use a cheap argon2, both in tests only. `WS_HEARTBEAT_SECONDS=3600` stops heartbeat frames from interleaving with the websocket frames tests assert on.
- Tests use the same Redis as the dev server (default `REDIS_URL`), so test broadcasts and presence keys show up in dev Redis. Anything touching matchmaking uses the `empty_queues` fixture.
- Fixtures (`conftest.py`): `client`, `auth_headers` (the default user), `superuser_headers`, `inviter_headers` / `opponent_headers` (users `inviter` / `opponent`), `empty_queues`. Helpers (`tests/helpers.py`, never import from conftest): `db_session()`, `login`, `signup_and_login`, `start_match`, `match_detail`.
- To simulate a stale concurrent write, keep a reference to the row you loaded. The SQLAlchemy identity map holds objects weakly, so an unreferenced row gets garbage-collected and the next query silently reads fresh state, and the test passes without exercising the conflict.

## Project Policies

- Work on `dev`; open PRs into `main`. Never push to `main` directly.
- `.github/workflows/ci.yml` runs lint and format, typecheck, tests, and a Docker build plus
  start-up check (the image must serve `/health`) on PRs into `main` and pushes to `dev`. It's a
  quality gate only, with no deploy step.
- Agents commit or push only when asked.
- A change is done when `make check` passes — plus `npm run check` in webcade when both repos changed.

### Working style
- State your assumptions. When a request has more than one reading, or something is unclear, name it and ask rather than picking silently.
- Choose the simplest design a senior engineer would sign off on; when a simpler approach exists than the one asked for, say so and push back.
- Make surgical changes: touch only what the task needs, and remove the imports, variables, and functions your own change left unused.
- Turn tasks into verifiable goals: reproduce a bug in a failing test before fixing it, cover new validation with tests for invalid inputs, and keep tests green before and after a refactor.

## Documentation Maintenance

1. Put detail in `docs/` (`architecture/`, `decisions/`, `guides/`, `reference/`, `plans/` —
   create a directory only when it gets its first file), not in this file.
2. Add a row to **Key Context Files** for every new doc; remove the row when a doc is deleted.
3. Rewrite docs to reflect current state rather than appending updates, and bump
   `Last updated:`.
4. kebab-case file names; number ADRs (`001-websocket-topics.md`).
5. `docs/reference/cross-repo-contract.md` exists in both repos; change both copies together.

## Working efficiently (token hygiene)
- Read code with Read (offset/limit) or Grep -n, never `cat` multiple files in one Bash call.
- Read a file only when you are about to edit it or the module map (`docs/architecture/backend.md`) is insufficient.
- Verify with `npm run check` (webcade) / `make check` (latterboard). Run the targeted test file first; run the full check once at the end, not after every edit.
- Browser verification: prefer read_page, get_page_text, or javascript_tool assertions over screenshots. Never Read screenshot PNGs back from the scratchpad. If a screenshot times out, retry at most once, then use get_page_text.
- For features touching 5+ files, write a short file-by-file plan and confirm it before the first edit.
