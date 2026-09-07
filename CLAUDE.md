# CLAUDE.md — Latterboard

FastAPI service for game scores and leaderboards with JWT auth. Python 3.10+, uv, SQLAlchemy + Alembic, Postgres 16 + Redis. Last reviewed: 2026-07-11.

Setup, run, test, and lint commands are all in `README.md` — use those (`uv run pytest`, `uv run ruff check app/ tests/`, `uv run mypy app/`).

## Conventions

- Daily counts and any per-day bucketing use the US Pacific timezone, not UTC (e.g. daily play limits in `app/api/routes/scores.py`).
- The DB driver is psycopg3 (`psycopg[binary]`), not psycopg2. Don't add psycopg2 to fix driver import errors; check the `DATABASE_URL` scheme instead.
- mypy is strict and ruff forbids print statements (T201).

## Deployment

- Deploys to Render as a Docker container using the repo `Dockerfile`. `DATABASE_URL`, `REDIS_URL`, and `SECRET_KEY` come from Render environment variables, mirroring `docker-compose.yml`.
