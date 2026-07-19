"""Small immutable raster values used by deterministic Analysis Agent math."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from affine import Affine  # type: ignore[import-untyped]
from numpy.typing import NDArray
from rasterio.crs import CRS  # type: ignore[import-untyped]

OUTPUT_NODATA = -9999.0


class GridMismatchError(ValueError):
    """Raised when rasters are not explicitly aligned on the same grid."""


@dataclass(frozen=True, slots=True)
class RasterGrid:
    """The complete spatial grid required to compare raster values safely."""

    crs: CRS
    transform: Affine
    height: int
    width: int

    @property
    def shape(self) -> tuple[int, int]:
        return (self.height, self.width)


@dataclass(frozen=True, slots=True)
class ContinuousRaster:
    """Float raster values with an explicit valid-pixel mask and spatial grid."""

    values: NDArray[np.float32]
    valid_mask: NDArray[np.bool_]
    grid: RasterGrid
    nodata: float = OUTPUT_NODATA

    def __post_init__(self) -> None:
        if self.values.shape != self.grid.shape:
            raise ValueError("values shape does not match raster grid")
        if self.valid_mask.shape != self.grid.shape:
            raise ValueError("valid mask shape does not match raster grid")


def require_aligned(reference: ContinuousRaster, candidate: ContinuousRaster) -> None:
    """Reject every spatial mismatch instead of relying on array shape alone."""

    if reference.grid.crs != candidate.grid.crs:
        raise GridMismatchError("raster crs mismatch")
    if reference.grid.transform != candidate.grid.transform:
        raise GridMismatchError("raster transform mismatch")
    if reference.grid.shape != candidate.grid.shape:
        raise GridMismatchError("raster shape mismatch")
