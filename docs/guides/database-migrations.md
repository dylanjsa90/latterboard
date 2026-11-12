# Database migrations

Last updated: 2026-11-12

The schema is owned by Alembic (`alembic/versions/`). `alembic/env.py` takes the database
URL from the app's `Settings`, so `DB_TYPE`, `DB_*`, `APP_ENV` and the env files pick the
database exactly as they do for the app. The app itself never creates or alters tables;
`init_db` only seeds rows.

| Where | How the schema gets there |
| ----- | ------------------------- |
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
docker compose run --rm app python -m app.schema_drift     # on the VM, with the root .env
```

Read-only. Prints the database's Alembic revision and each difference from the models
(`add_index ix_user_is_bot on user`, `add_column user.location`, …), and exits 1 if
there are any.

## One-time: adopting migrations on an existing database

A database built by the old `create_all` startup has the tables but no `alembic_version`,
so `alembic upgrade head` would try to create them again and fail. Mark it as already at
the baseline instead (`alembic stamp`), but only once its schema matches the baseline
exactly. Do this **before** deploying the image that runs `alembic upgrade head`.

1. Build the new image without restarting the running one: `docker compose build app`.
2. Check for drift: `docker compose run --rm app python -m app.schema_drift`.
3. Reconcile anything it reports. Databases from before migrations commonly lack
   `ix_user_is_bot`, because the startup column patch added `user.is_bot` without its
   index. For an `add_index` line, create the index, e.g.

   ```sql
   CREATE INDEX IF NOT EXISTS ix_user_is_bot ON "user" (is_bot);
   ```

   Anything other than missing indexes needs a closer look before going further.
4. Re-run step 2 until it reports no differences.
5. Stamp it: `docker compose run --rm app alembic stamp 0001`.
6. Deploy as usual (`docker compose up -d`). `alembic upgrade head` is now a no-op until
   the next migration.

For a local `sql_app.db`, run the same steps with `uv run` instead of
`docker compose run --rm app`, and apply the index with `sqlite3 sql_app.db`.
