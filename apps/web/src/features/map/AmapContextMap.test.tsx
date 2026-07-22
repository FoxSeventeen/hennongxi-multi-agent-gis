import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AmapContextMap,
  readAmapLoadTimeout,
  SHENNONGXI_CENTER_WGS84,
  type AmapJsApi,
  type AmapLoader,
  type AmapMap,
  type AmapMapOptions,
} from "./AmapContextMap";

class MockAmapMap implements AmapMap {
  static instances: MockAmapMap[] = [];
  readonly destroy = vi.fn<() => void>();
  readonly listeners = new Map<string, () => void>();

  constructor(
    readonly container: HTMLDivElement,
    readonly options: AmapMapOptions,
  ) {
    MockAmapMap.instances.push(this);
  }

  readonly on = (event: "complete", listener: () => void): void => {
    this.listeners.set(event, listener);
  };

  emitComplete(): void {
    this.listeners.get("complete")?.();
  }
}

function createSuccessfulApi(): AmapJsApi {
  const convertFrom = vi.fn<AmapJsApi["convertFrom"]>((_point, _type, callback) => {
    callback("complete", { info: "ok", locations: [{ lng: 110.3, lat: 31.26 }] });
  });
  return {
    Map: MockAmapMap,
    convertFrom,
  };
}

function createLoader(api: AmapJsApi): AmapLoader {
  return { load: vi.fn().mockResolvedValue(api) };
}

function offlineFallback() {
  return <div data-testid="offline-map">离线地图占位</div>;
}

afterEach(() => {
  MockAmapMap.instances.length = 0;
  vi.useRealTimers();
  delete window._AMapSecurityConfig;
});

