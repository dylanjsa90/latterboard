# Latterboard API

[![CI](https://github.com/dylanjsa90/latterboard/actions/workflows/ci.yml/badge.svg?branch=dev)](https://github.com/dylanjsa90/latterboard/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A FastAPI backend for browser games, with JWT authentication (password or Google sign-in):

- Daily word, sudoku, memory, and cipher puzzles, with stats and streaks
- Game scores and all-time, daily, and monthly leaderboards
- Head-to-head matches (turn-based, races, and co-op), matchmaking with a computer opponent
  fallback, and single-use invite links
- Vocab Challenger rooms and wordbooks
- A websocket for invites, presence, and live match updates

Its client is [webcade](https://github.com/dylanjsa90/webcade). Contributor and agent
conventions are in [`AGENTS.md`](AGENTS.md); every REST call and websocket frame between the two
is in [`docs/reference/cross-repo-contract.md`](docs/reference/cross-repo-contract.md), and the
module map is in [`docs/architecture/backend.md`](docs/architecture/backend.md).

Stack: Python 3.12, FastAPI, SQLAlchemy 2, Redis, SQLite. Production runs under systemd on a VM
(see [Production server](#production-server)); Postgres is supported but unused.

## Setup

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and run a local Redis, then:

```bash
uv sync                           # install dependencies
cp .env.example app/.env.local    # configure; at minimum set SECRET_KEY
uv run alembic upgrade head       # create the database schema
uv run prek install               # optional: run ruff on each commit
```

Run `uv run alembic upgrade head` again whenever you pull a new migration. Writing
migrations, and adopting them on a database made before they existed, is covered in
[`docs/guides/database-migrations.md`](docs/guides/database-migrations.md).

Settings are read from `app/.env.local` while `APP_ENV=local` (the default) and from `app/.env`
otherwise; real environment variables win over either file. `.env.example` lists every setting.
The defaults use SQLite at `./sql_app.db` and Redis at `redis://localhost:6379`.

A local install seeds a sign-in, `first.user@test.com` / `password`. Any other `APP_ENV` skips it
and refuses to start without a real `SECRET_KEY`.

Google sign-in stays off (its routes return 404) until `GOOGLE_CLIENT_ID` is set to the public
web client ID, the same value as webcade's `VITE_GOOGLE_CLIENT_ID`.

## Development server

```bash
uv run fastapi dev app/main.py
```

Runs on `http://localhost:8000` and reloads on edits. OpenAPI schema at `/openapi.json`, Swagger
UI at `/docs`, health check at `/health`.

## Running with Docker

Requires [Docker](https://docs.docker.com/get-docker/) and Docker Compose.

1. Copy `.env.example` to `.env` in the project root and fill it in; at minimum set
   `SECRET_KEY`. Compose defaults to `APP_ENV=production` and SQLite in the `app_data` volume.
   The image contains no env files; everything comes from this `.env`.
2. Start the app and Redis:

   ```bash
   docker compose up --build
   ```

   The API is at `http://localhost:8000`. The container waits for the database
   (`app/backend_pre_start.py`), runs `alembic upgrade head`, then starts Uvicorn. Docker is an
   option, not how production runs.
3. Stop it with `docker compose down` (`-v` also deletes the volumes).

To try Postgres, set `DB_TYPE=postgres`, `DB_HOST=db` and `POSTGRES_PASSWORD` in `.env` and run
`docker compose --profile local-db up --build`. The app connects with `sslmode=require`, which
this container doesn't offer yet (see `docs/plans/follow-ups.md`).

## Production server

Production is a Linux VM running Uvicorn under systemd, with SQLite and a local Redis.
[`deploy/latterboard.service`](deploy/latterboard.service) is the unit: it sets
`APP_ENV=production`, runs `alembic upgrade head` before starting, and serves one Uvicorn
worker (SQLite takes one writer at a time).

On the VM, settings come from `app/.env` (`APP_ENV=production` reads that file, not
`app/.env.local`). Set a real `SECRET_KEY`, `DB_TYPE=sqlite`, and `SQLITE_DATABASE_URL` as an
absolute path, e.g. `sqlite:////home/latterboard/latterboard/sql_app.db`.

To deploy:

1. From your machine, `UNZIP=1 scripts/upload-to-vm.sh user@host [remote_dir]`. It zips the
   tracked and untracked files (honouring `.gitignore`, so env files, databases and `.venv`
   stay out), copies the archive over `scp` and unpacks it over `<remote_dir>/latterboard`.
   Set `SSH_KEY` / `SSH_PORT` as needed. Files deleted from the repo stay on the VM.
2. On the VM, in that directory:

   ```bash
   uv sync --frozen --no-dev
   APP_ENV=production .venv/bin/python -m app.backup_db  # if this deploy brings a migration
   sudo systemctl restart latterboard                    # runs alembic upgrade head, then Uvicorn
   journalctl -u latterboard -n 50                       # check it started
   ```

### Backing up and restoring the database

SQLite runs in WAL mode, which keeps the newest commits in a side file, `sql_app.db-wal`,
next to `sql_app.db` (plus an index, `sql_app.db-shm`). So the database is not one file, and
`cp` is the wrong tool both ways:

- **Back up** with `APP_ENV=production .venv/bin/python -m app.backup_db [dest]` (default
  `sql_app.db.bak`). It's safe while the service is running and includes the commits still
  in `-wal`. A `cp` of `sql_app.db` misses those, and can catch a commit halfway.
- **Restore** only with the service stopped, and delete the old `-wal` and `-shm` files
  first. Otherwise SQLite replays that `-wal` onto the restored file, mixing data from two
  points in time or corrupting it. A clean stop folds `-wal` back into the database, but a
  crash leaves it behind, and a crash is when you're most likely to restore.

```bash
sudo systemctl stop latterboard
# Optional: keep what you're about to discard (this includes the -wal contents).
APP_ENV=production .venv/bin/python -m app.backup_db sql_app.db.before-restore
rm -f sql_app.db-wal sql_app.db-shm
cp sql_app.db.bak sql_app.db
sudo systemctl start latterboard    # migrates the restored file to head, then serves
```

Use the path from `SQLITE_DATABASE_URL` if the database isn't in the app directory. In
Docker, run the backup with `docker compose exec app python -m app.backup_db`. The file is
`/data/sql_app.db` on the `app_data` volume.

## Tests and checks

Tests need a local Redis.

```bash
make check                          # ruff + mypy + pytest; fails if any of them fails
uv run pytest tests/test_scores.py  # one file
uv run ruff format app/ tests/      # format
```

With coverage:

```bash
uv run coverage run -m pytest
uv run coverage report
uv run coverage html                # generates htmlcov/index.html
```

CI (`.github/workflows/ci.yml`) runs lint, format, type check, tests, and a Docker build and
start-up check on pushes to `dev` and pull requests into `main`. It doesn't deploy.

## License

[MIT](LICENSE)
