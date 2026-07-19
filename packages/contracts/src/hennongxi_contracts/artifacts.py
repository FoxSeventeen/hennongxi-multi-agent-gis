"""Task-bound artifact metadata; storage paths never cross service boundaries."""

from __future__ import annotations

from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

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
