"""Strict primitives shared by every version 1.0 contract."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints

CONTRACT_VERSION: Literal["1.0"] = "1.0"


class ContractModel(BaseModel):
    """Base model that prevents silent contract widening."""

    schema_version: Literal["1.0"] = CONTRACT_VERSION
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class AgentName(StrEnum):
    MASTER = "master"
    DATA = "data"
    ANALYSIS = "analysis"
    QUALITY = "quality"
    PUBLISHER = "publisher"


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must include an explicit UTC offset")
    return value


UtcDateTime = Annotated[datetime, AfterValidator(_require_utc)]
NonBlankText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000),
]
ShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]
StepId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^[a-z][a-z0-9_]{0,63}$"),
]
Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
