"""Independently startable Data Agent application."""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast

from fastapi import Request
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


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(
    _request: Request,
    _error: RequestValidationError,
) -> JSONResponse:
    response = ErrorResponse(
        error=StructuredError(
            code=ErrorCode.VALIDATION_ERROR,
            message="request body does not match the data preparation contract",
            retryable=False,
        )
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


@app.post(
    "/internal/v1/data/prepare",
    response_model=DataPrepareResult,
    responses={
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def prepare_data(command: DataPrepareCommand) -> DataPrepareResult:
    """Inspect approved local inputs in FastAPI's worker threadpool."""

    preparer = cast(DataPreparer, app.state.data_preparer)
    return preparer.prepare(command)
