"""Independently startable Publisher Agent application."""

from __future__ import annotations

import os
from pathlib import Path
from time import perf_counter
from typing import Annotated, cast
from uuid import UUID

import structlog
from fastapi import Path as ApiPath
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from hennongxi_contracts import (
    AgentName,
    ErrorCode,
    ErrorResponse,
    PublisherPublishCommand,
    PublisherPublishResult,
    StructuredError,
    TileArtifactType,
)
from hennongxi_observability import create_observed_agent_app
from starlette.exceptions import HTTPException as StarletteHTTPException

from hennongxi_publisher_agent.catalog import (
    PublishedTileIntegrityError,
    PublishedTileNotFoundError,
    PublisherArtifactCatalog,
)
from hennongxi_publisher_agent.publication import (
    PublicationArtifactError,
    PublicationConfigurationError,
    PublicationService,
)
from hennongxi_publisher_agent.tiles import (
    TileCoordinateError,
    TileOutsideSourceError,
    TileRenderer,
    TileSourceError,
)

PORT = 8004
app = create_observed_agent_app(AgentName.PUBLISHER, PORT)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:3000", "http://localhost:3000"],
    allow_methods=["GET"],
    allow_headers=["X-Correlation-ID"],
)
_catalog = PublisherArtifactCatalog(
    Path(os.environ.get("ARTIFACT_ROOT", "/data/outputs")),
    Path(os.environ.get("QUALITY_REPORT_ROOT", "/data/quality-reports")),
)
app.state.publisher_catalog = _catalog
app.state.tile_renderer = TileRenderer()
app.state.publication_service = PublicationService(
    _catalog,
    Path(os.environ.get("DATA_MANIFEST_PATH", "/app/data/manifest.json")),
)
_logger = structlog.get_logger("hennongxi.publisher")


class _PublisherServiceFailure(RuntimeError):
    def __init__(
        self,
        *,
        status_code: int,
        response: ErrorResponse,
        task_id: UUID,
        artifact_type: TileArtifactType,
    ) -> None:
        super().__init__(response.error.code.value)
        self.status_code = status_code
        self.response = response
        self.task_id = task_id
        self.artifact_type = artifact_type


class _PublicationServiceFailure(RuntimeError):
    def __init__(
        self,
        *,
        status_code: int,
        response: ErrorResponse,
        command: PublisherPublishCommand,
    ) -> None:
        super().__init__(response.error.code.value)
        self.status_code = status_code
        self.response = response
        self.command = command


def _failure(
    *,
    status_code: int,
    code: ErrorCode,
    message: str,
    retryable: bool,
    task_id: UUID,
    artifact_type: TileArtifactType,
) -> _PublisherServiceFailure:
    return _PublisherServiceFailure(
        status_code=status_code,
        response=ErrorResponse(
            error=StructuredError(
                code=code,
                message=message,
                retryable=retryable,
            )
        ),
        task_id=task_id,
        artifact_type=artifact_type,
    )


def _error_response(
    *,
    status_code: int,
    code: ErrorCode,
    message: str,
    retryable: bool,
) -> JSONResponse:
    response = ErrorResponse(
        error=StructuredError(
            code=code,
            message=message,
            retryable=retryable,
        )
    )
    return JSONResponse(status_code=status_code, content=response.model_dump(mode="json"))


