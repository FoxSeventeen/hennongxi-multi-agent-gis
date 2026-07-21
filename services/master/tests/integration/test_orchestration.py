from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from itertools import groupby
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from hennongxi_contracts import (
    AgentName,
    ArtifactType,
    CreateTaskRequest,
    ErrorCode,
    PlanSource,
    StepStatus,
    TaskStatus,
)
from hennongxi_master.agent_client import AgentClientConfig, AgentHttpClient
from hennongxi_master.orchestrator import TaskOrchestrator
from hennongxi_master.repository import TaskRepository, WatershedCreate
from hennongxi_master.worker import OrchestrationWorker, RecoveryTaskPlanner, WorkerConfig
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

DATABASE_URL = os.environ.get("DATABASE_URL")
AGENT_URLS = {
    "DATA_AGENT_BASE_URL": os.environ.get("DATA_AGENT_BASE_URL"),
    "ANALYSIS_AGENT_BASE_URL": os.environ.get("ANALYSIS_AGENT_BASE_URL"),
    "QUALITY_AGENT_BASE_URL": os.environ.get("QUALITY_AGENT_BASE_URL"),
    "PUBLISHER_AGENT_BASE_URL": os.environ.get("PUBLISHER_AGENT_BASE_URL"),
}
pytestmark = pytest.mark.skipif(
    DATABASE_URL is None or any(value is None for value in AGENT_URLS.values()),
    reason="PostGIS and all four private Agent URLs are required",
)

WATERSHED_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
REQUIRED_ARTIFACTS = {
    ArtifactType.NDVI_BEFORE,
    ArtifactType.NDVI_AFTER,
    ArtifactType.NDVI_DIFFERENCE,
    ArtifactType.CHANGE_CLASSIFICATION,
    ArtifactType.AREA_STATISTICS,
    ArtifactType.QUALITY_REPORT,
    ArtifactType.PDF_REPORT,
}
EXPECTED_ROUTES = [
    "/internal/v1/data/prepare",
    "/internal/v1/analysis/run",
    "/internal/v1/quality/evaluate",
    "/internal/v1/publisher/publish",
]


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


async def _create_task(repository: TaskRepository) -> tuple[UUID, UUID]:
    now = datetime.now(UTC)
    task_id = uuid4()
    correlation_id = uuid4()
    await repository.create_watershed(
        WatershedCreate(
            watershed_id=WATERSHED_ID,
            slug="shennongxi",
            name="神农溪流域",
            geometry={
                "type": "Polygon",
                "coordinates": [
                    [
                        [110.1, 31.0],
                        [110.6, 31.0],
                        [110.6, 31.5],
                        [110.1, 31.5],
                        [110.1, 31.0],
                    ]
                ],
            },
            source_metadata={"product_id": "hybas_as_lev12_v1c"},
            created_at=now,
        )
    )
    await repository.create_task(
        task_id=task_id,
        correlation_id=correlation_id,
        watershed_id=WATERSHED_ID,
        request=CreateTaskRequest(query="分析神农溪 2019 至 2024 年植被变化"),
        created_at=now,
    )
    return task_id, correlation_id


def _agent_config() -> AgentClientConfig:
    return AgentClientConfig.from_environment(
        {name: value for name, value in AGENT_URLS.items() if value is not None}
    )


def _worker(
    repository: TaskRepository,
    client: httpx.AsyncClient,
) -> OrchestrationWorker:
    orchestrator = TaskOrchestrator(
        repository,
        AgentHttpClient(_agent_config(), client),
        RecoveryTaskPlanner(None),
    )
    return OrchestrationWorker(
        repository,
        orchestrator,
        WorkerConfig(
            worker_id="master-integration-1",
            poll_interval_seconds=0.05,
            lease_seconds=120,
            heartbeat_interval_seconds=30,
        ),
    )


@pytest.mark.asyncio
async def test_worker_runs_complete_agent_chain_over_private_http(
    engine: AsyncEngine,
) -> None:
    repository = TaskRepository(engine)
    task_id, correlation_id = await _create_task(repository)
    observed_routes: list[str] = []

    async def observe_request(request: httpx.Request) -> None:
        observed_routes.append(request.url.path)
        assert request.headers["X-Correlation-ID"] == str(correlation_id)

    timeout = httpx.Timeout(connect=5, read=300, write=10, pool=5)
    async with httpx.AsyncClient(
        timeout=timeout,
        limits=httpx.Limits(max_connections=5, max_keepalive_connections=5),
        follow_redirects=False,
        trust_env=False,
        event_hooks={"request": [observe_request]},
    ) as client:
        assert await _worker(repository, client).run_once() is True

    task = await repository.get_task(task_id)
    events = await repository.list_events(task_id)
    assert task is not None
    assert task.status is TaskStatus.COMPLETED
    assert task.progress == 100
    assert task.correlation_id == correlation_id
    assert task.plan is not None and task.plan.source is PlanSource.BUILTIN_RECOVERY
    assert task.publication is not None
    assert task.publication.task_id == task_id
    assert task.publication.attempt == 1
    assert len(task.publication.resources) == 5
    assert tuple(step.status for step in task.steps) == (StepStatus.COMPLETED,) * 4
    assert {artifact.artifact_type for artifact in task.artifacts} == REQUIRED_ARTIFACTS
    assert all(artifact.task_id == task_id and artifact.attempt == 1 for artifact in task.artifacts)

    assert observed_routes == EXPECTED_ROUTES
    assert [status for status, _ in groupby(event.status for event in events)] == [
        TaskStatus.PLANNING,
        TaskStatus.DATA_PREPARING,
        TaskStatus.ANALYZING,
        TaskStatus.QUALITY_CHECKING,
        TaskStatus.PUBLISHING,
        TaskStatus.COMPLETED,
    ]
    assert [event.progress for event in events] == sorted(event.progress for event in events)
    assert all(
        event.task_id == task_id and event.attempt == 1 and event.correlation_id == correlation_id
        for event in events
    )
    assert {event.agent for event in events} == {
        AgentName.MASTER,
        AgentName.DATA,
        AgentName.ANALYSIS,
        AgentName.QUALITY,
        AgentName.PUBLISHER,
    }


@pytest.mark.parametrize(
    ("failure_mode", "expected_code"),
    [
        ("timeout", ErrorCode.DEPENDENCY_UNAVAILABLE),
        ("unreachable", ErrorCode.DEPENDENCY_UNAVAILABLE),
        ("invalid", ErrorCode.INTERNAL_ERROR),
    ],
)
@pytest.mark.asyncio
async def test_agent_transport_failures_persist_honest_step_failure(
    engine: AsyncEngine,
    failure_mode: str,
    expected_code: ErrorCode,
) -> None:
    repository = TaskRepository(engine)
    task_id, _correlation_id = await _create_task(repository)

    def fail(request: httpx.Request) -> httpx.Response:
        if failure_mode == "timeout":
            raise httpx.ReadTimeout("private timeout detail", request=request)
        if failure_mode == "unreachable":
            raise httpx.ConnectError("private network detail", request=request)
        return httpx.Response(200, json={"untrusted": "response"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
        assert await _worker(repository, client).run_once() is True

    task = await repository.get_task(task_id)
    events = await repository.list_events(task_id)
    assert task is not None and task.status is TaskStatus.FAILED
    assert task.last_error is not None and task.last_error.code is expected_code
    assert "private" not in task.last_error.message
    assert events[-1].status is TaskStatus.FAILED
    assert events[-1].step_id == "prepare_data"
    assert events[-1].agent is AgentName.DATA
    assert events[-1].error is not None and events[-1].error.code is expected_code
