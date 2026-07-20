from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid5

import numpy as np
import pytest
import rasterio
from hennongxi_contracts import (
    ArtifactRef,
    ArtifactStatus,
    ArtifactType,
    QualityConclusion,
    QualityEvaluateCommand,
)
from hennongxi_quality_agent.configuration import QualityConfigurationError
from hennongxi_quality_agent.evaluation import QualityEvaluator
from rasterio.transform import from_origin

TASK_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CORRELATION_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
NOW = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)

RASTER_TYPES = (
    ArtifactType.NDVI_BEFORE,
    ArtifactType.NDVI_AFTER,
    ArtifactType.NDVI_DIFFERENCE,
    ArtifactType.CHANGE_CLASSIFICATION,
)
FILENAMES = {
    **{artifact_type: f"{artifact_type.value.lower()}.tif" for artifact_type in RASTER_TYPES},
    ArtifactType.AREA_STATISTICS: "area_statistics.json",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_manifest(tmp_path: Path) -> Path:
    boundary = tmp_path / "watershed.geojson"
    boundary.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[0, 0], [200, 0], [200, 200], [0, 200], [0, 0]]],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "1.0",
        "quality": {
            "minimum_watershed_coverage_ratio": 0.95,
            "minimum_valid_pixel_ratio": 0.90,
        },
        "assets": [
            {
                "logical_id": "watershed",
                "storage": "bundle",
                "path": boundary.name,
                "byte_size": boundary.stat().st_size,
                "sha256": _sha256(boundary),
                "crs": "EPSG:32649",
                "bounds": [0, 0, 200, 200],
                "resolution": None,
            },
            *[
                {
                    "logical_id": logical_id,
                    "storage": "cache",
                    "path": f"{logical_id}.tif",
                    "crs": "EPSG:32649",
                    "bounds": [0, 0, 200, 200],
                    "resolution": [10, 10],
                }
                for logical_id in ("before_red", "before_nir", "after_red", "after_nir")
            ],
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def _write_analysis_outputs(root: Path, *, width: int, invalid_pixels: int) -> Path:
    directory = root / str(TASK_ID) / "attempt-1" / "analysis"
    directory.mkdir(parents=True)
    height = 20
    valid_count = width * height - invalid_pixels
    for artifact_type in RASTER_TYPES:
        classification = artifact_type is ArtifactType.CHANGE_CLASSIFICATION
        nodata = -128 if classification else -9999.0
        dtype = "int8" if classification else "float32"
        values = np.zeros((height, width), dtype=dtype)
        values.flat[:invalid_pixels] = nodata
        path = directory / FILENAMES[artifact_type]
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            width=width,
            height=height,
            count=1,
            dtype=dtype,
            crs="EPSG:32649",
            transform=from_origin(0, 200, 10, 10),
            nodata=nodata,
        ) as dataset:
            dataset.write(values, 1)
            if classification:
                dataset.update_tags(change_threshold="0.1")

    statistics = {
        "threshold": 0.1,
        "pixel_area_square_metres": 100.0,
        "valid_pixel_count": valid_count,
        "decrease_pixel_count": 0,
        "stable_pixel_count": valid_count,
        "increase_pixel_count": 0,
        "valid_area_square_metres": valid_count * 100.0,
        "decrease_area_square_metres": 0.0,
        "stable_area_square_metres": valid_count * 100.0,
        "increase_area_square_metres": 0.0,
    }
    (directory / FILENAMES[ArtifactType.AREA_STATISTICS]).write_text(
        json.dumps(statistics), encoding="utf-8"
    )
    return directory


