import type { IncomingMessage, ServerResponse } from "node:http";

const AMAP_PROXY_PREFIX = "/_AMapService";
const MAX_REQUEST_URL_LENGTH = 2_048;
const DEFAULT_TIMEOUT_MS = 3_000;
const DEFAULT_MAX_RESPONSE_BYTES = 256 * 1_024;
const DEFAULT_MAX_CONCURRENT = 8;

const ALLOWED_UPSTREAMS = new Map([
  [
    `${AMAP_PROXY_PREFIX}/v3/assistant/coordinate/convert`,
    "https://restapi.amap.com/v3/assistant/coordinate/convert",
  ],
]);

const ALLOWED_CONTENT_TYPES = new Set([
  "application/json",
  "application/javascript",
  "text/javascript",
]);

type NextFunction = () => void;

export interface AmapSecurityProxyOptions {
  securityCode?: string;
  upstreamFetch?: typeof fetch;
  timeoutMs?: number;
  maxResponseBytes?: number;
  maxConcurrent?: number;
}

interface ProxyErrorBody {
  error: {
    code: string;
    message: string;
  };
}

class ResponseTooLargeError extends Error {}

function sendError(
  response: ServerResponse,
  status: number,
  code: string,
  message: string,
): void {
  const body: ProxyErrorBody = { error: { code, message } };
  response.statusCode = status;
  response.setHeader("Content-Type", "application/json; charset=utf-8");
  response.setHeader("Cache-Control", "no-store");
  response.setHeader("X-Content-Type-Options", "nosniff");
  response.end(JSON.stringify(body));
}

function isProxyPath(pathname: string): boolean {
  return pathname === AMAP_PROXY_PREFIX || pathname.startsWith(`${AMAP_PROXY_PREFIX}/`);
}

function isSameOriginBrowserRequest(request: IncomingMessage): boolean {
  const fetchSite = request.headers["sec-fetch-site"];
  return fetchSite === undefined || fetchSite === "same-origin";
}

function parseDeclaredLength(response: Response): number | undefined {
  const rawLength = response.headers.get("content-length");
  if (rawLength === null) {
    return undefined;
  }
  if (!/^\d+$/.test(rawLength)) {
    throw new Error("invalid content length");
  }
  return Number(rawLength);
}

async function readBoundedBody(
  response: Response,
  maxResponseBytes: number,
): Promise<Uint8Array> {
  const declaredLength = parseDeclaredLength(response);
  if (declaredLength !== undefined && declaredLength > maxResponseBytes) {
    throw new ResponseTooLargeError();
  }

  const reader = response.body?.getReader();
  if (reader === undefined) {
    return new Uint8Array();
  }
  const chunks: Uint8Array[] = [];
  let totalLength = 0;

  for (;;) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    totalLength += value.byteLength;
    if (totalLength > maxResponseBytes) {
      await reader.cancel();
      throw new ResponseTooLargeError();
    }
    chunks.push(value);
  }

  const body = new Uint8Array(totalLength);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return body;
}

export function createAmapSecurityProxyMiddleware(
  options: AmapSecurityProxyOptions,
): (
  request: IncomingMessage,
  response: ServerResponse,
  next: NextFunction,
) => Promise<void> {
  const securityCode = options.securityCode?.trim() ?? "";
  const upstreamFetch = options.upstreamFetch ?? fetch;
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const maxResponseBytes = options.maxResponseBytes ?? DEFAULT_MAX_RESPONSE_BYTES;
  const maxConcurrent = options.maxConcurrent ?? DEFAULT_MAX_CONCURRENT;
  let activeRequests = 0;

  return async (request, response, next): Promise<void> => {
    const rawUrl = request.url ?? "";
    let requestUrl: URL;
    try {
      requestUrl = new URL(rawUrl, "http://localhost");
    } catch {
      next();
      return;
    }

    if (!isProxyPath(requestUrl.pathname)) {
      next();
      return;
    }
    if (securityCode.length === 0) {
      sendError(
        response,
        503,
        "AMAP_PROXY_NOT_CONFIGURED",
        "高德上下文地图安全代理未配置",
      );
      return;
    }
    if (request.method !== "GET") {
      response.setHeader("Allow", "GET");
      sendError(response, 405, "AMAP_PROXY_METHOD_NOT_ALLOWED", "只允许读取高德地图数据");
      return;
    }
    if (!isSameOriginBrowserRequest(request)) {
      sendError(response, 403, "AMAP_PROXY_CROSS_SITE_FORBIDDEN", "拒绝跨站代理请求");
      return;
    }
    if (rawUrl.length > MAX_REQUEST_URL_LENGTH) {
      sendError(response, 414, "AMAP_PROXY_REQUEST_TOO_LARGE", "代理请求超过允许长度");
      return;
    }

    const upstreamBaseUrl = ALLOWED_UPSTREAMS.get(requestUrl.pathname);
    if (upstreamBaseUrl === undefined) {
      sendError(response, 404, "AMAP_PROXY_PATH_NOT_ALLOWED", "该高德服务路径未获批准");
      return;
    }
    if (requestUrl.searchParams.has("jscode")) {
      sendError(response, 400, "AMAP_PROXY_INVALID_QUERY", "安全密钥只能由服务端附加");
      return;
    }
    if (activeRequests >= maxConcurrent) {
      sendError(response, 429, "AMAP_PROXY_BUSY", "高德地图代理当前繁忙，请稍后重试");
      return;
    }

    const upstreamUrl = new URL(upstreamBaseUrl);
    upstreamUrl.search = requestUrl.search;
    upstreamUrl.searchParams.set("jscode", securityCode);
    const controller = new AbortController();
    const timeout = setTimeout(() => {
      controller.abort();
    }, timeoutMs);
    activeRequests += 1;

    try {
      const upstreamResponse = await upstreamFetch(upstreamUrl, {
        method: "GET",
        headers: {
          Accept: "application/json, application/javascript, text/javascript",
          "Accept-Encoding": "identity",
        },
        redirect: "error",
        signal: controller.signal,
      });

      if (
        upstreamResponse.redirected ||
        upstreamResponse.status < 200 ||
        upstreamResponse.status >= 300
      ) {
        sendError(response, 502, "AMAP_PROXY_UPSTREAM_FAILED", "高德地图上游暂时不可用");
        return;
      }

      const contentType = upstreamResponse.headers.get("content-type") ?? "";
      const mediaType = contentType.split(";", 1)[0]?.trim().toLowerCase() ?? "";
      if (!ALLOWED_CONTENT_TYPES.has(mediaType)) {
        sendError(response, 502, "AMAP_PROXY_INVALID_RESPONSE", "高德地图上游响应格式无效");
        return;
      }

      const body = await readBoundedBody(upstreamResponse, maxResponseBytes);
      response.statusCode = 200;
      response.setHeader("Content-Type", contentType);
      response.setHeader("Cache-Control", "no-store");
      response.setHeader("X-Content-Type-Options", "nosniff");
      response.end(body);
    } catch (error) {
      if (controller.signal.aborted) {
        sendError(response, 504, "AMAP_PROXY_TIMEOUT", "高德地图上游请求超时");
      } else if (error instanceof ResponseTooLargeError) {
        sendError(
          response,
          502,
          "AMAP_PROXY_RESPONSE_TOO_LARGE",
          "高德地图上游响应超过安全上限",
        );
      } else {
        sendError(response, 502, "AMAP_PROXY_UPSTREAM_FAILED", "高德地图上游暂时不可用");
      }
    } finally {
      clearTimeout(timeout);
      activeRequests -= 1;
    }
  };
}
