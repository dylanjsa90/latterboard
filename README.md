# Latterboard API

A FastAPI backend for browser games, with JWT authentication:

- Daily word, sudoku, memory, and cipher puzzles, with stats and streaks
- Game scores and all-time, daily, and monthly leaderboards
- Head-to-head matches (turn-based, races, and co-op), matchmaking with a computer opponent
  fallback, and single-use invite links
- A websocket for invites, presence, and live match updates

Its client is [webcade](https://github.com/dylanjsa90/webcade). Contributor and agent
conventions are in `AGENTS.md`.

## Setup
If uv has not yet been installed instructions can be found at https://docs.astral.sh/uv/getting-started/installation/

```bash
uv sync                           # install dependencies
source .venv/bin/activate         # activate environment
cp .env.example app/.env.local    # configure environment variables
```

Settings are read from `app/.env.local` while `APP_ENV=local` (the default) and from `app/.env`
otherwise. Edit the file and set at minimum:

```
SECRET_KEY=<generate with: python -c "import secrets; print(secrets.token_hex(32))">
```

The defaults use SQLite at `./sql_app.db` (`DB_TYPE=sqlite`, `SQLITE_DATABASE_URL`) and Redis at
`redis://localhost:6379` (`REDIS_URL`), so a local Redis must be running.

## Running with Docker

Requires [Docker](https://docs.docker.com/get-docker/) and Docker Compose.

**1. Create a `.env` file in the project root:**

```
POSTGRES_USER=postgres
POSTGRES_PASSWORD=supersecret
POSTGRES_DB=latterboard
SECRET_KEY=<generate with: python -c "import secrets; print(secrets.token_hex(32))">
```

**2. Start all services:**

```bash
docker compose up --build
```

The API will be available at `http://localhost:8000`. Compose starts Redis and a Postgres
container alongside the app, but the app waits only for Redis and, with the default
`DB_TYPE=sqlite`, stores its data in a SQLite file inside the container.

**3. Stop and remove containers:**

```bash
docker compose down          # stop containers
docker compose down -v       # also delete database volume
```

## Development server

```bash
fastapi dev app/main.py
```

Runs on `http://localhost:8000`. OpenAPI schema at `/openapi.json`, Swagger UI at `/docs`.

## Production server (Gunicorn)

Gunicorn is not in the default dependencies. Install it first:

```bash
uv add gunicorn
```

Then run with Uvicorn workers:

```bash
gunicorn app.main:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000
```

Adjust `--workers` to `(2 × CPU cores) + 1`. For a single-core machine, use `--workers 2`.

## Running tests

Tests need a local Redis.

```bash
make check                   # mypy + pytest, errors and summary lines only
uv run pytest                # run all tests
uv run pytest -v             # verbose output
uv run pytest tests/test_scores.py  # single file
```

With coverage:

```bash
uv run coverage run -m pytest
uv run coverage report
uv run coverage html         # generates htmlcov/index.html
```

## Linting and type checking

```bash
uv run ruff check app/ tests/    # lint
uv run ruff format app/ tests/   # format
uv run mypy app/                 # type check
```
