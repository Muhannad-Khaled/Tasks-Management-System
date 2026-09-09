"""Drop task priority.

Priority was the last value in the system with no stated basis. The model set
it during extraction against no criteria, it defaulted to "medium" when the
model said nothing, nothing recomputed it, and no scheduling, grounding or
validation step ever read it. Its only effect was a Trello label, which put an
unsourced judgement on the card beside a measured grounding score and made the
two look like the same kind of fact.

On a platform whose claim is that every value traces to the document, a field
that cannot be traced is worse than a missing one. Removed rather than
back-filled with a rule nobody asked for.

Revision ID: 7c1e4a9b2d05
Revises: 3f9d2ab5c604
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7c1e4a9b2d05"
down_revision: str | Sequence[str] | None = "3f9d2ab5c604"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("project_tasks", "priority")


def downgrade() -> None:
    # server_default so the column can come back NOT NULL over existing rows.
    op.add_column(
        "project_tasks",
        sa.Column(
            "priority",
            sa.String(length=16),
            nullable=False,
            server_default="medium",
        ),
    )
