from __future__ import annotations

import io
import os
from pathlib import Path
from uuid import UUID

import httpx
import numpy as np
import pytest
from PIL import Image
from rio_tiler.io import Reader

PUBLISHER_AGENT_BASE_URL = os.environ.get("PUBLISHER_AGENT_BASE_URL")
ARTIFACT_ROOT_VALUE = os.environ.get("ARTIFACT_ROOT")
pytestmark = pytest.mark.skipif(
    PUBLISHER_AGENT_BASE_URL is None or ARTIFACT_ROOT_VALUE is None,
    reason="Publisher network test requires its URL and the mounted artifact volume",
)

TASK_ID = UUID("68686868-6868-4868-8868-686868686868")
ARTIFACT_ROOT = Path(ARTIFACT_ROOT_VALUE or "/data/outputs")
NDVI_COLORS = {
    (216, 179, 101),
    (246, 232, 195),
    (217, 240, 211),
    (166, 219, 160),
    (90, 174, 97),
    (0, 104, 55),
}


def test_quality_passed_real_raster_is_served_as_a_styled_transparent_png() -> None:
    assert PUBLISHER_AGENT_BASE_URL is not None
    source_path = ARTIFACT_ROOT / str(TASK_ID) / "attempt-1" / "analysis" / "ndvi_before.tif"
    with Reader(str(source_path)) as source:
        bounds = source.get_geographic_bounds("EPSG:4326")
        z = source.minzoom
        tile = source.tms.tile(
            (bounds[0] + bounds[2]) / 2,
            (bounds[1] + bounds[3]) / 2,
            z,
        )

    response = httpx.get(
        (
            f"{PUBLISHER_AGENT_BASE_URL}/api/v1/tiles/{TASK_ID}/"
            f"NDVI_BEFORE/{z}/{tile.x}/{tile.y}.png"
        ),
        timeout=30,
    )

    response.raise_for_status()
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "public, max-age=60, must-revalidate"
    assert response.headers["etag"].startswith('"')
    image = Image.open(io.BytesIO(response.content)).convert("RGBA")
    pixels = np.asarray(image)
    alpha = pixels[:, :, 3]
    opaque_colors = {
        tuple(int(channel) for channel in color)
        for color in np.unique(pixels[alpha > 0, :3], axis=0)
    }
    assert image.size == (256, 256)
    assert np.any(alpha == 0)
    assert np.any(alpha == 255)
    assert 2 <= len(opaque_colors) <= len(NDVI_COLORS)
    assert opaque_colors <= NDVI_COLORS
