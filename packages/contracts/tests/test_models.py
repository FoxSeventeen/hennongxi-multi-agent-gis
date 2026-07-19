from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest
from hennongxi_contracts import (
    AgentName,
    AnalysisRunCommand,
    ArtifactRef,
    ArtifactStatus,
    ArtifactType,
    DataPrepareCommand,
    ExecutionPlan,
    LogicalDatasetId,
    ModelCallRecord,
    ModelCallStatus,
    PlanSource,
    PlanStep,
    PlanStepKind,
    QualityEvaluateCommand,
    StructuredError,
    TaskEvent,
    TaskResponse,
    TaskStatus,
)
from pydantic import ValidationError

TASK_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
PLAN_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
CORRELATION_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
NOW = datetime(2026, 7, 19, 8, 0, tzinfo=UTC)
SHA256 = "a" * 64


def valid_plan_steps() -> tuple[PlanStep, ...]:
    definitions = (
        ("prepare_data", PlanStepKind.PREPARE_DATA, AgentName.DATA, ()),
        (
            "analyze_ndvi_change",
            PlanStepKind.ANALYZE_NDVI_CHANGE,
            AgentName.ANALYSIS,
            ("prepare_data",),
        ),
        (
            "evaluate_quality",
            PlanStepKind.EVALUATE_QUALITY,
            AgentName.QUALITY,
            ("analyze_ndvi_change",),
        ),
        (
            "publish_results",
            PlanStepKind.PUBLISH_RESULTS,
            AgentName.PUBLISHER,
            ("evaluate_quality",),
        ),
    )
    return tuple(
        PlanStep(
            step_id=step_id,
            kind=kind,
            agent=agent,
            order=order,
            title=step_id,
            depends_on=depends_on,
        )
        for order, (step_id, kind, agent, depends_on) in enumerate(definitions, start=1)
    )


def valid_model_call() -> ModelCallRecord:
    return ModelCallRecord(
        model="approved-model",
        started_at=NOW,
        duration_ms=250,
        status=ModelCallStatus.SUCCEEDED,
        input_tokens=100,
        output_tokens=50,
        response_sha256=SHA256,
    )


def test_real_llm_plan_accepts_only_the_fixed_agent_sequence() -> None:
    plan = ExecutionPlan(
        plan_id=PLAN_ID,
        task_id=TASK_ID,
        source=PlanSource.REAL_LLM,
        created_at=NOW,
        model_call=valid_model_call(),
        steps=valid_plan_steps(),
    )

    assert tuple(step.kind for step in plan.steps) == tuple(PlanStepKind)


@pytest.mark.parametrize("unsafe_field", ["command", "path", "sql", "url"])
def test_plan_step_rejects_executable_or_location_fields(unsafe_field: str) -> None:
    payload = valid_plan_steps()[0].model_dump(mode="json")
    payload[unsafe_field] = "do-not-execute"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        PlanStep.model_validate(payload)


def test_plan_rejects_a_step_assigned_to_the_wrong_agent() -> None:
    steps = [step.model_dump(mode="json") for step in valid_plan_steps()]
    steps[1]["agent"] = AgentName.PUBLISHER

    with pytest.raises(ValidationError, match="must run on agent"):
        ExecutionPlan(
            plan_id=PLAN_ID,
            task_id=TASK_ID,
            source=PlanSource.REAL_LLM,
            created_at=NOW,
            model_call=valid_model_call(),
            steps=steps,
        )


def test_plan_rejects_reordered_or_missing_fixed_steps() -> None:
    with pytest.raises(ValidationError, match="fixed ecological-monitoring sequence"):
        ExecutionPlan(
            plan_id=PLAN_ID,
            task_id=TASK_ID,
            source=PlanSource.REAL_LLM,
            created_at=NOW,
            model_call=valid_model_call(),
            steps=valid_plan_steps()[:-1],
        )


def test_real_plan_requires_sanitized_model_call_evidence() -> None:
    with pytest.raises(ValidationError, match="model_call"):
        ExecutionPlan(
            plan_id=PLAN_ID,
            task_id=TASK_ID,
            source=PlanSource.REAL_LLM,
            created_at=NOW,
            steps=valid_plan_steps(),
        )


def test_model_call_rejects_secrets_and_provider_payloads() -> None:
    payload = valid_model_call().model_dump(mode="json")
    payload["api_key"] = "secret"
    payload["response_body"] = "raw-provider-response"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ModelCallRecord.model_validate(payload)


def test_complete_artifact_requires_checksum_and_nonzero_size() -> None:
    with pytest.raises(ValidationError, match="complete artifact"):
        ArtifactRef(
            artifact_id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
            task_id=TASK_ID,
            attempt=1,
            artifact_type=ArtifactType.NDVI_BEFORE,
            status=ArtifactStatus.COMPLETE,
            media_type="image/tiff",
            created_at=NOW,
        )


