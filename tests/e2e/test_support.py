from fastapi.testclient import TestClient

from tests.e2e.support import app, create_support_app


def test_fake_llm_returns_only_the_approved_four_step_plan() -> None:
    response = TestClient(app).post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer deterministic-e2e-key"},
        json={"model": "deterministic-e2e", "messages": []},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["choices"][0]["message"]["content"] == (
        '{"steps":['
        '{"kind":"prepare_data","title":"准备批准数据"},'
        '{"kind":"analyze_ndvi_change","title":"计算 NDVI 变化"},'
        '{"kind":"evaluate_quality","title":"核验成果质量"},'
        '{"kind":"publish_results","title":"发布地图与报告"}'
        "]}"
    )
    assert payload["usage"] == {"prompt_tokens": 20, "completion_tokens": 40}


def test_fake_llm_rejects_any_other_bearer_value_without_echoing_it() -> None:
    response = TestClient(app).post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer must-not-be-echoed"},
        json={"model": "deterministic-e2e", "messages": []},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid E2E support credential"}
    assert "must-not-be-echoed" not in response.text


def test_publisher_failure_control_accepts_only_one_bounded_authenticated_failure() -> None:
    support = create_support_app()

    with TestClient(support) as client:
        accepted = client.put(
            "/internal/e2e/v1/publisher-failure",
            headers={"X-E2E-Control": "deterministic-e2e-control"},
            json={"failures": 1},
        )
        invalid = client.put(
            "/internal/e2e/v1/publisher-failure",
            headers={"X-E2E-Control": "deterministic-e2e-control"},
            json={"failures": 2},
        )
        rejected = client.put(
            "/internal/e2e/v1/publisher-failure",
            headers={"X-E2E-Control": "must-not-be-echoed"},
            json={"failures": 0},
        )

    assert accepted.status_code == 204
    assert support.state.publisher_failure_controller.failures_remaining == 1
    assert invalid.status_code == 422
    assert rejected.status_code == 404
    assert "must-not-be-echoed" not in rejected.text
