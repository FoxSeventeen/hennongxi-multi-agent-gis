"""Deterministic raster clipping at the Analysis Agent I/O boundary."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import numpy as np
from rasterio.crs import CRS  # type: ignore[import-untyped]
from rasterio.io import DatasetReader  # type: ignore[import-untyped]
from rasterio.mask import mask  # type: ignore[import-untyped]
from rasterio.warp import transform_geom  # type: ignore[import-untyped]

from hennongxi_analysis_agent.raster import (
    OUTPUT_NODATA,
    ContinuousRaster,
    RasterGrid,
)

GeoJSONGeometry = Mapping[str, object]


class RasterClipError(ValueError):
    """Raised when the complete study geometry cannot be clipped from a raster."""


class RasterMetadataError(ValueError):
    """Raised when required spatial metadata is absent or unusable."""


def clip_band_to_geometry(
    dataset: DatasetReader,
    *,
    geometries: Sequence[GeoJSONGeometry],
    geometry_crs: CRS,
    band: int = 1,
) -> ContinuousRaster:
    """Reproject and crop one band to the supplied complete study geometry."""

    if dataset.crs is None:
        raise RasterMetadataError("raster crs is required")
    if not geometries:
        raise RasterClipError("at least one clipping geometry is required")

    transformed = [
        transform_geom(geometry_crs, dataset.crs, geometry, precision=15) for geometry in geometries
    ]
    try:
        clipped, out_transform = mask(
            dataset,
            transformed,
            indexes=[band],
            crop=True,
            filled=False,
        )
    except ValueError as error:
        raise RasterClipError("clipping geometry does not overlap raster") from error

    masked_values = np.ma.asarray(clipped)[0]
    values = np.asarray(masked_values.data, dtype=np.float32).copy()
    valid_mask = ~np.ma.getmaskarray(masked_values) & np.isfinite(values)
    source_nodata = dataset.nodata
    nodata = (
        float(source_nodata)
        if source_nodata is not None and math.isfinite(source_nodata)
        else OUTPUT_NODATA
    )
    values[~valid_mask] = nodata
    return ContinuousRaster(
        values=values,
        valid_mask=valid_mask,
        grid=RasterGrid(
            crs=dataset.crs,
            transform=out_transform,
            height=values.shape[0],
            width=values.shape[1],
        ),
        nodata=nodata,
    )
