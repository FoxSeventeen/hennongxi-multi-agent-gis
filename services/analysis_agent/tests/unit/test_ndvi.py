from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pytest
from affine import Affine
from hennongxi_analysis_agent.ndvi import calculate_difference, calculate_ndvi
from hennongxi_analysis_agent.raster import ContinuousRaster, GridMismatchError, RasterGrid
from numpy.typing import NDArray
from rasterio.crs import CRS
from rasterio.transform import from_origin


def _grid(
    *,
    crs: CRS | None = None,
    transform: Affine | None = None,
    height: int = 3,
    width: int = 3,
) -> RasterGrid:
    return RasterGrid(
        crs=crs or CRS.from_epsg(32649),
        transform=transform or from_origin(400_000, 3_500_000, 10, 10),
        height=height,
        width=width,
    )


def _raster(
    values: list[list[float]],
    *,
    valid_mask: NDArray[np.bool_] | None = None,
    grid: RasterGrid | None = None,
) -> ContinuousRaster:
    array = np.asarray(values, dtype=np.float32)
    return ContinuousRaster(
        values=array,
        valid_mask=np.ones(array.shape, dtype=np.bool_) if valid_mask is None else valid_mask,
        grid=grid or _grid(height=array.shape[0], width=array.shape[1]),
    )


def test_ndvi_calculates_float_values_and_masks_every_invalid_source() -> None:
    red_valid = np.ones((3, 3), dtype=np.bool_)
    red_valid[1, 2] = False
    red = _raster(
        [
            [1, 1, -1],
            [np.nan, 1, 1],
            [2, 0, 1],
        ],
        valid_mask=red_valid,
    )
    nir = _raster(
        [
            [3, -1, 1],
            [2, np.inf, 1],
            [2, 4, 0],
        ]
    )

    result = calculate_ndvi(nir=nir, red=red)

    np.testing.assert_array_equal(
        result.valid_mask,
        [
            [True, False, False],
            [False, False, False],
            [True, True, True],
        ],
    )
    np.testing.assert_allclose(result.values[result.valid_mask], [0.5, 0.0, 1.0, -1.0])
    np.testing.assert_array_equal(result.values[~result.valid_mask], result.nodata)
    assert result.values.dtype == np.float32
    assert result.grid == nir.grid


def test_difference_subtracts_before_from_after_and_propagates_masks() -> None:
    before_valid = np.asarray([[True, True], [False, True]], dtype=np.bool_)
    after_valid = np.asarray([[True, False], [True, True]], dtype=np.bool_)
    grid = _grid(height=2, width=2)
    before = _raster([[0.2, 0.1], [0.0, np.inf]], valid_mask=before_valid, grid=grid)
    after = _raster([[0.4, 0.8], [0.5, 0.3]], valid_mask=after_valid, grid=grid)

    result = calculate_difference(after=after, before=before)

    np.testing.assert_array_equal(result.valid_mask, [[True, False], [False, False]])
    np.testing.assert_allclose(result.values[result.valid_mask], [0.2])
    np.testing.assert_array_equal(result.values[~result.valid_mask], result.nodata)
    assert result.grid == grid


@pytest.mark.parametrize(
    ("changed_raster", "expected_field"),
    [
        (
            lambda: _raster(
                [[1, 1, 1], [1, 1, 1], [1, 1, 1]],
                grid=_grid(crs=CRS.from_epsg(3857)),
            ),
            "crs",
        ),
        (
            lambda: _raster(
                [[1, 1, 1], [1, 1, 1], [1, 1, 1]],
                grid=_grid(transform=from_origin(400_010, 3_500_000, 10, 10)),
            ),
            "transform",
        ),
        (
            lambda: _raster([[1, 1, 1], [1, 1, 1]], grid=_grid(height=2)),
            "shape",
        ),
    ],
)
def test_ndvi_rejects_grid_mismatches(
    changed_raster: Callable[[], ContinuousRaster],
    expected_field: str,
) -> None:
    reference = _raster([[1, 1, 1], [1, 1, 1], [1, 1, 1]])

    with pytest.raises(GridMismatchError, match=expected_field):
        calculate_ndvi(nir=reference, red=changed_raster())
