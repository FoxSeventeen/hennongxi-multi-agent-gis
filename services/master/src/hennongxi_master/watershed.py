"""Load the fixed approved watershed from the verified local data bundle."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from hennongxi_contracts.common import Sha256Digest, UtcDateTime
from pydantic import BaseModel, ConfigDict, ValidationError

from hennongxi_master.repository import WatershedCreate

MAX_MANIFEST_BYTES = 2_000_000
MAX_BOUNDARY_BYTES = 5_000_000


class ApprovedWatershedError(ValueError):
    """Raised without source details when the approved bundle cannot be trusted."""


class _Approval(BaseModel):
    model_config = ConfigDict(extra="ignore")

    gate: Literal["G2"]
    status: Literal["approved"]


class _Asset(BaseModel):
    model_config = ConfigDict(extra="ignore")

    logical_id: str
    path: str
    sha256: Sha256Digest
    storage: str


class _Manifest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    approval: _Approval
    assets: tuple[_Asset, ...]


def load_approved_watershed(
    manifest_path: Path,
    *,
    created_at: UtcDateTime,
) -> WatershedCreate:
    try:
        manifest = _Manifest.model_validate_json(_read_bounded(manifest_path, MAX_MANIFEST_BYTES))
        matches = tuple(asset for asset in manifest.assets if asset.logical_id == "watershed")
        if len(matches) != 1 or matches[0].storage != "bundle":
            raise ApprovedWatershedError()
        asset = matches[0]
        data_root = manifest_path.resolve().parent
        boundary_path = (data_root / asset.path).resolve()
        if not boundary_path.is_relative_to(data_root):
            raise ApprovedWatershedError()
        boundary_bytes = _read_bounded(boundary_path, MAX_BOUNDARY_BYTES)
        if sha256(boundary_bytes).hexdigest() != asset.sha256:
            raise ApprovedWatershedError()
        boundary = json.loads(boundary_bytes)
        if boundary.get("type") != "FeatureCollection" or len(boundary.get("features", ())) != 1:
            raise ApprovedWatershedError()
        feature = boundary["features"][0]
        geometry = feature["geometry"]
        name = feature["properties"]["name_zh"]
        if not isinstance(geometry, dict) or not isinstance(name, str):
            raise ApprovedWatershedError()
        return WatershedCreate(
            watershed_id=uuid5(NAMESPACE_URL, f"hennongxi:watershed:{asset.sha256}"),
            slug="shennongxi",
            name=name,
            geometry=geometry,
            source_metadata={
                "approval_gate": manifest.approval.gate,
                "approval_status": manifest.approval.status,
                "logical_id": asset.logical_id,
                "path": asset.path,
                "sha256": asset.sha256,
            },
            created_at=created_at,
        )
    except ApprovedWatershedError:
        raise
    except (
        AttributeError,
        IndexError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
        ValidationError,
    ) as error:
        raise ApprovedWatershedError() from error


def _read_bounded(path: Path, limit: int) -> bytes:
    if path.stat().st_size > limit:
        raise ApprovedWatershedError()
    return path.read_bytes()
