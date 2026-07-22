"""Independently startable Data Agent application."""

from __future__ import annotations

import os
from pathlib import Path
from time import perf_counter
from typing import Annotated, cast
from uuid import UUID

import structlog
from fastapi import Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from hennongxi_contracts import (
    AgentName,
    DataPrepareCommand,
    DataPrepareResult,
    ErrorCode,
    ErrorResponse,
    StructuredError,
)
from hennongxi_observability import create_observed_agent_app

from hennongxi_data_agent.preparation import DataPreparationFailure, DataPreparer

PORT = 8001
app = create_observed_agent_app(AgentName.DATA, PORT)

_manifest_path = Path(os.environ.get("DATA_MANIFEST_PATH", "/app/data/manifest.json"))
app.state.data_preparer = DataPreparer(
    _manifest_path,
    data_root=_manifest_path.parent,
    cache_dir=Path(os.environ.get("DATA_CACHE_DIR", "/data/cache")),
)
_logger = structlog.get_logger("hennongxi.data")


class _UnexpectedDataFailure(RuntimeError):
    def __init__(self) -> None:
        super().__init__(ErrorCode.INTERNAL_ERROR.value)


def _error_response(
    *,
    code: ErrorCode,
    message: str,
    retryable: bool,
) -> ErrorResponse:
    return ErrorResponse(
        error=StructuredError(
            code=code,
            message=message,
            retryable=retryable,
        )
    )


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(
    _request: Request,
    _error: RequestValidationError,
) -> JSONResponse:
    _logger.warning("data_request_rejected", error_code=ErrorCode.VALIDATION_ERROR.value)
    response = _error_response(
        code=ErrorCode.VALIDATION_ERROR,
        message="request body does not match the data preparation contract",
        retryable=False,
    )
    return JSONResponse(status_code=422, content=response.model_dump(mode="json"))


@app.exception_handler(DataPreparationFailure)
async def data_preparation_failure_handler(
    _request: Request,
    error: DataPreparationFailure,
) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content=error.response.model_dump(mode="json"),
    )


@app.exception_handler(_UnexpectedDataFailure)
async def unexpected_data_failure_handler(
    _request: Request,
    _error: _UnexpectedDataFailure,
) -> JSONResponse:
    response = _error_response(
        code=ErrorCode.INTERNAL_ERROR,
        message="data preparation failed unexpectedly",
        retryable=True,
    )
    return JSONResponse(status_code=500, content=response.model_dump(mode="json"))


@app.post(
    "/internal/v1/data/prepare",
    response_model=DataPrepareResult,
    responses={
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def prepare_data(
    command: DataPrepareCommand,
    correlation_id: Annotated[UUID, Header(alias="X-Correlation-ID")],
) -> DataPrepareResult:
    """Inspect approved local inputs in FastAPI's worker threadpool."""

    identity = {
        "task_id": str(command.task_id),
        "step_id": command.step_id,
        "attempt": command.attempt,
        "correlation_id": str(command.correlation_id),
    }
    if correlation_id != command.correlation_id:
        _logger.warning(
            "data_prepare_rejected",
            task_id=str(command.task_id),
            step_id=command.step_id,
            attempt=command.attempt,
            error_code=ErrorCode.VALIDATION_ERROR.value,
            reason_code="CORRELATION_MISMATCH",
        )
        raise DataPreparationFailure(
            422,
            _error_response(
                code=ErrorCode.VALIDATION_ERROR,
                message="correlation header does not match the data command",
                retryable=False,
            ),
        )

    started = perf_counter()
    _logger.info("data_prepare_started", **identity)
    preparer = cast(DataPreparer, app.state.data_preparer)
    try:
        result = DataPrepareResult.model_validate(preparer.prepare(command))
        _logger.info(
            "data_prepare_completed",
            **identity,
            elapsed_ms=max(0, round((perf_counter() - started) * 1_000)),
            asset_count=len(result.assets),
        )
    except DataPreparationFailure as error:
        _logger.warning(
            "data_prepare_failed",
            **identity,
            elapsed_ms=max(0, round((perf_counter() - started) * 1_000)),
            error_code=error.response.error.code.value,
            retryable=error.response.error.retryable,
        )
        raise
    except Exception as error:
        _logger.error(
            "data_prepare_unexpected_failure",
            **identity,
            elapsed_ms=max(0, round((perf_counter() - started) * 1_000)),
            error_code=ErrorCode.INTERNAL_ERROR.value,
            error_type=type(error).__name__,
        )
        raise _UnexpectedDataFailure from None

    return result
