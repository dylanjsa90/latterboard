# Latterboard

> FastAPI backend for webcade's browser games: daily puzzles, snake scores and leaderboards,
> head-to-head matches with matchmaking and a computer opponent, and a websocket for live play.

Last updated: 2026-09-30

Python 3.10+ with uv, SQLAlchemy, Redis, JWT auth. SQLite locally; deploys to a VM as a Docker
container built from `Dockerfile`. Setup, Docker, and the production server are in `README.md`.

## Commands

| Command                         | Does                                                   |
| ------------------------------- | ------------------------------------------------------ |
| `make check`                    | mypy + pytest, printing only errors and summary lines  |
| `uv run pytest`                 | Tests (~55s, needs the local Redis)                    |
| `uv run ruff check app/ tests/` | Lint (logging instead of `print`: ruff enforces T201)  |
| `uv run mypy app/`              | Type check, strict mode                                |
| `fastapi dev app/main.py`       | Dev server; auto-reloads, so it picks up edits as you work |

ruff and mypy already report many errors on `dev`, so compare only the files you touched. For
demos, export `DB_TYPE=sqlite SQLITE_DATABASE_URL=sqlite:///<scratch file>` so seeding doesn't
touch the real `sql_app.db`.

## Key Context Files

Paths are in backticks, not `@` imports, so they load only when read.

| When working on...                                            | Read first                                        |
| ------------------------------------------------------------- | ------------------------------------------------- |
| Finding a file, what a module exports                         | `docs/architecture/backend.md`                    |
| Matches, race/co-op modes, matchmaking, the computer opponent | `docs/architecture/matches.md`                    |
| Any REST call, websocket frame, or new endpoint               | `docs/reference/cross-repo-contract.md`           |
| webcade, the React client                                     | `~/projects/webcade/AGENTS.md`                    |

## Conventions

### Settings and database
- `Settings` (`app/core/config.py`) reads `app/.env.local` while `APP_ENV=local` (the default) and `app/.env` otherwise.
- The database URL is computed from `DB_TYPE`, not read from `DATABASE_URL`: `sqlite` (the default) uses `SQLITE_DATABASE_URL`; any other value builds `postgresql+psycopg2://` from the lowercase `user`, `password`, `host`, `port`, `dbname` env vars. A `DATABASE_URL` env var only fills the unused `RAW_DATABASE_URL`, so the psycopg3 rewrite in `app/database.py` never runs. Both `psycopg[binary]` and `psycopg2-binary` are installed; for driver import errors, check `DB_TYPE` and the URL scheme before touching dependencies.
- Tables come from `Base.metadata.create_all` (`app/init_db.py`). There are no Alembic migrations yet (`alembic/` is a scaffold with no `versions/`, and its `env.py` reads `DATABASE_URL`, which the app itself ignores), so new columns and constraints won't reach existing databases. Call this out whenever you change a model.
- The one exception is `ensure_user_columns` (same file): at startup it adds any missing **nullable** `user` column. New `User` fields must therefore be nullable; anything else still needs that call-out.

### Dates and times
- DB `DateTime` columns are **naive UTC** (SQLite returns them naive). Use `app.utils.utcnow()` for anything compared to or stored in them. Comparing against `datetime.now(timezone.utc)` raises `TypeError`.
- Calendar dates are UTC: routes, seeding, and tests. Use `app.game.puzzles.today()`, never `date.today()`, which is local time and runs a day off in US evenings.
- The scores daily cap is the exception, and it is inconsistent: `POST /scores/` and `GET /scores/me/{game}/daily-count` count the US Pacific day, while `GET /scores/me/{game}/plays-today` and the cross-repo contract say UTC. Confirm which is intended before changing daily limits.

