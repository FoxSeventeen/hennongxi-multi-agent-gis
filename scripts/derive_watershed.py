"""Derive the complete Shennongxi candidate watershed from HydroBASINS topology."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import geopandas as gpd  # type: ignore[import-untyped]
import numpy as np
from shapely.geometry import mapping  # type: ignore[import-untyped]

DEFAULT_SOURCE = Path("data/cache/sources/hybas_as_lev12_v1c/hybas_as_lev12_v1c.shp")
DEFAULT_OUTPUT = Path("data/boundaries/shennongxi_watershed.geojson")
SHENNONGXI_OUTLET_HYBAS_ID = 4_120_733_210
SOURCE_BBOX = (109.7, 30.9, 110.6, 32.1)


def _json_default(value: object) -> object:
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def upstream_ids(segments: gpd.GeoDataFrame, *, outlet_id: int) -> frozenset[int]:
    """Return the outlet and every segment whose NEXT_DOWN chain reaches it."""
    available = {int(value) for value in segments.HYBAS_ID}
    if outlet_id not in available:
        raise ValueError(f"outlet HYBAS_ID {outlet_id} is missing from the source extract")

    selected = {outlet_id}
    while True:
        direct = {
            int(value) for value in segments.loc[segments.NEXT_DOWN.isin(selected), "HYBAS_ID"]
        }
        additions = direct - selected
        if not additions:
            return frozenset(selected)
        selected.update(additions)


def derive_watershed(
    segments: gpd.GeoDataFrame,
    *,
    outlet_id: int,
) -> gpd.GeoDataFrame:
    """Union the topology-selected segments and retain auditable source identifiers."""
    required_columns = {
        "HYBAS_ID",
        "NEXT_DOWN",
        "SUB_AREA",
        "UP_AREA",
        "PFAF_ID",
        "geometry",
    }
    missing = required_columns - set(segments.columns)
    if missing:
        raise ValueError(f"HydroBASINS source is missing columns: {sorted(missing)}")
    if segments.crs is None:
        raise ValueError("HydroBASINS source has no CRS")

    identifiers = upstream_ids(segments, outlet_id=outlet_id)
    selected = segments.loc[segments.HYBAS_ID.isin(identifiers)].copy()
    sub_area_sum = float(selected.SUB_AREA.sum())
    outlet_up_area = float(selected.loc[selected.HYBAS_ID.eq(outlet_id), "UP_AREA"].iloc[0])
    tolerance = max(1.0, outlet_up_area * 0.005)
    if not math.isclose(sub_area_sum, outlet_up_area, rel_tol=0, abs_tol=tolerance):
        raise ValueError(
            "selected segment area disagrees with the outlet upstream area; "
            "the source extract is probably incomplete"
        )

    source_ids = sorted(int(value) for value in selected.HYBAS_ID)
    merged = selected.geometry.union_all()
    if merged.is_empty or not merged.is_valid:
        raise ValueError("derived watershed geometry is empty or invalid")

    return gpd.GeoDataFrame(
        {
            "name_zh": ["神农溪流域"],
            "name_en": ["Shennongxi watershed"],
            "approval_status": ["approved"],
            "source_dataset": ["HydroBASINS standard level 12 Asia v1c"],
            "outlet_hybas_id": [outlet_id],
            "downstream_hybas_id": [
                int(selected.loc[selected.HYBAS_ID.eq(outlet_id), "NEXT_DOWN"].iloc[0])
            ],
            "source_hybas_ids": [source_ids],
            "source_segment_count": [len(source_ids)],
            "source_sub_area_sum_km2": [round(sub_area_sum, 1)],
            "outlet_up_area_km2": [round(outlet_up_area, 1)],
            "derivation": [
                "Union of the outlet HydroBASINS polygon and every level-12 polygon "
                "whose NEXT_DOWN chain reaches that outlet."
            ],
        },
        geometry=[merged],
        crs=segments.crs,
    )


def write_geojson(frame: gpd.GeoDataFrame, output: Path) -> None:
    if frame.crs is None:
        raise ValueError("derived watershed has no CRS")
    geographic = frame.to_crs("EPSG:4326")
    row = geographic.iloc[0]
    properties = {
        column: row[column] for column in geographic.columns if column != geographic.geometry.name
    }
    payload = {
        "type": "FeatureCollection",
        "name": "shennongxi_watershed",
        "features": [
            {
                "type": "Feature",
                "properties": properties,
                "geometry": mapping(row.geometry),
            }
        ],
    }
    rendered = (
        json.dumps(
            payload,
            default=_json_default,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    if not output.exists() or output.read_text(encoding="utf-8") != rendered:
        output.write_text(rendered, encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--outlet-id", type=int, default=SHENNONGXI_OUTLET_HYBAS_ID)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if not args.source.is_file():
        raise SystemExit(
            "HydroBASINS source is missing. Restore the ignored source cache before deriving."
        )
    source = gpd.read_file(args.source, bbox=SOURCE_BBOX)
    watershed = derive_watershed(source, outlet_id=args.outlet_id)
    write_geojson(watershed, args.output)
    print(
        f"Wrote {args.output} from {watershed.iloc[0]['source_segment_count']} "
        f"upstream-connected HydroBASINS segments."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
