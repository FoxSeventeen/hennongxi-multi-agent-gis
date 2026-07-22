import * as AMapLoader from "@amap/amap-jsapi-loader";
import { type ReactNode, useEffect, useRef, useState } from "react";

export const SHENNONGXI_CENTER_WGS84 = [110.299073, 31.262497] as const;
const DEFAULT_LOAD_TIMEOUT_MS = 5_000;

interface AmapLoadOptions {
  readonly key: string;
  readonly version: "2.0";
  readonly plugins: readonly string[];
}

interface AmapConversionResult {
  readonly info?: unknown;
  readonly locations?: unknown;
}

export interface AmapMap {
  readonly on: (event: "complete", listener: () => void) => void;
  readonly destroy: () => void;
}

export interface AmapMapOptions {
  readonly center: unknown;
  readonly resizeEnable: true;
  readonly viewMode: "2D";
  readonly zoom: number;
}

type AmapMapConstructor = new (
  container: HTMLDivElement,
  options: AmapMapOptions,
) => AmapMap;

export interface AmapJsApi {
  readonly Map: AmapMapConstructor;
  readonly convertFrom: (
    point: readonly [number, number],
    type: "gps",
    callback: (status: unknown, result: AmapConversionResult) => void,
  ) => void;
}

export interface AmapLoader {
  readonly load: (options: AmapLoadOptions) => Promise<unknown>;
}

export interface AmapContextMapConfig {
  readonly apiKey?: string;
  readonly loadTimeoutMs?: number;
  readonly loader?: AmapLoader;
}

interface AmapContextMapProps extends AmapContextMapConfig {
  readonly activeTaskId: string | null;
  readonly fallback: ReactNode;
}

type ContextMapState =
  | { readonly phase: "loading" }
  | { readonly phase: "ready" }
  | {
      readonly phase: "offline";
      readonly reason: "NOT_CONFIGURED" | "LOAD_TIMEOUT" | "LOAD_FAILED";
    };

declare global {
  interface Window {
    _AMapSecurityConfig?: {
      readonly serviceHost: string;
    };
  }
}

const officialLoader: AmapLoader = {
  load: async (options): Promise<unknown> =>
    await AMapLoader.load({ ...options, plugins: [...options.plugins] }),
};

export function readAmapLoadTimeout(rawValue: string | undefined): number {
  if (rawValue === undefined || !/^\d+$/.test(rawValue)) {
    return DEFAULT_LOAD_TIMEOUT_MS;
  }
  const timeout = Number(rawValue);
  return timeout >= 1_000 && timeout <= 15_000 ? timeout : DEFAULT_LOAD_TIMEOUT_MS;
}

export function AmapContextMap({
  activeTaskId,
  apiKey = import.meta.env.VITE_AMAP_JS_API_KEY,
  loadTimeoutMs = readAmapLoadTimeout(import.meta.env.VITE_AMAP_LOAD_TIMEOUT_MS),
  loader = officialLoader,
  fallback,
}: AmapContextMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const normalizedApiKey = apiKey?.trim() ?? "";
  const [loadState, setLoadState] = useState<ContextMapState>(
    normalizedApiKey.length === 0
      ? { phase: "offline", reason: "NOT_CONFIGURED" }
      : { phase: "loading" },
  );

  useEffect(() => {
    const container = containerRef.current;
    if (normalizedApiKey.length === 0 || container === null) {
      setLoadState({ phase: "offline", reason: "NOT_CONFIGURED" });
      return;
    }

    let disposed = false;
    let settled = false;
    let map: AmapMap | null = null;
    setLoadState({ phase: "loading" });
    window._AMapSecurityConfig = {
      serviceHost: `${window.location.origin}/_AMapService`,
    };

    const destroyMap = (): void => {
      const currentMap = map;
      map = null;
      currentMap?.destroy();
    };
    const timer = window.setTimeout(() => {
      if (disposed || settled) {
        return;
      }
      settled = true;
      destroyMap();
      setLoadState({ phase: "offline", reason: "LOAD_TIMEOUT" });
    }, loadTimeoutMs);
    const fail = (): void => {
      if (disposed || settled) {
        return;
      }
      settled = true;
      window.clearTimeout(timer);
      destroyMap();
      setLoadState({ phase: "offline", reason: "LOAD_FAILED" });
    };

    void loader
      .load({ key: normalizedApiKey, version: "2.0", plugins: [] })
      .then((candidateApi) => {
        if (disposed || settled) {
          return;
        }
        const api = requireAmapJsApi(candidateApi);
        api.convertFrom(SHENNONGXI_CENTER_WGS84, "gps", (status, result) => {
          if (disposed || settled || map !== null) {
            return;
          }
          if (status !== "complete" || result.info !== "ok" || !hasConvertedCenter(result)) {
            fail();
            return;
          }
          try {
            map = new api.Map(container, {
              center: result.locations[0],
              resizeEnable: true,
              viewMode: "2D",
              zoom: 10,
            });
            map.on("complete", () => {
              if (disposed || settled) {
                return;
              }
              settled = true;
              window.clearTimeout(timer);
              setLoadState({ phase: "ready" });
            });
          } catch {
            fail();
          }
        });
      })
      .catch(() => {
        fail();
      });

    return () => {
      disposed = true;
      window.clearTimeout(timer);
      destroyMap();
    };
  }, [loadTimeoutMs, loader, normalizedApiKey]);

  if (loadState.phase === "offline") {
    return fallback;
  }

  return (
    <div className="map-canvas amap-context-canvas">
      <div
        ref={containerRef}
        className="amap-surface"
        role="group"
        aria-label="高德普通道路位置参考，神农溪区域"
      />
      {loadState.phase === "loading" ? (
        <div className="map-loading" role="status" aria-live="polite">
          <span aria-hidden="true" />
          正在加载高德位置参考…
        </div>
      ) : null}
      <div className="amap-context-note" aria-live="polite">
        <strong>高德位置参考</strong>
        <span>
          {activeTaskId === null
            ? "等待创建任务；这里不表示遥感分析成果。"
            : `任务 ${activeTaskId.slice(0, 8)} 正在生成分析成果；高德仅提供位置参考。`}
        </span>
      </div>
    </div>
  );
}

function requireAmapJsApi(value: unknown): AmapJsApi {
  if (typeof value !== "object" || value === null) {
    throw new TypeError("AMap JS API unavailable");
  }
  const candidate = value as Record<string, unknown>;
  if (typeof candidate["Map"] !== "function" || typeof candidate["convertFrom"] !== "function") {
    throw new TypeError("AMap JS API contract invalid");
  }
  return value as AmapJsApi;
}

function hasConvertedCenter(
  result: AmapConversionResult,
): result is AmapConversionResult & { readonly locations: readonly [unknown, ...unknown[]] } {
  return Array.isArray(result.locations) && result.locations.length > 0;
}