### Redis
- Always use `settings.REDIS_URL`; never hardcode `localhost`. Docker compose sets `redis://redis:6379`.
- The pub/sub client in `ConnectionManager` needs `socket_timeout=None`. redis-py 8 defaults to 5s, so an idle channel times out, `_listen` exits, and broadcasts stop without any error. The `redis>=7.4.0` pin allows 8.x.
- `ConnectionManager` creates its Redis client in `start()` because `stop()` closes it. Don't share one client across restarts.
- fastapi-cache `@cache` doesn't work on endpoints that depend on `db`/`current_user`. The default key includes their reprs, which change every request, so it never hits and only piles up keys.

### Users
- `username` is the public handle: `[a-z0-9_]{3,20}`, lowercased by `schemas/user.py`. Look it up case-insensitively (`crud_user.get_user_by_username`). Accounts made before handles still have their email there, so never validate `username` on output.
- `birth_year` is private. Only `UserPrivate` (test-token, `POST /users/`, `PATCH /users/me`) carries it; `UserPublic` (returned to any signed-in user) and `PlayerPublic` must not.
- Google sign-in (`app/core/google.py` checks the ID token, `app/api/google_sign_in.py` holds what the two routes share) links accounts through `user_identity` (`provider`, `subject` = Google's `sub`, never the email). It is a table rather than `user` columns so `create_all` builds its unique constraint. `GOOGLE_CLIENT_ID` (the public web client ID, the same as webcade's `VITE_GOOGLE_CLIENT_ID`) turns it on; tests replace `google.verify_google_id_token`.
- Photos live in `user_avatar` (a separate table, so loading a user never loads the bytes). `app/lib/avatar.py` re-encodes every upload to a 256px WebP, which drops EXIF such as phone GPS. `user.avatar_version` versions the public URL, so it can be cached forever.

### Email
- `render_email_template` uses a plain jinja `Template`, which does **not** autoescape. Escape anything a player wrote (usernames, invite messages) with `markupsafe.escape`, as `generate_match_invite_email` does.
- Templates: edit `app/email-templates/src/*.mjml` and keep `build/*.html` in step. There's no MJML toolchain in the repo, so `build/match_invite.html` is hand-written.
- `send_email` asserts `settings.emails_enabled` (SMTP host and from-address set). Check it before sending; invite links report `emailed: false` instead when it's off.

### Puzzles
- Future-dated puzzle ids must return 404. Otherwise the endpoints leak upcoming answers and create rows for arbitrary dates.
- `PuzzleAttempt.puzzle_id` is a string key like `"word-2026-09-11"`, not a foreign key to `Puzzle.id`.
- `app/game/puzzle_seed_data.py`: use `shuffled_words()`, which returns a copy. Don't shuffle or mutate the shared word list; `wordle.py` uses it too.
- Known open issues (not fixed yet):
  - `attempt_count` comes from the client, so anyone can send 6 and get today's word answer.
  - Word guesses aren't checked with `wordle.is_valid_word`.
  - `(user_id, puzzle_id)` has no unique constraint.

### Tests
- `tests/conftest.py` sets env vars **before** any app import, because `Settings()` reads them at import time. Keep new overrides above the imports.
- Each test drops the DB, recreates it, and re-seeds it with `init_db`. `WS_HEARTBEAT_SECONDS=3600` stops heartbeat frames from interleaving with the websocket frames tests assert on.
- Tests use the same Redis as the dev server (default `REDIS_URL`), so test broadcasts and presence keys show up in dev Redis.
- Two-player fixtures `inviter_headers` / `opponent_headers` (users `inviter` / `opponent`) live in `conftest.py`.
- To simulate a stale concurrent write, keep a reference to the row you loaded. The SQLAlchemy identity map holds objects weakly, so an unreferenced row gets garbage-collected and the next query silently reads fresh state, and the test passes without exercising the conflict.

## Project Policies

- Work on `dev`; open PRs into `main`. Never push to `main` directly.
- `.github/workflows/ci.yml` runs lint, typecheck, test, and a Docker build check on PRs into
  `main` and pushes to `dev`. It's a quality gate only — no deploy step.
- Agents commit or push only when asked.
- A change is done when `make check` is clean for the files you touched — plus `npm run check` in webcade when both repos changed.

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
