from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import UniqueConstraint, create_engine, inspect, text

from app.core.base_class import Base
from app.schema_drift import schema_diff

ALEMBIC_DIR = Path(__file__).resolve().parents[1] / "alembic"


def _config() -> Config:
    # No ini file, so env.py leaves logging alone.
    config = Config()
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    return config


def _run(engine, fn, revision: str) -> None:
    config = _config()
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        fn(config, revision)


def test_migrations_have_a_single_head():
    assert len(ScriptDirectory.from_config(_config()).get_heads()) == 1


def test_migrations_build_exactly_the_models(tmp_path):
    """Fails when a model changes without a migration (or a migration without a
    model change): add one with `alembic revision --autogenerate`."""
    engine = create_engine(f"sqlite:///{tmp_path / 'migrated.db'}")
    with engine.connect() as conn:
        assert schema_diff(conn), "an empty database should differ from the models"

    _run(engine, command.upgrade, "head")
    with engine.connect() as conn:
        assert schema_diff(conn) == []

    _run(engine, command.downgrade, "base")
    assert inspect(engine).get_table_names() == ["alembic_version"]


def test_upgrade_names_the_baselines_unnamed_unique_constraints(tmp_path, monkeypatch):
    """Production's tables came from `create_all` before `Base.metadata` had a naming
    convention, so its unique constraints have no names. Autogenerate ignores names, so
    the test above can't see that; a later SQLite migration couldn't drop them."""
    engine = create_engine(f"sqlite:///{tmp_path / 'migrated.db'}")
    # Alembic names new constraints with the target metadata's convention; without
    # one, 0001 builds the unnamed constraints production has.
    monkeypatch.setattr(
        Base.metadata, "naming_convention", {"ix": "ix_%(column_0_label)s"}
    )
    _run(engine, command.upgrade, "0001")
    assert [
        uq["name"] for uq in inspect(engine).get_unique_constraints("user_identity")
    ] == [None]
    monkeypatch.undo()

    _run(engine, command.upgrade, "head")
    inspector = inspect(engine)
    expected = {
        (table.name, constraint.name)
        for table in Base.metadata.sorted_tables
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    migrated = {
        (table, uq["name"])
        for table in inspector.get_table_names()
        for uq in inspector.get_unique_constraints(table)
    }
    assert migrated == expected


def test_upgrade_keeps_one_attempt_per_player_and_puzzle(tmp_path):
    """Racing first moves could each insert an attempt before the unique constraint
    existed. The upgrade keeps the most finished one: completed, then won, then the
    most moves, then the oldest."""
    engine = create_engine(f"sqlite:///{tmp_path / 'migrated.db'}")
    _run(engine, command.upgrade, "0002")
    rows = [
        # (id, user, puzzle, attempts, won, completed)
        (1, 1, "word-2026-11-01", 2, False, False),
        (2, 1, "word-2026-11-01", 3, True, True),
        (3, 1, "word-2026-11-01", 5, False, False),
        (4, 1, "cipher-2026-11-01", 4, False, False),
        (5, 1, "cipher-2026-11-01", 4, False, False),
        (6, 1, "sudoku-2026-11-01", 9, False, True),
        (7, 1, "sudoku-2026-11-01", 3, True, True),
        (8, 2, "word-2026-11-01", 1, False, False),
    ]
    with engine.begin() as conn:
        for user_id in (1, 2):
            conn.execute(
                text(
                    'INSERT INTO "user" (id, email, username, hashed_password,'
                    " is_active, is_superuser) VALUES (:id, :email, :name, 'x', 1, 0)"
                ),
                {"id": user_id, "email": f"u{user_id}@test.com", "name": f"u{user_id}"},
            )
        for row_id, user_id, puzzle_id, count, won, completed in rows:
            conn.execute(
                text(
                    "INSERT INTO puzzle_attempt (id, user_id, game, puzzle_id,"
                    " attempt_count, won, completed, guesses)"
                    " VALUES (:id, :user, :game, :puzzle, :count, :won, :done, '[]')"
                ),
                {
                    "id": row_id,
                    "user": user_id,
                    "game": puzzle_id.split("-")[0],
                    "puzzle": puzzle_id,
                    "count": count,
                    "won": won,
                    "done": completed,
                },
            )

    _run(engine, command.upgrade, "head")
    with engine.connect() as conn:
        kept = conn.execute(text("SELECT id FROM puzzle_attempt ORDER BY id")).scalars()
        assert list(kept) == [2, 4, 7, 8]
