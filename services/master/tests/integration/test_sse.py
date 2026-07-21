from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from hennongxi_contracts import (
    AgentName,
    CreateTaskRequest,
    ErrorCode,
    ErrorResponse,
    StructuredError,
    TaskEvent,
    TaskResponse,
    TaskStatus,
)
from hennongxi_master.events import EventStore
from hennongxi_master.main import create_master_app
from hennongxi_master.repository import TaskRepository, TransitionCreate, WatershedCreate
from hennongxi_master.sse import TaskEventStreamer
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

DATABASE_URL = os.environ.get("DATABASE_URL")
REDIS_URL = os.environ.get("REDIS_URL")
pytestmark = pytest.mark.skipif(
    DATABASE_URL is None or REDIS_URL is None,
    reason="PostGIS and Redis integration test",
)

TASK_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
MISSING_TASK_ID = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
WATERSHED_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
CORRELATION_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
NOW = datetime(2026, 7, 21, 10, 0, tzinfo=UTC)


def _redis_test_url() -> str:
    assert REDIS_URL is not None
    parts = urlsplit(REDIS_URL)
    return urlunsplit(parts._replace(path="/13"))


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


@pytest_asyncio.fixture
async def redis_client() -> AsyncIterator[Redis]:
    value = Redis.from_url(_redis_test_url(), decode_responses=True)
    await value.flushdb()
    try:
        yield value
    finally:
        await value.flushdb()
        await value.aclose()


async def _create_terminal_task(
    repository: TaskRepository,
    redis_client: Redis,
) -> tuple[TaskEvent, TaskEvent]:
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
                        [110.5, 31.0],
                        [110.5, 31.4],
                        [110.1, 31.4],
                        [110.1, 31.0],
                    ]
                ],
            },
            source_metadata={"product_id": "hybas_as_lev12_v1c"},
            created_at=NOW,
        )
    )
    await repository.create_task(
        task_id=TASK_ID,
        correlation_id=CORRELATION_ID,
        watershed_id=WATERSHED_ID,
        request=CreateTaskRequest(query="分析神农溪植被变化"),
        created_at=NOW,
    )
    store = EventStore(repository, redis_client)
    planning = await store.append(
        TransitionCreate(
            task_id=TASK_ID,
            attempt=1,
            step_id="planning",
            agent=AgentName.MASTER,
            target_status=TaskStatus.PLANNING,
            progress=5,
            message="正在生成执行计划",
            elapsed_ms=10,
            occurred_at=NOW + timedelta(seconds=1),
        )
    )
    failure = await store.append(
        TransitionCreate(
            task_id=TASK_ID,
            attempt=1,
            step_id="planning",
            agent=AgentName.MASTER,
            target_status=TaskStatus.FAILED,
            progress=5,
            message="执行计划生成失败",
            elapsed_ms=20,
            occurred_at=NOW + timedelta(seconds=2),
            error=StructuredError(
                code=ErrorCode.INTERNAL_ERROR,
                message="执行计划生成失败",
                retryable=True,
            ),
        )
    )
    return planning.event, failure.event


def _master() -> TestClient:
    assert DATABASE_URL is not None
    app = create_master_app(
        {
            "DATABASE_URL": DATABASE_URL,
            "REDIS_URL": _redis_test_url(),
            "ORCHESTRATION_WORKER_ENABLED": "false",
            "SSE_REDIS_BLOCK_MS": "100",
            "SSE_HEARTBEAT_SECONDS": "1",
            "SSE_FALLBACK_POLL_SECONDS": "0.1",
        }
    )
    return TestClient(app)


def _events_from_response(body: str) -> tuple[TaskEvent, ...]:
    payloads = [
        line.removeprefix("data: ")
        for frame in body.split("\n\n")
        for line in frame.splitlines()
        if line.startswith("data: ")
    ]
    return tuple(TaskEvent.model_validate_json(payload) for payload in payloads)


async def test_endpoint_replays_resumes_and_matches_polling_terminal_state(
    engine: AsyncEngine,
    redis_client: Redis,
) -> None:
    repository = TaskRepository(engine)
    planning, failure = await _create_terminal_task(repository, redis_client)

    with _master() as client:
        streamed = client.get(f"/api/v1/tasks/{TASK_ID}/events")
        resumed = client.get(
            f"/api/v1/tasks/{TASK_ID}/events",
            headers={"Last-Event-ID": str(planning.sequence)},
        )
        exhausted = client.get(
            f"/api/v1/tasks/{TASK_ID}/events",
            headers={"Last-Event-ID": str(failure.sequence)},
        )
        queried = client.get(f"/api/v1/tasks/{TASK_ID}")

    assert streamed.status_code == 200
    assert streamed.headers["content-type"].startswith("text/event-stream")
    assert streamed.headers["cache-control"] == "no-cache, no-transform"
    assert _events_from_response(streamed.text) == (planning, failure)
    assert _events_from_response(resumed.text) == (failure,)
    assert exhausted.text == ""

    task = TaskResponse.model_validate(queried.json())
    assert task.status is failure.status is TaskStatus.FAILED
    assert task.progress == failure.progress
    assert task.last_error == failure.error


async def test_endpoint_replays_durable_history_after_redis_loss(
    engine: AsyncEngine,
    redis_client: Redis,
) -> None:
    repository = TaskRepository(engine)
    expected = await _create_terminal_task(repository, redis_client)
    await redis_client.flushdb()

    with _master() as client:
        response = client.get(f"/api/v1/tasks/{TASK_ID}/events")

    assert response.status_code == 200
    assert _events_from_response(response.text) == expected


async def test_endpoint_rejects_invalid_cursor_and_missing_task(
    engine: AsyncEngine,
    redis_client: Redis,
) -> None:
    repository = TaskRepository(engine)
    await _create_terminal_task(repository, redis_client)

    with _master() as client:
        invalid = client.get(
            f"/api/v1/tasks/{TASK_ID}/events",
            headers={"Last-Event-ID": "-1"},
        )
        missing = client.get(f"/api/v1/tasks/{MISSING_TASK_ID}/events")

    assert invalid.status_code == 422
    assert ErrorResponse.model_validate(invalid.json()).error.code is ErrorCode.VALIDATION_ERROR
    assert missing.status_code == 404
    assert ErrorResponse.model_validate(missing.json()).error.code is ErrorCode.TASK_NOT_FOUND


class _Connected:
    async def is_disconnected(self) -> bool:
        return False


async def test_slow_subscriber_does_not_block_an_independent_subscriber(
    engine: AsyncEngine,
    redis_client: Redis,
) -> None:
    repository = TaskRepository(engine)
    expected = await _create_terminal_task(repository, redis_client)
    streamer = TaskEventStreamer(EventStore(repository, redis_client))
    slow = streamer.stream(TASK_ID, _Connected())

    first = await anext(slow)
    fast_events = _events_from_response(
        b"".join([frame async for frame in streamer.stream(TASK_ID, _Connected())]).decode()
    )
    remaining = b"".join([frame async for frame in slow])
    slow_events = _events_from_response((first + remaining).decode())

    assert fast_events == expected
    assert slow_events == expected
