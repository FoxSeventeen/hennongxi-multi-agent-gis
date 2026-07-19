from __future__ import annotations

import os
from uuid import UUID

import httpx
import pytest
from hennongxi_contracts import (
    DataPrepareResult,
    ErrorCode,
    ErrorResponse,
    LogicalDatasetId,
)
from hennongxi_observability import CORRELATION_ID_HEADER

DATA_AGENT_BASE_URL = os.environ.get("DATA_AGENT_BASE_URL")
pytestmark = pytest.mark.skipif(
    DATA_AGENT_BASE_URL is None,
    reason="Master-style Data Agent network integration test",
)

TASK_ID = UUID("88888888-8888-4888-8888-888888888888")
CORRELATION_ID = UUID("77777777-7777-4777-8777-777777777777")


def _command() -> dict[str, object]:
    return {
        "task_id": str(TASK_ID),
        "step_id": "prepare_data",
        "attempt": 1,
        "correlation_id": str(CORRELATION_ID),
        "dataset_ids": [dataset_id.value for dataset_id in LogicalDatasetId],
    }


def test_master_style_http_call_returns_contract_metadata_and_rejects_paths() -> None:
    assert DATA_AGENT_BASE_URL is not None
    headers = {CORRELATION_ID_HEADER: str(CORRELATION_ID)}
    with httpx.Client(base_url=DATA_AGENT_BASE_URL, timeout=180) as client:
        response = client.post("/internal/v1/data/prepare", json=_command(), headers=headers)
        unsafe_command = _command()
        unsafe_command["input_path"] = "/etc/passwd"
        denied = client.post(
            "/internal/v1/data/prepare",
            json=unsafe_command,
            headers=headers,
        )

    response.raise_for_status()
    result = DataPrepareResult.model_validate(response.json())
    assert response.headers[CORRELATION_ID_HEADER] == str(CORRELATION_ID)
    assert result.task_id == TASK_ID
    assert result.correlation_id == CORRELATION_ID
    assert tuple(asset.dataset_id for asset in result.assets) == tuple(LogicalDatasetId)
    assert result.assets[3].acquired_on is not None
    assert result.assets[3].acquired_on.isoformat() == "2024-08-12"
    assert all(asset.grid is not None for asset in result.assets[1:])
    assert "path" not in response.text

    error = ErrorResponse.model_validate(denied.json())
    assert denied.status_code == 422
    assert error.error.code is ErrorCode.VALIDATION_ERROR
    assert "/etc/passwd" not in denied.text
