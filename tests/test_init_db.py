from sqlalchemy import create_engine, inspect, text

from app.init_db import ensure_user_columns

# Every nullable `user` column, which is what `ensure_user_columns` backfills.
PROFILE_COLUMNS = {"display_name", "birth_year", "location", "avatar_version", "is_bot"}


def test_ensure_user_columns_upgrades_a_legacy_user_table(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        # The `user` table as it was before profile fields existed.
        conn.execute(
            text(
                'CREATE TABLE "user" (id INTEGER PRIMARY KEY, email VARCHAR NOT NULL, '
                "username VARCHAR NOT NULL, hashed_password VARCHAR NOT NULL, "
                "is_active BOOLEAN, created_at DATETIME, updated_at DATETIME, "
                "is_superuser BOOLEAN)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO \"user\" (email, username, hashed_password) "
                "VALUES ('old@example.com', 'old@example.com', 'x')"
            )
        )

    assert set(ensure_user_columns(engine)) == PROFILE_COLUMNS
    assert PROFILE_COLUMNS <= {c["name"] for c in inspect(engine).get_columns("user")}
    # Idempotent: a second startup finds nothing to add.
    assert ensure_user_columns(engine) == []

    with engine.connect() as conn:
        row = conn.execute(text('SELECT username, display_name FROM "user"')).one()
    assert tuple(row) == ("old@example.com", None)
