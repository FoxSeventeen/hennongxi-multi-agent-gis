"""Public task acceptance and durable task-query routes."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast
from uuid import UUID, uuid4

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from hennongxi_contracts import (
    CreateTaskRequest,
    ErrorCode,
    ErrorResponse,
    StructuredError,
    TaskAcceptedResponse,
    TaskResponse,
)

from hennongxi_master.repository import WatershedCreate
from hennongxi_master.watershed import load_approved_watershed

APPROVED_WATERSHED_SLUG = "shennongxi"
DEFAULT_DATA_MANIFEST_PATH = "/app/data/manifest.json"
_logger = structlog.get_logger("hennongxi.master")


class TaskRepositoryPort(Protocol):
    async def get_watershed_id_by_slug(self, slug: str) -> UUID | None: ...

    async def ensure_watershed(self, value: WatershedCreate) -> None: ...

    async def create_task(
        self,
        *,
        task_id: UUID,
        correlation_id: UUID,
        watershed_id: UUID,
        request: CreateTaskRequest,
        created_at: datetime,
    ) -> TaskResponse: ...

    async def get_task(self, task_id: UUID) -> TaskResponse | None: ...


class _TaskApiFailure(RuntimeError):
    def __init__(self, status_code: int, response: ErrorResponse) -> None:
        super().__init__(response.error.code.value)
        self.status_code = status_code
        self.response = response


def _failure(
    *,
    status_code: int,
    code: ErrorCode,
    message: str,
    retryable: bool,
) -> _TaskApiFailure:
    return _TaskApiFailure(
        status_code,
        ErrorResponse(
            error=StructuredError(
                code=code,
                message=message,
                retryable=retryable,
            )
        ),
    )


def _repository(request: Request) -> TaskRepositoryPort:
    repository = cast(TaskRepositoryPort | None, request.app.state.task_repository)
    if repository is None:
        factory = cast(
            Callable[[], TaskRepositoryPort],
            request.app.state.task_repository_factory,
        )
        repository = factory()
        request.app.state.task_repository = repository
    return repository


def install_master_task_routes(app: FastAPI) -> None:
    """Install only the task routes already approved in the shared OpenAPI contract."""

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(
        _request: Request,
        _error: RequestValidationError,
    ) -> JSONResponse:
        _logger.warning("task_request_rejected", error_code=ErrorCode.VALIDATION_ERROR.value)
        response = ErrorResponse(
            error=StructuredError(
                code=ErrorCode.VALIDATION_ERROR,
                message="request does not match the task API contract",
                retryable=False,
            )
        )
        return JSONResponse(status_code=422, content=response.model_dump(mode="json"))

    @app.exception_handler(_TaskApiFailure)
    async def task_api_failure_handler(
        _request: Request,
        error: _TaskApiFailure,
    ) -> JSONResponse:
        _logger.warning(
            "task_api_failed",
            error_code=error.response.error.code.value,
            retryable=error.response.error.retryable,
        )
        return JSONResponse(
            status_code=error.status_code,
            content=error.response.model_dump(mode="json"),
        )

    @app.post(
        "/api/v1/tasks",
        status_code=202,
        response_model=TaskAcceptedResponse,
        responses={
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    async def create_task(payload: CreateTaskRequest, request: Request) -> TaskAcceptedResponse:
        repository = _repository(request)
        task_id = uuid4()
        correlation_id = UUID(str(request.state.correlation_id))
        created_at = datetime.now(UTC)
        try:
            watershed_id = await repository.get_watershed_id_by_slug(APPROVED_WATERSHED_SLUG)
            if watershed_id is None:
                watershed = load_approved_watershed(
                    Path(os.getenv("DATA_MANIFEST_PATH", DEFAULT_DATA_MANIFEST_PATH)),
                    created_at=created_at,
                )
                await repository.ensure_watershed(watershed)
                watershed_id = await repository.get_watershed_id_by_slug(APPROVED_WATERSHED_SLUG)
                if watershed_id != watershed.watershed_id:
                    raise ValueError("approved watershed identity mismatch")
            task = await repository.create_task(
                task_id=task_id,
                correlation_id=correlation_id,
                watershed_id=watershed_id,
                request=payload,
                created_at=created_at,
            )
        except _TaskApiFailure:
            raise
        except Exception as error:
            _logger.warning(
                "task_repository_unavailable",
                error_code=ErrorCode.DEPENDENCY_UNAVAILABLE.value,
                error_type=type(error).__name__,
            )
            raise _failure(
                status_code=503,
                code=ErrorCode.DEPENDENCY_UNAVAILABLE,
                message="task repository is unavailable",
                retryable=True,
            ) from error

        _logger.info(
            "task_accepted",
            task_id=str(task.task_id),
            attempt=task.current_attempt,
            correlation_id=str(task.correlation_id),
        )
        return TaskAcceptedResponse(
            task_id=task.task_id,
            status=task.status,
            created_at=task.created_at,
        )

    @app.get(
        "/api/v1/tasks/{task_id}",
        response_model=TaskResponse,
        responses={
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    async def get_task(task_id: UUID, request: Request) -> TaskResponse:
        try:
            task = await _repository(request).get_task(task_id)
        except Exception as error:
            _logger.warning(
                "task_repository_unavailable",
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
        return task
