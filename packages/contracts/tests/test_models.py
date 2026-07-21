from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
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
    DataAssetRef,
    DataPrepareCommand,
    ExecutionPlan,
    LogicalDatasetId,
    ModelCallRecord,
    ModelCallStatus,
    PlanSource,
    PlanStep,
    PlanStepKind,
    PublishedResource,
    PublisherPublishCommand,
    PublisherPublishResult,
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
    TileArtifactType,
    TileLegendEntry,
    TileMetadata,
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


def test_real_plan_requires_a_succeeded_model_call() -> None:
    failed_call = ModelCallRecord(
        model="approved-model",
        started_at=NOW,
        duration_ms=250,
        status=ModelCallStatus.FAILED,
        error_code="LLM_TIMEOUT",
    )

    with pytest.raises(ValidationError, match="succeeded model_call"):
        ExecutionPlan(
            plan_id=PLAN_ID,
            task_id=TASK_ID,
            source=PlanSource.REAL_LLM,
            created_at=NOW,
            model_call=failed_call,
            steps=valid_plan_steps(),
        )


def test_failed_model_call_requires_a_safe_error_code() -> None:
    with pytest.raises(ValidationError, match="failed model call requires error_code"):
        ModelCallRecord(
            model="approved-model",
            started_at=NOW,
            duration_ms=250,
            status=ModelCallStatus.FAILED,
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


def test_reuse_commands_require_an_earlier_attempt() -> None:
    inputs = tuple(
        DataAssetRef(
            dataset_id=dataset_id,
            checksum_sha256=SHA256,
            byte_size=1,
        )
        for dataset_id in LogicalDatasetId
    )
    analysis = AnalysisRunCommand(
        task_id=TASK_ID,
        step_id="analyze_ndvi_change",
        attempt=2,
        correlation_id=CORRELATION_ID,
        inputs=inputs,
        reuse_from_attempt=1,
    )
    quality = QualityEvaluateCommand(
        task_id=TASK_ID,
        step_id="evaluate_quality",
        attempt=2,
        correlation_id=CORRELATION_ID,
        artifacts=tuple(
            valid_artifact(attempt=2, artifact_type=artifact_type)
            for artifact_type in (
                ArtifactType.NDVI_BEFORE,
                ArtifactType.NDVI_AFTER,
                ArtifactType.NDVI_DIFFERENCE,
                ArtifactType.CHANGE_CLASSIFICATION,
                ArtifactType.AREA_STATISTICS,
            )
        ),
        analysis_elapsed_ms=1,
        reuse_from_attempt=1,
    )
    assert analysis.reuse_from_attempt == quality.reuse_from_attempt == 1

    with pytest.raises(ValidationError, match="earlier attempt"):
        AnalysisRunCommand.model_validate(analysis.model_dump() | {"reuse_from_attempt": 2})
    with pytest.raises(ValidationError, match="earlier attempt"):
        QualityEvaluateCommand.model_validate(quality.model_dump() | {"reuse_from_attempt": 3})


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


def _passing_quality() -> QualityMetrics:
    return QualityMetrics(
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


def _tile_metadata() -> TileMetadata:
    return TileMetadata(
        artifact_type=TileArtifactType.NDVI_BEFORE,
        bounds_wgs84=(110.0, 31.0, 111.0, 32.0),
        start_date=date(2019, 8, 19),
        end_date=date(2019, 8, 19),
        units="NDVI",
        attribution="Copernicus Sentinel-2 / Element 84 Earth Search",
        legend=(
            TileLegendEntry(value=-1.0, label="低", color="#8C510A"),
            TileLegendEntry(value=0.0, label="中", color="#F6E8C3"),
            TileLegendEntry(value=1.0, label="高", color="#01665E"),
        ),
    )


def test_tile_metadata_requires_safe_bounds_dates_and_ordered_color_stops() -> None:
    metadata = _tile_metadata()

    assert metadata.bounds_wgs84 == (110.0, 31.0, 111.0, 32.0)
    assert metadata.legend[0].color == "#8C510A"

    with pytest.raises(ValidationError, match="WGS84 bounds"):
        TileMetadata(
            **{
                **_tile_metadata().model_dump(exclude={"bounds_wgs84"}),
                "bounds_wgs84": (111.0, 31.0, 110.0, 32.0),
            }
        )

    with pytest.raises(ValidationError, match="strictly increasing"):
        TileMetadata(
            **{
                **_tile_metadata().model_dump(exclude={"legend"}),
                "legend": (
                    TileLegendEntry(value=0, label="零", color="#000000"),
                    TileLegendEntry(value=0, label="重复", color="#FFFFFF"),
                ),
            }
        )


def test_published_tile_resource_requires_matching_visual_metadata() -> None:
    resource = PublishedResource(
        artifact_id=valid_artifact().artifact_id,
        tile_template=(f"/api/v1/tiles/{TASK_ID}/NDVI_BEFORE/{{z}}/{{x}}/{{y}}.png"),
        tile_metadata=_tile_metadata(),
    )

    assert resource.tile_metadata is not None

    with pytest.raises(ValidationError, match="tile metadata"):
        PublishedResource(
            artifact_id=valid_artifact().artifact_id,
            tile_template=(f"/api/v1/tiles/{TASK_ID}/NDVI_BEFORE/{{z}}/{{x}}/{{y}}.png"),
        )


def test_publisher_command_requires_complete_passing_inputs() -> None:
    required_types = (
        ArtifactType.NDVI_BEFORE,
        ArtifactType.NDVI_AFTER,
        ArtifactType.NDVI_DIFFERENCE,
        ArtifactType.CHANGE_CLASSIFICATION,
        ArtifactType.AREA_STATISTICS,
        ArtifactType.QUALITY_REPORT,
    )
    command = PublisherPublishCommand(
        task_id=TASK_ID,
        step_id="publish_results",
        attempt=1,
        correlation_id=CORRELATION_ID,
        artifacts=tuple(valid_artifact(artifact_type=value) for value in required_types),
        quality=_passing_quality(),
    )
    assert len(command.artifacts) == 6

    with pytest.raises(ValidationError, match="complete publishable artifact set"):
        PublisherPublishCommand(
            task_id=TASK_ID,
            step_id="publish_results",
            attempt=1,
            correlation_id=CORRELATION_ID,
            artifacts=(valid_artifact(),),
            quality=_passing_quality(),
        )

    with pytest.raises(ValidationError, match="passing quality"):
        PublisherPublishCommand(
            task_id=TASK_ID,
            step_id="publish_results",
            attempt=1,
            correlation_id=CORRELATION_ID,
            artifacts=tuple(valid_artifact(artifact_type=value) for value in required_types),
            quality=_passing_quality().model_copy(
                update={"conclusion": QualityConclusion.FAIL, "passed": False}
            ),
        )


def _published_tile_resources() -> tuple[PublishedResource, ...]:
    resources = []
    for artifact_type in TileArtifactType:
        metadata = _tile_metadata().model_copy(
            update={
                "artifact_type": artifact_type,
                "start_date": (
                    date(2024, 8, 12)
                    if artifact_type is TileArtifactType.NDVI_AFTER
                    else date(2019, 8, 19)
                ),
                "end_date": (
                    date(2019, 8, 19)
                    if artifact_type is TileArtifactType.NDVI_BEFORE
                    else date(2024, 8, 12)
                ),
            }
        )
        resources.append(
            PublishedResource(
                artifact_id=valid_artifact(
                    artifact_type=ArtifactType(artifact_type.value)
                ).artifact_id,
                tile_template=(
                    f"/api/v1/tiles/{TASK_ID}/{artifact_type.value}/{{z}}/{{x}}/{{y}}.png"
                ),
                tile_metadata=metadata,
            )
        )
    return tuple(resources)


def _published_report() -> ArtifactRef:
    return valid_artifact(artifact_type=ArtifactType.PDF_REPORT).model_copy(
        update={
            "artifact_id": UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
            "media_type": "application/pdf",
        }
    )


def _published_resources_with_report() -> tuple[PublishedResource, ...]:
    report = _published_report()
    return (
        *_published_tile_resources(),
        PublishedResource(
            artifact_id=report.artifact_id,
            download_path=(f"/api/v1/tasks/{TASK_ID}/artifacts/{report.artifact_id}/download"),
        ),
    )


def test_publisher_result_requires_four_tiles_and_one_task_bound_pdf_report() -> None:
    result = PublisherPublishResult(
        task_id=TASK_ID,
        step_id="publish_results",
        attempt=1,
        correlation_id=CORRELATION_ID,
        resources=_published_resources_with_report(),
        report=_published_report(),
    )

    assert result.report.artifact_type is ArtifactType.PDF_REPORT
    assert {
        resource.tile_metadata.artifact_type
        for resource in result.resources
        if resource.tile_metadata is not None
    } == set(TileArtifactType)

    with pytest.raises(ValidationError, match="four tile resources"):
        PublisherPublishResult(
            task_id=TASK_ID,
            step_id="publish_results",
            attempt=1,
            correlation_id=CORRELATION_ID,
            resources=(*_published_tile_resources()[:-1], _published_resources_with_report()[-1]),
            report=_published_report(),
        )

    with pytest.raises(ValidationError, match="Field required"):
        PublisherPublishResult(
            task_id=TASK_ID,
            step_id="publish_results",
            attempt=1,
            correlation_id=CORRELATION_ID,
            resources=_published_tile_resources(),
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


def test_task_query_exposes_only_same_task_publication_metadata() -> None:
    publication = PublisherPublishResult(
        task_id=TASK_ID,
        step_id="publish_results",
        attempt=1,
        correlation_id=CORRELATION_ID,
        resources=_published_resources_with_report(),
        report=_published_report(),
    )
    response = TaskResponse(
        task_id=TASK_ID,
        query="监测神农溪生态变化",
        status=TaskStatus.COMPLETED,
        progress=100,
        current_attempt=1,
        correlation_id=CORRELATION_ID,
        created_at=NOW,
        updated_at=NOW,
        publication=publication,
    )

    assert response.publication == publication

    with pytest.raises(ValidationError, match="publication must belong to the current task"):
        TaskResponse(
            task_id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
            query="监测神农溪生态变化",
            status=TaskStatus.COMPLETED,
            progress=100,
            current_attempt=1,
            correlation_id=CORRELATION_ID,
            created_at=NOW,
            updated_at=NOW,
            publication=publication,
        )

    with pytest.raises(ValidationError, match="publication must belong to the current task"):
        TaskResponse(
            task_id=TASK_ID,
            query="监测神农溪生态变化",
            status=TaskStatus.COMPLETED,
            progress=100,
            current_attempt=1,
            correlation_id=UUID("ffffffff-ffff-4fff-8fff-ffffffffffff"),
            created_at=NOW,
            updated_at=NOW,
            publication=publication,
        )


def test_task_query_exposes_only_current_attempt_analysis_and_quality_results() -> None:
    analysis = AnalysisRunResult(
        task_id=TASK_ID,
        step_id="analyze_ndvi_change",
        attempt=1,
        correlation_id=CORRELATION_ID,
        artifacts=tuple(
            valid_artifact(artifact_type=artifact_type)
            for artifact_type in (
                ArtifactType.NDVI_BEFORE,
                ArtifactType.NDVI_AFTER,
                ArtifactType.NDVI_DIFFERENCE,
                ArtifactType.CHANGE_CLASSIFICATION,
                ArtifactType.AREA_STATISTICS,
            )
        ),
        statistics=AreaStatistics(
            increase_hectares=10,
            stable_hectares=20,
            decrease_hectares=5,
            valid_hectares=35,
        ),
        elapsed_ms=120,
    )
    quality = QualityEvaluateResult(
        task_id=TASK_ID,
        step_id="evaluate_quality",
        attempt=1,
        correlation_id=CORRELATION_ID,
        metrics=_passing_quality(),
        artifact=valid_artifact(artifact_type=ArtifactType.QUALITY_REPORT).model_copy(
            update={"media_type": "application/json"}
        ),
    )
    response = TaskResponse(
        task_id=TASK_ID,
        query="监测神农溪生态变化",
        status=TaskStatus.PUBLISHING,
        progress=80,
        current_attempt=1,
        correlation_id=CORRELATION_ID,
        created_at=NOW,
        updated_at=NOW,
        analysis=analysis,
        quality=quality,
    )

    assert response.analysis == analysis
    assert response.quality == quality

    for field, result in (("analysis", analysis), ("quality", quality)):
        with pytest.raises(ValidationError, match=f"{field} must belong to the current task"):
            TaskResponse(
                task_id=TASK_ID,
                query="监测神农溪生态变化",
                status=TaskStatus.PENDING,
                progress=0,
                current_attempt=2,
                correlation_id=CORRELATION_ID,
                created_at=NOW,
                updated_at=NOW,
                **{field: result},
            )
