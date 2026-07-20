from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import datetime
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from hennongxi_contracts import TaskResponse
from hennongxi_master.main import create_master_app
from hennongxi_master.planning import build_builtin_recovery_plan
from hennongxi_master.repository import TaskRepository
from hennongxi_observability import CORRELATION_ID_HEADER
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(DATABASE_URL is None, reason="PostGIS integration test")

CORRELATION_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    assert DATABASE_URL is not None
    value = create_async_engine(DATABASE_URL)
    async with value.begin() as connection:
        await connection.execute(text("TRUNCATE TABLE watersheds RESTART IDENTITY CASCADE"))
    try:
        yield value
    finally:
        async with value.begin() as connection:
            await connection.execute(text("TRUNCATE TABLE watersheds RESTART IDENTITY CASCADE"))
        await value.dispose()


async def test_new_master_reconstructs_a_created_task_with_its_plan(
    engine: AsyncEngine,
) -> None:
    repository = TaskRepository(engine)

    first_master = create_master_app()
    with TestClient(first_master) as client:
        accepted = client.post(
            "/api/v1/tasks",
            json={"query": "分析神农溪植被变化"},
            headers={CORRELATION_ID_HEADER: str(CORRELATION_ID)},
        )

    assert accepted.status_code == 202
    task_id = UUID(accepted.json()["task_id"])
    plan = build_builtin_recovery_plan(
        task_id=task_id,
        plan_id=uuid4(),
        created_at=datetime.fromisoformat(accepted.json()["created_at"]),
    )
    await repository.save_plan(plan, attempt=1)

    second_master = create_master_app()
    with TestClient(second_master) as client:
        queried = client.get(f"/api/v1/tasks/{task_id}")

    assert queried.status_code == 200
    reconstructed = TaskResponse.model_validate(queried.json())
    assert reconstructed.task_id == task_id
    assert reconstructed.correlation_id == CORRELATION_ID
    assert reconstructed.status.value == "PENDING"
    assert reconstructed.plan == plan
    assert tuple(step.step_id for step in reconstructed.steps) == tuple(
        step.step_id for step in plan.steps
    )
