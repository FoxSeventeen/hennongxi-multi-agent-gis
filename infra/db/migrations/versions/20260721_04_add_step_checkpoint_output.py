"""Persist validated step outputs as durable recovery checkpoints.

Revision ID: 20260721_04
Revises: 20260719_03
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260721_04"
down_revision: str | None = "20260719_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "steps",
        sa.Column("output", postgresql.JSONB(), nullable=True),
    )
    op.create_check_constraint(
        "ck_steps_output",
        "steps",
        "output IS NULL OR jsonb_typeof(output) = 'object'",
    )


def downgrade() -> None:
    raise RuntimeError("Database migrations are forward-only")
