"""Private deterministic upstreams used only by the Compose E2E profile."""

from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, status

_EXPECTED_AUTHORIZATION = "Bearer deterministic-e2e-key"
_PLAN_CONTENT = (
    '{"steps":['
    '{"kind":"prepare_data","title":"准备批准数据"},'
    '{"kind":"analyze_ndvi_change","title":"计算 NDVI 变化"},'
    '{"kind":"evaluate_quality","title":"核验成果质量"},'
    '{"kind":"publish_results","title":"发布地图与报告"}'
    "]}"
)

app = FastAPI(title="Hennongxi E2E Support", docs_url=None, redoc_url=None)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/chat/completions")
async def create_plan(
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    if authorization != _EXPECTED_AUTHORIZATION:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid E2E support credential",
        )
    return {
        "choices": [{"message": {"content": _PLAN_CONTENT}}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 40},
    }