describe("AmapContextMap", () => {
  it.each([
    [undefined, 5_000],
    ["", 5_000],
    ["999", 5_000],
    ["5000", 5_000],
    ["15000", 15_000],
    ["15001", 5_000],
  ])("把公开超时配置 %s 约束到安全范围", (rawValue, expected) => {
    expect(readAmapLoadTimeout(rawValue)).toBe(expected);
  });

  it("未配置 Web端 JS API Key 时不加载高德并立即显示离线地图", () => {
    const loader = createLoader(createSuccessfulApi());

    render(
      <AmapContextMap
        activeTaskId={null}
        apiKey=""
        loader={loader}
        fallback={offlineFallback()}
      />,
    );

    expect(screen.getByTestId("offline-map")).toBeVisible();
    expect(loader.load).not.toHaveBeenCalled();
    expect(window._AMapSecurityConfig).toBeUndefined();
  });

  it("用固定中心点临时转换后创建普通 2D 道路地图，并在地图完成后标为可用", async () => {
    const api = createSuccessfulApi();
    const loader = createLoader(api);

    render(
      <AmapContextMap
        activeTaskId={null}
        apiKey="browser-visible-test-key"
        loader={loader}
        fallback={offlineFallback()}
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent("正在加载高德位置参考");
    await waitFor(() => {
      expect(api.convertFrom).toHaveBeenCalledWith(
        SHENNONGXI_CENTER_WGS84,
        "gps",
        expect.any(Function),
      );
    });
    expect(loader.load).toHaveBeenCalledWith({
      key: "browser-visible-test-key",
      version: "2.0",
      plugins: [],
    });
    expect(window._AMapSecurityConfig).toEqual({
      serviceHost: `${window.location.origin}/_AMapService`,
    });
    expect(MockAmapMap.instances).toHaveLength(1);
    expect(MockAmapMap.instances[0]?.options).toMatchObject({
      center: { lng: 110.3, lat: 31.26 },
      viewMode: "2D",
      zoom: 10,
    });

    act(() => {
      MockAmapMap.instances[0]?.emitComplete();
    });
    expect(screen.getByText("高德位置参考")).toBeVisible();
    expect(
      screen.getByRole("group", { name: "高德普通道路位置参考，神农溪区域" }),
    ).toBeVisible();
  });

  it("任务 ID 更新时复用同一地图实例，不重复加载或转换", async () => {
    const api = createSuccessfulApi();
    const loader = createLoader(api);
    const { rerender } = render(
      <AmapContextMap
        activeTaskId={null}
        apiKey="browser-visible-test-key"
        loader={loader}
        fallback={offlineFallback()}
      />,
    );
    await waitFor(() => {
      expect(MockAmapMap.instances).toHaveLength(1);
    });

    rerender(
      <AmapContextMap
        activeTaskId="4f09fc09-6bd2-49fb-9636-7f4fb93baa44"
        apiKey="browser-visible-test-key"
        loader={loader}
        fallback={offlineFallback()}
      />,
    );

    expect(loader.load).toHaveBeenCalledOnce();
    expect(api.convertFrom).toHaveBeenCalledOnce();
    expect(MockAmapMap.instances).toHaveLength(1);
    expect(screen.getByText(/任务 4f09fc09 正在生成分析成果/)).toBeVisible();
  });

  it("超过配置时限仍未加载时销毁已创建地图并稳定回退", async () => {
    vi.useFakeTimers();
    const api = createSuccessfulApi();
    const loader = createLoader(api);
    render(
      <AmapContextMap
        activeTaskId={null}
        apiKey="browser-visible-test-key"
        loadTimeoutMs={5_000}
        loader={loader}
        fallback={offlineFallback()}
      />,
    );
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(MockAmapMap.instances).toHaveLength(1);

    act(() => {
      vi.advanceTimersByTime(5_000);
    });

    expect(screen.getByTestId("offline-map")).toBeVisible();
    expect(MockAmapMap.instances[0]?.destroy).toHaveBeenCalledOnce();
  });

  it("Loader 或坐标转换失败时回退，不保留高德原始错误", async () => {
    const loaderError = new Error("upstream URL and credential must not be rendered");
    const loader: AmapLoader = { load: vi.fn().mockRejectedValue(loaderError) };

    render(
      <AmapContextMap
        activeTaskId={null}
        apiKey="browser-visible-test-key"
        loader={loader}
        fallback={offlineFallback()}
      />,
    );

    expect(await screen.findByTestId("offline-map")).toBeVisible();
    expect(document.body).not.toHaveTextContent(loaderError.message);
    expect(MockAmapMap.instances).toHaveLength(0);
  });

  it("坐标转换未完成时回退，且不创建地图", async () => {
    const api: AmapJsApi = {
      Map: MockAmapMap,
      convertFrom: vi.fn<AmapJsApi["convertFrom"]>((_point, _type, callback) => {
        callback("error", { info: "provider detail", locations: [] });
      }),
    };

    render(
      <AmapContextMap
        activeTaskId={null}
        apiKey="browser-visible-test-key"
        loader={createLoader(api)}
        fallback={offlineFallback()}
      />,
    );

    expect(await screen.findByTestId("offline-map")).toBeVisible();
    expect(document.body).not.toHaveTextContent("provider detail");
    expect(MockAmapMap.instances).toHaveLength(0);
  });

  it("组件在 Loader 完成前卸载时不再转换坐标或创建地图", async () => {
    let resolveLoader: ((api: AmapJsApi) => void) | undefined;
    const loader: AmapLoader = {
      load: vi.fn(
        async () =>
          await new Promise<AmapJsApi>((resolve) => {
            resolveLoader = resolve;
          }),
      ),
    };
    const api = createSuccessfulApi();
    const { unmount } = render(
      <AmapContextMap
        activeTaskId={null}
        apiKey="browser-visible-test-key"
        loader={loader}
        fallback={offlineFallback()}
      />,
    );

    unmount();
    await act(async () => {
      resolveLoader?.(api);
      await Promise.resolve();
    });

    expect(api.convertFrom).not.toHaveBeenCalled();
    expect(MockAmapMap.instances).toHaveLength(0);
  });

  it("组件卸载时销毁地图实例", async () => {
    const api = createSuccessfulApi();
    const { unmount } = render(
      <AmapContextMap
        activeTaskId={null}
        apiKey="browser-visible-test-key"
        loader={createLoader(api)}
        fallback={offlineFallback()}
      />,
    );
    await waitFor(() => {
      expect(MockAmapMap.instances).toHaveLength(1);
    });

    unmount();

    expect(MockAmapMap.instances[0]?.destroy).toHaveBeenCalledOnce();
  });
});
