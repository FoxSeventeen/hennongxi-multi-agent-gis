"""Task-bound artifact metadata; storage paths never cross service boundaries."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import Field, FiniteFloat, StringConstraints, model_validator

from hennongxi_contracts.common import (
    ContractModel,
    Sha256Digest,
    ShortText,
    UtcDateTime,
)


class ArtifactType(StrEnum):
    DATA_MANIFEST = "DATA_MANIFEST"
    WATERSHED_BOUNDARY = "WATERSHED_BOUNDARY"
    NDVI_BEFORE = "NDVI_BEFORE"
    NDVI_AFTER = "NDVI_AFTER"
    NDVI_DIFFERENCE = "NDVI_DIFFERENCE"
    CHANGE_CLASSIFICATION = "CHANGE_CLASSIFICATION"
    AREA_STATISTICS = "AREA_STATISTICS"
    QUALITY_REPORT = "QUALITY_REPORT"
    PDF_REPORT = "PDF_REPORT"


class TileArtifactType(StrEnum):
    NDVI_BEFORE = "NDVI_BEFORE"
    NDVI_AFTER = "NDVI_AFTER"
    NDVI_DIFFERENCE = "NDVI_DIFFERENCE"
    CHANGE_CLASSIFICATION = "CHANGE_CLASSIFICATION"


HexColor = Annotated[str, StringConstraints(pattern=r"^#[0-9A-Fa-f]{6}$")]


class TileLegendEntry(ContractModel):
    value: FiniteFloat
    label: ShortText
    color: HexColor


class TileMetadata(ContractModel):
    artifact_type: TileArtifactType
    bounds_wgs84: tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]
    start_date: date
    end_date: date
    units: ShortText
    attribution: ShortText
    legend: tuple[TileLegendEntry, ...] = Field(min_length=2, max_length=12)

    @model_validator(mode="after")
    def require_safe_visualization_metadata(self) -> Self:
        west, south, east, north = self.bounds_wgs84
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise ValueError("WGS84 bounds must be ordered and within the valid domain")
        if self.start_date > self.end_date:
            raise ValueError("tile start_date cannot be after end_date")
        values = tuple(entry.value for entry in self.legend)
        if any(
            current >= following for current, following in zip(values, values[1:], strict=False)
        ):
            raise ValueError("tile legend values must be strictly increasing")
        return self


class ArtifactStatus(StrEnum):
    STAGING = "STAGING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


class ArtifactRef(ContractModel):
    artifact_id: UUID
    task_id: UUID
    attempt: int = Field(ge=1)
    artifact_type: ArtifactType
    status: ArtifactStatus
    media_type: ShortText
    created_at: UtcDateTime
    checksum_sha256: Sha256Digest | None = None
    byte_size: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def require_complete_metadata(self) -> Self:
        if self.status is ArtifactStatus.COMPLETE and (
            self.checksum_sha256 is None or self.byte_size is None
        ):
            raise ValueError("complete artifact requires checksum_sha256 and nonzero byte_size")
        return self
