"""Generate the checked-in OpenAPI contract without implementing service behavior."""

from __future__ import annotations

from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import Body, FastAPI, Header, Path
from fastapi.responses import Response

from hennongxi_contracts.artifacts import TileArtifactType
from hennongxi_contracts.commands import (
    AnalysisRunCommand,
    AnalysisRunResult,
    DataPrepareCommand,
    DataPrepareResult,
    PublisherPublishCommand,
    PublisherPublishResult,
    QualityEvaluateCommand,
    QualityEvaluateResult,
)
from hennongxi_contracts.errors import ErrorResponse
from hennongxi_contracts.events import TaskEvent
from hennongxi_contracts.health import HealthResponse, ReadinessResponse, ServiceHealth
from hennongxi_contracts.tasks import (
    CreateTaskRequest,
    RetryAcceptedResponse,
    TaskAcceptedResponse,
    TaskResponse,
)


class EventStreamResponse(Response):
    media_type = "text/event-stream"


class PngResponse(Response):
    media_type = "image/png"


class ArtifactDownloadResponse(Response):
    media_type = "application/pdf"


def _not_implemented() -> NoReturn:
    raise NotImplementedError("This application exists only to generate the API contract")


def _errors(*status_codes: int) -> dict[int | str, dict[str, object]]:
    return {
        status_code: {
            "model": ErrorResponse,
            "description": "Structured error without secret or provider payload fields.",
        }
        for status_code in status_codes
    }


def _resource_errors(*status_codes: int) -> dict[int | str, dict[str, object]]:
    return {
        status_code: {
            "description": "Structured error without secret or provider payload fields.",
            "content": {
                "application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}
            },
        }
        for status_code in status_codes
    }


