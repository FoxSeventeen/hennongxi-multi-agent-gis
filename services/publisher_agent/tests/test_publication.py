from __future__ import annotations

import json
from datetime import UTC, date, datetime
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid5

import numpy as np
import pytest
import rasterio
from hennongxi_contracts import (
    AnalysisRunResult,
    AreaStatistics,
    ArtifactRef,
    ArtifactStatus,
    ArtifactType,
    PublisherPublishCommand,
    QualityConclusion,
    QualityEvaluateResult,
    QualityMetrics,
    QualityThresholds,
    TileArtifactType,
)
from hennongxi_publisher_agent.catalog import ResolvedPublication, ResolvedTile
from hennongxi_publisher_agent.publication import (
    PublicationConfigurationError,
    PublicationService,
)
from hennongxi_publisher_agent.report_artifacts import ReportArtifactStore
from pypdf import PdfReader
from rasterio.transform import from_bounds

TASK_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CORRELATION_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
IDEMPOTENCY_KEY = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
NOW = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)


def _artifact(artifact_type: ArtifactType) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=uuid5(TASK_ID, f"analysis:1:{artifact_type.value}"),
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


def _write_rasters(root: Path) -> tuple[ResolvedTile, ...]:
    tiles = []
    for tile_type in TileArtifactType:
        path = root / f"{tile_type.value.lower()}.tif"
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            width=2,
            height=2,
            count=1,
            dtype="float32",
            crs="EPSG:4326",
            transform=from_bounds(110.1, 31.0, 110.6, 31.5, width=2, height=2),
            nodata=-9999.0,
        ) as dataset:
            dataset.write(np.ones((2, 2), dtype="float32"), 1)
        tiles.append(
            ResolvedTile(
                path=path,
                artifact=_artifact(ArtifactType(tile_type.value)),
                attempt=1,
            )
        )
    return tuple(tiles)


def _write_manifest(path: Path, *, include_after: bool = True) -> None:
    assets = []
    for logical_id, acquired_on in (
        ("before_red", "2019-08-19"),
        ("before_nir", "2019-08-19"),
        ("after_red", "2024-08-12"),
        ("after_nir", "2024-08-12"),
    ):
        if not include_after and logical_id.startswith("after"):
            continue
        assets.append(
            {
                "logical_id": logical_id,
                "acquired_on": acquired_on,
                "source_assets": [
                    {
                        "organization": (
                            "European Union / ESA Copernicus; Element 84 COG distribution"
                        )
                    }
                ],
            }
        )
    path.write_text(
        json.dumps(
            {
                "approval": {"gate": "G2", "status": "approved"},
                "assets": assets,
            }
        ),
        encoding="utf-8",
    )


class _Catalog:
    def __init__(self, publication: ResolvedPublication) -> None:
        self.publication = publication

    def resolve_publication(self, _command: PublisherPublishCommand) -> ResolvedPublication:
        return self.publication


def _resolved_publication(root: Path) -> ResolvedPublication:
    analysis = AnalysisRunResult(
        task_id=TASK_ID,
        step_id="analyze_ndvi_change",
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
            )
        ),
        statistics=AreaStatistics(
            increase_hectares=1.25,
            stable_hectares=3.5,
            decrease_hectares=0.75,
            valid_hectares=5.5,
        ),
        elapsed_ms=20,
    )
    quality = QualityEvaluateResult(
        task_id=TASK_ID,
        step_id="evaluate_quality",
        attempt=1,
        correlation_id=CORRELATION_ID,
        metrics=_quality(),
        artifact=_artifact(ArtifactType.QUALITY_REPORT),
    )
    return ResolvedPublication(
        attempt=1,
        correlation_id=CORRELATION_ID,
        tiles=_write_rasters(root),
        analysis=analysis,
        quality=quality,
    )


def test_publication_builds_four_task_bound_resources_from_manifest_and_rasters(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(manifest_path)
    publication = _resolved_publication(tmp_path)

    result = PublicationService(
        _Catalog(publication),
        manifest_path,
        ReportArtifactStore(tmp_path / "outputs"),
    ).publish(_command(), IDEMPOTENCY_KEY)

    assert result.report.artifact_type is ArtifactType.PDF_REPORT
    assert len(result.resources) == 5
    resources = {
        resource.tile_metadata.artifact_type: resource
        for resource in result.resources
        if resource.tile_metadata is not None
    }
    before = resources[TileArtifactType.NDVI_BEFORE]
    after = resources[TileArtifactType.NDVI_AFTER]
    difference = resources[TileArtifactType.NDVI_DIFFERENCE]
    assert before.tile_metadata.start_date == before.tile_metadata.end_date == date(2019, 8, 19)
    assert after.tile_metadata.start_date == after.tile_metadata.end_date == date(2024, 8, 12)
    assert difference.tile_metadata.start_date == date(2019, 8, 19)
    assert difference.tile_metadata.end_date == date(2024, 8, 12)
    assert difference.tile_metadata.bounds_wgs84 == pytest.approx((110.1, 31.0, 110.6, 31.5))
    assert "修改" in difference.tile_metadata.attribution
    assert "Copernicus" in difference.tile_metadata.attribution
    assert difference.tile_metadata.legend
    for artifact_type, resource in resources.items():
        assert resource.tile_template == (
            f"/api/v1/tiles/{TASK_ID}/{artifact_type.value}/{{z}}/{{x}}/{{y}}.png"
        )
    download = next(resource for resource in result.resources if resource.download_path is not None)
    assert download.artifact_id == result.report.artifact_id
    assert download.download_path == (
        f"/api/v1/tasks/{TASK_ID}/artifacts/{result.report.artifact_id}/download"
    )
    report_path = tmp_path / "outputs" / str(TASK_ID) / "attempt-1" / "publisher" / "report.pdf"
    report_text = "\n".join(
        page.extract_text() or "" for page in PdfReader(BytesIO(report_path.read_bytes())).pages
    )
    assert "增加 1.25 公顷" in report_text
    assert "结论 PASS" in report_text


def test_publication_rejects_incomplete_approved_source_metadata(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(manifest_path, include_after=False)
    publication = _resolved_publication(tmp_path)

    with pytest.raises(PublicationConfigurationError, match="source metadata"):
        PublicationService(
            _Catalog(publication),
            manifest_path,
            ReportArtifactStore(tmp_path / "outputs"),
        ).publish(_command(), IDEMPOTENCY_KEY)
