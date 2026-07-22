// @vitest-environment node

import type { IncomingHttpHeaders, IncomingMessage, ServerResponse } from "node:http";
import { describe, expect, it, vi } from "vitest";

import {
  createAmapSecurityProxyMiddleware,
  type AmapSecurityProxyOptions,
} from "./amapSecurityProxy";

interface ProxyResult {
  status: number;
  body: string;
  headers: Map<string, string | number | readonly string[]>;
  nextCalled: boolean;
}

interface ProxyRequestOptions {
  method?: string;
  headers?: IncomingHttpHeaders;
}

type ProxyMiddleware = ReturnType<typeof createAmapSecurityProxyMiddleware>;

async function invokeMiddleware(
  middleware: ProxyMiddleware,
  url: string,
  requestOptions: ProxyRequestOptions = {},
): Promise<ProxyResult> {
  const headers = new Map<string, string | number | readonly string[]>();
  const chunks: string[] = [];
  let nextCalled = false;
  const request = {
    headers: requestOptions.headers ?? {},
    method: requestOptions.method ?? "GET",
    url,
  } as IncomingMessage;
  const response = {
    statusCode: 200,
    end(chunk?: string | Uint8Array) {
      if (typeof chunk === "string") {
        chunks.push(chunk);
      } else if (chunk !== undefined) {
        chunks.push(new TextDecoder().decode(chunk));
      }
    },
    setHeader(name: string, value: string | number | readonly string[]) {
      headers.set(name.toLowerCase(), value);
      return this;
    },
  } as unknown as ServerResponse;

  await middleware(request, response, () => {
    nextCalled = true;
  });
  return { status: response.statusCode, body: chunks.join(""), headers, nextCalled };
}

async function invokeProxy(
  options: AmapSecurityProxyOptions,
  url: string,
  requestOptions: ProxyRequestOptions = {},
): Promise<ProxyResult> {
  return await invokeMiddleware(
    createAmapSecurityProxyMiddleware(options),
    url,
    requestOptions,
  );
}

