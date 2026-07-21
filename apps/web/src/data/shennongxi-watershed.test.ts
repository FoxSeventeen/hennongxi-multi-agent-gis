import { describe, expect, it } from "vitest";

import watershedBoundary from "./shennongxi-watershed.json";

describe("内置神农溪流域边界", () => {
  it("保留已批准 HydroBASINS 合并边界的全部坐标与来源元数据", () => {
    const feature = watershedBoundary.features[0];
    const ring = feature?.geometry.coordinates[0];
    if (feature === undefined || ring === undefined) {
      throw new Error("测试数据缺少神农溪流域面要素");
    }
    const longitudes = ring.map((coordinate) => requireCoordinate(coordinate, 0));
    const latitudes = ring.map((coordinate) => requireCoordinate(coordinate, 1));

    expect(watershedBoundary.type).toBe("FeatureCollection");
    expect(feature.geometry.type).toBe("Polygon");
    expect(feature.properties).toMatchObject({
      approval_status: "approved",
      name_zh: "神农溪流域",
      source_dataset: "HydroBASINS standard level 12 Asia v1c",
      source_segment_count: 8,
    });
    expect(ring).toHaveLength(213);
    expect(ring[0]).toEqual(ring.at(-1));
    expect([
      Math.min(...longitudes),
      Math.min(...latitudes),
      Math.max(...longitudes),
      Math.max(...latitudes),
    ]).toEqual([
      110.10833333333336,
      31.04583333333336,
      110.53750000000002,
      31.466666666666697,
    ]);
  });
});

function requireCoordinate(coordinate: readonly number[], index: 0 | 1): number {
  const value = coordinate[index];
  if (value === undefined) {
    throw new Error("测试数据包含不完整的坐标");
  }
  return value;
}
