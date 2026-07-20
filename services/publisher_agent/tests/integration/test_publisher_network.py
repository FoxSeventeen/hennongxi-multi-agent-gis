from __future__ import annotations

import io
import json
import os
from pathlib import Path
from uuid import UUID

import httpx
import numpy as np
import pytest
from hennongxi_contracts import (
    AnalysisRunResult,
    PublisherPublishCommand,
    PublisherPublishResult,
    QualityEvaluateResult,
    TileArtifactType,
)
from PIL import Image
from pypdf import PdfReader
from rio_tiler.io import Reader

PUBLISHER_AGENT_BASE_URL = os.environ.get("PUBLISHER_AGENT_BASE_URL")
ARTIFACT_ROOT_VALUE = os.environ.get("ARTIFACT_ROOT")
QUALITY_REPORT_ROOT_VALUE = os.environ.get("QUALITY_REPORT_ROOT")
pytestmark = pytest.mark.skipif(
    any(
        value is None
        for value in (
            PUBLISHER_AGENT_BASE_URL,
            ARTIFACT_ROOT_VALUE,
            QUALITY_REPORT_ROOT_VALUE,
        )
    ),
    reason="Publisher network test requires its URL and both mounted receipt volumes",
)

TASK_ID = UUID("68686868-6868-4868-8868-686868686868")
PUBLISH_IDEMPOTENCY_KEY = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
ARTIFACT_ROOT = Path(ARTIFACT_ROOT_VALUE or "/data/outputs")
QUALITY_REPORT_ROOT = Path(QUALITY_REPORT_ROOT_VALUE or "/data/quality-reports")
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


def test_real_receipts_publish_complete_browser_layer_metadata() -> None:
    assert PUBLISHER_AGENT_BASE_URL is not None
    analysis_payload = json.loads(
        (
            ARTIFACT_ROOT / str(TASK_ID) / "attempt-1" / "analysis" / "analysis_result.json"
        ).read_text(encoding="utf-8")
    )
    quality_payload = json.loads(
        (
            QUALITY_REPORT_ROOT / str(TASK_ID) / "attempt-1" / "quality" / "quality_result.json"
        ).read_text(encoding="utf-8")
    )
    analysis = AnalysisRunResult.model_validate(analysis_payload["result"])
    quality = QualityEvaluateResult.model_validate(quality_payload["result"])
    command = PublisherPublishCommand(
        task_id=TASK_ID,
        step_id="publish_results",
        attempt=1,
        correlation_id=analysis.correlation_id,
        artifacts=(*analysis.artifacts, quality.artifact),
        quality=quality.metrics,
    )

    response = httpx.post(
        f"{PUBLISHER_AGENT_BASE_URL}/internal/v1/publisher/publish",
        json=command.model_dump(mode="json"),
        headers={
            "X-Correlation-ID": str(command.correlation_id),
            "Idempotency-Key": str(PUBLISH_IDEMPOTENCY_KEY),
        },
        timeout=30,
    )

    response.raise_for_status()
    result = PublisherPublishResult.model_validate(response.json())
    assert result.report.artifact_type.value == "PDF_REPORT"
    assert len(result.resources) == 5
    resources = {
        resource.tile_metadata.artifact_type: resource
        for resource in result.resources
        if resource.tile_metadata is not None
    }
    assert set(resources) == set(TileArtifactType)
    assert resources[TileArtifactType.NDVI_BEFORE].tile_metadata.start_date.isoformat() == (
        "2019-08-19"
    )
    assert resources[TileArtifactType.NDVI_AFTER].tile_metadata.end_date.isoformat() == (
        "2024-08-12"
    )
    difference = resources[TileArtifactType.NDVI_DIFFERENCE].tile_metadata
    assert difference.start_date.isoformat() == "2019-08-19"
    assert difference.end_date.isoformat() == "2024-08-12"
    assert difference.bounds_wgs84 == pytest.approx(
        (110.107791, 31.044477, 110.538461, 31.468514),
        abs=1e-5,
    )
    assert "修改" in difference.attribution
    assert "Copernicus" in difference.attribution
    assert difference.legend

    report_resource = next(
        resource for resource in result.resources if resource.download_path is not None
    )
    assert report_resource.artifact_id == result.report.artifact_id
    download = httpx.get(
        f"{PUBLISHER_AGENT_BASE_URL}{report_resource.download_path}",
        timeout=30,
    )
    download.raise_for_status()
    assert download.headers["content-type"] == "application/pdf"
    assert download.headers["content-disposition"].startswith("attachment;")
    report_text = "\n".join(
        page.extract_text() or "" for page in PdfReader(io.BytesIO(download.content)).pages
    )
    assert str(TASK_ID) in report_text
    assert "2019-08-19" in report_text
    assert "2024-08-12" in report_text
    assert "结论 PASS" in report_text