function successfulUpstreamResponse(): Response {
  return new Response('{"status":"1","locations":"110.1,31.2"}', {
    status: 200,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

describe("高德 JS API 同源安全代理", () => {
  it("非代理路径交给后续 Vite 中间件", async () => {
    const upstreamFetch = vi.fn<typeof fetch>();

    const response = await invokeProxy(
      { securityCode: "server-only-test-code", upstreamFetch },
      "/src/main.tsx",
    );

    expect(response.nextCalled).toBe(true);
    expect(upstreamFetch).not.toHaveBeenCalled();
  });

  it("未配置服务端安全密钥时拒绝请求且不访问上游", async () => {
    const upstreamFetch = vi.fn<typeof fetch>();
    const response = await invokeProxy(
      { securityCode: "", upstreamFetch },
      "/_AMapService/v3/assistant/coordinate/convert?locations=110.1%2C31.2&coordsys=gps",
    );

    expect(response.status).toBe(503);
    expect(JSON.parse(response.body)).toMatchObject({
      error: { code: "AMAP_PROXY_NOT_CONFIGURED" },
    });
    expect(upstreamFetch).not.toHaveBeenCalled();
  });

  it("只把批准的坐标转换路径转发到固定 HTTPS 上游并由服务端附加安全密钥", async () => {
    const upstreamFetch = vi.fn<typeof fetch>(() => Promise.resolve(successfulUpstreamResponse()));
    const response = await invokeProxy(
      { securityCode: "server-only-test-code", upstreamFetch },
      "/_AMapService/v3/assistant/coordinate/convert?locations=110.1%2C31.2&coordsys=gps",
    );

    expect(response.status).toBe(200);
    expect(response.body).toContain('"status":"1"');
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(upstreamFetch).toHaveBeenCalledOnce();
    const [target, init] = upstreamFetch.mock.calls[0] ?? [];
    expect(target).toBeInstanceOf(URL);
    if (!(target instanceof URL)) {
      throw new TypeError("代理上游必须使用 URL 对象");
    }
    const targetUrl = new URL(target);
    expect(targetUrl.origin).toBe("https://restapi.amap.com");
    expect(targetUrl.pathname).toBe("/v3/assistant/coordinate/convert");
    expect(targetUrl.searchParams.get("coordsys")).toBe("gps");
    expect(targetUrl.searchParams.get("jscode")).toBe("server-only-test-code");
    expect(init).toMatchObject({ method: "GET", redirect: "error" });
  });

  it("把高德 application/json JSONP 规范化为可执行且 nosniff 的安全 JavaScript", async () => {
    const callback = "jsonp_482913_1720000000000_";
    const upstreamFetch = vi.fn<typeof fetch>(() =>
      Promise.resolve(
        new Response(
          `${callback}( { "status": "1", "info": "ok", "locations": "110.1,31.2" } )`,
          {
            status: 200,
            headers: { "content-type": "application/json" },
          },
        ),
      ),
    );

    const response = await invokeProxy(
      { securityCode: "server-only-test-code", upstreamFetch },
      `/_AMapService/v3/assistant/coordinate/convert?locations=110.1%2C31.2&coordsys=gps&callback=${callback}`,
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe(
      "application/javascript; charset=utf-8",
    );
    expect(response.headers.get("x-content-type-options")).toBe("nosniff");
    expect(response.body).toBe(
      `${callback}({"status":"1","info":"ok","locations":"110.1,31.2"});`,
    );
  });

  it("拒绝可注入脚本的 JSONP callback 且不访问上游", async () => {
    const upstreamFetch = vi.fn<typeof fetch>();

    const response = await invokeProxy(
      { securityCode: "server-only-test-code", upstreamFetch },
      "/_AMapService/v3/assistant/coordinate/convert?locations=110.1%2C31.2&coordsys=gps&callback=alert%281%29",
    );

    expect(response.status).toBe(400);
    expect(JSON.parse(response.body)).toMatchObject({
      error: { code: "AMAP_PROXY_INVALID_QUERY" },
    });
    expect(upstreamFetch).not.toHaveBeenCalled();
  });

  it("拒绝重复 callback 参数且不访问上游", async () => {
    const upstreamFetch = vi.fn<typeof fetch>();

    const response = await invokeProxy(
      { securityCode: "server-only-test-code", upstreamFetch },
      "/_AMapService/v3/assistant/coordinate/convert?callback=jsonp_first_&callback=jsonp_second_",
    );

    expect(response.status).toBe(400);
    expect(JSON.parse(response.body)).toMatchObject({
      error: { code: "AMAP_PROXY_INVALID_QUERY" },
    });
    expect(upstreamFetch).not.toHaveBeenCalled();
  });

  it("拒绝与请求 callback 不一致的上游 JSONP 正文", async () => {
    const upstreamFetch = vi.fn<typeof fetch>(() =>
      Promise.resolve(
        new Response('attackerCallback({"status":"1"})', {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      ),
    );

    const response = await invokeProxy(
      { securityCode: "server-only-test-code", upstreamFetch },
      "/_AMapService/v3/assistant/coordinate/convert?callback=jsonp_expected_",
    );

    expect(response.status).toBe(502);
    expect(JSON.parse(response.body)).toMatchObject({
      error: { code: "AMAP_PROXY_INVALID_RESPONSE" },
    });
    expect(response.body).not.toContain("attackerCallback");
  });

  it.each([
    ["POST", "/_AMapService/v3/assistant/coordinate/convert", 405, "AMAP_PROXY_METHOD_NOT_ALLOWED"],
    ["GET", "/_AMapService/v3/place/text", 404, "AMAP_PROXY_PATH_NOT_ALLOWED"],
    [
      "GET",
      "/_AMapService/v3/assistant/coordinate/convert?jscode=client-supplied",
      400,
      "AMAP_PROXY_INVALID_QUERY",
    ],
  ])("拒绝越界请求：%s %s", async (method, path, status, code) => {
    const upstreamFetch = vi.fn<typeof fetch>();
    const response = await invokeProxy(
      { securityCode: "server-only-test-code", upstreamFetch },
      path,
      { method },
    );

    expect(response.status).toBe(status);
    expect(JSON.parse(response.body)).toMatchObject({ error: { code } });
    expect(upstreamFetch).not.toHaveBeenCalled();
  });

  it("拒绝浏览器跨站请求，避免第三方页面消耗本地代理额度", async () => {
    const upstreamFetch = vi.fn<typeof fetch>();
    const response = await invokeProxy(
      { securityCode: "server-only-test-code", upstreamFetch },
      "/_AMapService/v3/assistant/coordinate/convert",
      { headers: { "sec-fetch-site": "cross-site" } },
    );

    expect(response.status).toBe(403);
    expect(upstreamFetch).not.toHaveBeenCalled();
  });

  it("限制上游响应体并且不把上游正文返回为代理错误", async () => {
    const upstreamFetch = vi.fn<typeof fetch>(() =>
      Promise.resolve(
        new Response("x".repeat(257), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      ),
    );
    const response = await invokeProxy(
      { securityCode: "server-only-test-code", upstreamFetch, maxResponseBytes: 256 },
      "/_AMapService/v3/assistant/coordinate/convert",
    );

    expect(response.status).toBe(502);
    expect(response.body).toContain("AMAP_PROXY_RESPONSE_TOO_LARGE");
    expect(response.body).not.toContain("xxxxx");
  });

  it("上游超时后返回脱敏错误，并明确禁止重定向", async () => {
    const upstreamFetch = vi.fn<typeof fetch>(
      async (_input, init) =>
        await new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => {
            reject(new DOMException("aborted", "AbortError"));
          });
        }),
    );
    const response = await invokeProxy(
      { securityCode: "server-only-test-code", upstreamFetch, timeoutMs: 10 },
      "/_AMapService/v3/assistant/coordinate/convert",
    );

    expect(response.status).toBe(504);
    expect(JSON.parse(response.body)).toMatchObject({
      error: { code: "AMAP_PROXY_TIMEOUT" },
    });
    expect(upstreamFetch.mock.calls[0]?.[1]).toMatchObject({ redirect: "error" });
  });

  it("达到并发上限时快速拒绝新请求", async () => {
    let releaseUpstream: (() => void) | undefined;
    const upstreamFetch = vi.fn<typeof fetch>(
      async () =>
        await new Promise<Response>((resolve) => {
          releaseUpstream = () => {
            resolve(successfulUpstreamResponse());
          };
        }),
    );
    const middlewareOptions = {
      securityCode: "server-only-test-code",
      upstreamFetch,
      maxConcurrent: 1,
    };
    const middleware = createAmapSecurityProxyMiddleware(middlewareOptions);
    const invoke = async (): Promise<ProxyResult> =>
      await invokeMiddleware(
        middleware,
        "/_AMapService/v3/assistant/coordinate/convert",
      );

    const firstRequest = invoke();
    await vi.waitFor(() => {
      expect(upstreamFetch).toHaveBeenCalledOnce();
    });
    const secondResponse = await invoke();

    expect(secondResponse.status).toBe(429);
    releaseUpstream?.();
    await expect(firstRequest).resolves.toMatchObject({ status: 200 });
  });
});
