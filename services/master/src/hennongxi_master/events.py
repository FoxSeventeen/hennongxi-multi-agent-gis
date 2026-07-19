"""Durable-first task event transport backed by bounded Redis Streams."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from hennongxi_contracts import TaskEvent
from redis.asyncio import Redis
from redis.exceptions import RedisError

from hennongxi_master.repository import TaskRepository, TransitionCreate

MAX_REPLAY_EVENTS = 1000
DEFAULT_MAX_EVENTS_PER_TASK = 1000


class ReplaySource(StrEnum):
    CACHE = "CACHE"
    DURABLE = "DURABLE"


@dataclass(frozen=True, slots=True)
class EventAppendResult:
    event: TaskEvent
    cached: bool


@dataclass(frozen=True, slots=True)
class EventReplay:
    events: tuple[TaskEvent, ...]
    source: ReplaySource


def task_event_stream_key(task_id: UUID) -> str:
    """Return the internal Redis key for one task's allow-listed UUID."""
    return f"hennongxi:task-events:{task_id}"


class EventStore:
    """Commit events to PostgreSQL before treating Redis as a rebuildable cache."""

    def __init__(
        self,
        repository: TaskRepository,
        redis: Redis,
        *,
        max_events_per_task: int = DEFAULT_MAX_EVENTS_PER_TASK,
    ) -> None:
        if not 1 <= max_events_per_task <= 10_000:
            raise ValueError("max_events_per_task must be between 1 and 10000")
        self._repository = repository
        self._redis = redis
        self._max_events_per_task = max_events_per_task

    async def append(self, transition: TransitionCreate) -> EventAppendResult:
        """Persist a transition and then copy its immutable event into Redis."""
        event = await self._repository.transition_task(transition)
        return EventAppendResult(event=event, cached=await self._cache_event(event))

    async def replay(
        self,
        task_id: UUID,
        *,
        after_sequence: int = 0,
        limit: int = MAX_REPLAY_EVENTS,
    ) -> EventReplay:
        """Replay a bounded batch, using PostgreSQL whenever the cache is incomplete."""
        if after_sequence < 0:
            raise ValueError("after_sequence cannot be negative")
        if not 1 <= limit <= MAX_REPLAY_EVENTS:
            raise ValueError("limit must be between 1 and 1000")

        durable = await self._repository.list_events(
            task_id,
            after_sequence=after_sequence,
            limit=limit,
        )
        try:
            cached = await self._read_cached(
                task_id,
                after_sequence=after_sequence,
                limit=limit,
            )
        except (RedisError, ValueError):
            return EventReplay(events=durable, source=ReplaySource.DURABLE)
        if cached == durable:
            return EventReplay(events=cached, source=ReplaySource.CACHE)
        return EventReplay(events=durable, source=ReplaySource.DURABLE)

    async def _cache_event(self, event: TaskEvent) -> bool:
        try:
            await self._redis.xadd(
                task_event_stream_key(event.task_id),
                {"event": event.model_dump_json()},
                # Stable database sequence IDs make Redis order independently auditable.
                id=f"{event.sequence}-0",
                # Exact MAXLEN keeps retention truly bounded, not approximately bounded.
                # Source: https://redis.io/docs/latest/commands/xadd/
                maxlen=self._max_events_per_task,
                approximate=False,
            )
        except RedisError:
            # PostgreSQL already committed. Cache loss must never rewrite task truth.
            return False
        return True

    async def _read_cached(
        self,
        task_id: UUID,
        *,
        after_sequence: int,
        limit: int,
    ) -> tuple[TaskEvent, ...]:
        # XRANGE's '(' prefix makes the resume cursor exclusive.
        # Source: https://redis.io/docs/latest/commands/xrange/#exclusive-ranges
        rows = await self._redis.xrange(
            task_event_stream_key(task_id),
            min=f"({after_sequence}-0",
            max="+",
            count=limit,
        )
        if not rows:
            return ()
        events: list[TaskEvent] = []
        for row_id, fields in rows:
            if row_id is None or fields is None:
                raise ValueError("Redis event entry is missing its ID or fields")
            events.append(_decode_stream_event(row_id, fields))
        return tuple(events)


def _decode_stream_event(
    row_id: object,
    fields: Mapping[bytes | str, bytes | str],
) -> TaskEvent:
    payload = fields.get("event", fields.get(b"event"))
    if not isinstance(payload, (str, bytes, bytearray)):
        raise ValueError("Redis event entry is missing its validated payload")
    event = TaskEvent.model_validate_json(payload)
    normalized_id = row_id.decode("ascii") if isinstance(row_id, bytes) else str(row_id)
    if normalized_id != f"{event.sequence}-0":
        raise ValueError("Redis event ID does not match the durable sequence")
    return event
