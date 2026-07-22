from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from hennongxi_contracts import (
    AnalysisRunCommand,
    AnalysisRunResult,
    AreaStatistics,
    ArtifactRef,
    ArtifactStatus,
    ArtifactType,
    DataAssetRef,
    DataPrepareCommand,
    DataPrepareResult,
    ErrorCode,
    LogicalDatasetId,
    PublisherPublishCommand,
    QualityConclusion,
    QualityEvaluateCommand,
    QualityMetrics,
    QualityThresholds,
)
from hennongxi_master.agent_client import (
    AgentCallError,
    AgentClientConfig,
    AgentHttpClient,
)
from pydantic import ValidationError

TASK_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
OTHER_TASK_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
CORRELATION_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
NOW = datetime(2026, 7, 21, 8, 0, tzinfo=UTC)
SHA256 = "a" * 64


class FailIfReadStream(httpx.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        raise AssertionError("unsafe Agent response body must not be read")
        yield b"unreachable-private-agent-body"


class ChunkedResponseStream(httpx.AsyncByteStream):
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


def _config() -> AgentClientConfig:
    return AgentClientConfig(
        data_base_url="http://data-agent:8001",
        analysis_base_url="http://analysis-agent:8002",
        quality_base_url="http://quality-agent:8003",
        publisher_base_url="http://publisher-agent:8004",
        connect_timeout_seconds=2,
        read_timeout_seconds=30,
    )


def _assets() -> tuple[DataAssetRef, ...]:
    return tuple(
        DataAssetRef(
            dataset_id=dataset_id,
            checksum_sha256=SHA256,
            byte_size=10,
        )
        for dataset_id in LogicalDatasetId
    )


def _artifact(
    artifact_type: ArtifactType,
    *,
    task_id: UUID = TASK_ID,
) -> ArtifactRef:
    media_type = {
        ArtifactType.AREA_STATISTICS: "application/json",
        ArtifactType.QUALITY_REPORT: "application/json",
        ArtifactType.PDF_REPORT: "application/pdf",
    }.get(artifact_type, "image/tiff; application=geotiff")
    return ArtifactRef(
        artifact_id=uuid4(),
        task_id=task_id,
        attempt=1,
        artifact_type=artifact_type,
        status=ArtifactStatus.COMPLETE,
        media_type=media_type,
        created_at=NOW,
        checksum_sha256=SHA256,
        byte_size=10,
    )


def _analysis_artifacts(*, task_id: UUID = TASK_ID) -> tuple[ArtifactRef, ...]:
    return tuple(
        _artifact(artifact_type, task_id=task_id)
        for artifact_type in (
            ArtifactType.NDVI_BEFORE,
            ArtifactType.NDVI_AFTER,
            ArtifactType.NDVI_DIFFERENCE,
            ArtifactType.CHANGE_CLASSIFICATION,
            ArtifactType.AREA_STATISTICS,
        )
    )


def _quality() -> QualityMetrics:
    return QualityMetrics(
        coverage_ratio=0.98,
        valid_pixel_ratio=0.96,
        output_complete=True,
        elapsed_ms=25,
        thresholds=QualityThresholds(
            minimum_watershed_coverage_ratio=0.95,
            minimum_valid_pixel_ratio=0.90,
        ),
        conclusion=QualityConclusion.PASS,
        passed=True,
        evidence=("范围通过", "像元通过", "成果完整", "耗时已记录"),
    )


def _data_command() -> DataPrepareCommand:
    return DataPrepareCommand(
        task_id=TASK_ID,
        step_id="prepare_data",
        attempt=1,
        correlation_id=CORRELATION_ID,
        dataset_ids=tuple(LogicalDatasetId),
    )


def _analysis_command() -> AnalysisRunCommand:
    return AnalysisRunCommand(
        task_id=TASK_ID,
        step_id="analyze_ndvi_change",
        attempt=1,
        correlation_id=CORRELATION_ID,
        inputs=_assets(),
    )


def _quality_command() -> QualityEvaluateCommand:
    return QualityEvaluateCommand(
        task_id=TASK_ID,
        step_id="evaluate_quality",
        attempt=1,
        correlation_id=CORRELATION_ID,
        artifacts=_analysis_artifacts(),
        analysis_elapsed_ms=120,
    )


def _publisher_command() -> PublisherPublishCommand:
    return PublisherPublishCommand(
        task_id=TASK_ID,
        step_id="publish_results",
        attempt=1,
        correlation_id=CORRELATION_ID,
        artifacts=(*_analysis_artifacts(), _artifact(ArtifactType.QUALITY_REPORT)),
        quality=_quality(),
    )


def test_agent_client_configuration_accepts_only_clean_http_origins() -> None:
    assert _config().analysis_base_url == "http://analysis-agent:8002"

    with pytest.raises(ValidationError, match="HTTP origin"):
        AgentClientConfig(
            **{
                **_config().model_dump(),
                "data_base_url": "http://user:private@data-agent:8001/private?token=secret",
            }
        )


@pytest.mark.asyncio
async def test_data_call_uses_fixed_route_and_propagates_correlation_identity() -> None:
    command = _data_command()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("http://data-agent:8001/internal/v1/data/prepare")
        assert request.headers["X-Correlation-ID"] == str(CORRELATION_ID)
        assert request.headers["Accept-Encoding"] == "identity"
        assert DataPrepareCommand.model_validate_json(request.content) == command
        result = DataPrepareResult(
            task_id=TASK_ID,
            step_id=command.step_id,
            attempt=command.attempt,
            correlation_id=CORRELATION_ID,
            assets=_assets(),
        )
        return httpx.Response(200, json=result.model_dump(mode="json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport_client:
        result = await AgentHttpClient(_config(), transport_client).prepare_data(command)

    assert result.task_id == TASK_ID
    assert {asset.dataset_id for asset in result.assets} == set(LogicalDatasetId)


@pytest.mark.asyncio
async def test_analysis_call_sends_stable_execution_headers_and_validates_response() -> None:
    command = _analysis_command()
    idempotency_key = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("http://analysis-agent:8002/internal/v1/analysis/run")
        assert request.headers["Idempotency-Key"] == str(idempotency_key)
        assert request.headers["X-Correlation-ID"] == str(CORRELATION_ID)
        assert "reuse_from_attempt" not in json.loads(request.content)
        result = AnalysisRunResult(
            task_id=TASK_ID,
            step_id=command.step_id,
            attempt=command.attempt,
            correlation_id=CORRELATION_ID,
            artifacts=_analysis_artifacts(),
            statistics=AreaStatistics(
                increase_hectares=10,
                stable_hectares=20,
                decrease_hectares=5,
                valid_hectares=35,
            ),
            elapsed_ms=120,
        )
        return httpx.Response(200, json=result.model_dump(mode="json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport_client:
        result = await AgentHttpClient(_config(), transport_client).run_analysis(
            command,
            idempotency_key=idempotency_key,
        )

    assert result.elapsed_ms == 120
    assert len(result.artifacts) == 5


@pytest.mark.asyncio
async def test_non_success_response_keeps_code_but_discards_private_body_text() -> None:
    private_text = "postgresql://private-user:private-password@internal/secret"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={
                "schema_version": "1.0",
                "error": {
                    "schema_version": "1.0",
                    "code": "DEPENDENCY_UNAVAILABLE",
                    "message": private_text,
                    "retryable": True,
                    "details": [],
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport_client:
        with pytest.raises(AgentCallError) as captured:
            await AgentHttpClient(_config(), transport_client).evaluate_quality(
                _quality_command(),
                idempotency_key=uuid4(),
            )

    assert captured.value.error.code is ErrorCode.DEPENDENCY_UNAVAILABLE
    assert captured.value.error.retryable is True
    assert private_text not in str(captured.value)
    assert private_text not in repr(captured.value)
    assert private_text not in captured.value.error.message


@pytest.mark.asyncio
async def test_success_response_with_wrong_scope_is_rejected_as_untrusted() -> None:
    command = _data_command()

    def handler(_request: httpx.Request) -> httpx.Response:
        result = DataPrepareResult(
            task_id=OTHER_TASK_ID,
            step_id=command.step_id,
            attempt=command.attempt,
            correlation_id=CORRELATION_ID,
            assets=_assets(),
        )
        return httpx.Response(200, json=result.model_dump(mode="json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport_client:
        with pytest.raises(AgentCallError) as captured:
            await AgentHttpClient(_config(), transport_client).prepare_data(command)

    assert captured.value.error.code is ErrorCode.INTERNAL_ERROR
    assert captured.value.error.retryable is True


@pytest.mark.asyncio
async def test_timeout_and_oversized_response_fail_without_leaking_transport_details() -> None:
    private_timeout = "request to http://private-token@analysis-agent timed out"

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(private_timeout, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler)) as client:
        with pytest.raises(AgentCallError) as timeout_error:
            await AgentHttpClient(_config(), client).publish_results(
                _publisher_command(),
                idempotency_key=uuid4(),
            )

    assert timeout_error.value.error.code is ErrorCode.DEPENDENCY_UNAVAILABLE
    assert private_timeout not in str(timeout_error.value)

    def oversized_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=ChunkedResponseStream((b"x" * (512 * 1024), b"x")),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(oversized_handler)) as client:
        with pytest.raises(AgentCallError) as oversized_error:
            await AgentHttpClient(_config(), client).prepare_data(_data_command())

    assert oversized_error.value.error.code is ErrorCode.INTERNAL_ERROR


@pytest.mark.parametrize(
    "headers",
    [
        {"content-encoding": "gzip"},
        {"content-encoding": ""},
        {"content-encoding": "\x0bidentity"},
        {"content-length": str(512 * 1024 + 1)},
        {"content-length": "+1"},
        {"content-length": "9" * 5_000},
    ],
    ids=[
        "encoded",
        "empty-encoding",
        "invalid-encoding-whitespace",
        "declared-oversized",
        "invalid-length",
        "pathological-length",
    ],
)
@pytest.mark.asyncio
async def test_agent_client_rejects_unsafe_response_before_reading_body(
    headers: dict[str, str],
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers=headers, stream=FailIfReadStream())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AgentCallError) as raised:
            await AgentHttpClient(_config(), client).prepare_data(_data_command())

    assert raised.value.error.code is ErrorCode.INTERNAL_ERROR
    assert raised.value.error.retryable is True
