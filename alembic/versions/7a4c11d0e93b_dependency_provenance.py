"""Where each task dependency came from

An arrow between two tasks moves every date downstream of it, and until now it
was the one claim in the plan with no recorded source. Existing rows are left
blank rather than backfilled: nobody recorded their provenance, and inventing
it here would be the same fabrication the column exists to catch.

Revision ID: 7a4c11d0e93b
Revises: f601e4e5ea1d
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "7a4c11d0e93b"
down_revision: Union[str, Sequence[str], None] = "f601e4e5ea1d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "task_dependencies",
        sa.Column("source_status", sa.String(length=16), nullable=False, server_default=""),
    )
    op.add_column(
        "task_dependencies",
        sa.Column("source_chunk_keys", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "task_dependencies",
        sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("task_dependencies", "rationale")
    op.drop_column("task_dependencies", "source_chunk_keys")
    op.drop_column("task_dependencies", "source_status")
