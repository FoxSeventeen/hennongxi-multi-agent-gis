from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from uuid import UUID

import numpy as np
import pytest
import rasterio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hennongxi_contracts import (
    DataPrepareResult,
    ErrorCode,
    ErrorResponse,
    LogicalDatasetId,
)
from hennongxi_data_agent.main import app
from hennongxi_data_agent.preparation import DataPreparer
from hennongxi_observability import CORRELATION_ID_HEADER
from rasterio.transform import from_origin

TASK_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CORRELATION_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
RASTER_IDS = tuple(dataset_id for dataset_id in LogicalDatasetId if dataset_id != "watershed")


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


def _write_raster(
    path: Path,
    *,
    left: float = 110.0,
    width: int = 10,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    values = np.full((10, width), 1000, dtype=np.uint16)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=width,
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


def _write_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    data_root = tmp_path / "data"
    cache_dir = tmp_path / "cache"
    boundary = data_root / "boundaries" / "watershed.geojson"
    _write_boundary(boundary)
    for dataset_id in RASTER_IDS:
        _write_raster(cache_dir / f"{dataset_id.value}.tif")

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
        raster = cache_dir / f"{dataset_id.value}.tif"
        is_before = dataset_id.value.startswith("before")
        is_red = dataset_id.value.endswith("red")
        assets.append(
            {
                "logical_id": dataset_id.value,
                "storage": "cache",
                "path": f"{dataset_id.value}.tif",
                "media_type": "image/tiff; application=geotiff",
                "byte_size": raster.stat().st_size,
                "sha256": _sha256(raster),
                "crs": "EPSG:4326",
                "bounds": [110.0, 31.0, 110.1, 31.1],
                "resolution": [0.01, 0.01],
                "resolution_unit": "degree",
                "nodata": 0,
                "data_type": "uint16",
                "acquired_on": "2019-08-19" if is_before else "2024-08-12",
                "band": "red" if is_red else "nir",
                "band_number": "B04" if is_red else "B08",
                "source_assets": [_source(dataset_id.value)],
                "derivation": "Deterministic test crop.",
            }
        )
    manifest = {
        "schema_version": "1.0",
        "dataset_name": "Shennongxi dual-date NDVI demonstration",
        "approval": {"gate": "G2", "status": "approved", "approved_on": "2026-07-19"},
        "quality": {
            "minimum_watershed_coverage_ratio": 0.95,
            "minimum_valid_pixel_ratio": 0.9,
        },
        "assets": assets,
    }
    manifest_path = data_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path, data_root, cache_dir


@pytest.fixture
def configured_app(tmp_path: Path) -> Iterator[tuple[FastAPI, Path, Path]]:
    manifest_path, data_root, cache_dir = _write_fixture(tmp_path)
    original = app.state.data_preparer
    app.state.data_preparer = DataPreparer(manifest_path, data_root=data_root, cache_dir=cache_dir)
    try:
        yield app, manifest_path, cache_dir
    finally:
        app.state.data_preparer = original


def _command() -> dict[str, object]:
    return {
        "task_id": str(TASK_ID),
        "step_id": "prepare_data",
        "attempt": 1,
        "correlation_id": str(CORRELATION_ID),
        "dataset_ids": [dataset_id.value for dataset_id in LogicalDatasetId],
    }


def _replace_raster_and_manifest(
    manifest_path: Path,
    cache_dir: Path,
    *,
    left: float = 110.0,
    width: int = 10,
) -> None:
    raster = cache_dir / "after_nir.tif"
    _write_raster(raster, left=left, width=width)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    asset = next(item for item in manifest["assets"] if item["logical_id"] == "after_nir")
    asset["byte_size"] = raster.stat().st_size
    asset["sha256"] = _sha256(raster)
    asset["bounds"] = [left, 31.0, left + width * 0.01, 31.1]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def _missing(_: Path, cache_dir: Path) -> None:
    (cache_dir / "before_red.tif").unlink()


def _corrupt(_: Path, cache_dir: Path) -> None:
    (cache_dir / "before_red.tif").write_bytes(b"corrupt")


def _misaligned(manifest_path: Path, cache_dir: Path) -> None:
    _replace_raster_and_manifest(manifest_path, cache_dir, width=8)


def _under_covering(manifest_path: Path, cache_dir: Path) -> None:
    _replace_raster_and_manifest(manifest_path, cache_dir, left=111.0)


def test_prepare_returns_contract_metadata_without_any_local_path(
    configured_app: tuple[FastAPI, Path, Path],
) -> None:
    configured, _, _ = configured_app
    with TestClient(configured) as client:
        response = client.post(
            "/internal/v1/data/prepare",
            json=_command(),
            headers={CORRELATION_ID_HEADER: str(CORRELATION_ID)},
        )

    result = DataPrepareResult.model_validate(response.json())
    assert response.status_code == 200
    assert response.headers[CORRELATION_ID_HEADER] == str(CORRELATION_ID)
    assert result.task_id == TASK_ID
    assert result.step_id == "prepare_data"
    assert result.attempt == 1
    assert result.correlation_id == CORRELATION_ID
    assert tuple(asset.dataset_id for asset in result.assets) == tuple(LogicalDatasetId)
    assert all(asset.grid is not None for asset in result.assets[1:])
    assert "path" not in response.text
    assert "example.test" not in response.text


@pytest.mark.parametrize(
    "invalidate",
    [_missing, _corrupt, _misaligned, _under_covering],
    ids=["missing", "corrupt", "misaligned", "under-covering"],
)
def test_invalid_cached_data_returns_sanitized_structured_failure(
    configured_app: tuple[FastAPI, Path, Path],
    invalidate: Callable[[Path, Path], None],
) -> None:
    configured, manifest_path, cache_dir = configured_app
    invalidate(manifest_path, cache_dir)

    with TestClient(configured) as client:
        response = client.post("/internal/v1/data/prepare", json=_command())

    error = ErrorResponse.model_validate(response.json())
    assert response.status_code == 409
    assert error.error.code is ErrorCode.DATA_INVALID
    assert error.error.retryable
    assert "path" not in response.text
    assert str(cache_dir) not in response.text
    assert "example.test" not in response.text


def test_prepare_rejects_path_injection_with_the_structured_error_contract(
    configured_app: tuple[FastAPI, Path, Path],
) -> None:
    configured, _, _ = configured_app
    payload = _command()
    payload["input_path"] = "/etc/passwd"

    with TestClient(configured) as client:
        response = client.post("/internal/v1/data/prepare", json=payload)

    error = ErrorResponse.model_validate(response.json())
    assert response.status_code == 422
    assert error.error.code is ErrorCode.VALIDATION_ERROR
    assert not error.error.retryable
    assert "/etc/passwd" not in response.text


def test_missing_manifest_returns_sanitized_dependency_failure(
    configured_app: tuple[FastAPI, Path, Path],
    tmp_path: Path,
) -> None:
    configured, _, cache_dir = configured_app
    configured.state.data_preparer = DataPreparer(
        tmp_path / "missing.json",
        data_root=tmp_path,
        cache_dir=cache_dir,
    )

    with TestClient(configured) as client:
        response = client.post("/internal/v1/data/prepare", json=_command())

    error = ErrorResponse.model_validate(response.json())
    assert response.status_code == 503
    assert error.error.code is ErrorCode.DEPENDENCY_UNAVAILABLE
    assert error.error.retryable
    assert str(tmp_path) not in response.text
