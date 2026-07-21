"""Public task-event endpoint backed by durable replay and bounded SSE tails."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, cast
from uuid import UUID

import structlog
from fastapi import FastAPI, Header, Request
from fastapi.responses import StreamingResponse
from hennongxi_contracts import ErrorCode, ErrorResponse, TaskStatus
from redis.asyncio import Redis

from hennongxi_master.events import EventStore
from hennongxi_master.sse import EventStreamConfig, TaskEventStreamer, parse_last_event_id
from hennongxi_master.tasks import TaskRepositoryPort, _failure, _repository

_LOGGER = structlog.get_logger("hennongxi.master.sse_api")
_TERMINAL_STATUSES = frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED})


def _event_store(request: Request, repository: TaskRepositoryPort) -> EventStore:
    store = cast(EventStore | None, request.app.state.event_store)
    if store is not None:
        return store
    factory = cast(
        Callable[[TaskRepositoryPort, Redis], EventStore],
        request.app.state.event_store_factory,
    )
    redis = cast(Redis, request.app.state.event_redis)
    store = factory(repository, redis)
    request.app.state.event_store = store
    return store


def install_master_event_routes(app: FastAPI) -> None:
    """Install the approved durable task-event stream."""

    @app.get(
        "/api/v1/tasks/{task_id}/events",
        response_class=StreamingResponse,
        responses={
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    async def stream_task_events(
        task_id: UUID,
        request: Request,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    ) -> StreamingResponse:
        try:
            after_sequence = parse_last_event_id(last_event_id)
        except ValueError as error:
            raise _failure(
                status_code=422,
                code=ErrorCode.VALIDATION_ERROR,
                message="Last-Event-ID is not a valid durable event sequence",
                retryable=False,
            ) from error

        repository = _repository(request)
        try:
            task = await repository.get_task(task_id)
        except Exception as error:
            _LOGGER.warning(
                "task_event_repository_unavailable",
                task_id=str(task_id),
                error_code=ErrorCode.DEPENDENCY_UNAVAILABLE.value,
                error_type=type(error).__name__,
            )
            raise _failure(
                status_code=503,
                code=ErrorCode.DEPENDENCY_UNAVAILABLE,
                message="task repository is unavailable",
                retryable=True,
            ) from error
        if task is None:
            raise _failure(
                status_code=404,
                code=ErrorCode.TASK_NOT_FOUND,
                message="task was not found",
                retryable=False,
            )

        store = _event_store(request, repository)
        config = cast(EventStreamConfig, request.app.state.event_stream_config)
        frames = TaskEventStreamer(store, config).stream(
            task_id,
            request,
            after_sequence=after_sequence,
            task_terminal=task.status in _TERMINAL_STATUSES,
        )
        return StreamingResponse(
            frames,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
