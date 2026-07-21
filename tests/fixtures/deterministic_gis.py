"""Small, approved GIS inputs shared by deterministic integration tests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from hennongxi_contracts import LogicalDatasetId
from rasterio.transform import from_origin

_CRS = "EPSG:32649"
_LEFT = 400_000.0
_BOTTOM = 3_430_000.0
_PIXEL_SIZE = 10.0
_WIDTH = 4
_HEIGHT = 4
_RIGHT = _LEFT + _WIDTH * _PIXEL_SIZE
_TOP = _BOTTOM + _HEIGHT * _PIXEL_SIZE
_BOUNDS = (_LEFT, _BOTTOM, _RIGHT, _TOP)


@dataclass(frozen=True, slots=True)
class DeterministicGisFixture:
    """Paths and exact mathematical expectations for one isolated test run."""

    manifest_path: Path
    data_root: Path
    cache_dir: Path
    artifact_root: Path
    quality_report_root: Path

    @property
    def expected_change_pixel_counts(self) -> dict[str, int]:
        return {"increase": 4, "stable": 8, "decrease": 4}


def write_deterministic_gis_fixture(root: Path) -> DeterministicGisFixture:
    """Write four real aligned GeoTIFFs and one complete approved manifest."""

    data_root = root / "data"
    cache_dir = root / "cache"
    artifact_root = root / "outputs"
    quality_report_root = root / "quality-reports"
    data_root.mkdir(parents=True)
    cache_dir.mkdir(parents=True)

    boundary_path = data_root / "boundaries" / "watershed.geojson"
    boundary_path.parent.mkdir()
    boundary_path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "crs": {"type": "name", "properties": {"name": _CRS}},
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"name": "deterministic watershed"},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [
                                    [_LEFT, _BOTTOM],
                                    [_RIGHT, _BOTTOM],
                                    [_RIGHT, _TOP],
                                    [_LEFT, _TOP],
                                    [_LEFT, _BOTTOM],
                                ]
                            ],
                        },
                    }
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    before_red = np.ones((_HEIGHT, _WIDTH), dtype=np.float32)
    before_nir = np.full((_HEIGHT, _WIDTH), 3.0, dtype=np.float32)
    after_red = np.ones((_HEIGHT, _WIDTH), dtype=np.float32)
    after_nir = np.full((_HEIGHT, _WIDTH), 3.0, dtype=np.float32)
    after_nir[0, :] = 5.0
    after_red[3, :] = 3.0
    after_nir[3, :] = 1.0
    raster_values = {
        LogicalDatasetId.BEFORE_RED: before_red,
        LogicalDatasetId.BEFORE_NIR: before_nir,
        LogicalDatasetId.AFTER_RED: after_red,
        LogicalDatasetId.AFTER_NIR: after_nir,
    }
    for dataset_id, values in raster_values.items():
        _write_raster(cache_dir / f"{dataset_id.value}.tif", values)

    assets: list[dict[str, object]] = [
        {
            "logical_id": LogicalDatasetId.WATERSHED.value,
            "storage": "bundle",
            "path": "boundaries/watershed.geojson",
            "media_type": "application/geo+json",
            "byte_size": boundary_path.stat().st_size,
            "sha256": _sha256(boundary_path),
            "crs": _CRS,
            "bounds": list(_BOUNDS),
            "source_assets": [_source("hybas-as-test")],
            "derivation": "完整上游连通流域的确定性测试多边形。",
        }
    ]
    for dataset_id in raster_values:
        raster_path = cache_dir / f"{dataset_id.value}.tif"
        is_before = dataset_id.value.startswith("before")
        is_red = dataset_id.value.endswith("red")
        assets.append(
            {
                "logical_id": dataset_id.value,
                "storage": "cache",
                "path": raster_path.name,
                "media_type": "image/tiff; application=geotiff",
                "byte_size": raster_path.stat().st_size,
                "sha256": _sha256(raster_path),
                "crs": _CRS,
                "bounds": list(_BOUNDS),
                "resolution": [_PIXEL_SIZE, _PIXEL_SIZE],
                "resolution_unit": "metre",
                "nodata": -9999.0,
                "data_type": "float32",
                "acquired_on": "2019-08-19" if is_before else "2024-08-12",
                "band": "red" if is_red else "nir",
                "band_number": "B04" if is_red else "B08",
                "source_assets": [_source(dataset_id.value)],
                "derivation": "从公开源数据生成的小型确定性测试裁剪。",
            }
        )

    manifest_path = data_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "dataset_name": "神农溪双时相 NDVI 确定性集成测试",
                "approval": {
                    "gate": "G2",
                    "status": "approved",
                    "approved_on": "2026-07-19",
                },
                "quality": {
                    "minimum_watershed_coverage_ratio": 0.95,
                    "minimum_valid_pixel_ratio": 0.90,
                },
                "assets": assets,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return DeterministicGisFixture(
        manifest_path=manifest_path,
        data_root=data_root,
        cache_dir=cache_dir,
        artifact_root=artifact_root,
        quality_report_root=quality_report_root,
    )


def _write_raster(path: Path, values: np.ndarray) -> None:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=_WIDTH,
        height=_HEIGHT,
        count=1,
        dtype="float32",
        crs=_CRS,
        transform=from_origin(_LEFT, _TOP, _PIXEL_SIZE, _PIXEL_SIZE),
        nodata=-9999.0,
    ) as dataset:
        dataset.write(values, 1)


def _source(product_id: str) -> dict[str, object]:
    return {
        "organization": "Copernicus Sentinel Integration Fixture",
        "product_id": product_id,
        "url": f"https://example.test/public/{product_id}.tif",
        "license": "Public test fixture",
        "license_url": "https://example.test/license",
        "byte_size": 1,
        "etag": '"deterministic-fixture"',
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
