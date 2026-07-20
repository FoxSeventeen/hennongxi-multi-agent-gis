from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import rasterio
from hennongxi_contracts import (
    AnalysisRunResult,
    ArtifactType,
    DataPrepareResult,
    LogicalDatasetId,
)
from hennongxi_observability import CORRELATION_ID_HEADER

DATA_AGENT_BASE_URL = os.environ.get("DATA_AGENT_BASE_URL")
ANALYSIS_AGENT_BASE_URL = os.environ.get("ANALYSIS_AGENT_BASE_URL")
pytestmark = pytest.mark.skipif(
    DATA_AGENT_BASE_URL is None or ANALYSIS_AGENT_BASE_URL is None,
    reason="Master-style Analysis Agent network integration test",
)

TASK_ID = UUID("12121212-1212-4212-8212-121212121212")
CORRELATION_ID = UUID("34343434-3434-4434-8434-343434343434")
IDEMPOTENCY_KEY = UUID("56565656-5656-4656-8656-565656565656")
ARTIFACT_ROOT = Path(os.environ.get("ARTIFACT_ROOT", "/data/outputs"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_master_style_http_call_generates_and_reuses_verified_artifacts() -> None:
    assert DATA_AGENT_BASE_URL is not None
    assert ANALYSIS_AGENT_BASE_URL is not None
    correlation_headers = {CORRELATION_ID_HEADER: str(CORRELATION_ID)}
    with httpx.Client(timeout=300) as client:
        prepared_response = client.post(
            f"{DATA_AGENT_BASE_URL}/internal/v1/data/prepare",
            json={
                "task_id": str(TASK_ID),
                "step_id": "prepare_data",
                "attempt": 1,
                "correlation_id": str(CORRELATION_ID),
                "dataset_ids": [dataset_id.value for dataset_id in LogicalDatasetId],
            },
            headers=correlation_headers,
        )
        prepared_response.raise_for_status()
        prepared = DataPrepareResult.model_validate(prepared_response.json())
        command = {
            "task_id": str(TASK_ID),
            "step_id": "analyze_ndvi_change",
            "attempt": 1,
            "correlation_id": str(CORRELATION_ID),
            "inputs": [asset.model_dump(mode="json") for asset in prepared.assets],
        }
        analysis_headers = {
            **correlation_headers,
            "Idempotency-Key": str(IDEMPOTENCY_KEY),
        }
        first_response = client.post(
            f"{ANALYSIS_AGENT_BASE_URL}/internal/v1/analysis/run",
            json=command,
            headers=analysis_headers,
        )
        repeated_response = client.post(
            f"{ANALYSIS_AGENT_BASE_URL}/internal/v1/analysis/run",
            json=command,
            headers=analysis_headers,
        )

    first_response.raise_for_status()
    repeated_response.raise_for_status()
    first = AnalysisRunResult.model_validate(first_response.json())
    repeated = AnalysisRunResult.model_validate(repeated_response.json())
    assert repeated == first
    assert first_response.headers[CORRELATION_ID_HEADER] == str(CORRELATION_ID)
    assert "/data/" not in first_response.text

    final_directory = ARTIFACT_ROOT / str(TASK_ID) / "attempt-1" / "analysis"
    by_type = {artifact.artifact_type: artifact for artifact in first.artifacts}
    for artifact_type in (
        ArtifactType.NDVI_BEFORE,
        ArtifactType.NDVI_AFTER,
        ArtifactType.NDVI_DIFFERENCE,
        ArtifactType.CHANGE_CLASSIFICATION,
    ):
        path = final_directory / f"{artifact_type.value.lower()}.tif"
        with rasterio.open(path) as dataset:
            assert dataset.crs is not None
            assert dataset.width > 0
            assert dataset.height > 0
            assert dataset.nodata is not None
            assert tuple(dataset.bounds) == prepared.assets[1].grid.bounds
        assert path.stat().st_size == by_type[artifact_type].byte_size
        assert _sha256(path) == by_type[artifact_type].checksum_sha256

    statistics_path = final_directory / "area_statistics.json"
    statistics = json.loads(statistics_path.read_text(encoding="utf-8"))
    assert statistics["valid_pixel_count"] > 0
    assert statistics["threshold"] == 0.1
    assert _sha256(statistics_path) == by_type[ArtifactType.AREA_STATISTICS].checksum_sha256
