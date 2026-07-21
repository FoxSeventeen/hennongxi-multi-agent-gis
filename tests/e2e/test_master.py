from fastapi.testclient import TestClient

from tests.e2e.master import create_e2e_master_app


def _app():
    return create_e2e_master_app(
        {
            "APP_ENV": "test",
            "ORCHESTRATION_WORKER_ENABLED": "false",
        }
    )


def test_e2e_master_mode_control_accepts_only_authenticated_enum_values() -> None:
    app = _app()

    with TestClient(app) as client:
        response = client.put(
            "/internal/e2e/v1/study-area-mode",
            headers={"X-E2E-Control": "deterministic-e2e-control"},
            json={"mode": "verified"},
        )

    assert response.status_code == 204
    assert app.state.e2e_study_area_controller.mode == "verified"


def test_e2e_master_mode_control_rejects_bad_credentials_without_echoing_them() -> None:
    with TestClient(_app()) as client:
        response = client.put(
            "/internal/e2e/v1/study-area-mode",
            headers={"X-E2E-Control": "must-not-be-echoed"},
            json={"mode": "degraded"},
        )

    assert response.status_code == 404
    assert "must-not-be-echoed" not in response.text


def test_e2e_master_cannot_start_outside_the_test_environment() -> None:
    try:
        create_e2e_master_app({"APP_ENV": "production"})
    except RuntimeError as error:
        assert str(error) == "E2E master requires APP_ENV=test"
    else:
        raise AssertionError("E2E master unexpectedly started outside APP_ENV=test")
