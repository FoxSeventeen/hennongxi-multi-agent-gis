from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid5

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hennongxi_contracts import (
    ArtifactRef,
    ArtifactStatus,
    ArtifactType,
    ErrorCode,
    ErrorResponse,
    QualityConclusion,
    QualityEvaluateResult,
    QualityMetrics,
    QualityThresholds,
)
from hennongxi_observability import CORRELATION_ID_HEADER
from hennongxi_quality_agent.artifacts import (
    QualityArtifactConflictError,
    QualityArtifactIntegrityError,
)
from hennongxi_quality_agent.configuration import QualityConfigurationError
from hennongxi_quality_agent.execution import QualityOutcome
from hennongxi_quality_agent.main import app
from structlog.testing import capture_logs

TASK_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CORRELATION_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
IDEMPOTENCY_KEY = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
NOW = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)


class _StubExecutor:
    def __init__(self, outcome: QualityOutcome | Exception) -> None:
        self._outcome = outcome

    def run(self, _command: object, _idempotency_key: UUID) -> QualityOutcome:
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def _command() -> dict[str, object]:
    return {
        "task_id": str(TASK_ID),
        "step_id": "evaluate_quality",
        "attempt": 1,
        "correlation_id": str(CORRELATION_ID),
        "artifacts": [],
        "analysis_elapsed_ms": 1250,
    }


def _result() -> QualityEvaluateResult:
    metrics = QualityMetrics(
        coverage_ratio=0.0,
        valid_pixel_ratio=0.0,
        output_complete=False,
        elapsed_ms=1250,
        thresholds=QualityThresholds(
            minimum_watershed_coverage_ratio=0.95,
            minimum_valid_pixel_ratio=0.90,
        ),
        conclusion=QualityConclusion.FAIL,
        passed=False,
        evidence=("覆盖不足", "有效像元不足", "输出不完整", "耗时已记录"),
    )
    return QualityEvaluateResult(
        task_id=TASK_ID,
        step_id="evaluate_quality",
        attempt=1,
        correlation_id=CORRELATION_ID,
        metrics=metrics,
        artifact=ArtifactRef(
            artifact_id=uuid5(TASK_ID, "quality:1:QUALITY_REPORT"),
            task_id=TASK_ID,
            attempt=1,
            artifact_type=ArtifactType.QUALITY_REPORT,
            status=ArtifactStatus.COMPLETE,
            media_type="application/json",
            created_at=NOW,
            checksum_sha256="a" * 64,
            byte_size=100,
        ),
    )


@pytest.fixture
def configured_app() -> Iterator[FastAPI]:
    original = app.state.quality_executor
    app.state.quality_executor = _StubExecutor(QualityOutcome(result=_result(), reused=False))
    try:
        yield app
    finally:
        app.state.quality_executor = original


def _headers(correlation_id: UUID = CORRELATION_ID) -> dict[str, str]:
    return {
        "Idempotency-Key": str(IDEMPOTENCY_KEY),
        CORRELATION_ID_HEADER: str(correlation_id),
    }


def test_quality_http_returns_contract_result_and_correlated_sanitized_logs(
    configured_app: FastAPI,
) -> None:
    with capture_logs() as logs, TestClient(configured_app) as client:
        response = client.post(
            "/internal/v1/quality/evaluate",
            json=_command(),
            headers=_headers(),
        )

    assert response.status_code == 200
    assert QualityEvaluateResult.model_validate(response.json()) == _result()
    assert response.headers[CORRELATION_ID_HEADER] == str(CORRELATION_ID)
    completed = next(log for log in logs if log["event"] == "quality_completed")
    assert completed["conclusion"] == "FAIL"
    assert completed["correlation_id"] == str(CORRELATION_ID)
    assert "/data/" not in str(logs)
    assert "secret" not in str(logs).lower()


@pytest.mark.parametrize("missing_header", ["Idempotency-Key", CORRELATION_ID_HEADER])
def test_quality_http_requires_identity_headers(
    configured_app: FastAPI,
    missing_header: str,
) -> None:
    headers = _headers()
    del headers[missing_header]
    with TestClient(configured_app) as client:
        response = client.post(
            "/internal/v1/quality/evaluate",
            json=_command(),
            headers=headers,
        )

    error = ErrorResponse.model_validate(response.json())
    assert response.status_code == 422
    assert error.error.code is ErrorCode.VALIDATION_ERROR


def test_quality_http_rejects_path_injection_without_echoing_it(
    configured_app: FastAPI,
) -> None:
    command = _command()
    command["artifact_path"] = "/etc/passwd"
    with TestClient(configured_app) as client:
        response = client.post(
            "/internal/v1/quality/evaluate",
            json=command,
            headers=_headers(),
        )

    assert response.status_code == 422
    assert "/etc/passwd" not in response.text


def test_quality_http_rejects_mismatched_correlation_identity(
    configured_app: FastAPI,
) -> None:
    with TestClient(configured_app) as client:
        response = client.post(
            "/internal/v1/quality/evaluate",
            json=_command(),
            headers=_headers(UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")),
        )

    error = ErrorResponse.model_validate(response.json())
    assert response.status_code == 422
    assert error.error.code is ErrorCode.VALIDATION_ERROR


@pytest.mark.parametrize(
    ("failure", "status_code", "expected_code", "retryable"),
    [
        (
            QualityConfigurationError("private manifest"),
            503,
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            True,
        ),
        (QualityArtifactConflictError("different key"), 409, ErrorCode.CONFLICT, False),
        (QualityArtifactIntegrityError("private report"), 409, ErrorCode.QUALITY_FAILED, True),
    ],
)
def test_quality_http_maps_expected_failures_without_private_details(
    configured_app: FastAPI,
    failure: Exception,
    status_code: int,
    expected_code: ErrorCode,
    retryable: bool,
) -> None:
    configured_app.state.quality_executor = _StubExecutor(failure)
    with TestClient(configured_app) as client:
        response = client.post(
            "/internal/v1/quality/evaluate",
            json=_command(),
            headers=_headers(),
        )

    error = ErrorResponse.model_validate(response.json())
    assert response.status_code == status_code
    assert error.error.code is expected_code
    assert error.error.retryable is retryable
    assert "private" not in response.text


def test_quality_http_sanitizes_unexpected_failures(configured_app: FastAPI) -> None:
    configured_app.state.quality_executor = _StubExecutor(RuntimeError("secret /tmp/private"))
    with TestClient(configured_app, raise_server_exceptions=False) as client:
        response = client.post(
            "/internal/v1/quality/evaluate",
            json=_command(),
            headers=_headers(),
        )

    error = ErrorResponse.model_validate(response.json())
    assert response.status_code == 500
    assert error.error.code is ErrorCode.INTERNAL_ERROR
    assert "secret" not in response.text.lower()
    assert "/tmp/private" not in response.text
