from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from datetime import date
from io import BytesIO
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from scripts.cache_demo_data import (
    DEFAULT_ACQUISITIONS,
    Acquisition,
    SourceCog,
    build_cache,
    materialize_source,
    normalize_reflectance,
)
from scripts.data_preflight import load_manifest, run_preflight

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _Response(BytesIO):
    def __init__(self, payload: bytes, *, etag: str) -> None:
        super().__init__(payload)
        self.headers = {
            "Content-Length": str(len(payload)),
            "ETag": etag,
        }


def test_cache_cli_can_be_invoked_by_its_documented_script_path() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/cache_demo_data.py", "--help"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "approved offline Sentinel-2" in completed.stdout


def test_default_acquisitions_are_the_exact_g2_approved_sources() -> None:
    before, after = DEFAULT_ACQUISITIONS

    assert before.item_id == "S2A_49RDQ_20190819_0_L2A"
    assert before.acquired_on == date(2019, 8, 19)
    assert before.cloud_cover == pytest.approx(7.617735)
    assert before.scale == pytest.approx(0.0001)
    assert before.offset == pytest.approx(0.0)
    assert after.item_id == "S2A_49RDQ_20240812_0_L2A"
    assert after.product_id == ("S2A_MSIL2A_20240812T031521_N0511_R118_T49RDQ_20240812T084251.SAFE")
    assert after.acquired_on == date(2024, 8, 12)
    assert after.cloud_cover == pytest.approx(6.090892)
    assert after.scale == pytest.approx(0.0001)
    assert after.offset == pytest.approx(-0.1)
    assert (after.red.byte_size, after.red.etag) == (
        209_096_458,
        '"a55dd5415755e8cbe5d6ae47a9b52a93-25"',
    )
    assert (after.nir.byte_size, after.nir.etag) == (
        253_879_149,
        '"1cca7fe777e92138567e5d874719d29c-31"',
    )
    assert (after.scl.byte_size, after.scl.etag) == (
        1_438_732,
        '"85926f6837db5c1a67359c4379f4794c"',
    )
    assert all(
        source.href.startswith("https://sentinel-cogs.s3.us-west-2.amazonaws.com/")
        for acquisition in DEFAULT_ACQUISITIONS
        for source in (acquisition.red, acquisition.nir, acquisition.scl)
    )


def test_source_materialization_is_atomic_and_detects_same_size_corruption(
    tmp_path: Path,
) -> None:
    payload = b"authoritative-cog-bytes"
    etag = '"source-etag"'
    source = SourceCog(
        href="https://example.test/public/source.tif",
        byte_size=len(payload),
        etag=etag,
    )
    calls = 0

    def opener(_: str) -> _Response:
        nonlocal calls
        calls += 1
        return _Response(payload, etag=etag)

    target = tmp_path / "sources" / "source.tif"
    first = materialize_source(source, target, opener=opener)

    assert target.read_bytes() == payload
    assert first.read_href == str(target)
    assert first.sha256 is not None
    assert calls == 1
    assert not target.with_suffix(".part").exists()

    target.write_bytes(b"x" * len(payload))
    second = materialize_source(source, target, opener=opener)

    assert target.read_bytes() == payload
    assert second.sha256 == first.sha256
    assert calls == 2


def _write_boundary(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"approval_status": "approved"},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [
                                    [110.0, 31.0],
                                    [110.04, 31.0],
                                    [110.04, 31.04],
                                    [110.0, 31.04],
                                    [110.0, 31.0],
                                ]
                            ],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _write_source(path: Path, values: np.ndarray, *, pixel_size: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=values.shape[1],
        height=values.shape[0],
        count=1,
        dtype=values.dtype,
        crs="EPSG:4326",
        transform=from_origin(110.0, 31.04, pixel_size, pixel_size),
        nodata=0,
    ) as dataset:
        dataset.write(values, 1)


def _cog(path: Path, name: str) -> SourceCog:
    return SourceCog(
        href=f"https://example.test/public/{name}.tif",
        read_href=str(path),
        byte_size=path.stat().st_size,
        etag=f'"{name}-immutable"',
    )


