from __future__ import annotations

from io import BytesIO
from pathlib import Path

import morecantile
import numpy as np
import pytest
import rasterio
from hennongxi_contracts import TileArtifactType
from hennongxi_publisher_agent.tiles import (
    TileCoordinateError,
    TileRenderer,
    TileSourceError,
    style_for,
)
from PIL import Image
from rasterio.transform import from_bounds


def _write_tile_aligned_fixture(path: Path) -> tuple[int, int, int]:
    tms = morecantile.tms.get("WebMercatorQuad")
    tile = tms.tile(110.3, 31.5, 8)
    bounds = tms.bounds(tile)
    values = np.empty((64, 64), dtype="float32")
    values[:, :32] = -0.5
    values[:, 32:] = 0.7
    values[:8, :] = -9999.0
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=64,
        height=64,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_bounds(*bounds, width=64, height=64),
        nodata=-9999.0,
    ) as dataset:
        dataset.write(values, 1)
    return tile.x, tile.y, tile.z


def test_ndvi_tile_is_a_stably_colored_transparent_256_png(tmp_path: Path) -> None:
    path = tmp_path / "ndvi_before.tif"
    x, y, z = _write_tile_aligned_fixture(path)

    payload = TileRenderer().render(path, TileArtifactType.NDVI_BEFORE, z=z, x=x, y=y)

    assert payload.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(BytesIO(payload)) as image:
        rgba = np.asarray(image.convert("RGBA"))
    assert rgba.shape == (256, 256, 4)
    assert tuple(rgba[16, 64]) == (0, 0, 0, 0)
    assert tuple(rgba[128, 64]) == (216, 179, 101, 255)
    assert tuple(rgba[128, 192]) == (0, 104, 55, 255)


def test_each_allowlisted_artifact_has_an_explicit_stable_style() -> None:
    for artifact_type in TileArtifactType:
        style = style_for(artifact_type)
        assert len(style.legend) >= 2
        assert style.units
        assert style.colormap

    assert style_for(TileArtifactType.NDVI_BEFORE) == style_for(TileArtifactType.NDVI_AFTER)
    assert style_for(TileArtifactType.NDVI_DIFFERENCE) != style_for(
        TileArtifactType.CHANGE_CLASSIFICATION
    )


@pytest.mark.parametrize(
    ("z", "x", "y"),
    [(-1, 0, 0), (25, 0, 0), (2, 4, 0), (2, 0, 4), (1, -1, 0)],
)
def test_renderer_rejects_coordinates_outside_the_slippy_map_grid(
    tmp_path: Path,
    z: int,
    x: int,
    y: int,
) -> None:
    path = tmp_path / "ndvi_before.tif"
    _write_tile_aligned_fixture(path)

    with pytest.raises(TileCoordinateError, match="tile coordinate"):
        TileRenderer().render(path, TileArtifactType.NDVI_BEFORE, z=z, x=x, y=y)


def test_renderer_rejects_missing_and_symlinked_sources(tmp_path: Path) -> None:
    missing = tmp_path / "missing.tif"
    with pytest.raises(TileSourceError, match="regular file"):
        TileRenderer().render(missing, TileArtifactType.NDVI_BEFORE, z=8, x=206, y=104)

    source = tmp_path / "source.tif"
    _write_tile_aligned_fixture(source)
    link = tmp_path / "linked.tif"
    link.symlink_to(source)
    with pytest.raises(TileSourceError, match="regular file"):
        TileRenderer().render(link, TileArtifactType.NDVI_BEFORE, z=8, x=206, y=104)