def create_contract_app() -> FastAPI:
    """Describe public and internal routes while keeping runtime handlers elsewhere."""

    app = FastAPI(
        title="神农溪分布式多 Agent GIS API",
        summary="Versioned workflow, resource, and internal Agent contracts",
        description=(
            "The Master owns workflow routes; Publisher owns read-only tile/download routes. "
            "Internal routes are reachable only on the Compose network."
        ),
        version="1.0.0",
        openapi_version="3.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.post(
        "/api/v1/tasks",
        operation_id="createTask",
        tags=["Master public"],
        status_code=202,
        response_model=TaskAcceptedResponse,
        responses=_errors(422, 503),
    )
    def create_task(request: Annotated[CreateTaskRequest, Body()]) -> TaskAcceptedResponse:
        """Accept a Chinese ecological-monitoring request without blocking on analysis."""

        _not_implemented()

    @app.get(
        "/api/v1/tasks/{task_id}",
        operation_id="getTask",
        tags=["Master public"],
        response_model=TaskResponse,
        responses=_errors(404, 422),
    )
    def get_task(task_id: Annotated[UUID, Path()]) -> TaskResponse:
        """Reconstruct durable task, plan, step, error, and artifact state."""

        _not_implemented()

    @app.get(
        "/api/v1/tasks/{task_id}/events",
        operation_id="streamTaskEvents",
        tags=["Master public"],
        response_model=TaskEvent,
        response_class=EventStreamResponse,
        responses=_resource_errors(404, 422),
        openapi_extra={
            "description": (
                "SSE stream. Each event id is the durable monotonic sequence and each data "
                "field is one TaskEvent JSON object. Last-Event-ID requests replay."
            )
        },
    )
    def stream_task_events(task_id: Annotated[UUID, Path()]) -> TaskEvent:
        _not_implemented()

    @app.post(
        "/api/v1/tasks/{task_id}/retry",
        operation_id="retryTask",
        tags=["Master public"],
        status_code=202,
        response_model=RetryAcceptedResponse,
        responses=_errors(404, 409, 422),
    )
    def retry_task(task_id: Annotated[UUID, Path()]) -> RetryAcceptedResponse:
        """Create an immutable new attempt from a validated safe checkpoint."""

        _not_implemented()

    @app.get(
        "/api/v1/health",
        operation_id="getAggregateHealth",
        tags=["Master public"],
        response_model=HealthResponse,
    )
    def get_health() -> HealthResponse:
        _not_implemented()

    @app.get(
        "/api/v1/config/readiness",
        operation_id="getConfigurationReadiness",
        tags=["Master public"],
        response_model=ReadinessResponse,
    )
    def get_readiness() -> ReadinessResponse:
        """Report blockers without returning keys, private URLs, or other secrets."""

        _not_implemented()

    @app.get(
        "/api/v1/tiles/{task_id}/{artifact_type}/{z}/{x}/{y}.png",
        operation_id="getArtifactTile",
        tags=["Publisher resources"],
        response_class=PngResponse,
        responses=_resource_errors(404, 409, 422, 500),
    )
    def get_artifact_tile(
        task_id: Annotated[UUID, Path()],
        artifact_type: Annotated[TileArtifactType, Path()],
        z: Annotated[int, Path(ge=0, le=24)],
        x: Annotated[int, Path(ge=0)],
        y: Annotated[int, Path(ge=0)],
    ) -> Response:
        _not_implemented()

    @app.get(
        "/api/v1/tasks/{task_id}/artifacts/{artifact_id}/download",
        operation_id="downloadArtifact",
        tags=["Publisher resources"],
        response_class=ArtifactDownloadResponse,
        responses=_resource_errors(404, 409, 422),
    )
    def download_artifact(
        task_id: Annotated[UUID, Path()], artifact_id: Annotated[UUID, Path()]
    ) -> Response:
        _not_implemented()

    @app.get(
        "/internal/v1/health",
        operation_id="getAgentHealth",
        tags=["Internal Agent"],
        response_model=ServiceHealth,
    )
    def get_agent_health() -> ServiceHealth:
        """Return local process health on the private Compose network."""

        _not_implemented()

    @app.post(
        "/internal/v1/data/prepare",
        operation_id="prepareData",
        tags=["Internal Agent"],
        response_model=DataPrepareResult,
        responses=_errors(409, 422, 503),
    )
    def prepare_data(command: Annotated[DataPrepareCommand, Body()]) -> DataPrepareResult:
        _not_implemented()

    @app.post(
        "/internal/v1/analysis/run",
        operation_id="runAnalysis",
        tags=["Internal Agent"],
        response_model=AnalysisRunResult,
        responses=_errors(409, 422, 500, 503),
    )
    def run_analysis(
        command: Annotated[AnalysisRunCommand, Body()],
        idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
        correlation_id: Annotated[UUID, Header(alias="X-Correlation-ID")],
    ) -> AnalysisRunResult:
        _not_implemented()

    @app.post(
        "/internal/v1/quality/evaluate",
        operation_id="evaluateQuality",
        tags=["Internal Agent"],
        response_model=QualityEvaluateResult,
        responses=_errors(409, 422, 500, 503),
    )
    def evaluate_quality(
        command: Annotated[QualityEvaluateCommand, Body()],
        idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
        correlation_id: Annotated[UUID, Header(alias="X-Correlation-ID")],
    ) -> QualityEvaluateResult:
        _not_implemented()

    @app.post(
        "/internal/v1/publisher/publish",
        operation_id="publishResults",
        tags=["Internal Agent"],
        response_model=PublisherPublishResult,
        responses=_errors(409, 422, 500, 503),
    )
    def publish_results(
        command: Annotated[PublisherPublishCommand, Body()],
        idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
        correlation_id: Annotated[UUID, Header(alias="X-Correlation-ID")],
    ) -> PublisherPublishResult:
        _not_implemented()

    return app


def build_openapi_document() -> dict[str, object]:
    """Return the deterministic source document checked into ``docs/openapi.yaml``."""

    return create_contract_app().openapi()