def _command(directory: Path, *, omit: ArtifactType | None = None) -> QualityEvaluateCommand:
    artifacts = []
    for artifact_type, filename in FILENAMES.items():
        if artifact_type is omit:
            continue
        path = directory / filename
        artifacts.append(
            ArtifactRef(
                artifact_id=uuid5(TASK_ID, f"analysis:1:{artifact_type.value}"),
                task_id=TASK_ID,
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
    return QualityEvaluateCommand(
        task_id=TASK_ID,
        step_id="evaluate_quality",
        attempt=1,
        correlation_id=CORRELATION_ID,
        artifacts=tuple(artifacts),
        analysis_elapsed_ms=1250,
    )


def test_threshold_edges_pass_with_explicit_metric_evidence(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path)
    artifact_root = tmp_path / "outputs"
    directory = _write_analysis_outputs(artifact_root, width=19, invalid_pixels=38)

    metrics = QualityEvaluator(manifest_path, artifact_root).evaluate(_command(directory))

    assert metrics.coverage_ratio == pytest.approx(0.95)
    assert metrics.valid_pixel_ratio == pytest.approx(0.90)
    assert metrics.output_complete
    assert metrics.elapsed_ms == 1250
    assert metrics.conclusion is QualityConclusion.PASS
    assert metrics.passed
    assert len(metrics.evidence) == 4


def test_coverage_below_threshold_fails_even_when_pixels_are_valid(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path)
    artifact_root = tmp_path / "outputs"
    directory = _write_analysis_outputs(artifact_root, width=18, invalid_pixels=0)

    metrics = QualityEvaluator(manifest_path, artifact_root).evaluate(_command(directory))

    assert metrics.coverage_ratio == pytest.approx(0.90)
    assert metrics.valid_pixel_ratio == pytest.approx(1.0)
    assert metrics.conclusion is QualityConclusion.FAIL
    assert not metrics.passed


def test_valid_pixels_below_threshold_fail_at_the_first_pixel_below_boundary(
    tmp_path: Path,
) -> None:
    manifest_path = _write_manifest(tmp_path)
    artifact_root = tmp_path / "outputs"
    directory = _write_analysis_outputs(artifact_root, width=20, invalid_pixels=41)

    metrics = QualityEvaluator(manifest_path, artifact_root).evaluate(_command(directory))

    assert metrics.coverage_ratio == pytest.approx(1.0)
    assert metrics.valid_pixel_ratio == pytest.approx(359 / 400)
    assert metrics.conclusion is QualityConclusion.FAIL
    assert not metrics.passed


def test_missing_or_tampered_outputs_cannot_pass(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path)
    artifact_root = tmp_path / "outputs"
    directory = _write_analysis_outputs(artifact_root, width=20, invalid_pixels=0)
    evaluator = QualityEvaluator(manifest_path, artifact_root)

    missing = evaluator.evaluate(_command(directory, omit=ArtifactType.AREA_STATISTICS))
    assert not missing.output_complete
    assert missing.conclusion is QualityConclusion.FAIL

    command = _command(directory)
    with (directory / FILENAMES[ArtifactType.NDVI_DIFFERENCE]).open("ab") as stream:
        stream.write(b"tampered")
    tampered = evaluator.evaluate(command)
    assert not tampered.output_complete
    assert tampered.conclusion is QualityConclusion.FAIL


def test_structurally_invalid_raster_fails_even_with_matching_metadata(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path)
    artifact_root = tmp_path / "outputs"
    directory = _write_analysis_outputs(artifact_root, width=20, invalid_pixels=0)
    classification_path = directory / FILENAMES[ArtifactType.CHANGE_CLASSIFICATION]
    with rasterio.open(classification_path, "r+") as dataset:
        values = dataset.read([1])[0]
        values[0, 0] = 7
        dataset.write(values, 1)

    metrics = QualityEvaluator(manifest_path, artifact_root).evaluate(_command(directory))

    assert not metrics.output_complete
    assert metrics.conclusion is QualityConclusion.FAIL


def test_manifest_boundary_path_cannot_escape_the_approved_bundle(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["assets"][0]["path"] = "../outside.geojson"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(QualityConfigurationError, match="reference is invalid"):
        QualityEvaluator(manifest_path, tmp_path / "outputs")


def test_manifest_cannot_weaken_the_approved_quality_floor(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["quality"]["minimum_watershed_coverage_ratio"] = 0.1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(QualityConfigurationError, match="reference is invalid"):
        QualityEvaluator(manifest_path, tmp_path / "outputs")
