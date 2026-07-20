from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
from hennongxi_contracts import (
    AgentName,
    ModelCallRecord,
    ModelCallStatus,
    PlanSource,
    PlanStepKind,
)
from hennongxi_master.planning import LlmPlanValidationError, build_llm_execution_plan

TASK_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
PLAN_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
NOW = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)


def model_call() -> ModelCallRecord:
    return ModelCallRecord(
        model="approved-model",
        started_at=NOW,
        duration_ms=250,
        status=ModelCallStatus.SUCCEEDED,
        input_tokens=120,
        output_tokens=80,
        response_sha256="a" * 64,
    )


def valid_provider_plan() -> dict[str, object]:
    return {
        "steps": [
            {"kind": "prepare_data", "title": "准备权威流域与双时相影像"},
            {"kind": "analyze_ndvi_change", "title": "计算 NDVI 与变化分级"},
            {"kind": "evaluate_quality", "title": "独立核验成果质量"},
            {"kind": "publish_results", "title": "发布地图与中文报告"},
        ]
    }


def test_llm_plan_builds_only_the_fixed_local_execution_chain() -> None:
    plan = build_llm_execution_plan(
        task_id=TASK_ID,
        plan_id=PLAN_ID,
        created_at=NOW,
        model_call=model_call(),
        provider_content=json.dumps(valid_provider_plan(), ensure_ascii=False),
    )

    assert plan.task_id == TASK_ID
    assert plan.plan_id == PLAN_ID
    assert plan.source is PlanSource.REAL_LLM
    assert plan.model_call == model_call()
    assert tuple(step.kind for step in plan.steps) == tuple(PlanStepKind)
    assert tuple(step.agent for step in plan.steps) == (
        AgentName.DATA,
        AgentName.ANALYSIS,
        AgentName.QUALITY,
        AgentName.PUBLISHER,
    )
    assert tuple(step.step_id for step in plan.steps) == (
        "prepare_data",
        "analyze_ndvi_change",
        "evaluate_quality",
        "publish_results",
    )
    assert tuple(step.depends_on for step in plan.steps) == (
        (),
        ("prepare_data",),
        ("analyze_ndvi_change",),
        ("evaluate_quality",),
    )


@pytest.mark.parametrize("unsafe_field", ["command", "path", "sql", "url"])
def test_llm_plan_rejects_unsafe_model_fields_without_echoing_them(
    unsafe_field: str,
) -> None:
    payload = valid_provider_plan()
    unsafe_value = f"private-{unsafe_field}-value"
    steps = payload["steps"]
    assert isinstance(steps, list)
    steps[0][unsafe_field] = unsafe_value

    with pytest.raises(LlmPlanValidationError) as raised:
        build_llm_execution_plan(
            task_id=TASK_ID,
            plan_id=PLAN_ID,
            created_at=NOW,
            model_call=model_call(),
            provider_content=json.dumps(payload, ensure_ascii=False),
        )

    assert raised.value.code == "LLM_PLAN_INVALID"
    assert str(raised.value) == "LLM plan failed schema validation"
    assert unsafe_value not in repr(raised.value)


def test_llm_plan_rejects_disallowed_steps_without_echoing_model_output() -> None:
    payload = valid_provider_plan()
    steps = payload["steps"]
    assert isinstance(steps, list)
    steps[0]["kind"] = "run_shell_private_value"

    with pytest.raises(LlmPlanValidationError) as raised:
        build_llm_execution_plan(
            task_id=TASK_ID,
            plan_id=PLAN_ID,
            created_at=NOW,
            model_call=model_call(),
            provider_content=json.dumps(payload, ensure_ascii=False),
        )

    assert raised.value.code == "LLM_PLAN_INVALID"
    assert "run_shell_private_value" not in str(raised.value)
    assert "run_shell_private_value" not in repr(raised.value)


def test_llm_plan_maps_malformed_json_to_a_sanitized_validation_error() -> None:
    malformed = '{"steps": [private-provider-output]}'

    with pytest.raises(LlmPlanValidationError) as raised:
        build_llm_execution_plan(
            task_id=TASK_ID,
            plan_id=PLAN_ID,
            created_at=NOW,
            model_call=model_call(),
            provider_content=malformed,
        )

    assert raised.value.code == "LLM_PLAN_INVALID"
    assert malformed not in str(raised.value)
    assert malformed not in repr(raised.value)
