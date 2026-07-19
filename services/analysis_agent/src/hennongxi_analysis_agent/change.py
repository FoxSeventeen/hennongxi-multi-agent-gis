"""Deterministic NDVI change classes and projected-area statistics."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import IntEnum

import numpy as np
from numpy.typing import NDArray

from hennongxi_analysis_agent.raster import ContinuousRaster, RasterGrid

DEFAULT_CHANGE_THRESHOLD = 0.1
CHANGE_NODATA = -128


class ChangeClass(IntEnum):
    """Stable numeric values written to the classified change raster."""

    DECREASE = -1
    STABLE = 0
    INCREASE = 1


class AreaCalculationError(ValueError):
    """Raised when a raster grid cannot yield defensible metric areas."""


@dataclass(frozen=True, slots=True)
class ClassifiedRaster:
    """Integer change classes with an explicit validity mask and spatial grid."""

    values: NDArray[np.int8]
    valid_mask: NDArray[np.bool_]
    grid: RasterGrid
    nodata: int = CHANGE_NODATA

    def __post_init__(self) -> None:
        if self.values.shape != self.grid.shape:
            raise ValueError("values shape does not match raster grid")
        if self.valid_mask.shape != self.grid.shape:
            raise ValueError("valid mask shape does not match raster grid")
        valid_codes = np.isin(self.values, tuple(ChangeClass))
        if np.any(self.valid_mask & ~valid_codes):
            raise ValueError("unknown change class code")


@dataclass(frozen=True, slots=True)
class AreaStatistics:
    """Projected pixel counts and areas for the fixed three-class policy."""

    threshold: float
    pixel_area_square_metres: float
    valid_pixel_count: int
    decrease_pixel_count: int
    stable_pixel_count: int
    increase_pixel_count: int
    valid_area_square_metres: float
    decrease_area_square_metres: float
    stable_area_square_metres: float
    increase_area_square_metres: float


def _require_threshold(threshold: float) -> None:
    if not math.isfinite(threshold) or threshold <= 0:
        raise ValueError("change threshold must be positive and finite")


def classify_change(
    difference: ContinuousRaster,
    *,
    threshold: float = DEFAULT_CHANGE_THRESHOLD,
) -> ClassifiedRaster:
    """Classify valid NDVI differences using symmetric inclusive boundaries."""

    _require_threshold(threshold)
    valid_mask = difference.valid_mask & np.isfinite(difference.values)
    values = np.full(difference.grid.shape, CHANGE_NODATA, dtype=np.int8)
    values[valid_mask] = ChangeClass.STABLE
    values[valid_mask & (difference.values <= -threshold)] = ChangeClass.DECREASE
    values[valid_mask & (difference.values >= threshold)] = ChangeClass.INCREASE
    return ClassifiedRaster(values=values, valid_mask=valid_mask, grid=difference.grid)


def summarize_class_areas(
    classified: ClassifiedRaster,
    *,
    threshold: float = DEFAULT_CHANGE_THRESHOLD,
) -> AreaStatistics:
    """Summarize class areas in square metres from a projected affine grid."""

    _require_threshold(threshold)
    if not classified.grid.crs.is_projected:
        raise AreaCalculationError("area statistics require a projected crs")

    transform = classified.grid.transform
    pixel_area_in_crs_units = abs(transform.a * transform.e - transform.b * transform.d)
    _, linear_unit_to_metres = classified.grid.crs.linear_units_factor
    pixel_area_square_metres = pixel_area_in_crs_units * linear_unit_to_metres**2
    if not math.isfinite(pixel_area_square_metres) or pixel_area_square_metres <= 0:
        raise AreaCalculationError("pixel area must be positive and finite")

    valid = classified.valid_mask
    decrease_count = int(np.count_nonzero(valid & (classified.values == ChangeClass.DECREASE)))
    stable_count = int(np.count_nonzero(valid & (classified.values == ChangeClass.STABLE)))
    increase_count = int(np.count_nonzero(valid & (classified.values == ChangeClass.INCREASE)))
    valid_count = decrease_count + stable_count + increase_count
    return AreaStatistics(
        threshold=threshold,
        pixel_area_square_metres=pixel_area_square_metres,
        valid_pixel_count=valid_count,
        decrease_pixel_count=decrease_count,
        stable_pixel_count=stable_count,
        increase_pixel_count=increase_count,
        valid_area_square_metres=valid_count * pixel_area_square_metres,
        decrease_area_square_metres=decrease_count * pixel_area_square_metres,
        stable_area_square_metres=stable_count * pixel_area_square_metres,
        increase_area_square_metres=increase_count * pixel_area_square_metres,
    )
