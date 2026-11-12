"""Compares the configured database's schema with the models. Read-only.

    uv run python -m app.schema_drift
    docker compose run --rm app python -m app.schema_drift   # on the VM

Prints the database's Alembic revision and every difference autogenerate finds, and
exits 1 if there are any. Run it before `alembic stamp` on a database that predates
migrations: stamping is only safe when this reports no differences.
"""

import sys
from typing import Any

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, create_engine

import app.models  # noqa: F401  (registers every model on Base.metadata)
from app.core.base_class import Base
from app.core.config import settings


def schema_diff(connection: Connection) -> list[Any]:
    """What autogenerate would put in a new migration for this database."""
    return list(compare_metadata(MigrationContext.configure(connection), Base.metadata))


def describe(entry: Any) -> str:
    """One line per difference, e.g. `add_column user.location`."""
    if isinstance(entry, list):  # column modifications come grouped
        return "; ".join(describe(item) for item in entry)
    op, *args = entry
    if op.startswith("modify_"):  # (op, schema, table, column, info, old, new)
        return f"{op} {args[1]}.{args[2]}: {args[4]!r} -> {args[5]!r}"
    if op.endswith("_column"):  # (op, schema, table, column)
        return f"{op} {args[1]}.{args[2].name}"
    # (op, table) or (op, index / constraint / foreign key); constraints may be unnamed.
    target = args[0]
    name = target.name or "(" + ", ".join(c.name for c in target.columns) + ")"
    table = getattr(target, "table", None)
    return f"{op} {name}" + (f" on {table.name}" if table is not None else "")


def main() -> int:
    engine = create_engine(settings.DATABASE_URL)
    with engine.connect() as conn:
        revision = MigrationContext.configure(conn).get_current_revision()
        diff = schema_diff(conn)
    engine.dispose()
    out = sys.stdout
    out.write(f"Alembic revision: {revision or 'none (not stamped)'}\n")
    if not diff:
        out.write("No differences: the schema matches the models.\n")
        return 0
    out.write(f"{len(diff)} difference(s):\n")
    for entry in diff:
        out.write(f"  {describe(entry)}\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
