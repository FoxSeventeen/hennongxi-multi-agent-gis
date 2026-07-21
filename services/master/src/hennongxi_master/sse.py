"""Durable Server-Sent Events framing and per-client replay loops."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from typing import Protocol
from uuid import UUID

import structlog
from hennongxi_contracts import TaskEvent, TaskStatus

from hennongxi_master.events import CacheWaitResult, EventReplay

MAX_DURABLE_SEQUENCE = 2**63 - 1
_LOGGER = structlog.get_logger("hennongxi.master.sse")
_TERMINAL_STATUSES = frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED})


class EventStreamStore(Protocol):
    async def replay(
        self,
        task_id: UUID,
        *,
        after_sequence: int,
        limit: int,
    ) -> EventReplay: ...

    async def wait_for_events(
        self,
        task_id: UUID,
        *,
        after_sequence: int,
        timeout_ms: int,
        limit: int,
    ) -> CacheWaitResult: ...


class DisconnectProbe(Protocol):
    async def is_disconnected(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class EventStreamConfig:
    replay_batch_size: int = 100
    redis_block_ms: int = 1000
    heartbeat_seconds: float = 15.0
    fallback_poll_seconds: float = 1.0

    def __post_init__(self) -> None:
        if not 1 <= self.replay_batch_size <= 1000:
            raise ValueError("replay_batch_size must be between 1 and 1000")
        if not 100 <= self.redis_block_ms <= 5000:
            raise ValueError("redis_block_ms must be between 100 and 5000")
        if not 1.0 <= self.heartbeat_seconds <= 60.0:
            raise ValueError("heartbeat_seconds must be between 1 and 60")
        if not 0.1 <= self.fallback_poll_seconds <= 10.0:
            raise ValueError("fallback_poll_seconds must be between 0.1 and 10")


def parse_last_event_id(value: str | None) -> int:
    """Parse the public numeric resume cursor without accepting alternate syntax."""
    if value is None or value == "":
        return 0
    if (
        not value.isascii()
        or not value.isdecimal()
        or (len(value) > 1 and value.startswith("0"))
        or len(value) > 19
    ):
        raise ValueError("Last-Event-ID must be a canonical non-negative integer")
    sequence = int(value)
    if sequence > MAX_DURABLE_SEQUENCE:
        raise ValueError("Last-Event-ID exceeds the durable sequence range")
    return sequence


def format_sse_event(event: TaskEvent) -> bytes:
    """Serialize one immutable task event using the WHATWG event-stream fields."""
    # A blank line dispatches the event; the durable sequence becomes the browser's
    # Last-Event-ID on reconnect. Source: https://html.spec.whatwg.org/multipage/server-sent-events.html
    return f"id: {event.sequence}\ndata: {event.model_dump_json()}\n\n".encode()


def heartbeat_frame() -> bytes:
    """Return a comment frame that keeps intermediaries alive without moving the cursor."""
    # Comment lines are ignored by EventSource and are recommended as periodic keepalives.
    # Source: https://html.spec.whatwg.org/multipage/server-sent-events.html#authoring-notes
    return b": heartbeat\n\n"


class TaskEventStreamer:
    """Replay durable history and use Redis only as a bounded low-latency wake-up."""

    def __init__(
        self,
        store: EventStreamStore,
        config: EventStreamConfig | None = None,
        *,
        monotonic_clock: Callable[[], float] = monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._store = store
        self.config = config or EventStreamConfig()
        self._monotonic = monotonic_clock
        self._sleep = sleep

    async def stream(
        self,
        task_id: UUID,
        disconnect: DisconnectProbe,
        *,
        after_sequence: int = 0,
        task_terminal: bool = False,
    ) -> AsyncIterator[bytes]:
        cursor = after_sequence
        last_heartbeat = self._monotonic()
        cache_degraded = False
        _LOGGER.info("task_event_stream_started", task_id=str(task_id), after_sequence=cursor)

        while True:
            if await disconnect.is_disconnected():
                _LOGGER.info("task_event_stream_disconnected", task_id=str(task_id), cursor=cursor)
                return
            try:
                replay = await self._store.replay(
                    task_id,
                    after_sequence=cursor,
                    limit=self.config.replay_batch_size,
                )
            except Exception as error:
                _LOGGER.warning(
                    "task_event_durable_replay_failed",
                    task_id=str(task_id),
                    cursor=cursor,
                    error_type=type(error).__name__,
                )
                return

            for event in replay.events:
                if event.sequence <= cursor:
                    continue
                if await disconnect.is_disconnected():
                    _LOGGER.info(
                        "task_event_stream_disconnected",
                        task_id=str(task_id),
                        cursor=cursor,
                    )
                    return
                yield format_sse_event(event)
                cursor = event.sequence
                last_heartbeat = self._monotonic()
                if event.status in _TERMINAL_STATUSES:
                    _LOGGER.info(
                        "task_event_stream_terminal",
                        task_id=str(task_id),
                        cursor=cursor,
                        status=event.status.value,
                    )
                    return

            if len(replay.events) == self.config.replay_batch_size:
                continue
            if task_terminal:
                _LOGGER.info(
                    "task_event_stream_terminal_cursor_reached",
                    task_id=str(task_id),
                    cursor=cursor,
                )
                return

            wait_result = await self._store.wait_for_events(
                task_id,
                after_sequence=cursor,
                timeout_ms=self.config.redis_block_ms,
                limit=self.config.replay_batch_size,
            )
            if wait_result is CacheWaitResult.UNAVAILABLE:
                if not cache_degraded:
                    _LOGGER.warning(
                        "task_event_stream_cache_degraded",
                        task_id=str(task_id),
                        cursor=cursor,
                    )
                    cache_degraded = True
                await self._sleep(self.config.fallback_poll_seconds)
                continue
            if cache_degraded:
                _LOGGER.info(
                    "task_event_stream_cache_recovered",
                    task_id=str(task_id),
                    cursor=cursor,
                )
                cache_degraded = False

            now = self._monotonic()
            if (
                wait_result is CacheWaitResult.TIMEOUT
                and now - last_heartbeat >= self.config.heartbeat_seconds
            ):
                if await disconnect.is_disconnected():
                    _LOGGER.info(
                        "task_event_stream_disconnected",
                        task_id=str(task_id),
                        cursor=cursor,
                    )
                    return
                yield heartbeat_frame()
                last_heartbeat = now
