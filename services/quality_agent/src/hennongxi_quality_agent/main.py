"""Independently startable Quality Agent application."""

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
    ErrorCode,
    ErrorResponse,
    QualityEvaluateCommand,
    QualityEvaluateResult,
    StructuredError,
)
from hennongxi_observability import create_observed_agent_app

from hennongxi_quality_agent.artifacts import (
    QualityArtifactConflictError,
    QualityArtifactIntegrityError,
    QualityArtifactStore,
)
from hennongxi_quality_agent.configuration import QualityConfigurationError
from hennongxi_quality_agent.execution import QualityExecutor

PORT = 8003
app = create_observed_agent_app(AgentName.QUALITY, PORT)

app.state.quality_executor = QualityExecutor(
    Path(os.environ.get("DATA_MANIFEST_PATH", "/app/data/manifest.json")),
    analysis_artifact_root=Path(os.environ.get("ARTIFACT_ROOT", "/data/outputs")),
    report_store=QualityArtifactStore(
        Path(os.environ.get("QUALITY_REPORT_ROOT", "/data/quality-reports"))
    ),
)
_logger = structlog.get_logger("hennongxi.quality")


class _QualityServiceFailure(RuntimeError):
    def __init__(
        self,
        *,
        status_code: int,
        response: ErrorResponse,
        command: QualityEvaluateCommand,
    ) -> None:
        super().__init__(response.error.code.value)
        self.status_code = status_code
        self.response = response
        self.task_id = command.task_id
        self.step_id = command.step_id
        self.attempt = command.attempt
        self.correlation_id = command.correlation_id


def _failure(
    command: QualityEvaluateCommand,
    *,
    status_code: int,
    code: ErrorCode,
    message: str,
    retryable: bool,
) -> _QualityServiceFailure:
    return _QualityServiceFailure(
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
    _logger.warning("quality_request_rejected", error_code=ErrorCode.VALIDATION_ERROR.value)
    response = ErrorResponse(
        error=StructuredError(
            code=ErrorCode.VALIDATION_ERROR,
            message="request does not match the quality evaluation contract",
            retryable=False,
        )
    )
    return JSONResponse(status_code=422, content=response.model_dump(mode="json"))


@app.exception_handler(_QualityServiceFailure)
async def quality_service_failure_handler(
    _request: Request,
    error: _QualityServiceFailure,
) -> JSONResponse:
    _logger.warning(
        "quality_failed",
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
async def unexpected_quality_failure_handler(
    _request: Request,
    error: Exception,
) -> JSONResponse:
    _logger.error(
        "quality_unexpected_failure",
        error_code=ErrorCode.INTERNAL_ERROR.value,
        error_type=type(error).__name__,
    )
    response = ErrorResponse(
        error=StructuredError(
            code=ErrorCode.INTERNAL_ERROR,
            message="quality evaluation failed unexpectedly",
            retryable=True,
        )
    )
    return JSONResponse(status_code=500, content=response.model_dump(mode="json"))


@app.post(
    "/internal/v1/quality/evaluate",
    response_model=QualityEvaluateResult,
    responses={
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def evaluate_quality(
    command: QualityEvaluateCommand,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    correlation_id: Annotated[UUID, Header(alias="X-Correlation-ID")],
) -> QualityEvaluateResult:
    """Inspect Analysis outputs in FastAPI's worker threadpool."""

    if correlation_id != command.correlation_id:
        raise _failure(
            command,
            status_code=422,
            code=ErrorCode.VALIDATION_ERROR,
            message="correlation header does not match the quality command",
            retryable=False,
        )

    _logger.info(
        "quality_started",
        task_id=str(command.task_id),
        step_id=command.step_id,
        attempt=command.attempt,
        correlation_id=str(command.correlation_id),
    )
    executor = cast(QualityExecutor, app.state.quality_executor)
    try:
        outcome = executor.run(command, idempotency_key)
    except QualityConfigurationError as error:
        raise _failure(
            command,
            status_code=503,
            code=ErrorCode.DEPENDENCY_UNAVAILABLE,
            message="approved quality reference data is unavailable",
            retryable=True,
        ) from error
    except QualityArtifactConflictError as error:
        raise _failure(
            command,
            status_code=409,
            code=ErrorCode.CONFLICT,
            message="quality attempt conflicts with an existing report",
            retryable=False,
        ) from error
    except QualityArtifactIntegrityError as error:
        raise _failure(
            command,
            status_code=409,
            code=ErrorCode.QUALITY_FAILED,
            message="published quality report failed integrity validation",
            retryable=True,
        ) from error
    except OSError as error:
        raise _failure(
            command,
            status_code=503,
            code=ErrorCode.DEPENDENCY_UNAVAILABLE,
            message="quality report storage is unavailable",
            retryable=True,
        ) from error

    metrics = outcome.result.metrics
    _logger.info(
        "quality_reused" if outcome.reused else "quality_completed",
        task_id=str(command.task_id),
        step_id=command.step_id,
        attempt=command.attempt,
        correlation_id=str(command.correlation_id),
        conclusion=metrics.conclusion.value,
        coverage_ratio=round(metrics.coverage_ratio, 4),
        valid_pixel_ratio=round(metrics.valid_pixel_ratio, 4),
        output_complete=metrics.output_complete,
        elapsed_ms=metrics.elapsed_ms,
    )
    return outcome.result
