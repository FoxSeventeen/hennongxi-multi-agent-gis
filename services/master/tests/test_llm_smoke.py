from __future__ import annotations

import json
from hashlib import sha256
from uuid import UUID

import httpx
import pytest
from hennongxi_contracts import PlanStepKind
from hennongxi_master.llm_smoke import execute_smoke

TASK_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
FAKE_CREDENTIAL = "test-smoke-private-credential"
BASE_URL = "https://private-smoke-provider.example/v1"
MODEL = "approved-smoke-model"
PRIVATE_PROVIDER_CONTENT = "private-model-title-that-must-not-be-recorded"


def environment() -> dict[str, str]:
    return {
        "LLM_API_KEY": FAKE_CREDENTIAL,
        "LLM_BASE_URL": BASE_URL,
        "LLM_MODEL": MODEL,
        "LLM_TIMEOUT_SECONDS": "7",
    }


def provider_response() -> bytes:
    plan = {
        "steps": [
            {"kind": "prepare_data", "title": PRIVATE_PROVIDER_CONTENT},
            {"kind": "analyze_ndvi_change", "title": "计算 NDVI 变化"},
            {"kind": "evaluate_quality", "title": "核验成果质量"},
            {"kind": "publish_results", "title": "发布地图与报告"},
        ]
    }
    payload = {
        "id": "private-provider-request-id",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": json.dumps(plan, ensure_ascii=False),
                }
            }
        ],
        "usage": {"prompt_tokens": 120, "completion_tokens": 80},
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()


@pytest.mark.asyncio
async def test_real_smoke_command_records_only_sanitized_success_evidence() -> None:
    raw_response = provider_response()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=raw_response)

    result = await execute_smoke(
        environment=environment(),
        task_id=TASK_ID,
        transport=httpx.MockTransport(handler),
    )

    assert result.exit_code == 0
    evidence = json.loads(result.output)
    assert evidence == {
        "ok": True,
        "provider_origin_sha256": sha256(BASE_URL.encode()).hexdigest(),
        "model": MODEL,
        "task_id": str(TASK_ID),
        "plan_id": evidence["plan_id"],
        "started_at": evidence["started_at"],
        "duration_ms": evidence["duration_ms"],
        "status": "SUCCEEDED",
        "input_tokens": 120,
        "output_tokens": 80,
        "response_sha256": sha256(raw_response).hexdigest(),
        "step_kinds": [kind.value for kind in PlanStepKind],
    }
    assert FAKE_CREDENTIAL not in result.output
    assert BASE_URL not in result.output
    assert PRIVATE_PROVIDER_CONTENT not in result.output
    assert "private-provider-request-id" not in result.output


@pytest.mark.asyncio
async def test_real_smoke_command_returns_sanitized_provider_failure() -> None:
    private_error = "private-provider-error-body"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text=private_error)

    result = await execute_smoke(
        environment=environment(),
        task_id=TASK_ID,
        transport=httpx.MockTransport(handler),
    )

    assert result.exit_code == 1
    evidence = json.loads(result.output)
    assert evidence["ok"] is False
    assert evidence["provider_origin_sha256"] == sha256(BASE_URL.encode()).hexdigest()
    assert evidence["model"] == MODEL
    assert evidence["status"] == "FAILED"
    assert evidence["error_code"] == "LLM_AUTHENTICATION_FAILED"
    assert evidence["retryable"] is False
    assert FAKE_CREDENTIAL not in result.output
    assert BASE_URL not in result.output
    assert private_error not in result.output


@pytest.mark.asyncio
async def test_real_smoke_command_reports_missing_configuration_without_values() -> None:
    private_invalid_value = "private-invalid-configuration-value"

    result = await execute_smoke(
        environment={"LLM_API_KEY": private_invalid_value},
        task_id=TASK_ID,
    )

    assert result.exit_code == 2
    assert json.loads(result.output) == {
        "ok": False,
        "error_code": "LLM_NOT_CONFIGURED",
    }
    assert private_invalid_value not in result.output


@pytest.mark.asyncio
async def test_real_smoke_command_hides_unclassified_internal_error() -> None:
    private_error = "private-unclassified-transport-detail"

    def handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError(private_error)

    result = await execute_smoke(
        environment=environment(),
        task_id=TASK_ID,
        transport=httpx.MockTransport(handler),
    )

    assert result.exit_code == 3
    assert json.loads(result.output) == {
        "ok": False,
        "error_code": "LLM_SMOKE_INTERNAL_ERROR",
    }
    assert private_error not in result.output
    assert FAKE_CREDENTIAL not in result.output
    assert BASE_URL not in result.output
