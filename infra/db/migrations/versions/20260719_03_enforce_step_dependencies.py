"""Enforce same-attempt step dependencies.

Revision ID: 20260719_03
Revises: 20260719_02
Create Date: 2026-07-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260719_03"
down_revision: str | None = "20260719_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_foreign_key(
        "fk_steps_dependency",
        "steps",
        "steps",
        ["task_id", "attempt", "depends_on_step_id"],
        ["task_id", "attempt", "step_id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    raise RuntimeError("Database migrations are forward-only")
