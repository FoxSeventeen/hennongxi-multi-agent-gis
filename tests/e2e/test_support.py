from fastapi.testclient import TestClient

from tests.e2e.support import app


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
