from __future__ import annotations

import numpy as np
import pytest
from affine import Affine
from hennongxi_analysis_agent.change import (
    CHANGE_NODATA,
    AreaCalculationError,
    ClassifiedRaster,
    classify_change,
    summarize_class_areas,
)
from hennongxi_analysis_agent.raster import ContinuousRaster, RasterGrid
from numpy.typing import NDArray
from rasterio.crs import CRS


def _continuous(
    values: list[list[float]],
    *,
    valid_mask: NDArray[np.bool_] | None = None,
    crs: CRS | None = None,
    transform: Affine | None = None,
) -> ContinuousRaster:
    array = np.asarray(values, dtype=np.float32)
    return ContinuousRaster(
        values=array,
        valid_mask=np.ones(array.shape, dtype=np.bool_) if valid_mask is None else valid_mask,
        grid=RasterGrid(
            crs=crs or CRS.from_epsg(32649),
            transform=transform or Affine(10, 0, 400_000, 0, -10, 3_500_000),
            height=array.shape[0],
            width=array.shape[1],
        ),
    )


def test_change_classification_is_deterministic_at_threshold_boundaries() -> None:
    valid_mask = np.ones((3, 3), dtype=np.bool_)
    valid_mask[2, 2] = False
    difference = _continuous(
        [
            [-0.2, -0.1, -0.0999],
            [0.0, 0.0999, 0.1],
            [0.2, np.nan, 0.5],
        ],
        valid_mask=valid_mask,
    )

    result = classify_change(difference, threshold=0.1)

    np.testing.assert_array_equal(
        result.values,
        [
            [-1, -1, 0],
            [0, 0, 1],
            [1, CHANGE_NODATA, CHANGE_NODATA],
        ],
    )
    np.testing.assert_array_equal(
        result.valid_mask,
        [
            [True, True, True],
            [True, True, True],
            [True, False, False],
        ],
    )
    assert result.values.dtype == np.int8
    assert result.grid == difference.grid


@pytest.mark.parametrize("threshold", [0.0, -0.1, np.nan, np.inf])
def test_change_classification_requires_a_positive_finite_threshold(threshold: float) -> None:
    with pytest.raises(ValueError, match="threshold"):
        classify_change(_continuous([[0.0]]), threshold=threshold)


def test_area_statistics_use_affine_pixel_area_and_sum_every_valid_class() -> None:
    grid = RasterGrid(
        crs=CRS.from_epsg(32649),
        transform=Affine(10, 2, 400_000, 1, -10, 3_500_000),
        height=2,
        width=3,
    )
    classified = ClassifiedRaster(
        values=np.asarray([[-1, 0, 1], [-1, 1, CHANGE_NODATA]], dtype=np.int8),
        valid_mask=np.asarray([[True, True, True], [True, True, False]], dtype=np.bool_),
        grid=grid,
        threshold=0.1,
    )

    result = summarize_class_areas(classified)

    assert result.pixel_area_square_metres == pytest.approx(102.0, abs=1e-9)
    assert result.valid_pixel_count == 5
    assert result.decrease_pixel_count == 2
    assert result.stable_pixel_count == 1
    assert result.increase_pixel_count == 2
    assert result.decrease_area_square_metres == pytest.approx(204.0, abs=1e-9)
    assert result.stable_area_square_metres == pytest.approx(102.0, abs=1e-9)
    assert result.increase_area_square_metres == pytest.approx(204.0, abs=1e-9)
    assert result.valid_area_square_metres == pytest.approx(510.0, abs=1e-9)
    assert result.valid_area_square_metres == pytest.approx(
        result.decrease_area_square_metres
        + result.stable_area_square_metres
        + result.increase_area_square_metres,
        abs=1e-9,
    )
    assert result.threshold == 0.1


def test_area_statistics_report_the_threshold_used_to_classify() -> None:
    classified = classify_change(_continuous([[-0.3, 0.0, 0.3]]), threshold=0.2)

    result = summarize_class_areas(classified)

    assert result.threshold == 0.2


def test_area_statistics_reject_geographic_crs() -> None:
    geographic = ClassifiedRaster(
        values=np.asarray([[0]], dtype=np.int8),
        valid_mask=np.asarray([[True]], dtype=np.bool_),
        grid=RasterGrid(
            crs=CRS.from_epsg(4326),
            transform=Affine(0.01, 0, 110, 0, -0.01, 31),
            height=1,
            width=1,
        ),
        threshold=0.1,
    )

    with pytest.raises(AreaCalculationError, match="projected"):
        summarize_class_areas(geographic)


def test_area_statistics_reject_degenerate_pixel_transform() -> None:
    degenerate = ClassifiedRaster(
        values=np.asarray([[0]], dtype=np.int8),
        valid_mask=np.asarray([[True]], dtype=np.bool_),
        grid=RasterGrid(
            crs=CRS.from_epsg(32649),
            transform=Affine(10, 0, 400_000, 0, 0, 3_500_000),
            height=1,
            width=1,
        ),
        threshold=0.1,
    )

    with pytest.raises(AreaCalculationError, match="pixel area"):
        summarize_class_areas(degenerate)


def test_classified_raster_rejects_unknown_valid_class_code() -> None:
    with pytest.raises(ValueError, match="class code"):
        ClassifiedRaster(
            values=np.asarray([[2]], dtype=np.int8),
            valid_mask=np.asarray([[True]], dtype=np.bool_),
            grid=RasterGrid(
                crs=CRS.from_epsg(32649),
                transform=Affine(10, 0, 400_000, 0, -10, 3_500_000),
                height=1,
                width=1,
            ),
            threshold=0.1,
        )
