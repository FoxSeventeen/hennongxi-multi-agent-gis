from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hennongxi_contracts import HealthState, ServiceHealth, ServiceName
from hennongxi_master.main import app

CHECKED_AT = datetime(2026, 7, 19, 8, 0, tzinfo=UTC)
DEPENDENCY_NAMES = (
    ServiceName.DATA,
    ServiceName.ANALYSIS,
    ServiceName.QUALITY,
    ServiceName.PUBLISHER,
    ServiceName.POSTGIS,
    ServiceName.REDIS,
)


@dataclass(frozen=True)
class StubDependencyProbe:
    services: tuple[ServiceHealth, ...]

    async def check_dependencies(self) -> tuple[ServiceHealth, ...]:
        return self.services


def dependency_health(
    unavailable: ServiceName | None = None,
) -> tuple[ServiceHealth, ...]:
    return tuple(
        ServiceHealth(
            service=name,
            state=(HealthState.UNAVAILABLE if name == unavailable else HealthState.HEALTHY),
            checked_at=CHECKED_AT,
            message=("health check failed" if name == unavailable else None),
        )
        for name in DEPENDENCY_NAMES
    )


@pytest.fixture
def master_app() -> Iterator[FastAPI]:
    original_probe = app.state.dependency_probe
    app.state.dependency_probe = StubDependencyProbe(dependency_health())
    try:
        yield app
    finally:
        app.state.dependency_probe = original_probe


def test_local_liveness_does_not_depend_on_aggregate_health(master_app: FastAPI) -> None:
    master_app.state.dependency_probe = StubDependencyProbe(dependency_health(ServiceName.POSTGIS))

    with TestClient(master_app) as client:
        response = client.get("/internal/v1/health")

    assert response.status_code == 200
    assert response.json()["state"] == "HEALTHY"
    assert response.json()["service"] == "master"


def test_aggregate_health_reports_every_runtime_dependency(master_app: FastAPI) -> None:
    with TestClient(master_app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "HEALTHY"
    assert [service["service"] for service in payload["services"]] == [
        "master",
        "data",
        "analysis",
        "quality",
        "publisher",
        "postgis",
        "redis",
    ]


def test_aggregate_health_is_unavailable_when_one_dependency_fails(
    master_app: FastAPI,
) -> None:
    master_app.state.dependency_probe = StubDependencyProbe(dependency_health(ServiceName.REDIS))

    with TestClient(master_app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["state"] == "UNAVAILABLE"
    assert (
        next(service for service in response.json()["services"] if service["service"] == "redis")[
            "state"
        ]
        == "UNAVAILABLE"
    )


def test_configuration_readiness_reports_safe_blockers(
    master_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("DATA_MANIFEST_PATH", "/missing/manifest.json")
    master_app.state.dependency_probe = StubDependencyProbe(dependency_health(ServiceName.DATA))

    with TestClient(master_app) as client:
        response = client.get("/api/v1/config/readiness")

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "1.0",
        "ready": False,
        "llm_configured": False,
        "data_configured": False,
        "blockers": [
            "LLM_NOT_CONFIGURED",
            "DATA_NOT_CONFIGURED",
            "DEPENDENCY_UNAVAILABLE",
        ],
        "messages": [
            "LLM configuration is incomplete",
            "data manifest is unavailable",
            "one or more runtime dependencies are unavailable",
        ],
    }


def test_configuration_readiness_never_returns_secrets_or_private_locations(
    master_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("LLM_API_KEY", "do-not-return-this-secret")
    monkeypatch.setenv("LLM_BASE_URL", "https://private-llm.example/v1")
    monkeypatch.setenv("LLM_MODEL", "private-model-name")
    monkeypatch.setenv("DATA_MANIFEST_PATH", str(manifest))

    with TestClient(master_app) as client:
        response = client.get("/api/v1/config/readiness")

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "1.0",
        "ready": True,
        "llm_configured": True,
        "data_configured": True,
        "blockers": [],
        "messages": [],
    }
    assert "do-not-return-this-secret" not in response.text
    assert "private-llm.example" not in response.text
    assert str(manifest) not in response.text
