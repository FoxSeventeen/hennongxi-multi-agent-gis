from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from hennongxi_master.watershed import ApprovedWatershedError, load_approved_watershed

NOW = datetime(2026, 7, 21, 1, 0, tzinfo=UTC)
PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_approved_watershed_loads_from_the_verified_bundle() -> None:
    watershed = load_approved_watershed(PROJECT_ROOT / "data/manifest.json", created_at=NOW)

    assert watershed.watershed_id.version == 5
    assert watershed.slug == "shennongxi"
    assert watershed.name == "神农溪流域"
    assert watershed.geometry["type"] == "Polygon"
    assert watershed.source_metadata == {
        "approval_gate": "G2",
        "approval_status": "approved",
        "logical_id": "watershed",
        "path": "boundaries/shennongxi_watershed.geojson",
        "sha256": "1c44b253e6220364109d6a62b17d2a66ef19ef12a6f4dc368ba1a41142eed7c3",
    }


@pytest.mark.parametrize("mutation", ["checksum", "path"])
def test_watershed_loader_rejects_tampering_and_path_escape(
    tmp_path: Path,
    mutation: str,
) -> None:
    source_manifest = PROJECT_ROOT / "data/manifest.json"
    payload = json.loads(source_manifest.read_text(encoding="utf-8"))
    asset = next(item for item in payload["assets"] if item["logical_id"] == "watershed")
    boundary_dir = tmp_path / "boundaries"
    boundary_dir.mkdir()
    shutil.copy2(
        PROJECT_ROOT / "data/boundaries/shennongxi_watershed.geojson",
        boundary_dir / "shennongxi_watershed.geojson",
    )
    if mutation == "checksum":
        asset["sha256"] = "0" * 64
    else:
        asset["path"] = "../private.geojson"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ApprovedWatershedError):
        load_approved_watershed(manifest, created_at=NOW)
