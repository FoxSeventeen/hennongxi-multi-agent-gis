from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid5

import hennongxi_publisher_agent.catalog as catalog_module
import pytest
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
from hennongxi_publisher_agent.catalog import (
    PublishedTileIntegrityError,
    PublishedTileNotFoundError,
    PublisherArtifactCatalog,
)

TASK_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
OTHER_TASK_ID = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
CORRELATION_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
IDEMPOTENCY_KEY = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
NOW = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)

ANALYSIS_FILENAMES = {
    ArtifactType.NDVI_BEFORE: "ndvi_before.tif",
    ArtifactType.NDVI_AFTER: "ndvi_after.tif",
    ArtifactType.NDVI_DIFFERENCE: "ndvi_difference.tif",
    ArtifactType.CHANGE_CLASSIFICATION: "change_classification.tif",
    ArtifactType.AREA_STATISTICS: "area_statistics.json",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_verified_attempt(
    analysis_root: Path,
    quality_root: Path,
    *,
    task_id: UUID = TASK_ID,
    passed: bool = True,
) -> Path:
    analysis_directory = analysis_root / str(task_id) / "attempt-1" / "analysis"
    analysis_directory.mkdir(parents=True)
    artifacts = []
    for artifact_type, filename in ANALYSIS_FILENAMES.items():
        path = analysis_directory / filename
        path.write_bytes(f"verified-{artifact_type.value}".encode())
        artifacts.append(
            ArtifactRef(
                artifact_id=uuid5(task_id, f"analysis:1:{artifact_type.value}"),
                task_id=task_id,
                attempt=1,
                artifact_type=artifact_type,
                status=ArtifactStatus.COMPLETE,
                media_type=(
                    "application/json"
                    if artifact_type is ArtifactType.AREA_STATISTICS
                    else "image/tiff; application=geotiff"
                ),
                created_at=NOW,
                checksum_sha256=_sha256(path),
                byte_size=path.stat().st_size,
            )
        )
    analysis_result = AnalysisRunResult(
        task_id=task_id,
        step_id="analyze_ndvi_change",
        attempt=1,
        correlation_id=CORRELATION_ID,
        artifacts=tuple(artifacts),
        statistics=AreaStatistics(
            increase_hectares=1,
            stable_hectares=2,
            decrease_hectares=3,
            valid_hectares=6,
        ),
        elapsed_ms=25,
    )
    (analysis_directory / "analysis_result.json").write_text(
        json.dumps(
            {
                "idempotency_key": str(IDEMPOTENCY_KEY),
                "result": analysis_result.model_dump(mode="json"),
            }
        ),
        encoding="utf-8",
    )

    metrics = QualityMetrics(
        coverage_ratio=0.95 if passed else 0.5,
        valid_pixel_ratio=0.90,
        output_complete=True,
        elapsed_ms=25,
        thresholds=QualityThresholds(
            minimum_watershed_coverage_ratio=0.95,
            minimum_valid_pixel_ratio=0.90,
        ),
        conclusion=QualityConclusion.PASS if passed else QualityConclusion.FAIL,
        passed=passed,
        evidence=("覆盖率", "有效像元率", "完整性", "耗时"),
    )
    quality_directory = quality_root / str(task_id) / "attempt-1" / "quality"
    quality_directory.mkdir(parents=True)
    report_path = quality_directory / "quality_report.json"
    report_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "task_id": str(task_id),
                "step_id": "evaluate_quality",
                "attempt": 1,
                "correlation_id": str(CORRELATION_ID),
                "metrics": metrics.model_dump(mode="json"),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    quality_result = QualityEvaluateResult(
        task_id=task_id,
        step_id="evaluate_quality",
        attempt=1,
        correlation_id=CORRELATION_ID,
        metrics=metrics,
        artifact=ArtifactRef(
            artifact_id=uuid5(task_id, "quality:1:QUALITY_REPORT"),
            task_id=task_id,
            attempt=1,
            artifact_type=ArtifactType.QUALITY_REPORT,
            status=ArtifactStatus.COMPLETE,
            media_type="application/json",
            created_at=NOW,
            checksum_sha256=_sha256(report_path),
            byte_size=report_path.stat().st_size,
        ),
    )
    (quality_directory / "quality_result.json").write_text(
        json.dumps(
            {
                "idempotency_key": str(IDEMPOTENCY_KEY),
                "result": quality_result.model_dump(mode="json"),
            }
        ),
        encoding="utf-8",
    )
    return analysis_directory


def test_catalog_resolves_only_checksum_verified_quality_passed_artifacts(
    tmp_path: Path,
) -> None:
    analysis_root = tmp_path / "outputs"
    quality_root = tmp_path / "quality-reports"
    directory = _write_verified_attempt(analysis_root, quality_root)
    catalog = PublisherArtifactCatalog(analysis_root, quality_root)

    resolved = catalog.resolve_tile(TASK_ID, TileArtifactType.NDVI_BEFORE)

    assert resolved.path == directory / "ndvi_before.tif"
    assert resolved.artifact.task_id == TASK_ID
    assert resolved.artifact.artifact_type is ArtifactType.NDVI_BEFORE
    assert resolved.attempt == 1


def test_catalog_does_not_publish_failed_quality_or_another_task(tmp_path: Path) -> None:
    analysis_root = tmp_path / "outputs"
    quality_root = tmp_path / "quality-reports"
    _write_verified_attempt(analysis_root, quality_root, passed=False)
    catalog = PublisherArtifactCatalog(analysis_root, quality_root)

    with pytest.raises(PublishedTileNotFoundError, match="published tile"):
        catalog.resolve_tile(TASK_ID, TileArtifactType.NDVI_BEFORE)
    with pytest.raises(PublishedTileNotFoundError, match="published tile"):
        catalog.resolve_tile(OTHER_TASK_ID, TileArtifactType.NDVI_BEFORE)


def test_catalog_invalidates_cached_resolution_after_raster_tampering(tmp_path: Path) -> None:
    analysis_root = tmp_path / "outputs"
    quality_root = tmp_path / "quality-reports"
    directory = _write_verified_attempt(analysis_root, quality_root)
    catalog = PublisherArtifactCatalog(analysis_root, quality_root)
    catalog.resolve_tile(TASK_ID, TileArtifactType.NDVI_BEFORE)

    with (directory / "ndvi_before.tif").open("ab") as stream:
        stream.write(b"tampered")

    with pytest.raises(PublishedTileIntegrityError, match="integrity"):
        catalog.resolve_tile(TASK_ID, TileArtifactType.NDVI_BEFORE)


def test_catalog_rejects_a_tile_when_another_required_output_is_tampered(
    tmp_path: Path,
) -> None:
    analysis_root = tmp_path / "outputs"
    quality_root = tmp_path / "quality-reports"
    directory = _write_verified_attempt(analysis_root, quality_root)
    with (directory / "ndvi_after.tif").open("ab") as stream:
        stream.write(b"tampered")

    with pytest.raises(PublishedTileIntegrityError, match="integrity"):
        PublisherArtifactCatalog(analysis_root, quality_root).resolve_tile(
            TASK_ID, TileArtifactType.NDVI_BEFORE
        )


def test_catalog_rejects_a_symlinked_raster_even_when_content_matches(tmp_path: Path) -> None:
    analysis_root = tmp_path / "outputs"
    quality_root = tmp_path / "quality-reports"
    directory = _write_verified_attempt(analysis_root, quality_root)
    path = directory / "ndvi_before.tif"
    outside = tmp_path / "outside.tif"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)

    with pytest.raises(PublishedTileIntegrityError, match="integrity"):
        PublisherArtifactCatalog(analysis_root, quality_root).resolve_tile(
            TASK_ID, TileArtifactType.NDVI_BEFORE
        )


