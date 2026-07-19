"""Validate the approved, offline dataset without accepting caller-controlled paths."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, Self

import geopandas as gpd  # type: ignore[import-untyped]
import numpy as np
import rasterio  # type: ignore[import-untyped]
from hennongxi_contracts import LogicalDatasetId  # type: ignore[import-untyped]
from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from rasterio.features import geometry_mask  # type: ignore[import-untyped]
from rasterio.mask import raster_geometry_mask  # type: ignore[import-untyped]
from shapely.geometry import box, mapping  # type: ignore[import-untyped]

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
PositivePair = tuple[Annotated[float, Field(gt=0)], Annotated[float, Field(gt=0)]]
Bounds = tuple[float, float, float, float]


class ManifestValidationError(ValueError):
    """Raised when the checked-in manifest is missing or violates its contract."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StorageKind(StrEnum):
    BUNDLE = "bundle"
    CACHE = "cache"


class Approval(StrictModel):
    gate: Literal["G2"]
    status: Literal["approved"]
    approved_on: date


class QualityThresholds(StrictModel):
    minimum_watershed_coverage_ratio: float = Field(ge=0.9, le=1.0)
    minimum_valid_pixel_ratio: float = Field(ge=0.8, le=1.0)


class SourceAsset(StrictModel):
    organization: str = Field(min_length=1, max_length=200)
    product_id: str = Field(min_length=1, max_length=300)
    url: AnyHttpUrl
    license: str = Field(min_length=1, max_length=200)
    license_url: AnyHttpUrl
    byte_size: int | None = Field(default=None, gt=0)
    etag: str | None = Field(default=None, min_length=1, max_length=300)
    sha256: Sha256 | None = None

    @field_validator("url", "license_url")
    @classmethod
    def require_public_stable_url(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.username or value.password or value.query or value.fragment:
            raise ValueError("source URLs must not contain credentials, query tokens, or fragments")
        return value


class ManifestAsset(StrictModel):
    logical_id: LogicalDatasetId
    storage: StorageKind
    path: str
    media_type: str = Field(min_length=1, max_length=200)
    byte_size: int = Field(gt=0)
    sha256: Sha256
    crs: str = Field(min_length=1, max_length=100)
    bounds: Bounds
    source_assets: tuple[SourceAsset, ...] = Field(min_length=1)
    derivation: str = Field(min_length=1, max_length=1_000)
    resolution: PositivePair | None = None
    resolution_unit: Literal["metre", "degree"] | None = None
    nodata: float | int | None = None
    data_type: str | None = Field(default=None, min_length=1, max_length=50)
    acquired_on: date | None = None
    band: Literal["red", "nir"] | None = None
    band_number: Literal["B04", "B08"] | None = None

    @field_validator("path")
    @classmethod
    def require_safe_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or not value or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("path must be a safe relative path")
        return value

    @model_validator(mode="after")
    def require_asset_specific_metadata(self) -> Self:
        left, bottom, right, top = self.bounds
        if not all(math.isfinite(value) for value in self.bounds) or left >= right or bottom >= top:
            raise ValueError("bounds must be finite and have positive width and height")

        if self.logical_id is LogicalDatasetId.WATERSHED:
            if self.storage is not StorageKind.BUNDLE:
                raise ValueError("watershed must use bundle storage")
            raster_only = (
                self.resolution,
                self.resolution_unit,
                self.nodata,
                self.data_type,
                self.acquired_on,
                self.band,
                self.band_number,
            )
            if any(value is not None for value in raster_only):
                raise ValueError("watershed must not contain raster-only metadata")
            return self

        if self.storage is not StorageKind.CACHE:
            raise ValueError("raster assets must use cache storage")
        required = (
            self.resolution,
            self.resolution_unit,
            self.nodata,
            self.data_type,
            self.acquired_on,
            self.band,
            self.band_number,
        )
        if any(value is None for value in required):
            raise ValueError("raster assets require complete grid, date, band, and nodata metadata")

        expected_band = "red" if self.logical_id.value.endswith("red") else "nir"
        expected_number = "B04" if expected_band == "red" else "B08"
        if self.band != expected_band or self.band_number != expected_number:
            raise ValueError(f"{self.logical_id.value} has an invalid band mapping")
        return self


class DatasetManifest(StrictModel):
    schema_version: Literal["1.0"]
    dataset_name: str = Field(min_length=1, max_length=300)
    approval: Approval
    quality: QualityThresholds
    assets: tuple[ManifestAsset, ...]

    @model_validator(mode="after")
    def require_complete_coherent_asset_set(self) -> Self:
        ids = tuple(asset.logical_id for asset in self.assets)
        required = frozenset(LogicalDatasetId)
        if len(ids) != len(required) or set(ids) != required:
            raise ValueError("assets must contain exactly the required logical IDs")

        by_id = {asset.logical_id: asset for asset in self.assets}
        before = {
            by_id[LogicalDatasetId.BEFORE_RED].acquired_on,
            by_id[LogicalDatasetId.BEFORE_NIR].acquired_on,
        }
        after = {
            by_id[LogicalDatasetId.AFTER_RED].acquired_on,
            by_id[LogicalDatasetId.AFTER_NIR].acquired_on,
        }
        if len(before) != 1 or len(after) != 1:
            raise ValueError("red and NIR bands from each acquisition must share one date")
        before_date = next(iter(before))
        after_date = next(iter(after))
        if before_date is None or after_date is None or before_date >= after_date:
            raise ValueError("before acquisition date must precede after acquisition date")
        return self


@dataclass(frozen=True)
class PreflightCheck:
    logical_id: str
    name: str
    ok: bool
    message: str


@dataclass
class PreflightReport:
    checks: list[PreflightCheck] = field(default_factory=list)
    valid_pixel_ratios: dict[str, float] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(check.ok for check in self.checks)

    def add(self, logical_id: str, name: str, ok: bool, message: str) -> None:
        self.checks.append(PreflightCheck(logical_id, name, ok, message))

    def format(self) -> str:
        lines = ["Demonstration data preflight"]
        for check in self.checks:
            marker = "PASS" if check.ok else "FAIL"
            lines.append(f"[{marker}] {check.logical_id} {check.name}: {check.message}")
        if not self.ok:
            lines.append(
                "Remediation: run `python scripts/cache_demo_data.py`, then repeat this preflight."
            )
        return "\n".join(lines)


@dataclass(frozen=True)
class RasterInspection:
    fingerprint: tuple[str, int, int, tuple[float, ...]]
    valid_ratio: float


def load_manifest(path: Path) -> DatasetManifest:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return DatasetManifest.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        raise ManifestValidationError(f"invalid data manifest: {error}") from error


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _matches_number(actual: float | int | None, expected: float | int | None) -> bool:
    if actual is None or expected is None:
        return actual is expected
    return math.isclose(float(actual), float(expected), rel_tol=0, abs_tol=1e-9)


def _resolve_asset(asset: ManifestAsset, *, data_root: Path, cache_dir: Path) -> Path:
    root = data_root if asset.storage is StorageKind.BUNDLE else cache_dir
    return root.joinpath(*PurePosixPath(asset.path).parts)


def _check_integrity(
    report: PreflightReport,
    asset: ManifestAsset,
    path: Path,
) -> bool:
    logical_id = asset.logical_id.value
    if not path.is_file():
        report.add(logical_id, "file", False, "required local file is missing")
        return False
    actual_size = path.stat().st_size
    if actual_size != asset.byte_size:
        report.add(
            logical_id,
            "byte-size",
            False,
            f"expected {asset.byte_size} bytes but found {actual_size}",
        )
        return False
    actual_hash = _sha256(path)
    if actual_hash != asset.sha256:
        report.add(logical_id, "sha256", False, "local file checksum does not match manifest")
        return False
    report.add(logical_id, "integrity", True, "size and SHA-256 match")
    return True


def _inspect_boundary(
    report: PreflightReport,
    asset: ManifestAsset,
    path: Path,
) -> gpd.GeoDataFrame | None:
    logical_id = asset.logical_id.value
    try:
        boundary = gpd.read_file(path)
    except Exception as error:  # Fiona/pyogrio expose multiple backend-specific exception types.
        report.add(logical_id, "GIS", False, f"boundary is unreadable ({type(error).__name__})")
        return None
    if boundary.empty or boundary.crs is None or boundary.geometry.is_empty.any():
        report.add(logical_id, "GIS", False, "boundary needs a CRS and non-empty geometry")
        return None
    crs_ok = rasterio.crs.CRS.from_user_input(boundary.crs) == rasterio.crs.CRS.from_user_input(
        asset.crs
    )
    bounds_ok = np.allclose(boundary.total_bounds, asset.bounds, rtol=0, atol=1e-6)
    geometry_ok = bool(boundary.geometry.is_valid.all())
    ok = crs_ok and bounds_ok and geometry_ok
    report.add(
        logical_id,
        "GIS",
        ok,
        "readable CRS, bounds, and valid geometry" if ok else "CRS, bounds, or geometry mismatch",
    )
    return boundary if ok else None


def _inspect_raster(
    report: PreflightReport,
    asset: ManifestAsset,
    path: Path,
    boundary: gpd.GeoDataFrame,
    quality: QualityThresholds,
) -> RasterInspection | None:
    logical_id = asset.logical_id.value
    try:
        with rasterio.open(path) as dataset:
            expected_crs = rasterio.crs.CRS.from_user_input(asset.crs)
            metadata_ok = (
                dataset.count == 1
                and dataset.crs == expected_crs
                and asset.resolution is not None
                and np.allclose(dataset.res, asset.resolution, rtol=0, atol=1e-9)
                and _matches_number(dataset.nodata, asset.nodata)
                and dataset.dtypes[0] == asset.data_type
                and np.allclose(dataset.bounds, asset.bounds, rtol=0, atol=1e-6)
            )
            report.add(
                logical_id,
                "metadata",
                bool(metadata_ok),
                "readable CRS, bounds, resolution, nodata, dtype, and band"
                if metadata_ok
                else "raster metadata does not match manifest",
            )
            if not metadata_ok or dataset.crs is None:
                return None

            projected = boundary.to_crs(dataset.crs)
            watershed = projected.geometry.union_all()
            raster_extent = box(*dataset.bounds)
            coverage_ratio = (
                watershed.intersection(raster_extent).area / watershed.area
                if watershed.area > 0
                else 0.0
            )
            coverage_ok = coverage_ratio >= quality.minimum_watershed_coverage_ratio
            report.add(
                logical_id,
                "coverage",
                coverage_ok,
                f"watershed coverage ratio {coverage_ratio:.4f}",
            )

            if not coverage_ok:
                valid_ratio = 0.0
            else:
                shapes = [mapping(watershed)]
                _, _, window = raster_geometry_mask(dataset, shapes, crop=True)
                data = dataset.read([1], window=window)[0]
                inside = geometry_mask(
                    shapes,
                    out_shape=data.shape,
                    transform=dataset.window_transform(window),
                    invert=True,
                )
                valid = inside & np.isfinite(data)
                if dataset.nodata is not None:
                    valid &= ~np.isclose(data, dataset.nodata)
                denominator = int(inside.sum())
                valid_ratio = float(valid.sum() / denominator) if denominator else 0.0

            valid_ok = valid_ratio >= quality.minimum_valid_pixel_ratio
            report.valid_pixel_ratios[logical_id] = valid_ratio
            report.add(
                logical_id,
                "valid-pixels",
                valid_ok,
                f"valid watershed pixel ratio {valid_ratio:.4f}",
            )
            fingerprint = (
                dataset.crs.to_string(),
                dataset.width,
                dataset.height,
                tuple(float(value) for value in dataset.transform),
            )
            return RasterInspection(fingerprint=fingerprint, valid_ratio=valid_ratio)
    except Exception as error:  # Rasterio raises several GDAL-backed exception types.
        report.add(logical_id, "GIS", False, f"raster is unreadable ({type(error).__name__})")
        return None


def run_preflight(
    manifest_path: Path,
    *,
    data_root: Path,
    cache_dir: Path,
) -> PreflightReport:
    manifest = load_manifest(manifest_path)
    report = PreflightReport()
    by_id = {asset.logical_id: asset for asset in manifest.assets}

    verified_paths: dict[LogicalDatasetId, Path] = {}
    for asset in manifest.assets:
        path = _resolve_asset(asset, data_root=data_root, cache_dir=cache_dir)
        if _check_integrity(report, asset, path):
            verified_paths[asset.logical_id] = path

    watershed_asset = by_id[LogicalDatasetId.WATERSHED]
    watershed_path = verified_paths.get(LogicalDatasetId.WATERSHED)
    boundary = (
        _inspect_boundary(report, watershed_asset, watershed_path)
        if watershed_path is not None
        else None
    )

    inspections: dict[LogicalDatasetId, RasterInspection] = {}
    if boundary is not None:
        for dataset_id in LogicalDatasetId:
            if dataset_id is LogicalDatasetId.WATERSHED:
                continue
            raster_path = verified_paths.get(dataset_id)
            if raster_path is None:
                continue
            inspection = _inspect_raster(
                report,
                by_id[dataset_id],
                raster_path,
                boundary,
                manifest.quality,
            )
            if inspection is not None:
                inspections[dataset_id] = inspection

    if len(inspections) == 4:
        fingerprints = {inspection.fingerprint for inspection in inspections.values()}
        grids_ok = len(fingerprints) == 1
        report.add(
            "rasters",
            "aligned-grid",
            grids_ok,
            "all four inputs share one pixel grid"
            if grids_ok
            else "the four raster inputs do not share one pixel grid",
        )
    return report
