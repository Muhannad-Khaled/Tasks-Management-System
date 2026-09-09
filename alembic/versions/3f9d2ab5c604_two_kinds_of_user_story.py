"""Two kinds of user story, and criteria that carry their evidence

A story hung off a requirement was inherited by every task implementing it, so
three technical tasks sharing one requirement carried one another's acceptance
criteria onto three separate Trello cards. Engineer stories hang off the task
instead, which is also the only level at which "how this gets built" means
anything.

Everything added is nullable or defaulted, and `kind` defaults to 'client' so
the stories already in the database keep the meaning they were written with.

Revision ID: 3f9d2ab5c604
Revises: e2f4a91c7b30
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "3f9d2ab5c604"
down_revision: Union[str, Sequence[str], None] = "e2f4a91c7b30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_stories",
        sa.Column("task_id", sa.String(36), nullable=True),
    )
    op.add_column(
        "user_stories",
        sa.Column("kind", sa.String(16), nullable=False, server_default="client"),
    )
    op.add_column(
        "user_stories",
        sa.Column("technical_notes", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "user_stories",
        sa.Column("technical_notes_chunk_keys", sa.Text(), nullable=False, server_default=""),
    )
    op.create_foreign_key(
        "fk_user_stories_task_id", "user_stories", "project_tasks", ["task_id"], ["id"]
    )
    op.create_index("ix_user_stories_task_id", "user_stories", ["task_id"])
    op.create_index("ix_user_stories_kind", "user_stories", ["kind"])

    op.add_column(
        "acceptance_criteria",
        sa.Column("measure", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "acceptance_criteria",
        sa.Column("source_chunk_keys", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("acceptance_criteria", "source_chunk_keys")
    op.drop_column("acceptance_criteria", "measure")
    op.drop_index("ix_user_stories_kind", table_name="user_stories")
    op.drop_index("ix_user_stories_task_id", table_name="user_stories")
    op.drop_constraint("fk_user_stories_task_id", "user_stories", type_="foreignkey")
    op.drop_column("user_stories", "technical_notes_chunk_keys")
    op.drop_column("user_stories", "technical_notes")
    op.drop_column("user_stories", "kind")
    op.drop_column("user_stories", "task_id")
