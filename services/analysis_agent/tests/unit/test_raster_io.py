from __future__ import annotations

import numpy as np
import pytest
from hennongxi_analysis_agent.raster_io import (
    RasterClipError,
    RasterMetadataError,
    clip_band_to_geometry,
)
from rasterio.crs import CRS
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from rasterio.warp import transform_geom


def _polygon(
    left: float,
    bottom: float,
    right: float,
    top: float,
    *,
    hole: tuple[float, float, float, float] | None = None,
) -> dict[str, object]:
    rings: list[list[list[float]]] = [
        [
            [left, bottom],
            [right, bottom],
            [right, top],
            [left, top],
            [left, bottom],
        ]
    ]
    if hole is not None:
        hole_left, hole_bottom, hole_right, hole_top = hole
        rings.append(
            [
                [hole_left, hole_bottom],
                [hole_left, hole_top],
                [hole_right, hole_top],
                [hole_right, hole_bottom],
                [hole_left, hole_bottom],
            ]
        )
    return {"type": "Polygon", "coordinates": rings}


def test_clip_band_reprojects_complete_geometry_and_preserves_grid_metadata() -> None:
    raster_crs = CRS.from_epsg(32649)
    boundary_crs = CRS.from_epsg(4326)
    projected_boundary = _polygon(
        400_010.1,
        3_499_970.1,
        400_039.9,
        3_499_999.9,
        hole=(400_020.1, 3_499_980.1, 400_029.9, 3_499_989.9),
    )
    boundary = transform_geom(raster_crs, boundary_crs, projected_boundary, precision=15)
    values = np.asarray(
        [
            [1, 2, 3, 4],
            [5, 6, 7, 8],
            [9, -9999, 11, 12],
            [13, 14, 15, 16],
        ],
        dtype=np.float32,
    )

    with MemoryFile() as memory_file:
        with memory_file.open(
            driver="GTiff",
            width=4,
            height=4,
            count=1,
            dtype="float32",
            crs=raster_crs,
            transform=from_origin(400_000, 3_500_000, 10, 10),
            nodata=-9999,
        ) as dataset:
            dataset.write(values, 1)
            result = clip_band_to_geometry(
                dataset,
                geometries=(boundary,),
                geometry_crs=boundary_crs,
            )

    assert result.grid.crs == raster_crs
    assert result.grid.transform == from_origin(400_010, 3_500_000, 10, 10)
    assert result.grid.shape == (3, 3)
    assert result.grid.bounds == (400_010.0, 3_499_970.0, 400_040.0, 3_500_000.0)
    assert result.nodata == -9999
    np.testing.assert_array_equal(
        result.valid_mask,
        [
            [True, True, True],
            [True, False, True],
            [False, True, True],
        ],
    )
    np.testing.assert_array_equal(result.values[result.valid_mask], [2, 3, 4, 6, 8, 11, 12])
    np.testing.assert_array_equal(result.values[~result.valid_mask], result.nodata)


def test_clip_band_rejects_a_non_overlapping_geometry() -> None:
    with MemoryFile() as memory_file:
        with memory_file.open(
            driver="GTiff",
            width=2,
            height=2,
            count=1,
            dtype="float32",
            crs="EPSG:32649",
            transform=from_origin(0, 20, 10, 10),
            nodata=-9999,
        ) as dataset:
            dataset.write(np.ones((2, 2), dtype=np.float32), 1)

            with pytest.raises(RasterClipError, match="overlap"):
                clip_band_to_geometry(
                    dataset,
                    geometries=(_polygon(100, 100, 110, 110),),
                    geometry_crs=CRS.from_epsg(32649),
                )


def test_clip_band_requires_raster_crs_metadata() -> None:
    with MemoryFile() as memory_file:
        with memory_file.open(
            driver="GTiff",
            width=2,
            height=2,
            count=1,
            dtype="float32",
            transform=from_origin(0, 20, 10, 10),
            nodata=-9999,
        ) as dataset:
            dataset.write(np.ones((2, 2), dtype=np.float32), 1)

            with pytest.raises(RasterMetadataError, match="crs"):
                clip_band_to_geometry(
                    dataset,
                    geometries=(_polygon(0, 0, 20, 20),),
                    geometry_crs=CRS.from_epsg(32649),
                )


def test_clip_band_requires_the_complete_geometry() -> None:
    with MemoryFile() as memory_file:
        with memory_file.open(
            driver="GTiff",
            width=2,
            height=2,
            count=1,
            dtype="float32",
            crs="EPSG:32649",
            transform=from_origin(0, 20, 10, 10),
        ) as dataset:
            dataset.write(np.ones((2, 2), dtype=np.float32), 1)

            with pytest.raises(RasterClipError, match="at least one"):
                clip_band_to_geometry(
                    dataset,
                    geometries=(),
                    geometry_crs=CRS.from_epsg(32649),
                )


def test_clip_band_supplies_a_finite_output_nodata_when_source_has_none() -> None:
    with MemoryFile() as memory_file:
        with memory_file.open(
            driver="GTiff",
            width=2,
            height=2,
            count=1,
            dtype="float32",
            crs="EPSG:32649",
            transform=from_origin(0, 20, 10, 10),
        ) as dataset:
            dataset.write(np.ones((2, 2), dtype=np.float32), 1)
            result = clip_band_to_geometry(
                dataset,
                geometries=(_polygon(0, 0, 20, 20),),
                geometry_crs=CRS.from_epsg(32649),
            )

    assert result.nodata == -9999
    assert result.valid_mask.all()
