from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hennongxi_contracts import CreateTaskRequest, ErrorResponse, TaskResponse, TaskStatus
from hennongxi_contracts.openapi import create_contract_app
from hennongxi_master.main import app
from hennongxi_master.repository import WatershedCreate
from hennongxi_observability import CORRELATION_ID_HEADER

WATERSHED_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
CORRELATION_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
TASK_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


@dataclass
class StubTaskRepository:
    watershed_id: UUID | None = WATERSHED_ID
    tasks: dict[UUID, TaskResponse] = field(default_factory=dict)
    create_calls: list[dict[str, Any]] = field(default_factory=list)
    failure: Exception | None = None
    provisioning_failure: Exception | None = None

    async def get_watershed_id_by_slug(self, slug: str) -> UUID | None:
        assert slug == "shennongxi"
        return self.watershed_id

    async def ensure_watershed(self, value: WatershedCreate) -> None:
        if self.provisioning_failure is not None:
            raise self.provisioning_failure
        self.watershed_id = value.watershed_id

    async def create_task(self, **values: Any) -> TaskResponse:
        if self.failure is not None:
            raise self.failure
        self.create_calls.append(values)
        task = TaskResponse(
            task_id=values["task_id"],
            query=values["request"].query,
            status=TaskStatus.PENDING,
            progress=0,
            current_attempt=1,
            correlation_id=values["correlation_id"],
            created_at=values["created_at"],
            updated_at=values["created_at"],
        )
        self.tasks[task.task_id] = task
        return task

    async def get_task(self, task_id: UUID) -> TaskResponse | None:
        if self.failure is not None:
            raise self.failure
        return self.tasks.get(task_id)


@pytest.fixture
def repository() -> StubTaskRepository:
    return StubTaskRepository()


@pytest.fixture
def master_app(repository: StubTaskRepository) -> FastAPI:
    return app


def test_valid_chinese_request_is_accepted_without_waiting(
    master_app: FastAPI,
    repository: StubTaskRepository,
) -> None:
    with TestClient(master_app) as client:
        master_app.state.task_repository = repository
        response = client.post(
            "/api/v1/tasks",
            json={"query": "  分析神农溪植被变化  "},
            headers={CORRELATION_ID_HEADER: str(CORRELATION_ID)},
        )

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "PENDING"
    assert UUID(payload["task_id"]).version == 4
    assert response.headers[CORRELATION_ID_HEADER] == str(CORRELATION_ID)
    assert len(repository.create_calls) == 1
    assert repository.create_calls[0]["watershed_id"] == WATERSHED_ID
    assert repository.create_calls[0]["correlation_id"] == CORRELATION_ID
    assert repository.create_calls[0]["request"] == CreateTaskRequest(query="分析神农溪植被变化")


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"query": "   "},
        {"query": "变" * 2_001},
        {"query": "分析植被变化", "path": "/private/data"},
    ],
)
def test_invalid_task_request_returns_the_shared_safe_error(
    master_app: FastAPI,
    repository: StubTaskRepository,
    body: dict[str, Any],
) -> None:
    with TestClient(master_app) as client:
        master_app.state.task_repository = repository
        response = client.post("/api/v1/tasks", json=body)

    assert response.status_code == 422
    error = ErrorResponse.model_validate(response.json())
    assert error.error.code.value == "VALIDATION_ERROR"
    assert error.error.retryable is False
    assert repository.create_calls == []
    assert "/private/data" not in response.text


def test_missing_watershed_returns_a_retryable_safe_blocker(
    master_app: FastAPI,
    repository: StubTaskRepository,
) -> None:
    repository.watershed_id = None
    repository.provisioning_failure = RuntimeError("approved watershed cannot be persisted")

    with TestClient(master_app) as client:
        master_app.state.task_repository = repository
        response = client.post("/api/v1/tasks", json={"query": "分析神农溪植被变化"})

    assert response.status_code == 503
    error = ErrorResponse.model_validate(response.json())
    assert error.error.code.value == "DEPENDENCY_UNAVAILABLE"
    assert error.error.retryable is True
    assert repository.create_calls == []


def test_repository_failure_never_exposes_connection_details(
    master_app: FastAPI,
    repository: StubTaskRepository,
) -> None:
    private_detail = "postgresql://private-user:private-password@private-host/database"
    repository.failure = RuntimeError(private_detail)

    with TestClient(master_app) as client:
        master_app.state.task_repository = repository
        response = client.post("/api/v1/tasks", json={"query": "分析神农溪植被变化"})

    assert response.status_code == 503
    assert private_detail not in response.text
    assert "private-password" not in response.text


def test_task_query_returns_the_durable_repository_projection(
    master_app: FastAPI,
    repository: StubTaskRepository,
) -> None:
    now = datetime(2026, 7, 21, 1, 0, tzinfo=UTC)
    repository.tasks[TASK_ID] = TaskResponse(
        task_id=TASK_ID,
        query="分析神农溪植被变化",
        status=TaskStatus.PENDING,
        progress=0,
        current_attempt=1,
        correlation_id=CORRELATION_ID,
        created_at=now,
        updated_at=now,
    )

    with TestClient(master_app) as client:
        master_app.state.task_repository = repository
        response = client.get(f"/api/v1/tasks/{TASK_ID}")

    assert response.status_code == 200
    assert TaskResponse.model_validate(response.json()) == repository.tasks[TASK_ID]


def test_missing_and_invalid_task_ids_return_shared_errors(
    master_app: FastAPI,
    repository: StubTaskRepository,
) -> None:
    with TestClient(master_app) as client:
        master_app.state.task_repository = repository
        missing = client.get(f"/api/v1/tasks/{TASK_ID}")
        invalid = client.get("/api/v1/tasks/not-a-uuid")

    assert missing.status_code == 404
    assert ErrorResponse.model_validate(missing.json()).error.code.value == "TASK_NOT_FOUND"
    assert invalid.status_code == 422
    assert ErrorResponse.model_validate(invalid.json()).error.code.value == "VALIDATION_ERROR"


def test_runtime_task_routes_match_the_shared_openapi_shapes() -> None:
    runtime_paths = app.openapi()["paths"]
    contract_paths = create_contract_app().openapi()["paths"]

    for path, method in (
        ("/api/v1/tasks", "post"),
        ("/api/v1/tasks/{task_id}", "get"),
    ):
        runtime = runtime_paths[path][method]
        contract = contract_paths[path][method]
        assert set(runtime["responses"]) == set(contract["responses"])
        assert runtime.get("requestBody") == contract.get("requestBody")
        assert runtime.get("parameters") == contract.get("parameters")
        for status_code in runtime["responses"]:
            runtime_content = runtime["responses"][status_code].get("content")
            contract_content = contract["responses"][status_code].get("content")
            assert runtime_content == contract_content
