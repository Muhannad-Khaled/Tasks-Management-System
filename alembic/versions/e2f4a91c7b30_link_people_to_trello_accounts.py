"""Link a person in the directory to a Trello account

Naming somebody in the card body says who owns the work. Assigning them on
Trello is what puts it in their own list of cards, and that needs the account
id — which nothing could infer from a name.

Revision ID: e2f4a91c7b30
Revises: c58e2b7f19a4
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "e2f4a91c7b30"
down_revision: Union[str, Sequence[str], None] = "c58e2b7f19a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "people",
        sa.Column("trello_member_id", sa.String(length=64), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("people", "trello_member_id")
