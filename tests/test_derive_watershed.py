from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import box

from scripts.derive_watershed import derive_watershed, upstream_ids, write_geojson


def _segments() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "HYBAS_ID": [10, 11, 12, 20],
            "NEXT_DOWN": [99, 10, 11, 99],
            "SUB_AREA": [4.0, 3.0, 2.0, 8.0],
            "UP_AREA": [9.0, 5.0, 2.0, 8.0],
            "PFAF_ID": [100, 101, 102, 200],
        },
        geometry=[
            box(0, 0, 1, 1),
            box(0, 1, 1, 2),
            box(0, 2, 1, 3),
            box(2, 0, 3, 1),
        ],
        crs="EPSG:4326",
    )


def test_upstream_trace_excludes_an_unrelated_tributary() -> None:
    assert upstream_ids(_segments(), outlet_id=10) == frozenset({10, 11, 12})


def test_derived_watershed_records_traceable_source_segments() -> None:
    result = derive_watershed(_segments(), outlet_id=10)

    assert len(result) == 1
    assert result.geometry.iloc[0].area == pytest.approx(3.0)
    assert result.iloc[0]["outlet_hybas_id"] == 10
    assert result.iloc[0]["source_hybas_ids"] == [10, 11, 12]
    assert result.iloc[0]["source_sub_area_sum_km2"] == pytest.approx(9.0)
    assert result.iloc[0]["outlet_up_area_km2"] == pytest.approx(9.0)
    assert result.iloc[0]["approval_status"] == "approved"


def test_derived_watershed_rejects_incomplete_source_extract() -> None:
    incomplete = _segments().loc[lambda frame: frame.HYBAS_ID != 12]

    with pytest.raises(ValueError, match="area disagrees"):
        derive_watershed(incomplete, outlet_id=10)


def test_geojson_writer_normalizes_dataframe_scalar_types(tmp_path: Path) -> None:
    output = tmp_path / "watershed.geojson"

    write_geojson(derive_watershed(_segments(), outlet_id=10), output)

    payload = json.loads(output.read_text(encoding="utf-8"))
    properties = payload["features"][0]["properties"]
    assert properties["outlet_hybas_id"] == 10
    assert properties["source_hybas_ids"] == [10, 11, 12]
