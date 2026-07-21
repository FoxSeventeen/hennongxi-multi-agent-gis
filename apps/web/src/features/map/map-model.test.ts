import { describe, expect, it } from "vitest";

import type {
  TaskPublication,
  TaskPublishedResource,
  TaskTileMetadata,
} from "../../api/task-contract";
import { buildMapPresentation } from "./map-model";

const taskId = "4f09fc09-6bd2-49fb-9636-7f4fb93baa44";

function tileResource(
  artifactType: TaskTileMetadata["artifactType"],
  startDate: string,
  endDate: string,
  units = "NDVI",
): TaskPublishedResource {
  return {
    artifactId: `${artifactType.toLowerCase()}-artifact`,
    tileTemplate: `/api/v1/tiles/${taskId}/${artifactType}/{z}/{x}/{y}.png`,
    downloadPath: null,
    tileMetadata: {
      artifactType,
      boundsWgs84: [110.1, 31, 110.6, 31.5],
      startDate,
      endDate,
      units,
      attribution: "Copernicus Sentinel-2，经批准的离线数据",
      legend: [
        { value: -1, label: "降低", color: "#A23F35" },
        { value: 0, label: "稳定", color: "#F2E8C9" },
        { value: 1, label: "增加", color: "#3F7652" },
      ],
    },
  };
}

function publication(resources: readonly TaskPublishedResource[]): TaskPublication {
  return {
    taskId,
    attempt: 1,
    correlationId: "f399c36a-6b76-4db5-a831-ebf6a170edf1",
    report: {
      artifactId: "55555555-5555-4555-8555-555555555555",
      createdAt: "2024-08-12T08:30:03Z",
      checksumSha256: "b".repeat(64),
      byteSize: 1024,
    },
    resources,
  };
}

const completeResources = [
  tileResource("NDVI_BEFORE", "2019-08-19", "2019-08-19"),
  tileResource("NDVI_AFTER", "2024-08-12", "2024-08-12"),
  tileResource("NDVI_DIFFERENCE", "2019-08-19", "2024-08-12"),
  tileResource("CHANGE_CLASSIFICATION", "2019-08-19", "2024-08-12", "变化类别"),
] as const;

describe("地图展示模型", () => {
  it("按稳定顺序生成三个 NDVI 图层，并完整保留 Publisher 元数据", () => {
    const result = buildMapPresentation(
      publication(completeResources),
      "http://localhost:8004",
    );

    expect(result.status).toBe("ready");
    if (result.status !== "ready") {
      return;
    }
    expect(result.presentation.boundsWgs84).toEqual([110.1, 31, 110.6, 31.5]);
    expect(result.presentation.layers.map((layer) => layer.artifactType)).toEqual([
      "NDVI_BEFORE",
      "NDVI_AFTER",
      "NDVI_DIFFERENCE",
    ]);
    expect(result.presentation.layers[0]).toMatchObject({
      label: "前期 NDVI",
      periodLabel: "2019-08-19",
      tileUrl: `http://localhost:8004/api/v1/tiles/${taskId}/NDVI_BEFORE/{z}/{x}/{y}.png`,
      units: "NDVI",
      attribution: "Copernicus Sentinel-2，经批准的离线数据",
      legend: [
        { value: -1, label: "降低", color: "#A23F35" },
        { value: 0, label: "稳定", color: "#F2E8C9" },
        { value: 1, label: "增加", color: "#3F7652" },
      ],
    });
    expect(result.presentation.layers[2]?.periodLabel).toBe("2019-08-19 — 2024-08-12");
  });

  it("对缺少图层、范围不一致或不安全的 Publisher 地址给出中文可恢复状态", () => {
    expect(
      buildMapPresentation(publication(completeResources.slice(1)), "http://localhost:8004"),
    ).toEqual({ status: "unavailable", message: "发布结果缺少前期 NDVI 图层。" });

    const after = tileResource("NDVI_AFTER", "2024-08-12", "2024-08-12");
    const mismatchedAfter: TaskPublishedResource = {
      ...after,
      tileMetadata:
        after.tileMetadata === null
          ? null
          : { ...after.tileMetadata, boundsWgs84: [110.2, 31, 110.6, 31.5] },
    };
    expect(
      buildMapPresentation(
        publication([completeResources[0], mismatchedAfter, ...completeResources.slice(2)]),
        "http://localhost:8004",
      ),
    ).toEqual({ status: "unavailable", message: "发布图层的空间范围不一致。" });

    expect(buildMapPresentation(publication(completeResources), "javascript:alert(1)")).toEqual({
      status: "unavailable",
      message: "Publisher 图层地址配置无效。",
    });
  });
});
