from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID

import numpy as np
import pytest
import rasterio
from hennongxi_analysis_agent.artifacts import AnalysisArtifactStore
from hennongxi_analysis_agent.execution import AnalysisExecutor, AnalysisInputError
from hennongxi_contracts import (
    AnalysisRunCommand,
    ArtifactType,
    DataAssetRef,
    LogicalDatasetId,
    RasterGrid,
)
from rasterio.transform import from_origin

TASK_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CORRELATION_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
IDEMPOTENCY_KEY = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_raster(path: Path, values: np.ndarray) -> None:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=4,
        height=4,
        count=1,
        dtype="float32",
        crs="EPSG:32649",
        transform=from_origin(0, 40, 10, 10),
        nodata=-9999.0,
    ) as dataset:
        dataset.write(values.astype(np.float32), 1)


def _write_fixture(tmp_path: Path) -> tuple[Path, Path, Path, tuple[DataAssetRef, ...]]:
    data_root = tmp_path / "data"
    cache_dir = tmp_path / "cache"
    data_root.mkdir()
    cache_dir.mkdir()

    boundary = data_root / "watershed.geojson"
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
                            "coordinates": [
                                [[0, 0], [40, 0], [40, 40], [0, 40], [0, 0]]
                            ],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    before_red = np.ones((4, 4), dtype=np.float32)
    before_nir = np.full((4, 4), 3, dtype=np.float32)
    after_red = np.ones((4, 4), dtype=np.float32)
    after_nir = np.full((4, 4), 3, dtype=np.float32)
    after_nir[0, :] = 5
    after_red[3, :] = 3
    after_nir[3, :] = 1
    values = {
        LogicalDatasetId.BEFORE_RED: before_red,
        LogicalDatasetId.BEFORE_NIR: before_nir,
        LogicalDatasetId.AFTER_RED: after_red,
        LogicalDatasetId.AFTER_NIR: after_nir,
    }
    for dataset_id, raster_values in values.items():
        _write_raster(cache_dir / f"{dataset_id.value}.tif", raster_values)

    manifest_assets: list[dict[str, object]] = [
        {
            "logical_id": "watershed",
            "storage": "bundle",
            "path": "watershed.geojson",
            "byte_size": boundary.stat().st_size,
            "sha256": _sha256(boundary),
            "crs": "EPSG:32649",
        }
    ]
    inputs: list[DataAssetRef] = [
        DataAssetRef(
            dataset_id=LogicalDatasetId.WATERSHED,
            checksum_sha256=_sha256(boundary),
            byte_size=boundary.stat().st_size,
        )
    ]
    grid = RasterGrid(
        crs="EPSG:32649",
        width=4,
        height=4,
        transform=(10.0, 0.0, 0.0, 0.0, -10.0, 40.0),
        bounds=(0.0, 0.0, 40.0, 40.0),
        nodata=-9999.0,
    )
    for dataset_id in values:
        raster_path = cache_dir / f"{dataset_id.value}.tif"
        manifest_assets.append(
            {
                "logical_id": dataset_id.value,
                "storage": "cache",
                "path": raster_path.name,
                "byte_size": raster_path.stat().st_size,
                "sha256": _sha256(raster_path),
                "crs": "EPSG:32649",
            }
        )
        inputs.append(
            DataAssetRef(
                dataset_id=dataset_id,
                checksum_sha256=_sha256(raster_path),
                byte_size=raster_path.stat().st_size,
                grid=grid,
                acquired_on="2019-08-19"
                if dataset_id.value.startswith("before")
                else "2024-08-12",
            )
        )

    manifest_path = data_root / "manifest.json"
    manifest_path.write_text(
        json.dumps({"schema_version": "1.0", "assets": manifest_assets}),
        encoding="utf-8",
    )
    return manifest_path, data_root, cache_dir, tuple(inputs)


def _command(inputs: tuple[DataAssetRef, ...]) -> AnalysisRunCommand:
    return AnalysisRunCommand(
        task_id=TASK_ID,
        step_id="analyze_ndvi_change",
        attempt=1,
        correlation_id=CORRELATION_ID,
        inputs=inputs,
    )


