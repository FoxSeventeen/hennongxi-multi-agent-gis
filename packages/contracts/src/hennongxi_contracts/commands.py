"""Path-free versioned commands exchanged between Agent services."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, FiniteFloat, model_validator

from hennongxi_contracts.artifacts import (
    ArtifactRef,
    ArtifactStatus,
    ArtifactType,
    TileArtifactType,
    TileMetadata,
)
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
        if self.step_id != "analyze_ndvi_change":
            raise ValueError("analysis command requires the analyze_ndvi_change step")
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
    elapsed_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def require_artifact_scope(self) -> Self:
        _require_artifact_scope(self.task_id, self.attempt, self.artifacts)
        required_types = {
            ArtifactType.NDVI_BEFORE,
            ArtifactType.NDVI_AFTER,
            ArtifactType.NDVI_DIFFERENCE,
            ArtifactType.CHANGE_CLASSIFICATION,
            ArtifactType.AREA_STATISTICS,
        }
        if (
            self.step_id != "analyze_ndvi_change"
            or len(self.artifacts) != len(required_types)
            or {artifact.artifact_type for artifact in self.artifacts} != required_types
            or any(artifact.status is not ArtifactStatus.COMPLETE for artifact in self.artifacts)
        ):
            raise ValueError(
                "analysis result requires the complete analysis artifact set for "
                "the analyze_ndvi_change step"
            )
        return self


QUALITY_INPUT_ARTIFACT_TYPES = frozenset(
    {
        ArtifactType.NDVI_BEFORE,
        ArtifactType.NDVI_AFTER,
        ArtifactType.NDVI_DIFFERENCE,
        ArtifactType.CHANGE_CLASSIFICATION,
        ArtifactType.AREA_STATISTICS,
    }
)


class QualityConclusion(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


class QualityThresholds(ContractModel):
    minimum_watershed_coverage_ratio: float = Field(ge=0, le=1)
    minimum_valid_pixel_ratio: float = Field(ge=0, le=1)
    output_complete_required: Literal[True] = True
    elapsed_minimum_ms: Literal[0] = 0


class QualityMetrics(ContractModel):
    coverage_ratio: float = Field(ge=0, le=1)
    valid_pixel_ratio: float = Field(ge=0, le=1)
    output_complete: bool
    elapsed_ms: int = Field(ge=0)
    thresholds: QualityThresholds
    conclusion: QualityConclusion
    passed: bool
    evidence: tuple[ShortText, ...] = Field(min_length=4)

    @model_validator(mode="after")
    def require_consistent_conclusion(self) -> Self:
        gates_pass = (
            self.coverage_ratio >= self.thresholds.minimum_watershed_coverage_ratio
            and self.valid_pixel_ratio >= self.thresholds.minimum_valid_pixel_ratio
            and self.output_complete is self.thresholds.output_complete_required
            and self.elapsed_ms >= self.thresholds.elapsed_minimum_ms
        )
        if self.conclusion is QualityConclusion.PASS and not gates_pass:
            raise ValueError("passing conclusion requires every quality gate")
        if self.passed != (self.conclusion is QualityConclusion.PASS):
            raise ValueError("passed must match the quality conclusion")
        return self


class QualityEvaluateCommand(InternalCommand):
    artifacts: tuple[ArtifactRef, ...]
    analysis_elapsed_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def require_artifact_scope(self) -> Self:
        _require_artifact_scope(self.task_id, self.attempt, self.artifacts)
        artifact_types = tuple(artifact.artifact_type for artifact in self.artifacts)
        if self.step_id != "evaluate_quality":
            raise ValueError("quality command requires the evaluate_quality step")
        if any(
            artifact_type not in QUALITY_INPUT_ARTIFACT_TYPES for artifact_type in artifact_types
        ):
            raise ValueError("quality command accepts only supported analysis artifact types")
        if len(set(artifact_types)) != len(artifact_types):
            raise ValueError("quality command requires unique supported analysis artifact types")
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
        if (
            self.step_id != "evaluate_quality"
            or self.artifact.artifact_type is not ArtifactType.QUALITY_REPORT
            or self.artifact.status is not ArtifactStatus.COMPLETE
            or self.artifact.media_type != "application/json"
        ):
            raise ValueError(
                "quality result requires a complete quality report for the evaluate_quality step"
            )
        return self


class PublisherPublishCommand(InternalCommand):
    artifacts: tuple[ArtifactRef, ...]
    quality: QualityMetrics

    @model_validator(mode="after")
    def require_artifact_scope(self) -> Self:
        _require_artifact_scope(self.task_id, self.attempt, self.artifacts)
        required_types = frozenset(
            {
                ArtifactType.NDVI_BEFORE,
                ArtifactType.NDVI_AFTER,
                ArtifactType.NDVI_DIFFERENCE,
                ArtifactType.CHANGE_CLASSIFICATION,
                ArtifactType.AREA_STATISTICS,
                ArtifactType.QUALITY_REPORT,
            }
        )
        artifact_types = tuple(artifact.artifact_type for artifact in self.artifacts)
        if self.step_id != "publish_results":
            raise ValueError("publisher command requires the publish_results step")
        if len(artifact_types) != len(required_types) or set(artifact_types) != required_types:
            raise ValueError("publisher command requires the complete publishable artifact set")
        if any(artifact.status is not ArtifactStatus.COMPLETE for artifact in self.artifacts):
            raise ValueError("publisher command requires complete artifacts")
        if self.quality.conclusion is not QualityConclusion.PASS or not self.quality.passed:
            raise ValueError("publisher command requires passing quality")
        return self


class PublishedResource(ContractModel):
    artifact_id: UUID
    tile_template: ShortText | None = None
    download_path: ShortText | None = None
    tile_metadata: TileMetadata | None = None

    @model_validator(mode="after")
    def require_safe_relative_routes(self) -> Self:
        for route in (self.tile_template, self.download_path):
            if route is not None and (
                not route.startswith("/api/v1/") or "://" in route or ".." in route
            ):
                raise ValueError("published resources must use safe /api/v1/ routes")
        if self.tile_template is None and self.download_path is None:
            raise ValueError("published resource requires a tile or download route")
        if self.tile_template is not None:
            if self.tile_metadata is None:
                raise ValueError("published tile resource requires tile metadata")
            expected_segment = f"/{self.tile_metadata.artifact_type.value}/"
            if expected_segment not in self.tile_template or not self.tile_template.endswith(
                "/{z}/{x}/{y}.png"
            ):
                raise ValueError("tile metadata must match the tile template")
        elif self.tile_metadata is not None:
            raise ValueError("tile metadata requires a tile template")
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
        if self.step_id != "publish_results":
            raise ValueError("publisher result requires the publish_results step")
        tile_resources = tuple(
            resource for resource in self.resources if resource.tile_template is not None
        )
        tile_types = tuple(
            resource.tile_metadata.artifact_type
            for resource in tile_resources
            if resource.tile_metadata is not None
        )
        if len(tile_resources) != 4 or set(tile_types) != set(TileArtifactType):
            raise ValueError("publisher result requires exactly four tile resources")
        for resource in tile_resources:
            expected_prefix = f"/api/v1/tiles/{self.task_id}/"
            if resource.tile_template is None or not resource.tile_template.startswith(
                expected_prefix
            ):
                raise ValueError("publisher tile resources must belong to the result task")

        download_resources = tuple(
            resource for resource in self.resources if resource.download_path is not None
        )
        _require_artifact_scope(self.task_id, self.attempt, (self.report,))
        if (
            self.report.artifact_type is not ArtifactType.PDF_REPORT
            or self.report.status is not ArtifactStatus.COMPLETE
            or self.report.media_type != "application/pdf"
        ):
            raise ValueError("publisher report must be a complete PDF artifact")
        if (
            len(download_resources) != 1
            or download_resources[0].artifact_id != self.report.artifact_id
        ):
            raise ValueError("publisher report requires one matching download resource")
        return self
