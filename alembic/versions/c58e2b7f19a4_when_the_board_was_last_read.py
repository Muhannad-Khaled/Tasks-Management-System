"""When the board was last read

Without it, "no differences" says nothing useful: it could mean the board
matches the plan, or that nobody has looked since the change was made. Those
are opposite facts and the PM was being shown the same sentence for both.

Revision ID: c58e2b7f19a4
Revises: 7a4c11d0e93b
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "c58e2b7f19a4"
down_revision: Union[str, Sequence[str], None] = "7a4c11d0e93b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("board_checked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "board_checked_at")
