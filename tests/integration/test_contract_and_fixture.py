from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.routing import APIRoute
from hennongxi_analysis_agent.main import app as analysis_app
from hennongxi_contracts import LogicalDatasetId
from hennongxi_data_agent.dataset import run_preflight
from hennongxi_data_agent.main import app as data_app
from hennongxi_master.main import app as master_app
from hennongxi_publisher_agent.main import app as publisher_app
from hennongxi_quality_agent.main import app as quality_app

from tests.fixtures.deterministic_gis import write_deterministic_gis_fixture

_APPS: tuple[FastAPI, ...] = (
    master_app,
    data_app,
    analysis_app,
    quality_app,
    publisher_app,
)
_UNMODELED_RESOURCE_ROUTES = {
    "/api/v1/tasks/{task_id}/events",
    "/api/v1/tiles/{task_id}/{artifact_type}/{z}/{x}/{y}.png",
    "/api/v1/tasks/{task_id}/artifacts/{artifact_id}/download",
}


def test_every_json_http_boundary_uses_one_shared_versioned_contract_package() -> None:
    observed_unmodeled_routes: set[str] = set()
    observed_models: set[type[object]] = set()

    for app in _APPS:
        for route in app.routes:
            if not isinstance(route, APIRoute):
                continue
            body_models = tuple(
                parameter.field_info.annotation for parameter in route.dependant.body_params
            )
            response_model = route.response_model
            if response_model is None:
                observed_unmodeled_routes.add(route.path)
            else:
                observed_models.add(response_model)
            observed_models.update(body_models)

    assert observed_unmodeled_routes == _UNMODELED_RESOURCE_ROUTES
    assert observed_models
    assert all(model.__module__.startswith("hennongxi_contracts.") for model in observed_models)
    assert all("schema_version" in model.model_fields for model in observed_models)


def test_small_real_raster_fixture_passes_data_preflight(tmp_path: Path) -> None:
    fixture = write_deterministic_gis_fixture(tmp_path)

    report = run_preflight(
        fixture.manifest_path,
        data_root=fixture.data_root,
        cache_dir=fixture.cache_dir,
    )

    assert report.ok, report.format()
    assert tuple(asset.dataset_id for asset in report.assets) == tuple(LogicalDatasetId)
    assert report.valid_pixel_ratios == {
        LogicalDatasetId.BEFORE_RED.value: 1.0,
        LogicalDatasetId.BEFORE_NIR.value: 1.0,
        LogicalDatasetId.AFTER_RED.value: 1.0,
        LogicalDatasetId.AFTER_NIR.value: 1.0,
    }
    assert fixture.expected_change_pixel_counts == {
        "increase": 4,
        "stable": 8,
        "decrease": 4,
    }