def _publication_failure(
    command: PublisherPublishCommand,
    *,
    status_code: int,
    code: ErrorCode,
    message: str,
    retryable: bool,
) -> _PublicationServiceFailure:
    return _PublicationServiceFailure(
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
    _logger.warning("publisher_request_rejected", error_code=ErrorCode.VALIDATION_ERROR.value)
    return _error_response(
        status_code=422,
        code=ErrorCode.VALIDATION_ERROR,
        message="request does not match the publisher resource contract",
        retryable=False,
    )


@app.exception_handler(StarletteHTTPException)
async def publisher_http_error_handler(
    _request: Request,
    error: StarletteHTTPException,
) -> JSONResponse:
    if error.status_code == 404:
        return _error_response(
            status_code=404,
            code=ErrorCode.TASK_NOT_FOUND,
            message="published resource was not found",
            retryable=False,
        )
    return _error_response(
        status_code=error.status_code,
        code=ErrorCode.VALIDATION_ERROR,
        message="publisher resource request was rejected",
        retryable=False,
    )


@app.exception_handler(_PublisherServiceFailure)
async def publisher_service_failure_handler(
    _request: Request,
    error: _PublisherServiceFailure,
) -> JSONResponse:
    _logger.warning(
        "publisher_tile_failed",
        task_id=str(error.task_id),
        artifact_type=error.artifact_type.value,
        error_code=error.response.error.code.value,
        retryable=error.response.error.retryable,
    )
    return JSONResponse(
        status_code=error.status_code,
        content=error.response.model_dump(mode="json"),
    )


@app.exception_handler(_PublicationServiceFailure)
async def publication_service_failure_handler(
    _request: Request,
    error: _PublicationServiceFailure,
) -> JSONResponse:
    _logger.warning(
        "publisher_metadata_failed",
        task_id=str(error.command.task_id),
        step_id=error.command.step_id,
        attempt=error.command.attempt,
        correlation_id=str(error.command.correlation_id),
        error_code=error.response.error.code.value,
        retryable=error.response.error.retryable,
    )
    return JSONResponse(
        status_code=error.status_code,
        content=error.response.model_dump(mode="json"),
    )


@app.exception_handler(Exception)
async def unexpected_publisher_failure_handler(
    _request: Request,
    error: Exception,
) -> JSONResponse:
    _logger.error(
        "publisher_unexpected_failure",
        error_code=ErrorCode.INTERNAL_ERROR.value,
        error_type=type(error).__name__,
    )
    return _error_response(
        status_code=500,
        code=ErrorCode.INTERNAL_ERROR,
        message="publisher resource request failed unexpectedly",
        retryable=True,
    )


@app.post(
    "/internal/v1/publisher/publish",
    response_model=PublisherPublishResult,
    responses={
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def publish_results(command: PublisherPublishCommand) -> PublisherPublishResult:
    """Return browser resource metadata only after independent receipt verification."""

    service = cast(PublicationService, app.state.publication_service)
    try:
        result = service.publish(command)
    except (
        PublishedTileIntegrityError,
        PublishedTileNotFoundError,
        PublicationArtifactError,
    ) as error:
        raise _publication_failure(
            command,
            status_code=409,
            code=ErrorCode.PUBLISHING_FAILED,
            message="publishable artifacts failed integrity validation",
            retryable=True,
        ) from error
    except PublicationConfigurationError as error:
        raise _publication_failure(
            command,
            status_code=503,
            code=ErrorCode.DEPENDENCY_UNAVAILABLE,
            message="approved publication source metadata is unavailable",
            retryable=True,
        ) from error

    _logger.info(
        "publisher_metadata_published",
        task_id=str(command.task_id),
        step_id=command.step_id,
        attempt=command.attempt,
        correlation_id=str(command.correlation_id),
        resource_count=len(result.resources),
    )
    return result


@app.get(
    "/api/v1/tiles/{task_id}/{artifact_type}/{z}/{x}/{y}.png",
    response_class=Response,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
def get_artifact_tile(
    task_id: Annotated[UUID, ApiPath()],
    artifact_type: Annotated[TileArtifactType, ApiPath()],
    z: Annotated[int, ApiPath(ge=0, le=24)],
    x: Annotated[int, ApiPath(ge=0)],
    y: Annotated[int, ApiPath(ge=0)],
) -> Response:
    """Render one verified local artifact without accepting a storage path."""

    started = perf_counter()
    catalog = cast(PublisherArtifactCatalog, app.state.publisher_catalog)
    renderer = cast(TileRenderer, app.state.tile_renderer)
    try:
        resolved = catalog.resolve_tile(task_id, artifact_type)
        payload = renderer.render(resolved.path, artifact_type, z=z, x=x, y=y)
    except (PublishedTileNotFoundError, TileOutsideSourceError) as error:
        raise _failure(
            status_code=404,
            code=ErrorCode.TASK_NOT_FOUND,
            message="published tile was not found",
            retryable=False,
            task_id=task_id,
            artifact_type=artifact_type,
        ) from error
    except (PublishedTileIntegrityError, TileSourceError) as error:
        raise _failure(
            status_code=409,
            code=ErrorCode.PUBLISHING_FAILED,
            message="published tile failed integrity validation",
            retryable=True,
            task_id=task_id,
            artifact_type=artifact_type,
        ) from error
    except TileCoordinateError as error:
        raise _failure(
            status_code=422,
            code=ErrorCode.VALIDATION_ERROR,
            message="tile coordinate is outside the Web Mercator grid",
            retryable=False,
            task_id=task_id,
            artifact_type=artifact_type,
        ) from error

    elapsed_ms = max(0, round((perf_counter() - started) * 1_000))
    _logger.info(
        "publisher_tile_served",
        task_id=str(task_id),
        artifact_type=artifact_type.value,
        artifact_id=str(resolved.artifact.artifact_id),
        attempt=resolved.attempt,
        z=z,
        x=x,
        y=y,
        elapsed_ms=elapsed_ms,
    )
    return Response(
        content=payload,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=60, must-revalidate",
            "ETag": f'"{resolved.artifact.checksum_sha256}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
