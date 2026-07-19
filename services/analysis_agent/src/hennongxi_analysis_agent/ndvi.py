"""Pure NDVI and temporal-difference calculations."""

from __future__ import annotations

import numpy as np

from hennongxi_analysis_agent.raster import ContinuousRaster, require_aligned


def calculate_ndvi(*, nir: ContinuousRaster, red: ContinuousRaster) -> ContinuousRaster:
    """Calculate ``(NIR - Red) / (NIR + Red)`` only for valid finite pixels."""

    require_aligned(nir, red)
    source_valid = (
        nir.valid_mask & red.valid_mask & np.isfinite(nir.values) & np.isfinite(red.values)
    )
    numerator = np.zeros(nir.grid.shape, dtype=np.float32)
    denominator = np.zeros(nir.grid.shape, dtype=np.float32)
    np.subtract(nir.values, red.values, out=numerator, where=source_valid)
    np.add(nir.values, red.values, out=denominator, where=source_valid)
    valid = source_valid & np.isfinite(denominator) & (denominator != 0)

    values = np.full(nir.grid.shape, nir.nodata, dtype=np.float32)
    np.divide(numerator, denominator, out=values, where=valid)
    valid &= np.isfinite(values)
    values[~valid] = nir.nodata
    return ContinuousRaster(values=values, valid_mask=valid, grid=nir.grid, nodata=nir.nodata)


def calculate_difference(*, after: ContinuousRaster, before: ContinuousRaster) -> ContinuousRaster:
    """Subtract before NDVI from after NDVI while preserving invalid pixels."""

    require_aligned(after, before)
    valid = (
        after.valid_mask
        & before.valid_mask
        & np.isfinite(after.values)
        & np.isfinite(before.values)
    )
    values = np.full(after.grid.shape, after.nodata, dtype=np.float32)
    np.subtract(after.values, before.values, out=values, where=valid)
    valid &= np.isfinite(values)
    values[~valid] = after.nodata
    return ContinuousRaster(
        values=values,
        valid_mask=valid,
        grid=after.grid,
        nodata=after.nodata,
    )
