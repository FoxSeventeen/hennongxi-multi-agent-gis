from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
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
    PublishedResource,
    PublisherPublishCommand,
    PublisherPublishResult,
    QualityConclusion,
    QualityMetrics,
    QualityThresholds,
    TileArtifactType,
    TileMetadata,
)
from hennongxi_observability import CORRELATION_ID_HEADER
from hennongxi_publisher_agent.catalog import (
    PublishedTileIntegrityError,
    PublishedTileNotFoundError,
)
from hennongxi_publisher_agent.main import app
from hennongxi_publisher_agent.publication import (
    PublicationArtifactError,
    PublicationConfigurationError,
)
from hennongxi_publisher_agent.tiles import style_for
from structlog.testing import capture_logs

TASK_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CORRELATION_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
NOW = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)


def _artifact(artifact_type: ArtifactType) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=uuid5(TASK_ID, f"publisher-test:{artifact_type.value}"),
        task_id=TASK_ID,
        attempt=1,
        artifact_type=artifact_type,
        status=ArtifactStatus.COMPLETE,
        media_type=(
            "application/json"
            if artifact_type in {ArtifactType.AREA_STATISTICS, ArtifactType.QUALITY_REPORT}
            else "image/tiff; application=geotiff"
        ),
        created_at=NOW,
        checksum_sha256="a" * 64,
        byte_size=100,
    )


def _quality() -> QualityMetrics:
    return QualityMetrics(
        coverage_ratio=1,
        valid_pixel_ratio=0.95,
        output_complete=True,
        elapsed_ms=20,
        thresholds=QualityThresholds(
            minimum_watershed_coverage_ratio=0.95,
            minimum_valid_pixel_ratio=0.9,
        ),
        conclusion=QualityConclusion.PASS,
        passed=True,
        evidence=("覆盖率通过", "有效像元率通过", "成果完整", "耗时已记录"),
    )


def _command() -> PublisherPublishCommand:
    return PublisherPublishCommand(
        task_id=TASK_ID,
        step_id="publish_results",
        attempt=1,
        correlation_id=CORRELATION_ID,
        artifacts=tuple(
            _artifact(artifact_type)
            for artifact_type in (
                ArtifactType.NDVI_BEFORE,
                ArtifactType.NDVI_AFTER,
                ArtifactType.NDVI_DIFFERENCE,
                ArtifactType.CHANGE_CLASSIFICATION,
                ArtifactType.AREA_STATISTICS,
                ArtifactType.QUALITY_REPORT,
            )
        ),
        quality=_quality(),
    )


def _result() -> PublisherPublishResult:
    resources = []
    for artifact_type in TileArtifactType:
        style = style_for(artifact_type)
        resources.append(
            PublishedResource(
                artifact_id=_artifact(ArtifactType(artifact_type.value)).artifact_id,
                tile_template=(
                    f"/api/v1/tiles/{TASK_ID}/{artifact_type.value}/{{z}}/{{x}}/{{y}}.png"
                ),
                tile_metadata=TileMetadata(
                    artifact_type=artifact_type,
                    bounds_wgs84=(110.1, 31.0, 110.6, 31.5),
                    start_date=date(2019, 8, 19),
                    end_date=(
                        date(2019, 8, 19)
                        if artifact_type is TileArtifactType.NDVI_BEFORE
                        else date(2024, 8, 12)
                    ),
                    units=style.units,
                    attribution="包含经修改的 Copernicus Sentinel 数据",
                    legend=style.legend,
                ),
            )
        )
    return PublisherPublishResult(
        task_id=TASK_ID,
        step_id="publish_results",
        attempt=1,
        correlation_id=CORRELATION_ID,
        resources=tuple(resources),
    )


class _PublicationService:
    def __init__(self, outcome: PublisherPublishResult | Exception) -> None:
        self._outcome = outcome

    def publish(self, _command: PublisherPublishCommand) -> PublisherPublishResult:
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


@pytest.fixture
def configured_app() -> Iterator[FastAPI]:
    previous = getattr(app.state, "publication_service", None)
    app.state.publication_service = _PublicationService(_result())
    try:
        yield app
    finally:
        if previous is None:
            del app.state.publication_service
        else:
            app.state.publication_service = previous


def test_publish_route_returns_task_bound_metadata_and_correlated_log(
    configured_app: FastAPI,
) -> None:
    with capture_logs() as logs, TestClient(configured_app) as client:
        response = client.post(
            "/internal/v1/publisher/publish",
            json=_command().model_dump(mode="json"),
            headers={CORRELATION_ID_HEADER: str(CORRELATION_ID)},
        )

    assert response.status_code == 200
    result = PublisherPublishResult.model_validate(response.json())
    assert result == _result()
    assert response.headers[CORRELATION_ID_HEADER] == str(CORRELATION_ID)
    published = next(log for log in logs if log["event"] == "publisher_metadata_published")
    assert published["task_id"] == str(TASK_ID)
    assert published["resource_count"] == 4
    assert "/data/" not in str(logs)


@pytest.mark.parametrize(
    ("failure", "status_code", "code"),
    [
        (PublishedTileIntegrityError("private /data/output"), 409, ErrorCode.PUBLISHING_FAILED),
        (PublishedTileNotFoundError("private /data/output"), 409, ErrorCode.PUBLISHING_FAILED),
        (PublicationArtifactError("private /data/output"), 409, ErrorCode.PUBLISHING_FAILED),
        (
            PublicationConfigurationError("private /app/data/manifest.json"),
            503,
            ErrorCode.DEPENDENCY_UNAVAILABLE,
        ),
    ],
)
def test_publish_route_maps_expected_failures_without_private_details(
    configured_app: FastAPI,
    failure: Exception,
    status_code: int,
    code: ErrorCode,
) -> None:
    configured_app.state.publication_service = _PublicationService(failure)
    with TestClient(configured_app) as client:
        response = client.post(
            "/internal/v1/publisher/publish",
            json=_command().model_dump(mode="json"),
        )

    error = ErrorResponse.model_validate(response.json())
    assert response.status_code == status_code
    assert error.error.code is code
    assert "private" not in response.text
    assert "/data/" not in response.text
    assert "/app/" not in response.text
