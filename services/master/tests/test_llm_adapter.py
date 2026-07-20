from __future__ import annotations

import json
from hashlib import sha256
from typing import Any
from uuid import UUID

import httpx
import pytest
from hennongxi_contracts import ModelCallStatus, PlanStepKind
from hennongxi_master.llm import (
    MAX_PROVIDER_RESPONSE_BYTES,
    LlmConfig,
    LlmConfigurationError,
    LlmPlanningAdapter,
    LlmPlanningError,
)

TASK_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
FAKE_CREDENTIAL = "test-provider-key-private-value"
BASE_URL = "https://private-provider.example/v1/"
MODEL = "approved-model"
QUERY = "监测神农溪流域两期 NDVI 变化，并生成中文报告"
PRIVATE_PROVIDER_DETAIL = "private-provider-response-detail"


def provider_plan() -> dict[str, object]:
    return {
        "steps": [
            {"kind": "prepare_data", "title": "准备权威流域与双时相影像"},
            {"kind": "analyze_ndvi_change", "title": "计算 NDVI 与变化分级"},
            {"kind": "evaluate_quality", "title": "独立核验成果质量"},
            {"kind": "publish_results", "title": "发布地图与中文报告"},
        ]
    }


def provider_response(*, plan: dict[str, object] | None = None) -> bytes:
    payload = {
        "id": "private-provider-request-id",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": json.dumps(plan or provider_plan(), ensure_ascii=False),
                }
            }
        ],
        "usage": {"prompt_tokens": 120, "completion_tokens": 80},
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()


def llm_config() -> LlmConfig:
    return LlmConfig.from_environment(
        {
            "LLM_API_KEY": FAKE_CREDENTIAL,
            "LLM_BASE_URL": BASE_URL,
            "LLM_MODEL": MODEL,
            "LLM_TIMEOUT_SECONDS": "7",
        }
    )


def assert_sanitized_error(error: LlmPlanningError, private_value: str) -> None:
    persisted = json.dumps(error.model_call.model_dump(mode="json"), ensure_ascii=False)
    assert private_value not in str(error)
    assert private_value not in repr(error)
    assert private_value not in persisted
    assert FAKE_CREDENTIAL not in str(error)
    assert FAKE_CREDENTIAL not in repr(error)
    assert FAKE_CREDENTIAL not in persisted


def test_llm_config_loads_only_supported_environment_values_and_hides_locations() -> None:
    config = llm_config()

    assert config.base_url == "https://private-provider.example/v1"
    assert config.model == MODEL
    assert config.timeout_seconds == 7
    assert config.api_key.get_secret_value() == FAKE_CREDENTIAL
    assert FAKE_CREDENTIAL not in repr(config)
    assert "private-provider.example" not in repr(config)


@pytest.mark.parametrize(
    "environment",
    [
        {},
        {
            "LLM_API_KEY": "private-key-in-invalid-config",
            "LLM_BASE_URL": "https://user:private-password@provider.example/v1",
            "LLM_MODEL": MODEL,
            "LLM_TIMEOUT_SECONDS": "7",
        },
        {
            "LLM_API_KEY": FAKE_CREDENTIAL,
            "LLM_BASE_URL": "file:///private/provider/socket",
            "LLM_MODEL": MODEL,
            "LLM_TIMEOUT_SECONDS": "7",
        },
    ],
)
def test_llm_config_rejects_invalid_values_without_echoing_them(
    environment: dict[str, str],
) -> None:
    serialized = json.dumps(environment)

    with pytest.raises(LlmConfigurationError) as raised:
        LlmConfig.from_environment(environment)

    assert raised.value.code == "LLM_NOT_CONFIGURED"
    assert str(raised.value) == "LLM configuration is invalid or incomplete"
    assert serialized not in repr(raised.value)
    for private_value in environment.values():
        assert private_value not in str(raised.value)
        assert private_value not in repr(raised.value)


