import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { TaskPublication, TaskTileMetadata } from "../api/task-contract";
import { MapWorkspace } from "./MapWorkspace";

const mapHarness = vi.hoisted(() => {
  type Listener = (event?: unknown) => void;
  class MockMap {
    static instances: MockMap[] = [];
    readonly listeners = new Map<string, Listener>();
    readonly addSource = vi.fn();
    readonly addLayer = vi.fn();
    readonly fitBounds = vi.fn();
    readonly setLayoutProperty = vi.fn();
    readonly remove = vi.fn();
    readonly addControl = vi.fn();

    constructor(readonly options: unknown) {
      MockMap.instances.push(this);
    }

    on(event: string, listener: Listener): this {
      this.listeners.set(event, listener);
      return this;
    }

    emit(event: string, payload?: unknown): void {
      this.listeners.get(event)?.(payload);
    }
  }
  return { MockMap };
});

vi.mock("maplibre-gl", () => ({
  default: {
    Map: mapHarness.MockMap,
    NavigationControl: class MockNavigationControl {
      readonly enabled = true;
    },
  },
}));

const taskId = "4f09fc09-6bd2-49fb-9636-7f4fb93baa44";

function tileMetadata(
  artifactType: TaskTileMetadata["artifactType"],
  startDate: string,
  endDate: string,
): TaskTileMetadata {
  return {
    artifactType,
    boundsWgs84: [110.1, 31, 110.6, 31.5],
    startDate,
    endDate,
    units: artifactType === "CHANGE_CLASSIFICATION" ? "变化类别" : "NDVI",
    attribution: "Copernicus Sentinel-2，经批准的离线数据",
    legend: [
      { value: -1, label: "降低", color: "#A23F35" },
      { value: 0, label: "稳定", color: "#F2E8C9" },
      { value: 1, label: "增加", color: "#3F7652" },
    ],
  };
}

const publication: TaskPublication = {
  taskId,
  attempt: 1,
  correlationId: "f399c36a-6b76-4db5-a831-ebf6a170edf1",
  resources: [
    ["NDVI_BEFORE", "2019-08-19", "2019-08-19"],
    ["NDVI_AFTER", "2024-08-12", "2024-08-12"],
    ["NDVI_DIFFERENCE", "2019-08-19", "2024-08-12"],
    ["CHANGE_CLASSIFICATION", "2019-08-19", "2024-08-12"],
  ].map(([artifactType, startDate, endDate], index) => {
    const typedArtifactType = artifactType as TaskTileMetadata["artifactType"];
    return {
      artifactId: `00000000-0000-4000-8000-00000000000${String(index)}`,
      tileTemplate: `/api/v1/tiles/${taskId}/${typedArtifactType}/{z}/{x}/{y}.png`,
      downloadPath: null,
      tileMetadata: tileMetadata(typedArtifactType, String(startDate), String(endDate)),
    };
  }),
};

describe("MapWorkspace", () => {
  beforeEach(() => {
    mapHarness.MockMap.instances.length = 0;
  });

  it("以差值图层为默认值，保持流域边界在最上层并可切换三个 NDVI 图层", async () => {
    const user = userEvent.setup();
    render(
      <MapWorkspace
        activeTaskId={taskId}
        publication={publication}
        publisherBaseUrl="http://localhost:8004"
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent("正在准备地图图层");
    const map = mapHarness.MockMap.instances[0];
    expect(map).toBeDefined();
    act(() => {
      map?.emit("load");
    });

    expect(screen.getByRole("group", { name: "NDVI 图层" })).toBeVisible();
    expect(screen.getByRole("button", { name: "NDVI 差值" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByText("2019-08-19 — 2024-08-12")).toBeVisible();
    expect(screen.getByText("流域边界始终显示")).toBeVisible();
    expect(screen.getByText("Copernicus Sentinel-2，经批准的离线数据")).toBeVisible();
    expect(screen.getByText("降低")).toBeVisible();
    expect(map?.addLayer.mock.calls.map(([layer]) => (layer as { id: string }).id)).toEqual([
      "ndvi-before",
      "ndvi-after",
      "ndvi-difference",
      "watershed-fill",
      "watershed-outline",
    ]);

    await user.click(screen.getByRole("button", { name: "后期 NDVI" }));
    expect(screen.getByText("2024-08-12")).toBeVisible();
    expect(map?.setLayoutProperty).toHaveBeenCalledWith("ndvi-after", "visibility", "visible");
    expect(map?.setLayoutProperty).toHaveBeenCalledWith("ndvi-difference", "visibility", "none");
  });

  it("瓦片失败时保留工作区并提供中文重载操作", async () => {
    const user = userEvent.setup();
    render(
      <MapWorkspace
        activeTaskId={taskId}
        publication={publication}
        publisherBaseUrl="http://localhost:8004"
      />,
    );
    const map = mapHarness.MockMap.instances[0];
    act(() => {
      map?.emit("load");
      map?.emit("error", { sourceId: "ndvi-difference" });
    });

    expect(screen.getByRole("alert")).toHaveTextContent("当前图层瓦片加载失败");
    await user.click(screen.getByRole("button", { name: "重新加载地图图层" }));
    expect(map?.remove).toHaveBeenCalledOnce();
    expect(mapHarness.MockMap.instances).toHaveLength(2);
  });
});
