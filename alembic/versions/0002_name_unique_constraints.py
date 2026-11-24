"""Name the unique constraints the baseline left unnamed.

Production's tables came from `create_all` before `Base.metadata` had a naming
convention, so `user_identity (provider, subject)` and `vocab_saved_word (user_id,
word_id)` have no names there, and a later SQLite migration couldn't drop them. Batch
mode reflects each table with the convention, which names the unnamed constraint so it
can be dropped, then adds it back under that name. A database migrated since the
convention already has the names; this rebuilds the same constraints.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-13 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# A copy of the unique-constraint rule in Base.metadata's convention, so this
# migration keeps meaning the same thing if the app's convention changes.
NAMING_CONVENTION = {"uq": "uq_%(table_name)s_%(column_0_N_name)s"}

UNIQUES = {
    "user_identity": ["provider", "subject"],
    "vocab_saved_word": ["user_id", "word_id"],
}


def upgrade() -> None:
    for table, columns in UNIQUES.items():
        name = f"uq_{table}_{'_'.join(columns)}"
        with op.batch_alter_table(
            table, naming_convention=NAMING_CONVENTION
        ) as batch_op:
            batch_op.drop_constraint(name, type_="unique")
            batch_op.create_unique_constraint(name, columns)


def downgrade() -> None:
    # The names change nothing about what the constraints enforce, so they stay.
    pass