def valid_artifact(*, task_id: UUID = TASK_ID, attempt: int = 1) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
        task_id=task_id,
        attempt=attempt,
        artifact_type=ArtifactType.NDVI_BEFORE,
        status=ArtifactStatus.COMPLETE,
        media_type="image/tiff",
        created_at=NOW,
        checksum_sha256=SHA256,
        byte_size=10,
    )


def test_artifact_contract_has_no_filesystem_path_field() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ArtifactRef.model_validate(
            {
                "artifact_id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                "task_id": str(TASK_ID),
                "attempt": 1,
                "artifact_type": "NDVI_BEFORE",
                "status": "COMPLETE",
                "media_type": "image/tiff",
                "created_at": NOW.isoformat(),
                "checksum_sha256": SHA256,
                "byte_size": 10,
                "path": "/data/private/result.tif",
            }
        )


@pytest.mark.parametrize(
    "bad_time",
    [
        datetime(2026, 7, 19, 8, 0),
        datetime(2026, 7, 19, 16, 0, tzinfo=timezone(timedelta(hours=8))),
    ],
)
def test_event_requires_an_explicit_utc_timestamp(bad_time: datetime) -> None:
    with pytest.raises(ValidationError, match="UTC"):
        TaskEvent(
            sequence=1,
            task_id=TASK_ID,
            step_id="prepare_data",
            attempt=1,
            correlation_id=CORRELATION_ID,
            agent=AgentName.DATA,
            status=TaskStatus.DATA_PREPARING,
            progress=10,
            message="正在准备数据",
            elapsed_ms=20,
            occurred_at=bad_time,
        )


def test_event_rejects_progress_outside_zero_to_one_hundred() -> None:
    with pytest.raises(ValidationError):
        TaskEvent(
            sequence=1,
            task_id=TASK_ID,
            step_id="prepare_data",
            attempt=1,
            correlation_id=CORRELATION_ID,
            agent=AgentName.DATA,
            status=TaskStatus.DATA_PREPARING,
            progress=101,
            message="invalid",
            elapsed_ms=20,
            occurred_at=NOW,
        )


def test_event_json_round_trip_preserves_version_and_identifiers() -> None:
    event = TaskEvent(
        sequence=1,
        task_id=TASK_ID,
        step_id="analyze_ndvi_change",
        attempt=1,
        correlation_id=CORRELATION_ID,
        agent=AgentName.ANALYSIS,
        status=TaskStatus.ANALYZING,
        progress=50,
        message="正在分析",
        elapsed_ms=20,
        occurred_at=NOW,
        artifacts=(valid_artifact(),),
    )

    assert TaskEvent.model_validate_json(event.model_dump_json()) == event
    assert event.schema_version == "1.0"


def test_event_rejects_an_artifact_from_another_task() -> None:
    with pytest.raises(ValidationError, match="same task and attempt"):
        TaskEvent(
            sequence=1,
            task_id=TASK_ID,
            step_id="analyze_ndvi_change",
            attempt=1,
            correlation_id=CORRELATION_ID,
            agent=AgentName.ANALYSIS,
            status=TaskStatus.ANALYZING,
            progress=50,
            message="invalid cross-task artifact",
            elapsed_ms=20,
            occurred_at=NOW,
            artifacts=(valid_artifact(task_id=UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")),),
        )


def test_error_contract_rejects_authorization_or_secret_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        StructuredError.model_validate(
            {
                "code": "INVALID_PLAN",
                "message": "计划无效",
                "retryable": False,
                "authorization": "Bearer secret",
            }
        )


def test_data_command_requires_the_complete_logical_dataset_allowlist() -> None:
    with pytest.raises(ValidationError, match="exactly the required logical dataset IDs"):
        DataPrepareCommand(
            task_id=TASK_ID,
            step_id="prepare_data",
            attempt=1,
            correlation_id=CORRELATION_ID,
            dataset_ids=(LogicalDatasetId.WATERSHED,),
        )


def test_internal_command_rejects_an_arbitrary_input_path() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AnalysisRunCommand.model_validate(
            {
                "task_id": str(TASK_ID),
                "step_id": "analyze_ndvi_change",
                "attempt": 1,
                "correlation_id": str(CORRELATION_ID),
                "inputs": [],
                "input_path": "/tmp/user-controlled.tif",
            }
        )


def test_quality_command_rejects_an_artifact_from_another_task() -> None:
    with pytest.raises(ValidationError, match="same task and attempt"):
        QualityEvaluateCommand(
            task_id=TASK_ID,
            step_id="evaluate_quality",
            attempt=1,
            correlation_id=CORRELATION_ID,
            artifacts=(valid_artifact(task_id=UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")),),
            analysis_elapsed_ms=100,
        )


def test_task_query_rejects_cross_task_artifacts() -> None:
    with pytest.raises(ValidationError, match="same task"):
        TaskResponse(
            task_id=TASK_ID,
            query="监测神农溪生态变化",
            status=TaskStatus.ANALYZING,
            progress=50,
            current_attempt=1,
            correlation_id=CORRELATION_ID,
            created_at=NOW,
            updated_at=NOW,
            artifacts=(valid_artifact(task_id=UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")),),
        )