def _acquisitions(tmp_path: Path) -> tuple[Acquisition, Acquisition]:
    raw_red = np.full((4, 4), 1000, dtype=np.uint16)
    raw_nir = np.full((4, 4), 2000, dtype=np.uint16)
    scl = np.full((4, 4), 4, dtype=np.uint8)
    scl[0, -1] = 9
    acquisitions: list[Acquisition] = []
    for role, acquired_on, scale, offset in (
        ("before", date(2019, 8, 19), 0.0001, 0.0),
        ("after", date(2024, 8, 22), 0.0001, -0.1),
    ):
        red_path = tmp_path / "sources" / f"{role}_red.tif"
        nir_path = tmp_path / "sources" / f"{role}_nir.tif"
        scl_path = tmp_path / "sources" / f"{role}_scl.tif"
        _write_source(red_path, raw_red, pixel_size=0.01)
        _write_source(nir_path, raw_nir, pixel_size=0.01)
        _write_source(scl_path, scl, pixel_size=0.01)
        acquisitions.append(
            Acquisition(
                role=role,
                item_id=f"test-{role}",
                product_id=f"TEST_{role.upper()}_L2A",
                acquired_on=acquired_on,
                platform="sentinel-2a",
                tile="MGRS-49RDQ",
                cloud_cover=5.0,
                processing_baseline="test",
                scale=scale,
                offset=offset,
                red=_cog(red_path, f"{role}-red"),
                nir=_cog(nir_path, f"{role}-nir"),
                scl=_cog(scl_path, f"{role}-scl"),
            )
        )
    return acquisitions[0], acquisitions[1]


def test_normalize_reflectance_applies_scale_offset_and_all_masks() -> None:
    raw = np.array([[0, 1000], [2000, 3000]], dtype=np.uint16)
    scl = np.array([[4, 9], [4, 4]], dtype=np.uint8)
    inside = np.array([[True, True], [False, True]])

    result = normalize_reflectance(
        raw,
        scl,
        inside,
        scale=0.0001,
        offset=-0.1,
        source_nodata=0,
        output_nodata=-9999.0,
    )

    assert result.dtype == np.float32
    assert result[0, 0] == -9999.0  # source nodata
    assert result[0, 1] == -9999.0  # cloud in SCL
    assert result[1, 0] == -9999.0  # outside watershed
    assert result[1, 1] == pytest.approx(0.2)


def test_build_cache_writes_preflight_clean_manifest_and_reuses_it_offline(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    cache_dir = data_root / "cache" / "demo"
    boundary_path = data_root / "boundaries" / "watershed.geojson"
    manifest_path = data_root / "manifest.json"
    _write_boundary(boundary_path)
    acquisitions = _acquisitions(tmp_path)

    first = build_cache(
        boundary_path=boundary_path,
        data_root=data_root,
        cache_dir=cache_dir,
        manifest_path=manifest_path,
        acquisitions=acquisitions,
        approval_date=date(2026, 7, 19),
    )

    assert not first.reused
    manifest = load_manifest(manifest_path)
    assert manifest.approval.status == "approved"
    assert {asset.logical_id.value for asset in manifest.assets} == {
        "watershed",
        "before_red",
        "before_nir",
        "after_red",
        "after_nir",
    }
    report = run_preflight(manifest_path, data_root=data_root, cache_dir=cache_dir)
    assert report.ok, report.format()

    with rasterio.open(cache_dir / "before_red.tif") as before_red:
        values = before_red.read([1])[0]
        assert before_red.dtypes == ("float32",)
        assert before_red.nodata == -9999.0
        assert values[0, 0] == pytest.approx(0.1)
        assert values[0, -1] == -9999.0

    for source in tmp_path.joinpath("sources").glob("*.tif"):
        source.unlink()

    second = build_cache(
        boundary_path=boundary_path,
        data_root=data_root,
        cache_dir=cache_dir,
        manifest_path=manifest_path,
        acquisitions=acquisitions,
        approval_date=date(2026, 7, 19),
    )

    assert second.reused


def test_build_cache_invalidates_clean_manifest_when_approved_sources_change(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    cache_dir = data_root / "cache" / "demo"
    boundary_path = data_root / "boundaries" / "watershed.geojson"
    manifest_path = data_root / "manifest.json"
    _write_boundary(boundary_path)
    before, after = _acquisitions(tmp_path)

    first = build_cache(
        boundary_path=boundary_path,
        data_root=data_root,
        cache_dir=cache_dir,
        manifest_path=manifest_path,
        acquisitions=(before, after),
        approval_date=date(2026, 7, 19),
    )
    assert not first.reused

    replacement_after = replace(
        after,
        item_id="replacement-after",
        product_id="REPLACEMENT_AFTER_L2A",
        acquired_on=date(2024, 8, 12),
        red=replace(after.red, href="https://example.test/replacement/after-red.tif"),
        nir=replace(after.nir, href="https://example.test/replacement/after-nir.tif"),
        scl=replace(after.scl, href="https://example.test/replacement/after-scl.tif"),
    )
    second = build_cache(
        boundary_path=boundary_path,
        data_root=data_root,
        cache_dir=cache_dir,
        manifest_path=manifest_path,
        acquisitions=(before, replacement_after),
        approval_date=date(2026, 7, 19),
    )

    assert not second.reused
    manifest = load_manifest(manifest_path)
    after_assets = [
        asset for asset in manifest.assets if asset.logical_id.value.startswith("after_")
    ]
    assert {asset.acquired_on for asset in after_assets} == {date(2024, 8, 12)}
    assert all(
        str(asset.source_assets[0].url).startswith("https://example.test/replacement/")
        for asset in after_assets
    )
