# Latterboard  API

A FastAPI service for tracking game scores and leaderboards with JWT authentication.

## Setup
If uv has not yet been installed instructions can be found at https://docs.astral.sh/uv/getting-started/installation/

```bash
uv sync                      # install dependencies
source .venv/bin/activate    # activate environment
cp app/.env.example app/.env # configure environment variables
```

Edit `app/.env` and set at minimum:

```
DATABASE_URL=sqlite:///./sql_app.db
SECRET_KEY=<generate with: python -c "import secrets; print(secrets.token_hex(32))">
REDIS_URL=redis://localhost:6379
```

## Development server

```bash
fastapi dev app/main.py
```

Runs on `http://localhost:8000`. Interactive docs at `/api/v1/openapi.json` (Swagger UI at `/docs`).

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

```bash
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
