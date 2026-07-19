"""Create the durable workflow and PostGIS schema.

Revision ID: 20260719_01
Revises: None
Create Date: 2026-07-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260719_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TASK_STATES = (
    "PENDING",
    "PLANNING",
    "DATA_PREPARING",
    "ANALYZING",
    "QUALITY_CHECKING",
    "PUBLISHING",
    "COMPLETED",
    "FAILED",
)
STEP_STATES = ("PENDING", "RUNNING", "COMPLETED", "FAILED", "SKIPPED")
AGENTS = ("master", "data", "analysis", "quality", "publisher")
PLAN_SOURCES = ("REAL_LLM", "BUILTIN_RECOVERY")
MODEL_CALL_STATES = ("SUCCEEDED", "FAILED")
ARTIFACT_STATES = ("STAGING", "COMPLETE", "FAILED")
ARTIFACT_TYPES = (
    "DATA_MANIFEST",
    "WATERSHED_BOUNDARY",
    "NDVI_BEFORE",
    "NDVI_AFTER",
    "NDVI_DIFFERENCE",
    "CHANGE_CLASSIFICATION",
    "AREA_STATISTICS",
    "QUALITY_REPORT",
    "PDF_REPORT",
)


def _values(items: tuple[str, ...]) -> str:
    return ", ".join(repr(item) for item in items)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.create_table(
        "watersheds",
        sa.Column("watershed_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("source_metadata", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("watershed_id", name="pk_watersheds"),
        sa.UniqueConstraint("slug", name="uq_watersheds_slug"),
        sa.CheckConstraint("slug ~ '^[a-z][a-z0-9-]{0,99}$'", name="ck_watersheds_slug"),
        sa.CheckConstraint("btrim(name) <> ''", name="ck_watersheds_name"),
        sa.CheckConstraint(
            "jsonb_typeof(source_metadata) = 'object'",
            name="ck_watersheds_source_metadata",
        ),
    )
    op.execute("ALTER TABLE watersheds ADD COLUMN geometry geometry(MULTIPOLYGON, 4326) NOT NULL")
    op.execute("CREATE INDEX ix_watersheds_geometry ON watersheds USING GIST (geometry)")

    op.create_table(
        "tasks",
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("watershed_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("progress", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("current_attempt", sa.Integer(), server_default="1", nullable=False),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("last_error", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("task_id", name="pk_tasks"),
        sa.ForeignKeyConstraint(
            ["watershed_id"], ["watersheds.watershed_id"], name="fk_tasks_watershed"
        ),
        sa.UniqueConstraint("correlation_id", name="uq_tasks_correlation_id"),
        sa.CheckConstraint(
            f"status IN ({_values(TASK_STATES)})",
            name="ck_tasks_status",
        ),
        sa.CheckConstraint("progress BETWEEN 0 AND 100", name="ck_tasks_progress"),
        sa.CheckConstraint("current_attempt >= 1", name="ck_tasks_current_attempt"),
        sa.CheckConstraint(
            "char_length(query) BETWEEN 1 AND 2000 AND btrim(query) <> ''",
            name="ck_tasks_query",
        ),
        sa.CheckConstraint("updated_at >= created_at", name="ck_tasks_timestamps"),
        sa.CheckConstraint(
            "last_error IS NULL OR jsonb_typeof(last_error) = 'object'",
            name="ck_tasks_last_error",
        ),
    )
    op.create_index("ix_tasks_runnable", "tasks", ["status", "created_at"], unique=False)

    op.create_table(
        "attempts",
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("resume_from_step_id", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("task_id", "attempt", name="pk_attempts"),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.task_id"], name="fk_attempts_task", ondelete="CASCADE"
        ),
        sa.CheckConstraint("attempt >= 1", name="ck_attempts_number"),
        sa.CheckConstraint(
            f"status IN ({_values(TASK_STATES)})",
            name="ck_attempts_status",
        ),
        sa.CheckConstraint(
            "resume_from_step_id IS NULL OR resume_from_step_id ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="ck_attempts_resume_step",
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR started_at IS NOT NULL",
            name="ck_attempts_completion",
        ),
    )
    op.create_foreign_key(
        "fk_tasks_current_attempt",
        "tasks",
        "attempts",
        ["task_id", "current_attempt"],
        ["task_id", "attempt"],
        deferrable=True,
        initially="DEFERRED",
    )

    op.create_table(
        "plans",
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("plan_id", name="pk_plans"),
        sa.ForeignKeyConstraint(
            ["task_id", "attempt"],
            ["attempts.task_id", "attempts.attempt"],
            name="fk_plans_attempt",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("task_id", "attempt", name="uq_plans_task_attempt"),
        sa.UniqueConstraint("plan_id", "task_id", "attempt", name="uq_plans_identity_scope"),
        sa.CheckConstraint(
            f"source IN ({_values(PLAN_SOURCES)})",
            name="ck_plans_source",
        ),
        sa.CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_plans_payload"),
    )

    op.create_table(
        "model_calls",
        sa.Column("model_call_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("response_sha256", sa.String(64), nullable=True),
        sa.Column("error_code", sa.String(200), nullable=True),
        sa.PrimaryKeyConstraint("model_call_id", name="pk_model_calls"),
        sa.ForeignKeyConstraint(
            ["plan_id"], ["plans.plan_id"], name="fk_model_calls_plan", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("plan_id", name="uq_model_calls_plan"),
        sa.CheckConstraint("btrim(model) <> ''", name="ck_model_calls_model"),
        sa.CheckConstraint("duration_ms >= 0", name="ck_model_calls_duration"),
        sa.CheckConstraint(
            f"status IN ({_values(MODEL_CALL_STATES)})",
            name="ck_model_calls_status",
        ),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="ck_model_calls_input_tokens",
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="ck_model_calls_output_tokens",
        ),
        sa.CheckConstraint(
            "response_sha256 IS NULL OR response_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_model_calls_response_sha256",
        ),
        sa.CheckConstraint(
            "status <> 'SUCCEEDED' OR response_sha256 IS NOT NULL",
            name="ck_model_calls_success_hash",
        ),
    )

    op.create_table(
        "steps",
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("step_id", sa.String(64), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("agent", sa.String(16), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("depends_on_step_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), server_default="PENDING", nullable=False),
        sa.Column("progress", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("elapsed_ms", sa.Integer(), nullable=True),
        sa.Column("error", postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("task_id", "attempt", "step_id", name="pk_steps"),
        sa.ForeignKeyConstraint(
            ["plan_id", "task_id", "attempt"],
            ["plans.plan_id", "plans.task_id", "plans.attempt"],
            name="fk_steps_plan_scope",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("task_id", "attempt", "position", name="uq_steps_attempt_position"),
        sa.CheckConstraint("step_id ~ '^[a-z][a-z0-9_]{0,63}$'", name="ck_steps_id"),
        sa.CheckConstraint("position BETWEEN 1 AND 4", name="ck_steps_position"),
        sa.CheckConstraint(
            f"agent IN ({_values(AGENTS)})",
            name="ck_steps_agent",
        ),
        sa.CheckConstraint(
            f"status IN ({_values(STEP_STATES)})",
            name="ck_steps_status",
        ),
        sa.CheckConstraint("progress BETWEEN 0 AND 100", name="ck_steps_progress"),
        sa.CheckConstraint("btrim(title) <> ''", name="ck_steps_title"),
        sa.CheckConstraint(
            "depends_on_step_id IS NULL OR depends_on_step_id ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="ck_steps_dependency",
        ),
        sa.CheckConstraint(
            "elapsed_ms IS NULL OR elapsed_ms >= 0",
            name="ck_steps_elapsed",
        ),
        sa.CheckConstraint(
            "error IS NULL OR jsonb_typeof(error) = 'object'",
            name="ck_steps_error",
        ),
        sa.CheckConstraint(
            "status <> 'COMPLETED' OR "
            "(progress = 100 AND completed_at IS NOT NULL AND error IS NULL)",
            name="ck_steps_completed_evidence",
        ),
        sa.CheckConstraint(
            "status <> 'FAILED' OR (completed_at IS NOT NULL AND error IS NOT NULL)",
            name="ck_steps_failed_evidence",
        ),
    )
    op.create_index("ix_steps_status", "steps", ["task_id", "attempt", "status"])

    op.create_table(
        "artifacts",
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("step_id", sa.String(64), nullable=False),
        sa.Column("artifact_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("media_type", sa.String(200), nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("checksum_sha256", sa.String(64), nullable=True),
        sa.Column("byte_size", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("artifact_id", name="pk_artifacts"),
        sa.ForeignKeyConstraint(
            ["task_id", "attempt", "step_id"],
            ["steps.task_id", "steps.attempt", "steps.step_id"],
            name="fk_artifacts_step",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "artifact_id", "task_id", "attempt", name="uq_artifacts_identity_scope"
        ),
        sa.UniqueConstraint(
            "task_id", "attempt", "artifact_type", name="uq_artifacts_attempt_type"
        ),
        sa.CheckConstraint(
            f"artifact_type IN ({_values(ARTIFACT_TYPES)})",
            name="ck_artifacts_type",
        ),
        sa.CheckConstraint(
            f"status IN ({_values(ARTIFACT_STATES)})",
            name="ck_artifacts_status",
        ),
        sa.CheckConstraint("btrim(media_type) <> ''", name="ck_artifacts_media_type"),
        sa.CheckConstraint(
            "storage_key ~ '^[A-Za-z0-9][A-Za-z0-9._/-]{0,499}$' "
            "AND strpos(storage_key, '..') = 0 AND strpos(storage_key, chr(92)) = 0",
            name="ck_artifacts_storage_key",
        ),
        sa.CheckConstraint(
            "checksum_sha256 IS NULL OR checksum_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_artifacts_checksum",
        ),
        sa.CheckConstraint(
            "byte_size IS NULL OR byte_size > 0",
            name="ck_artifacts_byte_size",
        ),
        sa.CheckConstraint(
            "status <> 'COMPLETE' OR (checksum_sha256 IS NOT NULL AND byte_size > 0)",
            name="ck_artifacts_complete_metadata",
        ),
    )
    op.create_index("ix_artifacts_task", "artifacts", ["task_id", "attempt"])

    op.create_table(
        "events",
        sa.Column(
            "sequence",
            sa.BigInteger(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("step_id", sa.String(64), nullable=False),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("progress", sa.SmallInteger(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("elapsed_ms", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error", postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("sequence", name="pk_events"),
        sa.ForeignKeyConstraint(
            ["task_id", "attempt"],
            ["attempts.task_id", "attempts.attempt"],
            name="fk_events_attempt",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("sequence", "task_id", "attempt", name="uq_events_identity_scope"),
        sa.CheckConstraint("step_id ~ '^[a-z][a-z0-9_]{0,63}$'", name="ck_events_step_id"),
        sa.CheckConstraint(
            f"agent IN ({_values(AGENTS)})",
            name="ck_events_agent",
        ),
        sa.CheckConstraint(
            f"status IN ({_values(TASK_STATES)})",
            name="ck_events_status",
        ),
        sa.CheckConstraint("progress BETWEEN 0 AND 100", name="ck_events_progress"),
        sa.CheckConstraint(
            "char_length(message) BETWEEN 1 AND 2000 AND btrim(message) <> ''",
            name="ck_events_message",
        ),
        sa.CheckConstraint("elapsed_ms >= 0", name="ck_events_elapsed"),
        sa.CheckConstraint(
            "error IS NULL OR jsonb_typeof(error) = 'object'",
            name="ck_events_error",
        ),
    )
    op.create_index("ix_events_task_sequence", "events", ["task_id", "sequence"])

    op.create_table(
        "event_artifacts",
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("sequence", "artifact_id", name="pk_event_artifacts"),
        sa.ForeignKeyConstraint(
            ["sequence", "task_id", "attempt"],
            ["events.sequence", "events.task_id", "events.attempt"],
            name="fk_event_artifacts_event_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id", "task_id", "attempt"],
            ["artifacts.artifact_id", "artifacts.task_id", "artifacts.attempt"],
            name="fk_event_artifacts_artifact_scope",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_event_artifacts_artifact", "event_artifacts", ["artifact_id"])

    op.create_table(
        "worker_claims",
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(100), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("task_id", "attempt", name="pk_worker_claims"),
        sa.ForeignKeyConstraint(
            ["task_id", "attempt"],
            ["attempts.task_id", "attempts.attempt"],
            name="fk_worker_claims_attempt",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "worker_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$'",
            name="ck_worker_claims_worker_id",
        ),
        sa.CheckConstraint(
            "heartbeat_at >= claimed_at AND lease_expires_at > heartbeat_at",
            name="ck_worker_claims_lease",
        ),
        sa.CheckConstraint(
            "released_at IS NULL OR released_at >= claimed_at",
            name="ck_worker_claims_release",
        ),
    )
    op.create_index("ix_worker_claims_lease", "worker_claims", ["lease_expires_at"])


def downgrade() -> None:
    raise RuntimeError("Database migrations are forward-only")
