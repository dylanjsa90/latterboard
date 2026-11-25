# Database migrations

Last updated: 2026-11-23

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
`server_default` (or a backfill before the constraint).

`Base.metadata` has a naming convention (`app/core/base_class.py`), so every constraint
gets a predictable name (`uq_<table>_<columns>`, `fk_…`, `pk_<table>`) and a later
migration can drop it on SQLite. Production's foreign keys and primary keys predate it and
are unnamed; to drop one, pass `naming_convention=` to `op.batch_alter_table`, which names
the reflected constraint by the same rule (as `0002` does for unique constraints).

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
so `alembic upgrade head` would try to create them again and fail. Bring it to the
baseline schema, mark it as at the baseline (`alembic stamp 0001`), then let `alembic
upgrade head` run every later migration. Do this **before** the first `alembic upgrade
head` against it.

Always stamp `0001`, never `head` or a later revision. Stamping only records a revision;
it runs nothing. Stamping past `0001` skips those migrations' changes for good, and
`alembic upgrade head` then reports success against a schema that is missing them. In
November 2026 production was stamped at `0003` while it lacked `vocab_room` and
`vocab_saved_word`, so the app failed with `no such table: vocab_room`. It was repaired
on 2026-11-23 with the steps below and now matches the models at `0003`, so this is only
needed for another old database, such as a local `sql_app.db` from before migrations.

`app.schema_drift` compares against the models (head), not the baseline, so an old
database never reaches "No differences" before it is stamped. The goal is that it reports
**only** the changes later migrations make. At `0003` those are exactly:

```
remove_index ix_puzzle_attempt_user_puzzle on puzzle_attempt
add_constraint uq_puzzle_attempt_user_id_puzzle_id on puzzle_attempt
add_constraint uq_user_identity_provider_subject on user_identity
add_constraint uq_vocab_saved_word_user_id_word_id on vocab_saved_word
```

(`0002` names the two unique constraints `create_all` left unnamed, and `0003` swaps the
attempt index for a unique constraint.) When a new migration lands, add its differences
to this list.

On a VM, in the app directory:

1. `uv sync --frozen --no-dev`, then stop the service and back up the database:
   `sudo systemctl stop latterboard && APP_ENV=production .venv/bin/python -m app.backup_db sql_app.db.pre-alembic`.
2. Check for drift: `APP_ENV=production .venv/bin/python -m app.schema_drift`.
3. Reconcile anything it reports beyond the list above:
   - `add_index`: databases from before migrations commonly lack `ix_user_is_bot`,
     because the startup column patch added `user.is_bot` without its index. Create it
     through the app's own settings, so it lands in the same file (no `sqlite3` CLI
     needed):

     ```bash
     APP_ENV=production .venv/bin/python -c "
     from sqlalchemy import text
     from app.database import engine
     with engine.begin() as conn:
         conn.execute(text('CREATE INDEX IF NOT EXISTS ix_user_is_bot ON \"user\" (is_bot)'))
     "
     ```

   - `add_table`: the database predates that model. Create the tables from the models
     (their indexes come with them), naming each table the report lists:

     ```bash
     APP_ENV=production .venv/bin/python -c "
     import app.models
     from app.core.base_class import Base
     from app.database import engine
     Base.metadata.create_all(engine, tables=[Base.metadata.tables[t] for t in ('vocab_room', 'vocab_saved_word')], checkfirst=True)
     "
     ```

     A table created this way already has its constraint names, so it drops out of the
     list above (here, the `vocab_saved_word` line).

   Anything else (columns, other constraints) needs a closer look before going further.
4. Re-run step 2 until it reports only lines from the list above.
5. Stamp it at the baseline: `APP_ENV=production .venv/bin/alembic stamp 0001`.
6. `APP_ENV=production .venv/bin/alembic upgrade head`, then re-run step 2; it must now
   report no differences. `0003` deletes duplicate puzzle attempts (keeping the most
   finished one per player and puzzle), losing their moves.
7. Install or update the unit if needed (`deploy/latterboard.service`), then
   `sudo systemctl start latterboard`.

For a local `sql_app.db`, run the same steps without `APP_ENV=production`. In Docker, use
`docker compose exec app …` against the running container, which sees the `app_data`
volume.

## Repairing a database stamped too far

Symptoms: `no such table` or a missing column at runtime while `alembic current` shows
head, or `app.schema_drift` reporting differences at head. The stamp skipped migrations
that never ran. Stop the service and back up as in step 1 above, then follow steps 2 to
4, step the stamp back with `alembic stamp 0001` (it rewrites `alembic_version` only,
running no downgrades), and finish with steps 6 and 7.
