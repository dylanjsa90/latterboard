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

Stack: Python 3.12, FastAPI, SQLAlchemy 2, Redis, SQLite locally and Postgres in production.

## Setup

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and run a local Redis, then:

```bash
uv sync                           # install dependencies
cp .env.example app/.env.local    # configure; at minimum set SECRET_KEY
uv run prek install               # optional: run ruff on each commit
```

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

1. Copy `.env.example` to `.env` in the project root and fill it in. Compose defaults to
   `APP_ENV=production` and `DB_TYPE=postgres`, so set `SECRET_KEY` and the `DB_*` connection
   settings. The image contains no env files; everything comes from this `.env`.
2. Start the app and Redis:

   ```bash
   docker compose up --build
   ```

   The API is at `http://localhost:8000`. The container waits for the database
   (`app/backend_pre_start.py`), then starts Uvicorn.
3. Stop it with `docker compose down` (`-v` also deletes the volumes).

To try the Postgres path without a hosted database, set `DB_HOST=db` and `POSTGRES_PASSWORD` in
`.env` and run `docker compose --profile local-db up --build`.

## Production server

The Docker image runs a single Uvicorn process. To run without Docker:

```bash
uv sync --no-dev
APP_ENV=production uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
```

Live updates go through Redis pub/sub, so more than one worker is fine.

## Copying to a VM

`scripts/upload-to-vm.sh user@host [remote_dir]` zips the tracked and untracked files (honouring
`.gitignore`, so `.env` files, local databases and `.venv` stay out) and copies the archive over
`scp`. Set `SSH_KEY` / `SSH_PORT` as needed, and `UNZIP=1` to unpack it into
`<remote_dir>/latterboard`. Env files are deliberately not copied; create the root `.env` on the
VM.

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
start-up check on pushes to `dev` and pull requests into `main`.

## License

[MIT](LICENSE)
