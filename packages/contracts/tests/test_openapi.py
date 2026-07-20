from __future__ import annotations

from pathlib import Path

import yaml
from hennongxi_contracts.openapi import build_openapi_document
from openapi_spec_validator import validate

ROOT = Path(__file__).parents[3]
OPENAPI_PATH = ROOT / "docs" / "openapi.yaml"

PUBLIC_PATHS = {
    "/api/v1/tasks",
    "/api/v1/tasks/{task_id}",
    "/api/v1/tasks/{task_id}/events",
    "/api/v1/tasks/{task_id}/retry",
    "/api/v1/health",
    "/api/v1/config/readiness",
    "/api/v1/tiles/{task_id}/{artifact_type}/{z}/{x}/{y}.png",
    "/api/v1/tasks/{task_id}/artifacts/{artifact_id}/download",
}

INTERNAL_PATHS = {
    "/internal/v1/health",
    "/internal/v1/data/prepare",
    "/internal/v1/analysis/run",
    "/internal/v1/quality/evaluate",
    "/internal/v1/publisher/publish",
}


def load_checked_in_openapi() -> dict[str, object]:
    with OPENAPI_PATH.open(encoding="utf-8") as stream:
        document = yaml.safe_load(stream)
    assert isinstance(document, dict)
    return document


def test_checked_in_openapi_is_valid_and_matches_the_model_source() -> None:
    document = load_checked_in_openapi()

    validate(document)
    assert document == build_openapi_document()


def test_openapi_contains_only_the_approved_public_and_internal_paths() -> None:
    document = load_checked_in_openapi()
    paths = document["paths"]

    assert isinstance(paths, dict)
    assert set(paths) == PUBLIC_PATHS | INTERNAL_PATHS


def test_create_retry_and_sse_status_and_content_types_are_frozen() -> None:
    document = load_checked_in_openapi()
    paths = document["paths"]

    assert "202" in paths["/api/v1/tasks"]["post"]["responses"]
    assert "202" in paths["/api/v1/tasks/{task_id}/retry"]["post"]["responses"]
    event_content = paths["/api/v1/tasks/{task_id}/events"]["get"]["responses"]["200"]["content"]
    assert set(event_content) == {"text/event-stream"}


def test_publisher_resource_routes_are_read_only() -> None:
    document = load_checked_in_openapi()
    paths = document["paths"]

    for path in PUBLIC_PATHS:
        if path.startswith("/api/v1/tiles/") or path.endswith("/download"):
            operations = set(paths[path]) - {"parameters"}
            assert operations == {"get"}


def test_tile_route_documents_png_success_and_json_failures() -> None:
    document = load_checked_in_openapi()
    responses = document["paths"]["/api/v1/tiles/{task_id}/{artifact_type}/{z}/{x}/{y}.png"]["get"][
        "responses"
    ]

    assert set(responses["200"]["content"]) == {"image/png"}
    for status_code in ("404", "409", "422", "500"):
        assert set(responses[status_code]["content"]) == {"application/json"}


def test_analysis_route_requires_idempotency_header_and_returns_timing() -> None:
    document = load_checked_in_openapi()
    operation = document["paths"]["/internal/v1/analysis/run"]["post"]

    headers = {
        parameter["name"]: parameter
        for parameter in operation["parameters"]
        if parameter["in"] == "header"
    }
    assert set(headers) == {"Idempotency-Key", "X-Correlation-ID"}
    assert all(header["required"] is True for header in headers.values())

    result_schema = document["components"]["schemas"]["AnalysisRunResult"]
    assert "elapsed_ms" in result_schema["required"]
    assert "500" in operation["responses"]


def test_quality_route_requires_idempotency_and_correlation_headers() -> None:
    document = load_checked_in_openapi()
    operation = document["paths"]["/internal/v1/quality/evaluate"]["post"]

    headers = {
        parameter["name"]: parameter
        for parameter in operation["parameters"]
        if parameter["in"] == "header"
    }
    assert set(headers) == {"Idempotency-Key", "X-Correlation-ID"}
    assert all(header["required"] is True for header in headers.values())

    metrics_schema = document["components"]["schemas"]["QualityMetrics"]
    assert {"thresholds", "conclusion", "evidence"} <= set(metrics_schema["required"])
    assert "500" in operation["responses"]


def test_publisher_resources_expose_complete_tile_visualization_metadata() -> None:
    document = load_checked_in_openapi()
    schemas = document["components"]["schemas"]

    resource = schemas["PublishedResource"]
    assert "tile_metadata" in resource["properties"]
    tile_metadata = schemas["TileMetadata"]
    assert {
        "artifact_type",
        "bounds_wgs84",
        "start_date",
        "end_date",
        "units",
        "attribution",
        "legend",
    } <= set(tile_metadata["required"])
    legend = schemas["TileLegendEntry"]
    assert {"value", "label", "color"} <= set(legend["required"])
