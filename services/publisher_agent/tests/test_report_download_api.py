from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hennongxi_contracts import ErrorCode, ErrorResponse
from hennongxi_observability import CORRELATION_ID_HEADER
from hennongxi_publisher_agent.main import app
from hennongxi_publisher_agent.report_artifacts import (
    ReportArtifactOutcome,
    ReportArtifactStore,
)
from structlog.testing import capture_logs

TASK_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
OTHER_TASK_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
CORRELATION_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
IDEMPOTENCY_KEY = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
CREATED_AT = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)
PDF_PAYLOAD = b"%PDF-1.4\n% downloadable report fixture\n%%EOF\n"


@pytest.fixture
def configured_report_app(tmp_path: Path) -> Iterator[tuple[FastAPI, ReportArtifactOutcome]]:
    store = ReportArtifactStore(tmp_path)
    report = store.publish(
        task_id=TASK_ID,
        attempt=1,
        idempotency_key=IDEMPOTENCY_KEY,
        created_at=CREATED_AT,
        payload=PDF_PAYLOAD,
    )
    previous = app.state.report_store
    app.state.report_store = store
    try:
        yield app, report
    finally:
        app.state.report_store = previous


def _path(task_id: UUID, artifact_id: UUID) -> str:
    return f"/api/v1/tasks/{task_id}/artifacts/{artifact_id}/download"


def test_download_route_returns_verified_task_report_with_safe_headers_and_log(
    configured_report_app: tuple[FastAPI, ReportArtifactOutcome],
) -> None:
    configured_app, report = configured_report_app
    with capture_logs() as logs, TestClient(configured_app) as client:
        response = client.get(
            _path(TASK_ID, report.artifact.artifact_id),
            headers={
                CORRELATION_ID_HEADER: str(CORRELATION_ID),
                "Origin": "http://127.0.0.1:3000",
            },
        )

    assert response.status_code == 200
    assert response.content == PDF_PAYLOAD
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="hennongxi-{TASK_ID}-report.pdf"'
    )
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["etag"] == f'"{report.artifact.checksum_sha256}"'
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"
    assert response.headers[CORRELATION_ID_HEADER] == str(CORRELATION_ID)
    served = next(log for log in logs if log["event"] == "publisher_report_served")
    assert served["task_id"] == str(TASK_ID)
    assert served["artifact_id"] == str(report.artifact.artifact_id)
    assert served["attempt"] == 1
    assert str(report.path) not in str(logs)


def test_download_route_rejects_wrong_task_unknown_artifact_and_corruption(
    configured_report_app: tuple[FastAPI, ReportArtifactOutcome],
) -> None:
    configured_app, report = configured_report_app
    with TestClient(configured_app) as client:
        wrong_task = client.get(_path(OTHER_TASK_ID, report.artifact.artifact_id))
        unknown_artifact = client.get(_path(TASK_ID, UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")))
        report.path.write_bytes(b"corrupted")
        corrupted = client.get(_path(TASK_ID, report.artifact.artifact_id))

    assert wrong_task.status_code == 404
    assert unknown_artifact.status_code == 404
    assert corrupted.status_code == 409
    assert ErrorResponse.model_validate(wrong_task.json()).error.code is ErrorCode.TASK_NOT_FOUND
    assert (
        ErrorResponse.model_validate(unknown_artifact.json()).error.code is ErrorCode.TASK_NOT_FOUND
    )
    assert ErrorResponse.model_validate(corrupted.json()).error.code is ErrorCode.PUBLISHING_FAILED
