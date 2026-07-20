"""Load the approved quality policy and reference watershed grid."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import cast

import numpy as np
from affine import Affine  # type: ignore[import-untyped]
from hennongxi_contracts import QualityThresholds
from numpy.typing import NDArray
from pydantic import ValidationError
from rasterio.crs import CRS  # type: ignore[import-untyped]
from rasterio.features import geometry_mask  # type: ignore[import-untyped]
from rasterio.transform import from_origin  # type: ignore[import-untyped]
from rasterio.warp import transform_geom  # type: ignore[import-untyped]

_RASTER_IDS = frozenset({"before_red", "before_nir", "after_red", "after_nir"})
_MAX_REFERENCE_PIXELS = 50_000_000


class QualityConfigurationError(ValueError):
    """Raised when approved quality reference data cannot be trusted."""


@dataclass(frozen=True, slots=True)
class ReferenceGrid:
    crs: CRS
    width: int
    height: int
    transform: Affine
    bounds: tuple[float, float, float, float]
    watershed_geometries: tuple[dict[str, object], ...]
    watershed_mask: NDArray[np.bool_]


@dataclass(frozen=True, slots=True)
class QualityConfiguration:
    thresholds: QualityThresholds
    grid: ReferenceGrid


def load_quality_configuration(manifest_path: Path) -> QualityConfiguration:
    """Read only the approved policy, boundary, and common raster-grid fields."""

    try:
        manifest = _object(json.loads(manifest_path.read_text(encoding="utf-8")))
        thresholds = QualityThresholds.model_validate(manifest["quality"])
        if (
            thresholds.minimum_watershed_coverage_ratio < 0.9
            or thresholds.minimum_valid_pixel_ratio < 0.8
        ):
            raise ValueError("quality thresholds are weaker than the approved policy floor")
        assets = manifest["assets"]
        if not isinstance(assets, list):
            raise TypeError("assets must be a list")
        by_id = {
            str(asset["logical_id"]): asset
            for value in assets
            if isinstance(value, dict)
            for asset in (_object(value),)
        }
        if len(assets) != len(by_id) or set(by_id) != _RASTER_IDS | {"watershed"}:
            raise ValueError("manifest must contain the approved logical assets")

        raster_assets = [by_id[logical_id] for logical_id in sorted(_RASTER_IDS)]
        fingerprints = {
            (
                str(asset["crs"]),
                _bounds(asset["bounds"]),
                _resolution(asset["resolution"]),
            )
            for asset in raster_assets
        }
        if len(fingerprints) != 1:
            raise ValueError("approved raster assets do not share one reference grid")
        crs_value, bounds, resolution = fingerprints.pop()
        crs = CRS.from_user_input(crs_value)
        width = _dimension(bounds[2] - bounds[0], resolution[0])
        height = _dimension(bounds[3] - bounds[1], resolution[1])
        if width * height > _MAX_REFERENCE_PIXELS:
            raise ValueError("approved reference grid exceeds the quality inspection limit")
        transform = from_origin(bounds[0], bounds[3], resolution[0], resolution[1])

        boundary_asset = by_id["watershed"]
        boundary_path = _resolve_bundle_path(manifest_path.parent, boundary_asset["path"])
        if (
            boundary_path.stat().st_size != _positive_integer(boundary_asset["byte_size"])
            or _sha256(boundary_path) != boundary_asset["sha256"]
        ):
            raise ValueError("watershed boundary does not match its approved metadata")
        boundary = _object(json.loads(boundary_path.read_text(encoding="utf-8")))
        features = boundary["features"]
        if not isinstance(features, list):
            raise TypeError("boundary features must be a list")
        raw_geometries = tuple(
            _object(feature)["geometry"]
            for feature in features
            if isinstance(feature, dict) and isinstance(feature.get("geometry"), dict)
        )
        if not raw_geometries:
            raise ValueError("watershed boundary has no geometry")
        boundary_crs = CRS.from_user_input(str(boundary_asset["crs"]))
        geometries = tuple(
            cast(
                dict[str, object],
                transform_geom(boundary_crs, crs, cast(dict[str, object], geometry)),
            )
            for geometry in raw_geometries
        )
        watershed_mask = geometry_mask(
            geometries,
            out_shape=(height, width),
            transform=transform,
            invert=True,
        )
        if not np.any(watershed_mask):
            raise ValueError("watershed does not overlap the approved reference grid")
    except (
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        ValidationError,
    ) as error:
        raise QualityConfigurationError(
            "approved quality manifest or watershed reference is invalid"
        ) from error

    return QualityConfiguration(
        thresholds=thresholds,
        grid=ReferenceGrid(
            crs=crs,
            width=width,
            height=height,
            transform=transform,
            bounds=bounds,
            watershed_geometries=geometries,
            watershed_mask=watershed_mask,
        ),
    )


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise TypeError("expected a JSON object with string keys")
    return cast(dict[str, object], value)


def _bounds(value: object) -> tuple[float, float, float, float]:
    if not isinstance(value, list) or len(value) != 4:
        raise TypeError("bounds must have four values")
    bounds = (float(value[0]), float(value[1]), float(value[2]), float(value[3]))
    left, bottom, right, top = bounds
    if not all(math.isfinite(item) for item in bounds) or left >= right or bottom >= top:
        raise ValueError("bounds are invalid")
    return bounds


def _resolution(value: object) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2:
        raise TypeError("resolution must have two values")
    resolution = (float(value[0]), float(value[1]))
    if not all(math.isfinite(item) and item > 0 for item in resolution):
        raise ValueError("resolution is invalid")
    return resolution


def _dimension(span: float, resolution: float) -> int:
    dimension = round(span / resolution)
    if dimension < 1 or not math.isclose(dimension * resolution, span, abs_tol=1e-6):
        raise ValueError("bounds are not divisible by raster resolution")
    return dimension


def _resolve_bundle_path(root: Path, value: object) -> Path:
    if not isinstance(value, str):
        raise TypeError("boundary path must be a string")
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("boundary path must stay within the approved data bundle")
    resolved_root = root.resolve()
    resolved = (resolved_root / relative).resolve(strict=False)
    resolved.relative_to(resolved_root)
    if not resolved.is_file() or resolved.is_symlink():
        raise OSError("approved boundary is unavailable")
    return resolved


def _positive_integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("expected a positive integer")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
