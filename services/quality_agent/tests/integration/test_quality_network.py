from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from hennongxi_contracts import (
    AnalysisRunResult,
    DataPrepareResult,
    LogicalDatasetId,
    QualityConclusion,
    QualityEvaluateResult,
)
from hennongxi_observability import CORRELATION_ID_HEADER

DATA_AGENT_BASE_URL = os.environ.get("DATA_AGENT_BASE_URL")
ANALYSIS_AGENT_BASE_URL = os.environ.get("ANALYSIS_AGENT_BASE_URL")
QUALITY_AGENT_BASE_URL = os.environ.get("QUALITY_AGENT_BASE_URL")
QUALITY_REPORT_ROOT_VALUE = os.environ.get("QUALITY_REPORT_ROOT")
pytestmark = pytest.mark.skipif(
    any(
        value is None
        for value in (
            DATA_AGENT_BASE_URL,
            ANALYSIS_AGENT_BASE_URL,
            QUALITY_AGENT_BASE_URL,
            QUALITY_REPORT_ROOT_VALUE,
        )
    ),
    reason="Quality network test requires three Agent URLs and the mounted report volume",
)

TASK_ID = UUID("68686868-6868-4868-8868-686868686868")
CORRELATION_ID = UUID("79797979-7979-4979-8979-797979797979")
ANALYSIS_KEY = UUID("8a8a8a8a-8a8a-4a8a-8a8a-8a8a8a8a8a8a")
QUALITY_KEY = UUID("91919191-9191-4191-8191-919191919191")
QUALITY_REPORT_ROOT = Path(QUALITY_REPORT_ROOT_VALUE or "/data/quality-reports")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_data_analysis_quality_private_network_produces_a_verified_report() -> None:
    assert DATA_AGENT_BASE_URL is not None
    assert ANALYSIS_AGENT_BASE_URL is not None
    assert QUALITY_AGENT_BASE_URL is not None
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

        analysis_response = client.post(
            f"{ANALYSIS_AGENT_BASE_URL}/internal/v1/analysis/run",
            json={
                "task_id": str(TASK_ID),
                "step_id": "analyze_ndvi_change",
                "attempt": 1,
                "correlation_id": str(CORRELATION_ID),
                "inputs": [asset.model_dump(mode="json") for asset in prepared.assets],
            },
            headers={**correlation_headers, "Idempotency-Key": str(ANALYSIS_KEY)},
        )
        analysis_response.raise_for_status()
        analysis = AnalysisRunResult.model_validate(analysis_response.json())

        quality_command = {
            "task_id": str(TASK_ID),
            "step_id": "evaluate_quality",
            "attempt": 1,
            "correlation_id": str(CORRELATION_ID),
            "artifacts": [artifact.model_dump(mode="json") for artifact in analysis.artifacts],
            "analysis_elapsed_ms": analysis.elapsed_ms,
        }
        quality_headers = {**correlation_headers, "Idempotency-Key": str(QUALITY_KEY)}
        first_response = client.post(
            f"{QUALITY_AGENT_BASE_URL}/internal/v1/quality/evaluate",
            json=quality_command,
            headers=quality_headers,
        )
        repeated_response = client.post(
            f"{QUALITY_AGENT_BASE_URL}/internal/v1/quality/evaluate",
            json=quality_command,
            headers=quality_headers,
        )

    first_response.raise_for_status()
    repeated_response.raise_for_status()
    first = QualityEvaluateResult.model_validate(first_response.json())
    repeated = QualityEvaluateResult.model_validate(repeated_response.json())
    assert repeated == first
    assert first.metrics.conclusion is QualityConclusion.PASS
    assert first.metrics.coverage_ratio >= 0.95
    assert first.metrics.valid_pixel_ratio >= 0.90
    assert first.metrics.output_complete
    assert first_response.headers[CORRELATION_ID_HEADER] == str(CORRELATION_ID)
    assert "/data/" not in first_response.text

    report_path = (
        QUALITY_REPORT_ROOT / str(TASK_ID) / "attempt-1" / "quality" / "quality_report.json"
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["task_id"] == str(TASK_ID)
    assert report["metrics"] == first.metrics.model_dump(mode="json")
    assert report_path.stat().st_size == first.artifact.byte_size
    assert _sha256(report_path) == first.artifact.checksum_sha256
