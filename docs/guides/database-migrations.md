# Database migrations

Last updated: 2026-11-12

The schema is owned by Alembic (`alembic/versions/`). `alembic/env.py` takes the database
URL from the app's `Settings`, so `DB_TYPE`, `DB_*`, `APP_ENV` and the env files pick the
database exactly as they do for the app. The app itself never creates or alters tables;
`init_db` only seeds rows.

| Where | How the schema gets there |
| ----- | ------------------------- |
| Production VM | The systemd unit (`deploy/latterboard.service`) runs `alembic upgrade head` in `ExecStartPre`; a failed migration leaves the service stopped |
| Docker image | `CMD` runs `alembic upgrade head` before Uvicorn; a failed migration stops the container |
| Local dev | `uv run alembic upgrade head` after setup and after pulling a new migration |
| Tests | `create_all` per test (fast); `tests/test_migrations.py` checks the migrations build exactly the models |

## Changing a model

1. Edit the model.
2. Generate the migration against a database that is at head:

   ```bash
   uv run alembic revision --autogenerate -m "add user.timezone"
   ```

3. Read the generated file. Autogenerate misses renames (it emits a drop plus an add,
   losing the data), server-default changes, and data backfills; write those by hand.
   On SQLite the operations run inside `batch_alter_table`, which copies the table.
4. `uv run alembic upgrade head`, then `uv run alembic downgrade -1` and upgrade again to
   check both directions.
5. `uv run pytest tests/test_migrations.py` fails until the models and migrations agree.

Postgres needs a `NOT NULL` column added to a table with rows to have a
`server_default` (or a backfill before the constraint). Unique constraints should be
named, so a later migration can drop them on SQLite.

## Checking a database: `app.schema_drift`

```bash
uv run python -m app.schema_drift                          # local
APP_ENV=production uv run python -m app.schema_drift       # on the VM (reads app/.env)
```

Read-only. Prints the database's Alembic revision and each difference from the models
(`add_index ix_user_is_bot on user`, `add_column user.location`, …), and exits 1 if
there are any.

## One-time: adopting migrations on an existing database

A database built by the old `create_all` startup has the tables but no `alembic_version`,
so `alembic upgrade head` would try to create them again and fail. Mark it as already at
the baseline instead (`alembic stamp`), but only once its schema matches the baseline
exactly. Do this **before** the first `alembic upgrade head` against it.

Production has been adopted (November 2026: it lacked only `ix_user_is_bot`, which was
added before stamping `0001`), so this is only needed for another old database, such as a
local `sql_app.db` from before migrations. On a VM, in the app directory:

1. `uv sync --frozen --no-dev`, then stop the service and back up the database:
   `sudo systemctl stop latterboard && APP_ENV=production .venv/bin/python -m app.backup_db sql_app.db.pre-alembic`.
2. Check for drift: `APP_ENV=production uv run python -m app.schema_drift`.
3. Reconcile anything it reports. Databases from before migrations commonly lack
   `ix_user_is_bot`, because the startup column patch added `user.is_bot` without its
   index. For an `add_index` line, create the index through the app's own settings, so it
   lands in the same file (no `sqlite3` CLI needed):

   ```bash
   APP_ENV=production .venv/bin/python -c "
   from sqlalchemy import text
   from app.database import engine
   with engine.begin() as conn:
       conn.execute(text('CREATE INDEX IF NOT EXISTS ix_user_is_bot ON \"user\" (is_bot)'))
   "
   ```

   Anything other than missing indexes needs a closer look before going further.
4. Re-run step 2 until it reports no differences.
5. Stamp it: `APP_ENV=production uv run alembic stamp 0001`.
6. Install or update the unit if needed (`deploy/latterboard.service`), then
   `sudo systemctl start latterboard`. `alembic upgrade head` is now a no-op until the
   next migration.

For a local `sql_app.db`, run the same steps without `APP_ENV=production`. In Docker, use
`docker compose exec app …` against the running container, which sees the `app_data`
volume.