@pytest.mark.asyncio
async def test_llm_adapter_sends_bounded_request_and_returns_sanitized_plan() -> None:
    raw_response = provider_response()
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(
            200,
            content=raw_response,
            headers={"content-type": "application/json"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        plan = await LlmPlanningAdapter(llm_config(), client).create_plan(
            task_id=TASK_ID,
            query=QUERY,
        )

    request = captured["request"]
    assert isinstance(request, httpx.Request)
    assert request.method == "POST"
    assert str(request.url) == "https://private-provider.example/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {FAKE_CREDENTIAL}"
    assert FAKE_CREDENTIAL.encode() not in request.content
    request_payload = json.loads(request.content)
    assert request_payload["model"] == MODEL
    assert request_payload["temperature"] == 0
    assert request_payload["max_tokens"] == 800
    assert request_payload["response_format"] == {"type": "json_object"}
    assert request_payload["messages"][-1] == {"role": "user", "content": QUERY}

    assert plan.task_id == TASK_ID
    assert tuple(step.kind for step in plan.steps) == tuple(PlanStepKind)
    assert plan.model_call is not None
    assert plan.model_call.status is ModelCallStatus.SUCCEEDED
    assert plan.model_call.model == MODEL
    assert plan.model_call.input_tokens == 120
    assert plan.model_call.output_tokens == 80
    assert plan.model_call.response_sha256 == sha256(raw_response).hexdigest()
    serialized_plan = plan.model_dump_json()
    assert FAKE_CREDENTIAL not in serialized_plan
    assert "private-provider-request-id" not in serialized_plan


@pytest.mark.parametrize(
    ("status_code", "expected_code", "retryable"),
    [
        (400, "LLM_PROVIDER_REJECTED", False),
        (401, "LLM_AUTHENTICATION_FAILED", False),
        (403, "LLM_AUTHENTICATION_FAILED", False),
        (429, "LLM_RATE_LIMITED", True),
        (500, "LLM_PROVIDER_UNAVAILABLE", True),
    ],
)
@pytest.mark.asyncio
async def test_llm_adapter_maps_http_failures_without_reading_provider_body(
    status_code: int,
    expected_code: str,
    retryable: bool,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text=PRIVATE_PROVIDER_DETAIL)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(LlmPlanningError) as raised:
            await LlmPlanningAdapter(llm_config(), client).create_plan(
                task_id=TASK_ID,
                query=QUERY,
            )

    error = raised.value
    assert error.code == expected_code
    assert error.retryable is retryable
    assert error.model_call.status is ModelCallStatus.FAILED
    assert error.model_call.error_code == expected_code
    assert error.model_call.response_sha256 is None
    assert_sanitized_error(error, PRIVATE_PROVIDER_DETAIL)


@pytest.mark.asyncio
async def test_llm_adapter_maps_timeout_without_exposing_transport_details() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(PRIVATE_PROVIDER_DETAIL, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(LlmPlanningError) as raised:
            await LlmPlanningAdapter(llm_config(), client).create_plan(
                task_id=TASK_ID,
                query=QUERY,
            )

    assert raised.value.code == "LLM_TIMEOUT"
    assert raised.value.retryable is True
    assert_sanitized_error(raised.value, PRIVATE_PROVIDER_DETAIL)


@pytest.mark.asyncio
async def test_llm_adapter_maps_network_failure_without_exposing_transport_details() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(PRIVATE_PROVIDER_DETAIL, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(LlmPlanningError) as raised:
            await LlmPlanningAdapter(llm_config(), client).create_plan(
                task_id=TASK_ID,
                query=QUERY,
            )

    assert raised.value.code == "LLM_PROVIDER_UNAVAILABLE"
    assert raised.value.retryable is True
    assert_sanitized_error(raised.value, PRIVATE_PROVIDER_DETAIL)


@pytest.mark.asyncio
async def test_llm_adapter_maps_malformed_envelope_without_echoing_response() -> None:
    raw_response = json.dumps({"detail": PRIVATE_PROVIDER_DETAIL}).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=raw_response)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(LlmPlanningError) as raised:
            await LlmPlanningAdapter(llm_config(), client).create_plan(
                task_id=TASK_ID,
                query=QUERY,
            )

    assert raised.value.code == "LLM_RESPONSE_INVALID"
    assert raised.value.retryable is True
    assert raised.value.model_call.response_sha256 == sha256(raw_response).hexdigest()
    assert_sanitized_error(raised.value, PRIVATE_PROVIDER_DETAIL)


@pytest.mark.asyncio
async def test_llm_adapter_rejects_oversized_response_without_retaining_it() -> None:
    raw_response = (PRIVATE_PROVIDER_DETAIL.encode() * MAX_PROVIDER_RESPONSE_BYTES)[
        : MAX_PROVIDER_RESPONSE_BYTES + 1
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=raw_response)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(LlmPlanningError) as raised:
            await LlmPlanningAdapter(llm_config(), client).create_plan(
                task_id=TASK_ID,
                query=QUERY,
            )

    assert raised.value.code == "LLM_RESPONSE_INVALID"
    assert raised.value.retryable is True
    assert raised.value.model_call.response_sha256 is None
    assert_sanitized_error(raised.value, PRIVATE_PROVIDER_DETAIL)


@pytest.mark.asyncio
async def test_llm_adapter_rejects_disallowed_plan_without_echoing_model_content() -> None:
    invalid_plan = provider_plan()
    steps = invalid_plan["steps"]
    assert isinstance(steps, list)
    steps[0]["command"] = PRIVATE_PROVIDER_DETAIL
    raw_response = provider_response(plan=invalid_plan)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=raw_response)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(LlmPlanningError) as raised:
            await LlmPlanningAdapter(llm_config(), client).create_plan(
                task_id=TASK_ID,
                query=QUERY,
            )

    assert raised.value.code == "LLM_PLAN_INVALID"
    assert raised.value.retryable is False
    assert raised.value.model_call.response_sha256 == sha256(raw_response).hexdigest()
    assert_sanitized_error(raised.value, PRIVATE_PROVIDER_DETAIL)
