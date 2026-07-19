"""Path-free versioned commands exchanged between Agent services."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, FiniteFloat, model_validator

from hennongxi_contracts.artifacts import ArtifactRef
from hennongxi_contracts.common import (
    ContractModel,
    Sha256Digest,
    ShortText,
    StepId,
)


class LogicalDatasetId(StrEnum):
    WATERSHED = "watershed"
    BEFORE_RED = "before_red"
    BEFORE_NIR = "before_nir"
    AFTER_RED = "after_red"
    AFTER_NIR = "after_nir"


REQUIRED_DATASET_IDS = frozenset(LogicalDatasetId)


def _require_artifact_scope(
    task_id: UUID, attempt: int, artifacts: tuple[ArtifactRef, ...]
) -> None:
    if any(artifact.task_id != task_id or artifact.attempt != attempt for artifact in artifacts):
        raise ValueError("artifacts must belong to the same task and attempt")


class InternalCommand(ContractModel):
    task_id: UUID
    step_id: StepId
    attempt: int = Field(ge=1)
    correlation_id: UUID


class DataPrepareCommand(InternalCommand):
    dataset_ids: tuple[LogicalDatasetId, ...]

    @model_validator(mode="after")
    def require_dataset_allowlist(self) -> Self:
        if self.step_id != "prepare_data":
            raise ValueError("data preparation command requires the prepare_data step")
        if (
            len(self.dataset_ids) != len(REQUIRED_DATASET_IDS)
            or set(self.dataset_ids) != REQUIRED_DATASET_IDS
        ):
            raise ValueError("dataset_ids must contain exactly the required logical dataset IDs")
        return self


class RasterGrid(ContractModel):
    crs: ShortText
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    transform: tuple[float, float, float, float, float, float]
    bounds: tuple[float, float, float, float]
    nodata: FiniteFloat

    @model_validator(mode="after")
    def require_valid_bounds(self) -> Self:
        left, bottom, right, top = self.bounds
        if left >= right or bottom >= top:
            raise ValueError("bounds must have positive width and height")
        return self


class DataAssetRef(ContractModel):
    dataset_id: LogicalDatasetId
    checksum_sha256: Sha256Digest
    byte_size: int = Field(gt=0)
    grid: RasterGrid | None = None
    acquired_on: date | None = None


class DataPrepareResult(ContractModel):
    task_id: UUID
    step_id: StepId
    attempt: int = Field(ge=1)
    correlation_id: UUID
    assets: tuple[DataAssetRef, ...]

    @model_validator(mode="after")
    def require_complete_manifest(self) -> Self:
        ids = tuple(asset.dataset_id for asset in self.assets)
        if len(ids) != len(REQUIRED_DATASET_IDS) or set(ids) != REQUIRED_DATASET_IDS:
            raise ValueError("assets must contain exactly the required logical dataset IDs")
        return self


class AnalysisRunCommand(InternalCommand):
    inputs: tuple[DataAssetRef, ...]

    @model_validator(mode="after")
    def require_complete_inputs(self) -> Self:
        ids = tuple(asset.dataset_id for asset in self.inputs)
        if len(ids) != len(REQUIRED_DATASET_IDS) or set(ids) != REQUIRED_DATASET_IDS:
            raise ValueError("inputs must contain exactly the required logical dataset IDs")
        return self


class AreaStatistics(ContractModel):
    increase_hectares: float = Field(ge=0)
    stable_hectares: float = Field(ge=0)
    decrease_hectares: float = Field(ge=0)
    valid_hectares: float = Field(gt=0)


class AnalysisRunResult(ContractModel):
    task_id: UUID
    step_id: StepId
    attempt: int = Field(ge=1)
    correlation_id: UUID
    artifacts: tuple[ArtifactRef, ...]
    statistics: AreaStatistics

    @model_validator(mode="after")
    def require_artifact_scope(self) -> Self:
        _require_artifact_scope(self.task_id, self.attempt, self.artifacts)
        return self


class QualityMetrics(ContractModel):
    coverage_ratio: float = Field(ge=0, le=1)
    valid_pixel_ratio: float = Field(ge=0, le=1)
    output_complete: bool
    elapsed_ms: int = Field(ge=0)
    passed: bool
    evidence: tuple[ShortText, ...] = ()


class QualityEvaluateCommand(InternalCommand):
    artifacts: tuple[ArtifactRef, ...]
    analysis_elapsed_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def require_artifact_scope(self) -> Self:
        _require_artifact_scope(self.task_id, self.attempt, self.artifacts)
        return self


class QualityEvaluateResult(ContractModel):
    task_id: UUID
    step_id: StepId
    attempt: int = Field(ge=1)
    correlation_id: UUID
    metrics: QualityMetrics
    artifact: ArtifactRef

    @model_validator(mode="after")
    def require_artifact_scope(self) -> Self:
        _require_artifact_scope(self.task_id, self.attempt, (self.artifact,))
        return self


class PublisherPublishCommand(InternalCommand):
    artifacts: tuple[ArtifactRef, ...]
    quality: QualityMetrics

    @model_validator(mode="after")
    def require_artifact_scope(self) -> Self:
        _require_artifact_scope(self.task_id, self.attempt, self.artifacts)
        return self


class PublishedResource(ContractModel):
    artifact_id: UUID
    tile_template: ShortText | None = None
    download_path: ShortText | None = None

    @model_validator(mode="after")
    def require_safe_relative_routes(self) -> Self:
        for route in (self.tile_template, self.download_path):
            if route is not None and (
                not route.startswith("/api/v1/") or "://" in route or ".." in route
            ):
                raise ValueError("published resources must use safe /api/v1/ routes")
        if self.tile_template is None and self.download_path is None:
            raise ValueError("published resource requires a tile or download route")
        return self


class PublisherPublishResult(ContractModel):
    task_id: UUID
    step_id: StepId
    attempt: int = Field(ge=1)
    correlation_id: UUID
    resources: tuple[PublishedResource, ...]
    report: ArtifactRef

    @model_validator(mode="after")
    def require_artifact_scope(self) -> Self:
        _require_artifact_scope(self.task_id, self.attempt, (self.report,))
        return self
