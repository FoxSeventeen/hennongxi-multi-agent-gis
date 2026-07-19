from __future__ import annotations

from collections.abc import Iterator
from importlib import import_module

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hennongxi_observability import CORRELATION_ID_HEADER

SERVICE_MODULES = (
    ("hennongxi_master.main", "master", 8000),
    ("hennongxi_data_agent.main", "data", 8001),
    ("hennongxi_analysis_agent.main", "analysis", 8002),
    ("hennongxi_quality_agent.main", "quality", 8003),
    ("hennongxi_publisher_agent.main", "publisher", 8004),
)


@pytest.fixture(params=SERVICE_MODULES, ids=[item[1] for item in SERVICE_MODULES])
def service(request: pytest.FixtureRequest) -> Iterator[tuple[FastAPI, str, int]]:
    module_name, service_name, port = request.param
    module = import_module(module_name)
    app = module.app
    with TestClient(app):
        yield app, service_name, port


def test_each_agent_is_an_independent_app_with_its_assigned_port(
    service: tuple[FastAPI, str, int],
) -> None:
    app, service_name, port = service

    assert app.state.service_name == service_name
    assert app.state.port == port
    assert app.state.started is True


def test_each_agent_exposes_only_its_approved_health_routes(
    service: tuple[FastAPI, str, int],
) -> None:
    app, service_name, _ = service
    application_paths = {route.path for route in app.routes}

    expected_paths = {"/internal/v1/health"}
    if service_name == "master":
        expected_paths |= {"/api/v1/health", "/api/v1/config/readiness"}
    assert application_paths == expected_paths
    with TestClient(app) as client:
        response = client.get("/internal/v1/health")

    assert response.status_code == 200
    assert response.json()["service"] == service_name
    assert response.json()["state"] == "HEALTHY"
    assert response.json()["schema_version"] == "1.0"


def test_health_request_propagates_the_correlation_header(
    service: tuple[FastAPI, str, int],
) -> None:
    app, _, _ = service
    correlation_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"

    with TestClient(app) as client:
        response = client.get(
            "/internal/v1/health",
            headers={CORRELATION_ID_HEADER: correlation_id},
        )

    assert response.headers[CORRELATION_ID_HEADER] == correlation_id


def test_five_modules_do_not_share_one_fastapi_instance() -> None:
    apps = [import_module(module_name).app for module_name, _, _ in SERVICE_MODULES]

    assert len({id(app) for app in apps}) == 5
