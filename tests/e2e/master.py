"""Test-only Master entrypoint with deterministic study-area verification."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Response, status
from hennongxi_master.amap import AmapVerification, AmapVerificationCode
from hennongxi_master.main import create_master_app
from hennongxi_master.runtime import create_worker_runtime
from hennongxi_master.study_area import StudyAreaGrounder
from pydantic import BaseModel, ConfigDict

_CONTROL_CREDENTIAL = "deterministic-e2e-control"


class E2eStudyAreaMode(StrEnum):
    VERIFIED = "verified"
    DEGRADED = "degraded"


class _ModeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: E2eStudyAreaMode


@dataclass(slots=True)
class _StudyAreaController:
    mode: E2eStudyAreaMode = E2eStudyAreaMode.DEGRADED


@dataclass(frozen=True, slots=True, repr=False)
class _DeterministicStudyAreaVerifier:
    controller: _StudyAreaController

    async def verify(self) -> AmapVerification:
        verified = self.controller.mode is E2eStudyAreaMode.VERIFIED
        return AmapVerification(
            code=(
                AmapVerificationCode.VERIFIED
                if verified
                else AmapVerificationCode.PROVIDER_UNAVAILABLE
            ),
            checked_at=datetime.now(UTC),
            duration_ms=0,
            retryable=not verified,
            match_count=1 if verified else 0,
        )


def create_e2e_master_app(environment: Mapping[str, str] | None = None) -> FastAPI:
    values = os.environ if environment is None else environment
    if values.get("APP_ENV") != "test":
        raise RuntimeError("E2E master requires APP_ENV=test")

    controller = _StudyAreaController()
    master = create_master_app(values)
    master.state.e2e_study_area_controller = controller
    master.state.worker_runtime_factory = lambda repository, config, event_store: (
        create_worker_runtime(
            repository,
            config,
            values,
            event_store,
            study_area_grounder=StudyAreaGrounder(_DeterministicStudyAreaVerifier(controller)),
        )
    )

    @master.put(
        "/internal/e2e/v1/study-area-mode",
        status_code=status.HTTP_204_NO_CONTENT,
        include_in_schema=False,
    )
    async def set_study_area_mode(
        payload: _ModeRequest,
        control_credential: Annotated[
            str | None,
            Header(alias="X-E2E-Control"),
        ] = None,
    ) -> Response:
        if control_credential != _CONTROL_CREDENTIAL:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
        controller.mode = payload.mode
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return master


app = create_e2e_master_app() if os.environ.get("APP_ENV") == "test" else None
