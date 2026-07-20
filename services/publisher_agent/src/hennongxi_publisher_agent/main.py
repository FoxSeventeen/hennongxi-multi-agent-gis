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
app.state.publisher_catalog = PublisherArtifactCatalog(
    Path(os.environ.get("ARTIFACT_ROOT", "/data/outputs")),
    Path(os.environ.get("QUALITY_REPORT_ROOT", "/data/quality-reports")),
)
app.state.tile_renderer = TileRenderer()
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
