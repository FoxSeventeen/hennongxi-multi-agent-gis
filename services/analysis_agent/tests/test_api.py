from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid5

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hennongxi_analysis_agent.artifacts import ArtifactConflictError
from hennongxi_analysis_agent.execution import AnalysisInputError, AnalysisOutcome
from hennongxi_analysis_agent.main import app
from hennongxi_contracts import (
    AnalysisRunResult,
    AreaStatistics,
    ArtifactRef,
    ArtifactStatus,
    ArtifactType,
    DataAssetRef,
    ErrorCode,
    ErrorResponse,
    LogicalDatasetId,
    RasterGrid,
)
from hennongxi_observability import CORRELATION_ID_HEADER
from structlog.testing import capture_logs

TASK_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CORRELATION_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
IDEMPOTENCY_KEY = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
NOW = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)


class _StubExecutor:
    def __init__(self, outcome: AnalysisOutcome | Exception) -> None:
        self._outcome = outcome

    def run(self, _command: object, _idempotency_key: UUID) -> AnalysisOutcome:
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def _inputs() -> list[dict[str, object]]:
    grid = RasterGrid(
        crs="EPSG:32649",
        width=4,
        height=4,
        transform=(10.0, 0.0, 0.0, 0.0, -10.0, 40.0),
        bounds=(0.0, 0.0, 40.0, 40.0),
        nodata=-9999.0,
    )
    return [
        DataAssetRef(
            dataset_id=dataset_id,
            checksum_sha256="a" * 64,
            byte_size=100,
            grid=None if dataset_id is LogicalDatasetId.WATERSHED else grid,
        ).model_dump(mode="json")
        for dataset_id in LogicalDatasetId
    ]


def _command() -> dict[str, object]:
    return {
        "task_id": str(TASK_ID),
        "step_id": "analyze_ndvi_change",
        "attempt": 1,
        "correlation_id": str(CORRELATION_ID),
        "inputs": _inputs(),
    }


def _result() -> AnalysisRunResult:
    artifact_types = (
        ArtifactType.NDVI_BEFORE,
        ArtifactType.NDVI_AFTER,
        ArtifactType.NDVI_DIFFERENCE,
        ArtifactType.CHANGE_CLASSIFICATION,
        ArtifactType.AREA_STATISTICS,
    )
    return AnalysisRunResult(
        task_id=TASK_ID,
        step_id="analyze_ndvi_change",
        attempt=1,
        correlation_id=CORRELATION_ID,
        artifacts=tuple(
            ArtifactRef(
                artifact_id=uuid5(TASK_ID, artifact_type.value),
                task_id=TASK_ID,
                attempt=1,
                artifact_type=artifact_type,
                status=ArtifactStatus.COMPLETE,
                media_type="application/json"
                if artifact_type is ArtifactType.AREA_STATISTICS
                else "image/tiff; application=geotiff",
                created_at=NOW,
                checksum_sha256="a" * 64,
                byte_size=100,
            )
            for artifact_type in artifact_types
        ),
        statistics=AreaStatistics(
            increase_hectares=1,
            stable_hectares=2,
            decrease_hectares=3,
            valid_hectares=6,
        ),
        elapsed_ms=25,
    )


@pytest.fixture
def configured_app() -> Iterator[FastAPI]:
    original = app.state.analysis_executor
    app.state.analysis_executor = _StubExecutor(
        AnalysisOutcome(result=_result(), reused=False)
    )
    try:
        yield app
    finally:
        app.state.analysis_executor = original


def test_analysis_http_returns_contract_result_and_sanitized_correlated_logs(
    configured_app: FastAPI,
) -> None:
    headers = {
        "Idempotency-Key": str(IDEMPOTENCY_KEY),
        CORRELATION_ID_HEADER: str(CORRELATION_ID),
    }
    with capture_logs() as logs, TestClient(configured_app) as client:
        response = client.post("/internal/v1/analysis/run", json=_command(), headers=headers)

    result = AnalysisRunResult.model_validate(response.json())
    assert response.status_code == 200
    assert response.headers[CORRELATION_ID_HEADER] == str(CORRELATION_ID)
    assert result == _result()
    completed = next(log for log in logs if log["event"] == "analysis_completed")
    assert completed["task_id"] == str(TASK_ID)
    assert completed["correlation_id"] == str(CORRELATION_ID)
    assert "/data/" not in str(logs)
    assert "secret" not in str(logs).lower()


