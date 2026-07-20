from __future__ import annotations

import json
import logging
from io import StringIO
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from hennongxi_observability import (
    CORRELATION_ID_HEADER,
    CorrelationIdMiddleware,
    configure_logging,
    correlation_headers,
)


def create_probe_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)

    @app.get("/probe")
    def probe() -> dict[str, dict[str, str]]:
        return {"outbound_headers": correlation_headers()}

    return app


def test_correlation_id_is_echoed_and_available_to_outbound_calls() -> None:
    correlation_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

    with TestClient(create_probe_app()) as client:
        response = client.get("/probe", headers={CORRELATION_ID_HEADER: correlation_id})

    assert response.status_code == 200
    assert response.headers[CORRELATION_ID_HEADER] == correlation_id
    assert response.json()["outbound_headers"] == {CORRELATION_ID_HEADER: correlation_id}


def test_missing_or_invalid_correlation_id_is_replaced_with_a_uuid() -> None:
    with TestClient(create_probe_app()) as client:
        missing = client.get("/probe")
        invalid = client.get("/probe", headers={CORRELATION_ID_HEADER: "../../unsafe"})

    assert UUID(missing.headers[CORRELATION_ID_HEADER])
    assert UUID(invalid.headers[CORRELATION_ID_HEADER])
    assert invalid.headers[CORRELATION_ID_HEADER] != "../../unsafe"


def test_request_log_is_json_traceable_and_does_not_include_query_secrets() -> None:
    stream = StringIO()
    configure_logging(stream=stream)
    correlation_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

    with TestClient(create_probe_app()) as client:
        response = client.get(
            "/probe?api_key=must-not-be-logged",
            headers={CORRELATION_ID_HEADER: correlation_id},
        )

    records = [json.loads(line) for line in stream.getvalue().splitlines() if line]
    completed = next(record for record in records if record["event"] == "request_completed")
    assert response.status_code == 200
    assert completed["correlation_id"] == correlation_id
    assert completed["path"] == "/probe"
    assert completed["status_code"] == 200
    assert "must-not-be-logged" not in stream.getvalue()


def test_external_http_access_logs_are_dropped_before_secrets_can_render() -> None:
    stream = StringIO()
    configure_logging(stream=stream)
    private_value = "private-authorization-value"

    logging.getLogger("httpx").warning("Authorization: Bearer %s", private_value)
    logging.getLogger("hennongxi.master").warning("safe model-call status")

    rendered = stream.getvalue()
    assert "safe model-call status" in rendered
    assert private_value not in rendered
    assert "Authorization" not in rendered
