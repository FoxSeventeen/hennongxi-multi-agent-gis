"""Use a PostgreSQL-compatible storage-key constraint.

Revision ID: 20260719_02
Revises: 20260719_01
Create Date: 2026-07-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260719_02"
down_revision: str | None = "20260719_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_artifacts_storage_key", "artifacts", type_="check")
    op.create_check_constraint(
        "ck_artifacts_storage_key",
        "artifacts",
        "char_length(storage_key) BETWEEN 1 AND 500 "
        "AND storage_key ~ '^[A-Za-z0-9][A-Za-z0-9._/-]*$' "
        "AND strpos(storage_key, '..') = 0 AND strpos(storage_key, chr(92)) = 0",
    )


def downgrade() -> None:
    raise RuntimeError("Database migrations are forward-only")
