"""Deterministic, path-internal rendering for allow-listed analysis rasters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from hennongxi_contracts import TileArtifactType, TileLegendEntry
from rasterio.errors import RasterioError  # type: ignore[import-untyped]
from rio_tiler.errors import RioTilerError, TileOutsideBounds
from rio_tiler.io import Reader

type RGBA = tuple[int, int, int, int]
type IntervalColorMap = tuple[tuple[tuple[float, float], RGBA], ...]
type DiscreteColorMap = dict[int, RGBA]
type ColorMap = IntervalColorMap | DiscreteColorMap

_NDVI_COLORMAP: Final[IntervalColorMap] = (
    ((-1.000001, -0.2), (216, 179, 101, 255)),
    ((-0.2, 0.0), (246, 232, 195, 255)),
    ((0.0, 0.2), (217, 240, 211, 255)),
    ((0.2, 0.4), (166, 219, 160, 255)),
    ((0.4, 0.6), (90, 174, 97, 255)),
    ((0.6, 1.000001), (0, 104, 55, 255)),
)
_DIFFERENCE_COLORMAP: Final[IntervalColorMap] = (
    ((-2.000001, -0.25), (178, 24, 43, 255)),
    ((-0.25, -0.1), (239, 138, 98, 255)),
    ((-0.1, 0.1), (247, 247, 247, 255)),
    ((0.1, 0.25), (166, 219, 160, 255)),
    ((0.25, 2.000001), (27, 120, 55, 255)),
)
_CLASSIFICATION_COLORMAP: Final[DiscreteColorMap] = {
    -1: (178, 24, 43, 255),
    0: (247, 247, 247, 255),
    1: (27, 120, 55, 255),
}


@dataclass(frozen=True, slots=True)
class TileStyle:
    units: str
    legend: tuple[TileLegendEntry, ...]
    colormap: ColorMap


_NDVI_STYLE: Final[TileStyle] = TileStyle(
    units="NDVI",
    legend=(
        TileLegendEntry(value=-1.0, label="低植被指数", color="#D8B365"),
        TileLegendEntry(value=-0.2, label="较低", color="#F6E8C3"),
        TileLegendEntry(value=0.0, label="零值", color="#D9F0D3"),
        TileLegendEntry(value=0.2, label="较高", color="#A6DBA0"),
        TileLegendEntry(value=0.4, label="高", color="#5AAE61"),
        TileLegendEntry(value=0.6, label="很高", color="#006837"),
        TileLegendEntry(value=1.0, label="上限", color="#006837"),
    ),
    colormap=_NDVI_COLORMAP,
)
_DIFFERENCE_STYLE: Final[TileStyle] = TileStyle(
    units="NDVI 变化值",
    legend=(
        TileLegendEntry(value=-2.0, label="显著下降", color="#B2182B"),
        TileLegendEntry(value=-0.25, label="下降", color="#EF8A62"),
        TileLegendEntry(value=-0.1, label="稳定下界", color="#F7F7F7"),
        TileLegendEntry(value=0.1, label="稳定上界", color="#A6DBA0"),
        TileLegendEntry(value=0.25, label="上升", color="#1B7837"),
        TileLegendEntry(value=2.0, label="显著上升", color="#1B7837"),
    ),
    colormap=_DIFFERENCE_COLORMAP,
)
_CLASSIFICATION_STYLE: Final[TileStyle] = TileStyle(
    units="变化类别",
    legend=(
        TileLegendEntry(value=-1.0, label="下降", color="#B2182B"),
        TileLegendEntry(value=0.0, label="稳定", color="#F7F7F7"),
        TileLegendEntry(value=1.0, label="上升", color="#1B7837"),
    ),
    colormap=_CLASSIFICATION_COLORMAP,
)
_STYLES: Final[dict[TileArtifactType, TileStyle]] = {
    TileArtifactType.NDVI_BEFORE: _NDVI_STYLE,
    TileArtifactType.NDVI_AFTER: _NDVI_STYLE,
    TileArtifactType.NDVI_DIFFERENCE: _DIFFERENCE_STYLE,
    TileArtifactType.CHANGE_CLASSIFICATION: _CLASSIFICATION_STYLE,
}


class TileCoordinateError(ValueError):
    """Raised when XYZ values cannot identify a Web Mercator tile."""


class TileSourceError(RuntimeError):
    """Raised when a fixed local source cannot be rendered safely."""


class TileOutsideSourceError(TileSourceError):
    """Raised when a valid XYZ tile does not intersect the source raster."""


def style_for(artifact_type: TileArtifactType) -> TileStyle:
    """Return the immutable visualization policy for an allow-listed artifact."""

    return _STYLES[artifact_type]


class TileRenderer:
    """Read one fixed local raster through Rio-Tiler and encode one RGBA PNG."""

    def render(
        self,
        path: Path,
        artifact_type: TileArtifactType,
        *,
        z: int,
        x: int,
        y: int,
    ) -> bytes:
        _validate_coordinate(z=z, x=x, y=y)
        if path.is_symlink() or not path.is_file():
            raise TileSourceError("tile source must be a regular file")

        try:
            with Reader(str(path)) as source:
                image = source.tile(
                    x,
                    y,
                    z,
                    tilesize=256,
                    indexes=1,
                    resampling_method="nearest",
                )
            payload = image.render(img_format="PNG", colormap=style_for(artifact_type).colormap)
            if not isinstance(payload, bytes):
                raise TileSourceError("tile renderer returned an invalid payload")
            return payload
        except TileOutsideBounds as error:
            raise TileOutsideSourceError("tile does not intersect the published source") from error
        except (OSError, ValueError, RasterioError, RioTilerError) as error:
            raise TileSourceError("published tile source cannot be rendered") from error


def _validate_coordinate(*, z: int, x: int, y: int) -> None:
    if z < 0 or z > 24 or x < 0 or y < 0 or x >= 2**z or y >= 2**z:
        raise TileCoordinateError("tile coordinate is outside the Web Mercator grid")
