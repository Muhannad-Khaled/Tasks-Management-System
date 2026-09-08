"""incremental board push

Revision ID: d31f0a7c58e2
Revises: c9de6fd2d5e6
Create Date: 2026-09-06 10:12:03.881204

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd31f0a7c58e2'
down_revision: Union[str, Sequence[str], None] = 'c9de6fd2d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # server_default on every one of these: the tables already hold rows, and a
    # NOT NULL column with no default cannot be added to a populated table.
    op.add_column(
        'projects',
        sa.Column('board_id', sa.String(length=255), nullable=False, server_default=''),
    )
    op.add_column(
        'projects',
        sa.Column('board_url', sa.String(length=512), nullable=False, server_default=''),
    )
    op.add_column(
        'project_tasks',
        sa.Column('board_dirty', sa.Boolean(), nullable=False, server_default='false'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('project_tasks', 'board_dirty')
    op.drop_column('projects', 'board_url')
    op.drop_column('projects', 'board_id')
