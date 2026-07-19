"""Build and verify the approved offline Sentinel-2 demonstration cache."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Literal, Protocol, Self, cast
from urllib.request import urlopen

import geopandas as gpd  # type: ignore[import-untyped]
import numpy as np
import rasterio  # type: ignore[import-untyped]
from rasterio.enums import Resampling  # type: ignore[import-untyped]
from rasterio.features import geometry_mask, geometry_window  # type: ignore[import-untyped]
from rasterio.warp import reproject  # type: ignore[import-untyped]
from shapely.geometry import mapping  # type: ignore[import-untyped]

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.data_preflight import (
    DatasetManifest,
    ManifestValidationError,
    load_manifest,
    run_preflight,
)

OUTPUT_NODATA = -9999.0
MASKED_SCL_CLASSES = frozenset({0, 1, 3, 8, 9, 10, 11})
SENTINEL_LICENSE = "Copernicus Sentinel data legal notice (free, full and open use)"
SENTINEL_LICENSE_URL = (
    "https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice"
)


@dataclass(frozen=True)
class SourceCog:
    """One immutable public source object plus an optional test read location."""

    href: str
    byte_size: int
    etag: str
    read_href: str | None = None
    sha256: str | None = None

    @property
    def source(self) -> str:
        return self.read_href or self.href


@dataclass(frozen=True)
class Acquisition:
    role: Literal["before", "after"]
    item_id: str
    product_id: str
    acquired_on: date
    platform: str
    tile: str
    cloud_cover: float
    processing_baseline: str
    scale: float
    offset: float
    red: SourceCog
    nir: SourceCog
    scl: SourceCog


_SENTINEL_COGS = "https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/49/R/DQ"
_BEFORE_PREFIX = f"{_SENTINEL_COGS}/2019/8/S2A_49RDQ_20190819_0_L2A"
_AFTER_PREFIX = f"{_SENTINEL_COGS}/2024/8/S2A_49RDQ_20240812_0_L2A"

DEFAULT_ACQUISITIONS = (
    Acquisition(
        role="before",
        item_id="S2A_49RDQ_20190819_0_L2A",
        product_id="S2A_MSIL2A_20190819T031541_N0213_R118_T49RDQ_20190819T072545.SAFE",
        acquired_on=date(2019, 8, 19),
        platform="sentinel-2a",
        tile="MGRS-49RDQ",
        cloud_cover=7.617735,
        processing_baseline="02.13",
        scale=0.0001,
        offset=0.0,
        red=SourceCog(
            href=f"{_BEFORE_PREFIX}/B04.tif",
            byte_size=213_701_170,
            etag='"392e85607586145aebe1788773902e85-26"',
        ),
        nir=SourceCog(
            href=f"{_BEFORE_PREFIX}/B08.tif",
            byte_size=251_428_986,
            etag='"f42030d96e21d78229197a33f01e945c-30"',
        ),
        scl=SourceCog(
            href=f"{_BEFORE_PREFIX}/SCL.tif",
            byte_size=2_794_209,
            etag='"14650eb42dd4e0541d9e8e764852f3dc"',
        ),
    ),
    Acquisition(
        role="after",
        item_id="S2A_49RDQ_20240812_0_L2A",
        product_id="S2A_MSIL2A_20240812T031521_N0511_R118_T49RDQ_20240812T084251.SAFE",
        acquired_on=date(2024, 8, 12),
        platform="sentinel-2a",
        tile="MGRS-49RDQ",
        cloud_cover=6.090892,
        processing_baseline="05.11",
        scale=0.0001,
        offset=-0.1,
        red=SourceCog(
            href=f"{_AFTER_PREFIX}/B04.tif",
            byte_size=209_096_458,
            etag='"a55dd5415755e8cbe5d6ae47a9b52a93-25"',
        ),
        nir=SourceCog(
            href=f"{_AFTER_PREFIX}/B08.tif",
            byte_size=253_879_149,
            etag='"1cca7fe777e92138567e5d874719d29c-31"',
        ),
        scl=SourceCog(
            href=f"{_AFTER_PREFIX}/SCL.tif",
            byte_size=1_438_732,
            etag='"85926f6837db5c1a67359c4379f4794c"',
        ),
    ),
)


@dataclass(frozen=True)
class CacheResult:
    reused: bool
    manifest_path: Path


class DownloadResponse(Protocol):
    headers: Mapping[str, str]

    def read(self, size: int = -1) -> bytes: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None: ...


@dataclass(frozen=True)
class OutputGrid:
    crs: rasterio.crs.CRS
    transform: rasterio.Affine
    width: int
    height: int
    source_transform: rasterio.Affine
    source_width: int
    source_height: int
    inside_watershed: np.ndarray
    shapes: tuple[dict[str, object], ...]
    window: rasterio.windows.Window


def normalize_reflectance(
    raw: np.ndarray,
    scl: np.ndarray,
    inside_watershed: np.ndarray,
    *,
    scale: float,
    offset: float,
    source_nodata: float | int | None,
    output_nodata: float,
) -> np.ndarray:
    """Normalize one band and apply source, quality, and watershed masks."""
    if raw.shape != scl.shape or raw.shape != inside_watershed.shape:
        raise ValueError("raw band, SCL, and watershed mask must share one shape")

    normalized = raw.astype(np.float32) * np.float32(scale) + np.float32(offset)
    valid = inside_watershed.astype(bool, copy=False) & np.isfinite(normalized)
    valid &= ~np.isin(scl, tuple(MASKED_SCL_CLASSES))
    if source_nodata is not None:
        valid &= raw != source_nodata
    normalized[~valid] = np.float32(output_nodata)
    return normalized


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _open_url(href: str) -> DownloadResponse:
    return cast(DownloadResponse, urlopen(href))


def materialize_source(
    source: SourceCog,
    target: Path,
    *,
    opener: Callable[[str], DownloadResponse] = _open_url,
) -> SourceCog:
    """Atomically cache and checksum one immutable approved source COG."""
    checksum_path = target.with_suffix(f"{target.suffix}.sha256")
    if target.is_file() and target.stat().st_size == source.byte_size and checksum_path.is_file():
        expected = checksum_path.read_text(encoding="ascii").strip()
        actual = _sha256(target)
        if expected == actual:
            return replace(source, read_href=str(target), sha256=actual)

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".part")
    digest = hashlib.sha256()
    byte_count = 0
    try:
        with opener(source.href) as response, temporary.open("wb") as destination:
            content_length = response.headers.get("Content-Length")
            response_etag = response.headers.get("ETag")
            if content_length is None or int(content_length) != source.byte_size:
                raise ValueError("source Content-Length does not match the approved byte size")
            if response_etag != source.etag:
                raise ValueError("source ETag does not match the approved immutable object")
            while chunk := response.read(1024 * 1024):
                destination.write(chunk)
                digest.update(chunk)
                byte_count += len(chunk)
        if byte_count != source.byte_size:
            raise ValueError("downloaded source byte size does not match approval")
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    checksum = digest.hexdigest()
    checksum_temporary = checksum_path.with_suffix(".tmp")
    checksum_temporary.write_text(f"{checksum}\n", encoding="ascii")
    os.replace(checksum_temporary, checksum_path)
    return replace(source, read_href=str(target), sha256=checksum)


def _raster_env() -> rasterio.Env:
    # Rasterio's documented window reads let GDAL request only intersecting COG blocks.
    # Source: https://rasterio.readthedocs.io/en/stable/topics/windowed-rw.html
    return rasterio.Env(
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.TIF",
        GDAL_HTTP_MULTIPLEX="YES",
        GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES",
    )


def _load_boundary(path: Path) -> gpd.GeoDataFrame:
    boundary = gpd.read_file(path)
    if boundary.empty or boundary.crs is None or not boundary.geometry.is_valid.all():
        raise ValueError("approved watershed boundary needs a CRS and valid geometry")
    statuses = set(boundary.get("approval_status", []))
    if statuses != {"approved"}:
        raise ValueError("watershed boundary is not marked approved")
    return boundary


def _output_grid(source_href: str, boundary: gpd.GeoDataFrame) -> OutputGrid:
    with rasterio.open(source_href) as source:
        if source.crs is None or source.count != 1:
            raise ValueError("source red COG needs one band and a CRS")
        projected = boundary.to_crs(source.crs)
        watershed = projected.geometry.union_all()
        shapes = (mapping(watershed),)
        window = geometry_window(source, shapes).round_offsets().round_lengths()
        transform = source.window_transform(window)
        width = int(window.width)
        height = int(window.height)
        inside = geometry_mask(
            shapes,
            out_shape=(height, width),
            transform=transform,
            invert=True,
        )
        return OutputGrid(
            crs=source.crs,
            transform=transform,
            width=width,
            height=height,
            source_transform=source.transform,
            source_width=source.width,
            source_height=source.height,
            inside_watershed=inside,
            shapes=shapes,
            window=window,
        )


def _assert_source_grid(source: rasterio.DatasetReader, grid: OutputGrid) -> None:
    if (
        source.crs != grid.crs
        or source.transform != grid.source_transform
        or source.width != grid.source_width
        or source.height != grid.source_height
    ):
        raise ValueError("approved red/NIR source COGs do not share one pixel grid")


def _read_scl(source_href: str, grid: OutputGrid) -> np.ndarray:
    with rasterio.open(source_href) as source:
        if source.crs is None:
            raise ValueError("SCL source has no CRS")
        scl_window = geometry_window(source, grid.shapes).round_offsets().round_lengths()
        scl = source.read([1], window=scl_window)[0]
        destination = np.zeros((grid.height, grid.width), dtype=np.uint8)
        # SCL is categorical, so nearest-neighbour is required during the 20 m to 10 m warp.
        # Source: https://rasterio.readthedocs.io/en/stable/topics/reproject.html
        reproject(
            source=scl,
            destination=destination,
            src_transform=source.window_transform(scl_window),
            src_crs=source.crs,
            src_nodata=source.nodata,
            dst_transform=grid.transform,
            dst_crs=grid.crs,
            dst_nodata=0,
            resampling=Resampling.nearest,
        )
        return destination


def _write_output(
    output: Path,
    values: np.ndarray,
    grid: OutputGrid,
    *,
    logical_id: str,
    acquisition: Acquisition,
    band_number: str,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp.tif")
    profile: dict[str, object] = {
        "driver": "GTiff",
        "width": grid.width,
        "height": grid.height,
        "count": 1,
        "dtype": "float32",
        "crs": grid.crs,
        "transform": grid.transform,
        "nodata": OUTPUT_NODATA,
        "compress": "DEFLATE",
        "predictor": 3,
    }
    if grid.width >= 16 and grid.height >= 16:
        profile.update(tiled=True, blockxsize=512, blockysize=512)
    with rasterio.open(temporary, "w", **profile) as destination:
        destination.write(values, 1)
        destination.set_band_description(1, logical_id)
        destination.update_tags(
            logical_id=logical_id,
            acquired_on=acquisition.acquired_on.isoformat(),
            source_item_id=acquisition.item_id,
            source_product_id=acquisition.product_id,
            source_band=band_number,
            reflectance_scale=str(acquisition.scale),
            reflectance_offset=str(acquisition.offset),
            scl_masked_classes=",".join(str(value) for value in sorted(MASKED_SCL_CLASSES)),
        )
        if min(grid.width, grid.height) >= 512:
            factors = [
                factor for factor in (2, 4, 8, 16) if min(grid.width, grid.height) // factor >= 1
            ]
            destination.build_overviews(factors, Resampling.average)
            destination.update_tags(ns="rio_overview", resampling="average")
    os.replace(temporary, output)


def _process_acquisition(
    acquisition: Acquisition,
    grid: OutputGrid,
    cache_dir: Path,
) -> None:
    scl = _read_scl(acquisition.scl.source, grid)
    for band, source_cog, band_number in (
        ("red", acquisition.red, "B04"),
        ("nir", acquisition.nir, "B08"),
    ):
        logical_id = f"{acquisition.role}_{band}"
        with rasterio.open(source_cog.source) as source:
            _assert_source_grid(source, grid)
            raw = source.read([1], window=grid.window)[0]
            values = normalize_reflectance(
                raw,
                scl,
                grid.inside_watershed,
                scale=acquisition.scale,
                offset=acquisition.offset,
                source_nodata=source.nodata,
                output_nodata=OUTPUT_NODATA,
            )
        _write_output(
            cache_dir / f"{logical_id}.tif",
            values,
            grid,
            logical_id=logical_id,
            acquisition=acquisition,
            band_number=band_number,
        )


def _materialize_acquisitions(
    acquisitions: tuple[Acquisition, Acquisition],
    source_cache_dir: Path,
) -> tuple[Acquisition, Acquisition]:
    materialized: list[Acquisition] = []
    for acquisition in acquisitions:
        sources: dict[str, SourceCog] = {}
        for name, source in (
            ("red", acquisition.red),
            ("nir", acquisition.nir),
            ("scl", acquisition.scl),
        ):
            print(
                f"Verifying source cache for {acquisition.role} {name.upper()}...",
                flush=True,
            )
            cached = materialize_source(
                source,
                source_cache_dir / f"{acquisition.role}_{name}.tif",
            )
            if cached.sha256 is None:
                raise RuntimeError("materialized source has no SHA-256")
            sources[name] = cached
            print(
                f"Source ready: {acquisition.role} {name.upper()} "
                f"({source.byte_size / 1_000_000:.1f} MB, "
                f"SHA-256 {cached.sha256[:12]}...).",
                flush=True,
            )
        materialized.append(
            replace(
                acquisition,
                red=sources["red"],
                nir=sources["nir"],
                scl=sources["scl"],
            )
        )
    return materialized[0], materialized[1]


def _source_record(
    acquisition: Acquisition,
    source: SourceCog,
    asset_name: str,
) -> dict[str, object]:
    return {
        "organization": "European Union / ESA Copernicus; Element 84 COG distribution",
        "product_id": f"{acquisition.product_id}/{asset_name}",
        "url": source.href,
        "license": SENTINEL_LICENSE,
        "license_url": SENTINEL_LICENSE_URL,
        "byte_size": source.byte_size,
        "etag": source.etag,
        "sha256": source.sha256,
    }


def _asset_metadata(path: Path) -> dict[str, object]:
    with rasterio.open(path) as dataset:
        if dataset.crs is None:
            raise ValueError(f"generated raster {path.name} has no CRS")
        return {
            "byte_size": path.stat().st_size,
            "sha256": _sha256(path),
            "crs": dataset.crs.to_string(),
            "bounds": [float(value) for value in dataset.bounds],
            "resolution": [float(value) for value in dataset.res],
            "resolution_unit": "metre" if dataset.crs.is_projected else "degree",
            "nodata": dataset.nodata,
            "data_type": dataset.dtypes[0],
        }


def _build_manifest(
    *,
    boundary_path: Path,
    data_root: Path,
    cache_dir: Path,
    acquisitions: tuple[Acquisition, Acquisition],
    approval_date: date,
) -> DatasetManifest:
    boundary = gpd.read_file(boundary_path)
    if boundary.crs is None:
        raise ValueError("generated manifest boundary has no CRS")
    assets: list[dict[str, object]] = [
        {
            "logical_id": "watershed",
            "storage": "bundle",
            "path": boundary_path.relative_to(data_root).as_posix(),
            "media_type": "application/geo+json",
            "byte_size": boundary_path.stat().st_size,
            "sha256": _sha256(boundary_path),
            "crs": boundary.crs.to_string(),
            "bounds": [float(value) for value in boundary.total_bounds],
            "source_assets": [
                {
                    "organization": "WWF HydroSHEDS / HydroBASINS",
                    "product_id": "hybas_as_lev12_v1c",
                    "url": (
                        "https://data.hydrosheds.org/file/hydrobasins/standard/"
                        "hybas_as_lev12_v1c.zip"
                    ),
                    "license": "HydroBASINS license under the HydroSHEDS terms",
                    "license_url": "https://www.hydrosheds.org/products/hydrobasins",
                    "byte_size": 80_155_135,
                    "etag": None,
                    "sha256": ("05e98a001fc526cd5fcdbbc8144fe0aa3ce6712c35624f72e4728b219193fdb9"),
                }
            ],
            "derivation": (
                "Union of eight level-12 polygons whose NEXT_DOWN chain reaches outlet "
                "HYBAS_ID 4120733210; downstream Yangtze polygon excluded."
            ),
        }
    ]

    for acquisition in acquisitions:
        for band, band_number, source in (
            ("red", "B04", acquisition.red),
            ("nir", "B08", acquisition.nir),
        ):
            logical_id = f"{acquisition.role}_{band}"
            local_path = cache_dir / f"{logical_id}.tif"
            assets.append(
                {
                    "logical_id": logical_id,
                    "storage": "cache",
                    "path": local_path.relative_to(cache_dir).as_posix(),
                    "media_type": "image/tiff; application=geotiff",
                    **_asset_metadata(local_path),
                    "acquired_on": acquisition.acquired_on.isoformat(),
                    "band": band,
                    "band_number": band_number,
                    "source_assets": [
                        _source_record(acquisition, source, band_number),
                        _source_record(acquisition, acquisition.scl, "SCL"),
                    ],
                    "derivation": (
                        "Contains modified Copernicus Sentinel data "
                        f"{acquisition.acquired_on.year}. "
                        f"Range-read watershed crop; reflectance=DN*{acquisition.scale}"
                        f"{acquisition.offset:+g}; SCL classes "
                        f"{','.join(str(value) for value in sorted(MASKED_SCL_CLASSES))} and "
                        "pixels outside the watershed set to -9999 nodata."
                    ),
                }
            )

    return DatasetManifest.model_validate(
        {
            "schema_version": "1.0",
            "dataset_name": "Shennongxi dual-date NDVI demonstration",
            "approval": {
                "gate": "G2",
                "status": "approved",
                "approved_on": approval_date.isoformat(),
            },
            "quality": {
                "minimum_watershed_coverage_ratio": 0.95,
                "minimum_valid_pixel_ratio": 0.90,
            },
            "assets": assets,
        }
    )


def _write_manifest(manifest: DatasetManifest, path: Path) -> None:
    rendered = (
        json.dumps(
            manifest.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    temporary = path.with_suffix(".tmp.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text(rendered, encoding="utf-8")
    os.replace(temporary, path)


def _manifest_matches_approval(
    path: Path,
    acquisitions: tuple[Acquisition, Acquisition],
    approval_date: date,
) -> bool:
    try:
        manifest = load_manifest(path)
    except ManifestValidationError:
        return False
    if manifest.approval.approved_on != approval_date:
        return False

    by_id = {asset.logical_id.value: asset for asset in manifest.assets}
    for acquisition in acquisitions:
        for band, band_number, source in (
            ("red", "B04", acquisition.red),
            ("nir", "B08", acquisition.nir),
        ):
            asset = by_id.get(f"{acquisition.role}_{band}")
            if asset is None or asset.acquired_on != acquisition.acquired_on:
                return False
            expected_sources = (
                (source, band_number),
                (acquisition.scl, "SCL"),
            )
            if len(asset.source_assets) != len(expected_sources):
                return False
            for recorded, (expected, asset_name) in zip(
                asset.source_assets,
                expected_sources,
                strict=True,
            ):
                if (
                    recorded.product_id != f"{acquisition.product_id}/{asset_name}"
                    or str(recorded.url) != expected.href
                    or recorded.byte_size != expected.byte_size
                    or recorded.etag != expected.etag
                ):
                    return False
    return True


def build_cache(
    *,
    boundary_path: Path,
    data_root: Path,
    cache_dir: Path,
    manifest_path: Path,
    acquisitions: tuple[Acquisition, Acquisition],
    approval_date: date,
    source_cache_dir: Path | None = None,
) -> CacheResult:
    if manifest_path.is_file() and _manifest_matches_approval(
        manifest_path,
        acquisitions,
        approval_date,
    ):
        try:
            existing = run_preflight(
                manifest_path,
                data_root=data_root,
                cache_dir=cache_dir,
            )
        except ManifestValidationError:
            existing = None
        if existing is not None and existing.ok:
            return CacheResult(reused=True, manifest_path=manifest_path)

    if len(acquisitions) != 2 or {item.role for item in acquisitions} != {"before", "after"}:
        raise ValueError("cache requires exactly one before and one after acquisition")
    before = next(item for item in acquisitions if item.role == "before")
    after = next(item for item in acquisitions if item.role == "after")
    if before.acquired_on >= after.acquired_on:
        raise ValueError("before acquisition date must precede after acquisition date")

    if source_cache_dir is not None:
        before, after = _materialize_acquisitions((before, after), source_cache_dir)

    boundary = _load_boundary(boundary_path)
    cache_dir.mkdir(parents=True, exist_ok=True)
    with _raster_env():
        grid = _output_grid(before.red.source, boundary)
        for acquisition in (before, after):
            print(
                f"Preparing {acquisition.role} acquisition {acquisition.acquired_on}...",
                flush=True,
            )
            _process_acquisition(acquisition, grid, cache_dir)

    manifest = _build_manifest(
        boundary_path=boundary_path,
        data_root=data_root,
        cache_dir=cache_dir,
        acquisitions=(before, after),
        approval_date=approval_date,
    )
    _write_manifest(manifest, manifest_path)
    report = run_preflight(manifest_path, data_root=data_root, cache_dir=cache_dir)
    if not report.ok:
        raise RuntimeError(report.format())
    return CacheResult(reused=False, manifest_path=manifest_path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument(
        "--boundary",
        type=Path,
        default=Path("data/boundaries/shennongxi_watershed.geojson"),
    )
    parser.add_argument("--cache-dir", type=Path, default=Path("data/cache/demo"))
    parser.add_argument(
        "--source-cache-dir",
        type=Path,
        default=Path("data/cache/sources/sentinel2"),
    )
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest.json"))
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        result = build_cache(
            boundary_path=args.boundary,
            data_root=args.data_root,
            cache_dir=args.cache_dir,
            manifest_path=args.manifest,
            acquisitions=DEFAULT_ACQUISITIONS,
            approval_date=date(2026, 7, 19),
            source_cache_dir=args.source_cache_dir,
        )
    except Exception as error:
        print(f"Cache build failed ({type(error).__name__}): {error}")
        print("Remediation: verify network access and the approved source list, then retry.")
        return 1
    action = "Reused verified offline cache" if result.reused else "Built verified offline cache"
    print(f"{action}; manifest: {result.manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
