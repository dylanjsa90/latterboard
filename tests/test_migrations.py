from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

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
