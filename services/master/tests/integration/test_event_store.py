from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import cast
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import pytest
import pytest_asyncio
from hennongxi_contracts import AgentName, CreateTaskRequest, TaskEvent, TaskStatus
from hennongxi_master.events import EventStore, ReplaySource, task_event_stream_key
from hennongxi_master.repository import TaskRepository, TransitionCreate, WatershedCreate
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
WATERSHED_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
CORRELATION_ID = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


def redis_test_url() -> str:
    assert REDIS_URL is not None
    parts = urlsplit(REDIS_URL)
    return urlunsplit(parts._replace(path="/15"))


def unavailable_redis_url() -> str:
    parts = urlsplit(redis_test_url())
    assert parts.hostname is not None
    return urlunsplit(parts._replace(netloc=f"{parts.hostname}:6399"))


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
    value = Redis.from_url(redis_test_url(), decode_responses=True)
    await value.flushdb()
    try:
        yield value
    finally:
        await value.flushdb()
        await value.aclose()


async def create_pending_task(repository: TaskRepository) -> None:
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


def transition(
    *,
    status: TaskStatus,
    progress: int,
    step_id: str,
    agent: AgentName,
) -> TransitionCreate:
    return TransitionCreate(
        task_id=TASK_ID,
        attempt=1,
        step_id=step_id,
        agent=agent,
        target_status=status,
        progress=progress,
        message=f"任务进入 {status.value}",
        elapsed_ms=10,
        occurred_at=NOW,
    )


@pytest.mark.asyncio
async def test_append_preserves_order_fields_and_bounded_replay(
    engine: AsyncEngine,
    redis_client: Redis,
) -> None:
    repository = TaskRepository(engine)
    await create_pending_task(repository)
    store = EventStore(repository, redis_client, max_events_per_task=2)

    results = (
        await store.append(
            transition(
                status=TaskStatus.PLANNING,
                progress=5,
                step_id="planning",
                agent=AgentName.MASTER,
            )
        ),
        await store.append(
            transition(
                status=TaskStatus.DATA_PREPARING,
                progress=10,
                step_id="prepare_data",
                agent=AgentName.DATA,
            )
        ),
        await store.append(
            transition(
                status=TaskStatus.ANALYZING,
                progress=25,
                step_id="analyze_ndvi_change",
                agent=AgentName.ANALYSIS,
            )
        ),
    )

    assert [result.event.sequence for result in results] == [1, 2, 3]
    assert all(result.cached for result in results)
    key = task_event_stream_key(TASK_ID)
    assert await redis_client.xlen(key) == 2
    cached_rows = cast(list[tuple[str, dict[str, str]]], await redis_client.xrange(key))
    assert [row_id for row_id, _ in cached_rows] == ["2-0", "3-0"]
    assert [TaskEvent.model_validate_json(fields["event"]) for _, fields in cached_rows] == [
        results[1].event,
        results[2].event,
    ]

    complete_replay = await store.replay(TASK_ID, after_sequence=0)
    resumed_replay = await store.replay(TASK_ID, after_sequence=1)
    assert complete_replay.source is ReplaySource.DURABLE
    assert complete_replay.events == tuple(result.event for result in results)
    assert resumed_replay.source is ReplaySource.CACHE
    assert resumed_replay.events == tuple(result.event for result in results[1:])


@pytest.mark.asyncio
async def test_replay_falls_back_to_durable_history_after_redis_flush(
    engine: AsyncEngine,
    redis_client: Redis,
) -> None:
    repository = TaskRepository(engine)
    await create_pending_task(repository)
    store = EventStore(repository, redis_client)
    appended = await store.append(
        transition(
            status=TaskStatus.PLANNING,
            progress=5,
            step_id="planning",
            agent=AgentName.MASTER,
        )
    )

    await redis_client.flushdb()
    replay = await store.replay(TASK_ID)

    assert replay.source is ReplaySource.DURABLE
    assert replay.events == (appended.event,)


@pytest.mark.asyncio
async def test_redis_unavailability_never_loses_history_or_falsifies_task_state(
    engine: AsyncEngine,
) -> None:
    repository = TaskRepository(engine)
    await create_pending_task(repository)
    unavailable = Redis.from_url(
        unavailable_redis_url(),
        decode_responses=True,
        socket_connect_timeout=0.1,
        socket_timeout=0.1,
    )
    try:
        store = EventStore(repository, unavailable)
        appended = await store.append(
            transition(
                status=TaskStatus.PLANNING,
                progress=5,
                step_id="planning",
                agent=AgentName.MASTER,
            )
        )
        replay = await store.replay(TASK_ID)
        task = await repository.get_task(TASK_ID)
    finally:
        await unavailable.aclose()

    assert not appended.cached
    assert replay.source is ReplaySource.DURABLE
    assert replay.events == (appended.event,)
    assert task is not None
    assert task.status is TaskStatus.PLANNING
    assert task.progress == 5
