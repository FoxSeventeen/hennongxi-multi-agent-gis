"""Independently startable Analysis Agent application."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, cast
from uuid import UUID

import structlog
from fastapi import Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from hennongxi_contracts import (
    AgentName,
    AnalysisRunCommand,
    AnalysisRunResult,
    ErrorCode,
    ErrorResponse,
    StructuredError,
)
from hennongxi_observability import create_observed_agent_app

from hennongxi_analysis_agent.artifacts import (
    AnalysisArtifactStore,
    ArtifactConflictError,
    ArtifactIntegrityError,
)
from hennongxi_analysis_agent.execution import AnalysisExecutor, AnalysisInputError

PORT = 8002
app = create_observed_agent_app(AgentName.ANALYSIS, PORT)

_manifest_path = Path(os.environ.get("DATA_MANIFEST_PATH", "/app/data/manifest.json"))
app.state.analysis_executor = AnalysisExecutor(
    _manifest_path,
    data_root=_manifest_path.parent,
    cache_dir=Path(os.environ.get("DATA_CACHE_DIR", "/data/cache")),
    artifact_store=AnalysisArtifactStore(Path(os.environ.get("ARTIFACT_ROOT", "/data/outputs"))),
)
_logger = structlog.get_logger("hennongxi.analysis")


class _AnalysisServiceFailure(RuntimeError):
    def __init__(
        self,
        *,
        status_code: int,
        response: ErrorResponse,
        command: AnalysisRunCommand,
    ) -> None:
        super().__init__(response.error.code.value)
        self.status_code = status_code
        self.response = response
        self.task_id = command.task_id
        self.step_id = command.step_id
        self.attempt = command.attempt
        self.correlation_id = command.correlation_id


def _failure(
    command: AnalysisRunCommand,
    *,
    status_code: int,
    code: ErrorCode,
    message: str,
    retryable: bool,
) -> _AnalysisServiceFailure:
    return _AnalysisServiceFailure(
        status_code=status_code,
        response=ErrorResponse(
            error=StructuredError(
                code=code,
                message=message,
                retryable=retryable,
            )
        ),
        command=command,
    )


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(
    _request: Request,
    _error: RequestValidationError,
) -> JSONResponse:
    _logger.warning("analysis_request_rejected", error_code=ErrorCode.VALIDATION_ERROR.value)
    response = ErrorResponse(
        error=StructuredError(
            code=ErrorCode.VALIDATION_ERROR,
            message="request does not match the analysis execution contract",
            retryable=False,
        )
    )
    return JSONResponse(status_code=422, content=response.model_dump(mode="json"))


@app.exception_handler(_AnalysisServiceFailure)
async def analysis_service_failure_handler(
    _request: Request,
    error: _AnalysisServiceFailure,
) -> JSONResponse:
    _logger.warning(
        "analysis_failed",
        task_id=str(error.task_id),
        step_id=error.step_id,
        attempt=error.attempt,
        correlation_id=str(error.correlation_id),
        error_code=error.response.error.code.value,
        retryable=error.response.error.retryable,
    )
    return JSONResponse(
        status_code=error.status_code,
        content=error.response.model_dump(mode="json"),
    )


@app.exception_handler(Exception)
async def unexpected_analysis_failure_handler(
    _request: Request,
    error: Exception,
) -> JSONResponse:
    _logger.error(
        "analysis_unexpected_failure",
        error_code=ErrorCode.INTERNAL_ERROR.value,
        error_type=type(error).__name__,
    )
    response = ErrorResponse(
        error=StructuredError(
            code=ErrorCode.INTERNAL_ERROR,
            message="analysis execution failed unexpectedly",
            retryable=True,
        )
    )
    return JSONResponse(status_code=500, content=response.model_dump(mode="json"))


@app.post(
    "/internal/v1/analysis/run",
    response_model=AnalysisRunResult,
    responses={
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def run_analysis(
    command: AnalysisRunCommand,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    correlation_id: Annotated[UUID, Header(alias="X-Correlation-ID")],
) -> AnalysisRunResult:
    """Run blocking raster work in FastAPI's worker threadpool."""

    if correlation_id != command.correlation_id:
        raise _failure(
            command,
            status_code=422,
            code=ErrorCode.VALIDATION_ERROR,
            message="correlation header does not match the analysis command",
            retryable=False,
        )

    _logger.info(
        "analysis_started",
        task_id=str(command.task_id),
        step_id=command.step_id,
        attempt=command.attempt,
        correlation_id=str(command.correlation_id),
    )
    executor = cast(AnalysisExecutor, app.state.analysis_executor)
    try:
        outcome = executor.run(command, idempotency_key)
    except AnalysisInputError as error:
        raise _failure(
            command,
            status_code=409,
            code=ErrorCode.DATA_INVALID,
            message="approved analysis inputs failed validation",
            retryable=True,
        ) from error
    except ArtifactConflictError as error:
        raise _failure(
            command,
            status_code=409,
            code=ErrorCode.CONFLICT,
            message="analysis attempt conflicts with an existing result",
            retryable=False,
        ) from error
    except ArtifactIntegrityError as error:
        raise _failure(
            command,
            status_code=409,
            code=ErrorCode.ANALYSIS_FAILED,
            message="published analysis artifacts failed integrity validation",
            retryable=True,
        ) from error
    except OSError as error:
        raise _failure(
            command,
            status_code=503,
            code=ErrorCode.DEPENDENCY_UNAVAILABLE,
            message="analysis artifact storage is unavailable",
            retryable=True,
        ) from error

    _logger.info(
        "analysis_reused" if outcome.reused else "analysis_completed",
        task_id=str(command.task_id),
        step_id=command.step_id,
        attempt=command.attempt,
        correlation_id=str(command.correlation_id),
        elapsed_ms=outcome.result.elapsed_ms,
        artifact_count=len(outcome.result.artifacts),
    )
    return outcome.result