def test_executor_writes_georeferenced_rasters_and_detailed_area_statistics(
    tmp_path: Path,
) -> None:
    manifest_path, data_root, cache_dir, inputs = _write_fixture(tmp_path)
    store = AnalysisArtifactStore(tmp_path / "outputs")
    executor = AnalysisExecutor(
        manifest_path,
        data_root=data_root,
        cache_dir=cache_dir,
        artifact_store=store,
    )

    outcome = executor.run(_command(inputs), IDEMPOTENCY_KEY)

    assert not outcome.reused
    assert outcome.result.statistics.increase_hectares == pytest.approx(0.04)
    assert outcome.result.statistics.stable_hectares == pytest.approx(0.08)
    assert outcome.result.statistics.decrease_hectares == pytest.approx(0.04)
    assert outcome.result.statistics.valid_hectares == pytest.approx(0.16)
    assert outcome.result.elapsed_ms >= 0

    final_directory = store.final_directory(TASK_ID, 1)
    expected_rasters = {
        ArtifactType.NDVI_BEFORE,
        ArtifactType.NDVI_AFTER,
        ArtifactType.NDVI_DIFFERENCE,
        ArtifactType.CHANGE_CLASSIFICATION,
    }
    by_type = {artifact.artifact_type: artifact for artifact in outcome.result.artifacts}
    for artifact_type in expected_rasters:
        artifact_path = final_directory / f"{artifact_type.value.lower()}.tif"
        with rasterio.open(artifact_path) as dataset:
            assert dataset.crs.to_string() == "EPSG:32649"
            assert dataset.bounds == rasterio.coords.BoundingBox(0, 0, 40, 40)
            assert dataset.width == 4
            assert dataset.height == 4
            expected_nodata = (
                -128 if artifact_type is ArtifactType.CHANGE_CLASSIFICATION else -9999
            )
            assert dataset.nodata == expected_nodata
        assert _sha256(artifact_path) == by_type[artifact_type].checksum_sha256

    statistics_path = final_directory / "area_statistics.json"
    statistics = json.loads(statistics_path.read_text(encoding="utf-8"))
    assert statistics["threshold"] == 0.1
    assert statistics["increase_pixel_count"] == 4
    assert statistics["stable_pixel_count"] == 8
    assert statistics["decrease_pixel_count"] == 4
    assert _sha256(statistics_path) == by_type[ArtifactType.AREA_STATISTICS].checksum_sha256

    repeated = executor.run(_command(inputs), IDEMPOTENCY_KEY)
    assert repeated.reused
    assert repeated.result == outcome.result


def test_executor_rejects_stale_input_checksum_without_publishing(tmp_path: Path) -> None:
    manifest_path, data_root, cache_dir, inputs = _write_fixture(tmp_path)
    stale_inputs = list(inputs)
    stale_inputs[1] = stale_inputs[1].model_copy(update={"checksum_sha256": "f" * 64})
    store = AnalysisArtifactStore(tmp_path / "outputs")
    executor = AnalysisExecutor(
        manifest_path,
        data_root=data_root,
        cache_dir=cache_dir,
        artifact_store=store,
    )

    with pytest.raises(AnalysisInputError, match="metadata does not match"):
        executor.run(_command(tuple(stale_inputs)), IDEMPOTENCY_KEY)

    assert not store.final_directory(TASK_ID, 1).exists()


def test_executor_rejects_manifest_path_traversal_without_publishing(tmp_path: Path) -> None:
    manifest_path, data_root, cache_dir, inputs = _write_fixture(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["assets"][1]["path"] = "../outside.tif"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    store = AnalysisArtifactStore(tmp_path / "outputs")
    executor = AnalysisExecutor(
        manifest_path,
        data_root=data_root,
        cache_dir=cache_dir,
        artifact_store=store,
    )

    with pytest.raises(AnalysisInputError, match="approved storage root"):
        executor.run(_command(inputs), IDEMPOTENCY_KEY)

    assert not store.final_directory(TASK_ID, 1).exists()
