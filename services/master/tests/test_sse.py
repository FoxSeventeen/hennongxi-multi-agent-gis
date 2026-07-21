from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID

import pytest
from hennongxi_contracts import AgentName, TaskEvent, TaskStatus
from hennongxi_master.events import CacheWaitResult, EventReplay, ReplaySource
from hennongxi_master.sse import (
    EventStreamConfig,
    TaskEventStreamer,
    format_sse_event,
    heartbeat_frame,
    parse_last_event_id,
)

TASK_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CORRELATION_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
NOW = datetime(2026, 7, 21, 10, 0, tzinfo=UTC)


def _event(sequence: int, status: TaskStatus, progress: int) -> TaskEvent:
    return TaskEvent(
        sequence=sequence,
        task_id=TASK_ID,
        step_id="planning",
        attempt=1,
        correlation_id=CORRELATION_ID,
        agent=AgentName.MASTER,
        status=status,
        progress=progress,
        message=f"任务进度 {progress}",
        elapsed_ms=10,
        occurred_at=NOW,
    )


class _Store:
    def __init__(
        self,
        events: list[TaskEvent],
        waits: list[CacheWaitResult] | None = None,
    ) -> None:
        self.events = events
        self.waits = waits or []
        self.replay_cursors: list[int] = []
        self.wait_cursors: list[int] = []

    async def replay(
        self,
        task_id: UUID,
        *,
        after_sequence: int,
        limit: int,
    ) -> EventReplay:
        assert task_id == TASK_ID
        self.replay_cursors.append(after_sequence)
        events = tuple(event for event in self.events if event.sequence > after_sequence)[:limit]
        return EventReplay(events=events, source=ReplaySource.DURABLE)

    async def wait_for_events(
        self,
        task_id: UUID,
        *,
        after_sequence: int,
        timeout_ms: int,
        limit: int,
    ) -> CacheWaitResult:
        assert task_id == TASK_ID
        assert timeout_ms > 0
        assert limit > 0
        self.wait_cursors.append(after_sequence)
        return self.waits.pop(0) if self.waits else CacheWaitResult.TIMEOUT


class _DisconnectProbe:
    def __init__(self) -> None:
        self.disconnected = False
        self.calls = 0

    async def is_disconnected(self) -> bool:
        self.calls += 1
        return self.disconnected


class _Clock:
    def __init__(self, *values: float) -> None:
        self.values = list(values)

    def __call__(self) -> float:
        return self.values.pop(0) if self.values else 1000.0


async def _collect(stream: AsyncIterator[bytes]) -> list[bytes]:
    return [frame async for frame in stream]


def test_sse_frames_follow_utf8_event_stream_format() -> None:
    event = _event(7, TaskStatus.ANALYZING, 25)

    assert format_sse_event(event) == (f"id: 7\ndata: {event.model_dump_json()}\n\n".encode())
    assert heartbeat_frame() == b": heartbeat\n\n"


@pytest.mark.parametrize("value", ["-1", "+1", "1.0", " 1", "1\n2", "x", "9" * 20])
def test_last_event_id_rejects_noncanonical_or_oversized_values(value: str) -> None:
    with pytest.raises(ValueError, match="Last-Event-ID"):
        parse_last_event_id(value)


def test_last_event_id_accepts_absent_zero_and_durable_sequence() -> None:
    assert parse_last_event_id(None) == 0
    assert parse_last_event_id("") == 0
    assert parse_last_event_id("0") == 0
    assert parse_last_event_id("9223372036854775807") == 9223372036854775807


@pytest.mark.asyncio
async def test_reconnect_replays_only_events_after_last_event_id_and_closes_on_terminal() -> None:
    store = _Store(
        [
            _event(10, TaskStatus.PLANNING, 5),
            _event(14, TaskStatus.ANALYZING, 25),
            _event(20, TaskStatus.COMPLETED, 100),
        ]
    )
    streamer = TaskEventStreamer(store)

    frames = await _collect(streamer.stream(TASK_ID, _DisconnectProbe(), after_sequence=10))

    assert [frame.split(b"\n", 1)[0] for frame in frames] == [b"id: 14", b"id: 20"]
    assert store.wait_cursors == []


@pytest.mark.asyncio
async def test_timeout_emits_heartbeat_without_advancing_resume_cursor() -> None:
    store = _Store([], [CacheWaitResult.TIMEOUT])
    streamer = TaskEventStreamer(
        store,
        EventStreamConfig(heartbeat_seconds=15.0),
        monotonic_clock=_Clock(0.0, 15.0),
    )
    stream = streamer.stream(TASK_ID, _DisconnectProbe(), after_sequence=4)

    assert await anext(stream) == heartbeat_frame()
    await stream.aclose()

    assert store.replay_cursors == [4]
    assert store.wait_cursors == [4]


@pytest.mark.asyncio
async def test_redis_loss_polls_durable_history_and_delivers_terminal_event() -> None:
    store = _Store([], [CacheWaitResult.UNAVAILABLE])
    sleeps: list[float] = []

    async def durable_poll(delay: float) -> None:
        sleeps.append(delay)
        store.events.append(_event(3, TaskStatus.FAILED, 30))

    streamer = TaskEventStreamer(store, sleep=durable_poll)

    frames = await _collect(streamer.stream(TASK_ID, _DisconnectProbe()))

    assert [frame.split(b"\n", 1)[0] for frame in frames] == [b"id: 3"]
    assert sleeps == [streamer.config.fallback_poll_seconds]


@pytest.mark.asyncio
async def test_disconnected_client_stops_before_requesting_more_history() -> None:
    store = _Store(
        [
            _event(1, TaskStatus.PLANNING, 5),
            _event(2, TaskStatus.ANALYZING, 25),
        ]
    )
    probe = _DisconnectProbe()
    stream = TaskEventStreamer(store).stream(TASK_ID, probe)

    assert (await anext(stream)).startswith(b"id: 1\n")
    probe.disconnected = True
    with pytest.raises(StopAsyncIteration):
        await anext(stream)

    assert probe.calls >= 2


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("replay_batch_size", 0),
        ("redis_block_ms", 0),
        ("heartbeat_seconds", 0.0),
        ("fallback_poll_seconds", 0.0),
    ],
)
def test_stream_config_rejects_unbounded_or_busy_loop_values(name: str, value: int | float) -> None:
    with pytest.raises(ValueError, match=name):
        EventStreamConfig(**{name: value})
