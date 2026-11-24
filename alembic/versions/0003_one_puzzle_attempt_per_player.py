"""One puzzle attempt per player per puzzle.

Racing first moves could each insert a row for the same (user_id, puzzle_id). Keep the
most finished one (completed, then won, then the most moves, then the oldest), delete
the rest, and replace the plain (user_id, puzzle_id) index with a unique constraint.
The deleted rows' moves are lost; the kept row is the one stats already counted.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-13 10:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DELETE_DUPLICATES = """
DELETE FROM puzzle_attempt WHERE id NOT IN (
    SELECT id FROM (
        SELECT id, ROW_NUMBER() OVER (
            PARTITION BY user_id, puzzle_id
            ORDER BY completed DESC, won DESC, attempt_count DESC, id
        ) AS rank
        FROM puzzle_attempt
    ) AS ranked
    WHERE rank = 1
)
"""


def upgrade() -> None:
    op.execute(sa.text(DELETE_DUPLICATES))
    with op.batch_alter_table("puzzle_attempt") as batch_op:
        batch_op.drop_index("ix_puzzle_attempt_user_puzzle")
        batch_op.create_unique_constraint(
            "uq_puzzle_attempt_user_id_puzzle_id", ["user_id", "puzzle_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("puzzle_attempt") as batch_op:
        batch_op.drop_constraint("uq_puzzle_attempt_user_id_puzzle_id", type_="unique")
        batch_op.create_index(
            "ix_puzzle_attempt_user_puzzle", ["user_id", "puzzle_id"], unique=False
        )
