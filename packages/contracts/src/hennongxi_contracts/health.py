"""Aggregate health and configuration-readiness contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import model_validator

from hennongxi_contracts.common import ContractModel, NonBlankText, ShortText, UtcDateTime


class ServiceName(StrEnum):
    MASTER = "master"
    DATA = "data"
    ANALYSIS = "analysis"
    QUALITY = "quality"
    PUBLISHER = "publisher"
    POSTGIS = "postgis"
    REDIS = "redis"


class HealthState(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


class ServiceHealth(ContractModel):
    service: ServiceName
    state: HealthState
    checked_at: UtcDateTime
    message: ShortText | None = None


class HealthResponse(ContractModel):
    state: HealthState
    checked_at: UtcDateTime
    services: tuple[ServiceHealth, ...]


class ReadinessBlocker(StrEnum):
    LLM_NOT_CONFIGURED = "LLM_NOT_CONFIGURED"
    DATA_NOT_CONFIGURED = "DATA_NOT_CONFIGURED"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"


class ReadinessResponse(ContractModel):
    ready: bool
    llm_configured: bool
    data_configured: bool
    blockers: tuple[ReadinessBlocker, ...] = ()
    messages: tuple[NonBlankText, ...] = ()

    @model_validator(mode="after")
    def require_honest_readiness(self) -> Self:
        if self.ready != (not self.blockers):
            raise ValueError("ready must be true exactly when blockers is empty")
        if self.llm_configured == (ReadinessBlocker.LLM_NOT_CONFIGURED in self.blockers):
            raise ValueError("llm_configured conflicts with blockers")
        if self.data_configured == (ReadinessBlocker.DATA_NOT_CONFIGURED in self.blockers):
            raise ValueError("data_configured conflicts with blockers")
        return self