def test_catalog_rejects_cross_task_receipts_moved_under_the_requested_task(
    tmp_path: Path,
) -> None:
    analysis_root = tmp_path / "outputs"
    quality_root = tmp_path / "quality-reports"
    _write_verified_attempt(
        analysis_root,
        quality_root,
        task_id=OTHER_TASK_ID,
    )
    (analysis_root / str(OTHER_TASK_ID)).rename(analysis_root / str(TASK_ID))
    (quality_root / str(OTHER_TASK_ID)).rename(quality_root / str(TASK_ID))

    with pytest.raises(PublishedTileIntegrityError, match="integrity"):
        PublisherArtifactCatalog(analysis_root, quality_root).resolve_tile(
            TASK_ID, TileArtifactType.NDVI_BEFORE
        )


def test_catalog_reuses_unchanged_file_fingerprints_without_rehashing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analysis_root = tmp_path / "outputs"
    quality_root = tmp_path / "quality-reports"
    _write_verified_attempt(analysis_root, quality_root)
    catalog = PublisherArtifactCatalog(analysis_root, quality_root)
    first = catalog.resolve_tile(TASK_ID, TileArtifactType.NDVI_BEFORE)

    def fail_if_rehashed(_path: Path) -> str:
        raise AssertionError("unchanged cached tile must not be rehashed")

    monkeypatch.setattr(catalog_module, "_sha256", fail_if_rehashed)

    assert catalog.resolve_tile(TASK_ID, TileArtifactType.NDVI_BEFORE) == first


def _publish_command(analysis_root: Path, quality_root: Path) -> PublisherPublishCommand:
    analysis_payload = json.loads(
        (
            analysis_root / str(TASK_ID) / "attempt-1" / "analysis" / "analysis_result.json"
        ).read_text(encoding="utf-8")
    )
    quality_payload = json.loads(
        (quality_root / str(TASK_ID) / "attempt-1" / "quality" / "quality_result.json").read_text(
            encoding="utf-8"
        )
    )
    analysis = AnalysisRunResult.model_validate(analysis_payload["result"])
    quality = QualityEvaluateResult.model_validate(quality_payload["result"])
    return PublisherPublishCommand(
        task_id=TASK_ID,
        step_id="publish_results",
        attempt=1,
        correlation_id=CORRELATION_ID,
        artifacts=(*analysis.artifacts, quality.artifact),
        quality=quality.metrics,
    )


def test_catalog_resolves_publish_command_only_when_receipts_match_exactly(tmp_path: Path) -> None:
    analysis_root = tmp_path / "outputs"
    quality_root = tmp_path / "quality-reports"
    _write_verified_attempt(analysis_root, quality_root)
    command = _publish_command(analysis_root, quality_root)
    catalog = PublisherArtifactCatalog(analysis_root, quality_root)

    publication = catalog.resolve_publication(command)

    assert publication.attempt == 1
    assert publication.correlation_id == CORRELATION_ID
    assert publication.analysis.statistics.valid_hectares == 6
    assert publication.quality.metrics == command.quality
    assert tuple(tile.artifact.artifact_type for tile in publication.tiles) == tuple(
        ArtifactType(tile_type.value) for tile_type in TileArtifactType
    )

    refs = list(command.artifacts)
    refs[0] = refs[0].model_copy(update={"checksum_sha256": "b" * 64})
    mismatched = command.model_copy(update={"artifacts": tuple(refs)})
    with pytest.raises(PublishedTileIntegrityError, match="integrity"):
        catalog.resolve_publication(mismatched)
