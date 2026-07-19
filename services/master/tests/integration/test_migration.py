from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

EXPECTED_TABLES = {
    "alembic_version",
    "artifacts",
    "attempts",
    "event_artifacts",
    "events",
    "model_calls",
    "plans",
    "steps",
    "tasks",
    "watersheds",
    "worker_claims",
}
EXPECTED_CONSTRAINTS = {
    "ck_artifacts_complete_metadata",
    "ck_events_progress",
    "ck_steps_progress",
    "ck_tasks_progress",
    "ck_tasks_status",
    "fk_artifacts_step",
    "fk_events_attempt",
    "fk_steps_dependency",
    "pk_steps",
    "uq_steps_attempt_position",
}


@pytest.mark.asyncio
async def test_alembic_head_creates_postgis_schema_and_named_constraints() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.connect() as connection:
            table_names = set(
                await connection.scalars(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public'"
                    )
                )
            )
            constraints = set(
                await connection.scalars(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE connamespace = 'public'::regnamespace"
                    )
                )
            )
            geometry = (
                await connection.execute(
                    text(
                        "SELECT type, srid FROM geometry_columns "
                        "WHERE f_table_schema = 'public' AND f_table_name = 'watersheds'"
                    )
                )
            ).one()
            timestamp_types = set(
                await connection.scalars(
                    text(
                        "SELECT data_type FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND column_name LIKE '%_at'"
                    )
                )
            )
    finally:
        await engine.dispose()

    assert table_names >= EXPECTED_TABLES
    assert constraints >= EXPECTED_CONSTRAINTS
    assert geometry == ("MULTIPOLYGON", 4326)
    assert timestamp_types == {"timestamp with time zone"}
