from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest
from hennongxi_contracts import (
    AgentName,
    AnalysisRunCommand,
    AnalysisRunResult,
    AreaStatistics,
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
    QualityConclusion,
    QualityEvaluateCommand,
    QualityEvaluateResult,
    QualityMetrics,
    QualityThresholds,
    RasterGrid,
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


def valid_artifact(
    *,
    task_id: UUID = TASK_ID,
    attempt: int = 1,
    artifact_type: ArtifactType = ArtifactType.NDVI_BEFORE,
) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
        task_id=task_id,
        attempt=attempt,
        artifact_type=artifact_type,
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


def test_data_command_is_scoped_to_the_prepare_data_step() -> None:
    with pytest.raises(ValidationError, match="prepare_data"):
        DataPrepareCommand(
            task_id=TASK_ID,
            step_id="analyze_ndvi_change",
            attempt=1,
            correlation_id=CORRELATION_ID,
            dataset_ids=tuple(LogicalDatasetId),
        )


def test_raster_grid_carries_explicit_nodata_metadata() -> None:
    grid = RasterGrid(
        crs="EPSG:32649",
        width=4072,
        height=4675,
        transform=(10.0, 0.0, 415240.0, 0.0, -10.0, 3481620.0),
        bounds=(415240.0, 3434870.0, 455960.0, 3481620.0),
        nodata=-9999.0,
    )

    assert grid.nodata == -9999.0


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


def test_analysis_command_is_scoped_to_the_analysis_step() -> None:
    with pytest.raises(ValidationError, match="analyze_ndvi_change"):
        AnalysisRunCommand(
            task_id=TASK_ID,
            step_id="prepare_data",
            attempt=1,
            correlation_id=CORRELATION_ID,
            inputs=(),
        )


def test_analysis_result_requires_elapsed_time_and_complete_artifact_set() -> None:
    artifacts = tuple(
        valid_artifact(artifact_type=artifact_type)
        for artifact_type in (
            ArtifactType.NDVI_BEFORE,
            ArtifactType.NDVI_AFTER,
            ArtifactType.NDVI_DIFFERENCE,
            ArtifactType.CHANGE_CLASSIFICATION,
            ArtifactType.AREA_STATISTICS,
        )
    )
    result = AnalysisRunResult(
        task_id=TASK_ID,
        step_id="analyze_ndvi_change",
        attempt=1,
        correlation_id=CORRELATION_ID,
        artifacts=artifacts,
        statistics=AreaStatistics(
            increase_hectares=1,
            stable_hectares=2,
            decrease_hectares=3,
            valid_hectares=6,
        ),
        elapsed_ms=25,
    )

    assert result.elapsed_ms == 25


def test_analysis_result_rejects_incomplete_artifact_set() -> None:
    with pytest.raises(ValidationError, match="complete analysis artifact set"):
        AnalysisRunResult(
            task_id=TASK_ID,
            step_id="analyze_ndvi_change",
            attempt=1,
            correlation_id=CORRELATION_ID,
            artifacts=(valid_artifact(),),
            statistics=AreaStatistics(
                increase_hectares=1,
                stable_hectares=2,
                decrease_hectares=3,
                valid_hectares=6,
            ),
            elapsed_ms=25,
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


def test_quality_command_allows_missing_outputs_but_rejects_duplicate_types() -> None:
    artifact = valid_artifact(artifact_type=ArtifactType.NDVI_BEFORE)

    command = QualityEvaluateCommand(
        task_id=TASK_ID,
        step_id="evaluate_quality",
        attempt=1,
        correlation_id=CORRELATION_ID,
        artifacts=(artifact,),
        analysis_elapsed_ms=100,
    )
    assert command.artifacts == (artifact,)

    with pytest.raises(ValidationError, match="unique supported analysis artifact types"):
        QualityEvaluateCommand(
            task_id=TASK_ID,
            step_id="evaluate_quality",
            attempt=1,
            correlation_id=CORRELATION_ID,
            artifacts=(artifact, artifact),
            analysis_elapsed_ms=100,
        )


def test_quality_command_rejects_non_analysis_artifacts_and_wrong_step() -> None:
    with pytest.raises(ValidationError, match="supported analysis artifact types"):
        QualityEvaluateCommand(
            task_id=TASK_ID,
            step_id="evaluate_quality",
            attempt=1,
            correlation_id=CORRELATION_ID,
            artifacts=(valid_artifact(artifact_type=ArtifactType.PDF_REPORT),),
            analysis_elapsed_ms=100,
        )

    with pytest.raises(ValidationError, match="evaluate_quality"):
        QualityEvaluateCommand(
            task_id=TASK_ID,
            step_id="analyze_ndvi_change",
            attempt=1,
            correlation_id=CORRELATION_ID,
            artifacts=(),
            analysis_elapsed_ms=100,
        )


def test_quality_metrics_expose_thresholds_units_and_boundary_pass() -> None:
    metrics = QualityMetrics(
        coverage_ratio=0.95,
        valid_pixel_ratio=0.90,
        output_complete=True,
        elapsed_ms=0,
        thresholds=QualityThresholds(
            minimum_watershed_coverage_ratio=0.95,
            minimum_valid_pixel_ratio=0.90,
        ),
        conclusion=QualityConclusion.PASS,
        passed=True,
        evidence=(
            "流域覆盖率 0.9500，阈值 >= 0.9500",
            "有效像元率 0.9000，阈值 >= 0.9000",
            "输出完整性 5/5，要求 5/5",
            "Analysis 耗时 0 ms，要求为非负整数",
        ),
    )

    assert metrics.conclusion is QualityConclusion.PASS
    assert metrics.thresholds.elapsed_minimum_ms == 0


def test_quality_metrics_reject_an_inconsistent_passing_conclusion() -> None:
    with pytest.raises(ValidationError, match="passing conclusion requires every quality gate"):
        QualityMetrics(
            coverage_ratio=0.9499,
            valid_pixel_ratio=0.90,
            output_complete=True,
            elapsed_ms=1,
            thresholds=QualityThresholds(
                minimum_watershed_coverage_ratio=0.95,
                minimum_valid_pixel_ratio=0.90,
            ),
            conclusion=QualityConclusion.PASS,
            passed=True,
            evidence=("覆盖不足", "有效像元达标", "输出完整", "耗时已记录"),
        )


def test_quality_result_requires_a_complete_quality_report() -> None:
    metrics = QualityMetrics(
        coverage_ratio=0.95,
        valid_pixel_ratio=0.90,
        output_complete=True,
        elapsed_ms=10,
        thresholds=QualityThresholds(
            minimum_watershed_coverage_ratio=0.95,
            minimum_valid_pixel_ratio=0.90,
        ),
        conclusion=QualityConclusion.PASS,
        passed=True,
        evidence=("覆盖达标", "有效像元达标", "输出完整", "耗时已记录"),
    )

    with pytest.raises(ValidationError, match="complete quality report"):
        QualityEvaluateResult(
            task_id=TASK_ID,
            step_id="evaluate_quality",
            attempt=1,
            correlation_id=CORRELATION_ID,
            metrics=metrics,
            artifact=valid_artifact(artifact_type=ArtifactType.NDVI_BEFORE),
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
