from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from hennongxi_contracts import LogicalDatasetId
from rasterio.transform import from_origin

from scripts.data_preflight import ManifestValidationError, load_manifest, run_preflight

RASTER_IDS = ("before_red", "before_nir", "after_red", "after_nir")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_boundary(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"name": "test watershed"},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [
                                    [110.01, 31.01],
                                    [110.07, 31.01],
                                    [110.07, 31.07],
                                    [110.01, 31.07],
                                    [110.01, 31.01],
                                ]
                            ],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _write_raster(path: Path, *, left: float = 110.0, value: int = 1000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    values = np.full((10, 10), value, dtype=np.uint16)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=10,
        height=10,
        count=1,
        dtype="uint16",
        crs="EPSG:4326",
        transform=from_origin(left, 31.1, 0.01, 0.01),
        nodata=0,
    ) as dataset:
        dataset.write(values, 1)


def _source(product_id: str) -> dict[str, object]:
    return {
        "organization": "Example authoritative producer",
        "product_id": product_id,
        "url": f"https://example.test/public/{product_id}.tif",
        "license": "Public domain",
        "license_url": "https://example.test/license",
        "byte_size": 123,
        "etag": '"immutable-etag"',
    }


def _manifest(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, object]]:
    data_root = tmp_path / "data"
    cache_dir = tmp_path / "cache"
    boundary = data_root / "boundaries" / "watershed.geojson"
    _write_boundary(boundary)

    for dataset_id in RASTER_IDS:
        _write_raster(cache_dir / f"{dataset_id}.tif")

    assets: list[dict[str, object]] = [
        {
            "logical_id": "watershed",
            "storage": "bundle",
            "path": "boundaries/watershed.geojson",
            "media_type": "application/geo+json",
            "byte_size": boundary.stat().st_size,
            "sha256": _sha256(boundary),
            "crs": "EPSG:4326",
            "bounds": [110.01, 31.01, 110.07, 31.07],
            "source_assets": [_source("watershed-source")],
            "derivation": "One complete upstream-connected watershed polygon.",
        }
    ]

    for dataset_id in RASTER_IDS:
        raster = cache_dir / f"{dataset_id}.tif"
        is_before = dataset_id.startswith("before")
        is_red = dataset_id.endswith("red")
        assets.append(
            {
                "logical_id": dataset_id,
                "storage": "cache",
                "path": f"{dataset_id}.tif",
                "media_type": "image/tiff; application=geotiff",
                "byte_size": raster.stat().st_size,
                "sha256": _sha256(raster),
                "crs": "EPSG:4326",
                "bounds": [110.0, 31.0, 110.1, 31.1],
                "resolution": [0.01, 0.01],
                "resolution_unit": "degree",
                "nodata": 0,
                "data_type": "uint16",
                "acquired_on": "2019-08-19" if is_before else "2024-08-22",
                "band": "red" if is_red else "nir",
                "band_number": "B04" if is_red else "B08",
                "source_assets": [_source(dataset_id)],
                "derivation": "Deterministic test crop.",
            }
        )

    manifest: dict[str, object] = {
        "schema_version": "1.0",
        "dataset_name": "Shennongxi dual-date NDVI demonstration",
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
    }
    manifest_path = data_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path, data_root, cache_dir, manifest


def test_manifest_requires_exact_allowlist_and_safe_local_paths(tmp_path: Path) -> None:
    manifest_path, _, _, manifest = _manifest(tmp_path)
    manifest["assets"] = [
        asset
        for asset in manifest["assets"]
        if asset["logical_id"] != "after_nir"  # type: ignore[index]
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ManifestValidationError, match="exactly the required logical IDs"):
        load_manifest(manifest_path)

    _, _, _, manifest = _manifest(tmp_path)
    manifest["assets"][1]["path"] = "../outside.tif"  # type: ignore[index]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ManifestValidationError, match="safe relative path"):
        load_manifest(manifest_path)


def test_preflight_verifies_checksums_metadata_grids_and_coverage(tmp_path: Path) -> None:
    manifest_path, data_root, cache_dir, _ = _manifest(tmp_path)

    report = run_preflight(manifest_path, data_root=data_root, cache_dir=cache_dir)

    assert report.ok, report.format()
    assert report.valid_pixel_ratios.keys() == set(RASTER_IDS)
    assert all(value == pytest.approx(1.0) for value in report.valid_pixel_ratios.values())
    assert tuple(asset.dataset_id for asset in report.assets) == tuple(LogicalDatasetId)
    assert report.assets[0].grid is None
    for asset in report.assets[1:]:
        assert asset.grid is not None
        assert asset.grid.crs == "EPSG:4326"
        assert asset.grid.width == 10
        assert asset.grid.height == 10
        assert asset.grid.bounds == pytest.approx((110.0, 31.0, 110.1, 31.1))
        assert asset.grid.nodata == 0
        assert asset.acquired_on is not None
        assert "path" not in asset.model_dump()


def test_preflight_reports_generic_remediation_without_source_url(tmp_path: Path) -> None:
    manifest_path, data_root, cache_dir, _ = _manifest(tmp_path)
    (cache_dir / "before_red.tif").write_bytes(b"corrupt")

    report = run_preflight(manifest_path, data_root=data_root, cache_dir=cache_dir)
    rendered = report.format()

    assert not report.ok
    assert "before_red" in rendered
    assert "python scripts/cache_demo_data.py" in rendered
    assert "https://example.test" not in rendered
    assert report.assets == ()


def test_preflight_rejects_under_covering_raster_even_with_valid_checksum(
    tmp_path: Path,
) -> None:
    manifest_path, data_root, cache_dir, manifest = _manifest(tmp_path)
    displaced = cache_dir / "after_nir.tif"
    _write_raster(displaced, left=111.0)
    target = next(
        asset
        for asset in manifest["assets"]
        if asset["logical_id"] == "after_nir"  # type: ignore[index]
    )
    target["byte_size"] = displaced.stat().st_size
    target["sha256"] = _sha256(displaced)
    target["bounds"] = [111.0, 31.0, 111.1, 31.1]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    report = run_preflight(manifest_path, data_root=data_root, cache_dir=cache_dir)

    assert not report.ok
    assert "after_nir" in report.format()
    assert "coverage" in report.format().lower()