@pytest.mark.parametrize("missing_header", ["Idempotency-Key", CORRELATION_ID_HEADER])
def test_analysis_http_requires_identity_headers(
    configured_app: FastAPI,
    missing_header: str,
) -> None:
    headers = {
        "Idempotency-Key": str(IDEMPOTENCY_KEY),
        CORRELATION_ID_HEADER: str(CORRELATION_ID),
    }
    del headers[missing_header]

    with TestClient(configured_app) as client:
        response = client.post("/internal/v1/analysis/run", json=_command(), headers=headers)

    error = ErrorResponse.model_validate(response.json())
    assert response.status_code == 422
    assert error.error.code is ErrorCode.VALIDATION_ERROR


def test_analysis_http_rejects_path_injection_without_echoing_it(
    configured_app: FastAPI,
) -> None:
    command = _command()
    command["input_path"] = "/etc/passwd"

    with TestClient(configured_app) as client:
        response = client.post(
            "/internal/v1/analysis/run",
            json=command,
            headers={
                "Idempotency-Key": str(IDEMPOTENCY_KEY),
                CORRELATION_ID_HEADER: str(CORRELATION_ID),
            },
        )

    error = ErrorResponse.model_validate(response.json())
    assert response.status_code == 422
    assert error.error.code is ErrorCode.VALIDATION_ERROR
    assert "/etc/passwd" not in response.text


def test_analysis_http_rejects_mismatched_correlation_identity(
    configured_app: FastAPI,
) -> None:
    with TestClient(configured_app) as client:
        response = client.post(
            "/internal/v1/analysis/run",
            json=_command(),
            headers={
                "Idempotency-Key": str(IDEMPOTENCY_KEY),
                CORRELATION_ID_HEADER: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
            },
        )

    error = ErrorResponse.model_validate(response.json())
    assert response.status_code == 422
    assert error.error.code is ErrorCode.VALIDATION_ERROR


@pytest.mark.parametrize(
    ("failure", "expected_code", "retryable"),
    [
        (AnalysisInputError("private /data/cache/source.tif"), ErrorCode.DATA_INVALID, True),
        (
            ArtifactConflictError("different idempotency key for /data/outputs"),
            ErrorCode.CONFLICT,
            False,
        ),
    ],
)
def test_analysis_http_maps_expected_failures_without_private_details(
    configured_app: FastAPI,
    failure: Exception,
    expected_code: ErrorCode,
    retryable: bool,
) -> None:
    configured_app.state.analysis_executor = _StubExecutor(failure)

    with TestClient(configured_app) as client:
        response = client.post(
            "/internal/v1/analysis/run",
            json=_command(),
            headers={
                "Idempotency-Key": str(IDEMPOTENCY_KEY),
                CORRELATION_ID_HEADER: str(CORRELATION_ID),
            },
        )

    error = ErrorResponse.model_validate(response.json())
    assert response.status_code == 409
    assert error.error.code is expected_code
    assert error.error.retryable is retryable
    assert "/data/" not in response.text


def test_analysis_http_sanitizes_unexpected_failures(configured_app: FastAPI) -> None:
    configured_app.state.analysis_executor = _StubExecutor(RuntimeError("secret /tmp/private"))

    with TestClient(configured_app, raise_server_exceptions=False) as client:
        response = client.post(
            "/internal/v1/analysis/run",
            json=_command(),
            headers={
                "Idempotency-Key": str(IDEMPOTENCY_KEY),
                CORRELATION_ID_HEADER: str(CORRELATION_ID),
            },
        )

    error = ErrorResponse.model_validate(response.json())
    assert response.status_code == 500
    assert error.error.code is ErrorCode.INTERNAL_ERROR
    assert "secret" not in response.text.lower()
    assert "/tmp/private" not in response.text
