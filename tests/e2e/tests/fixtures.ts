import { createServer, request, type Server } from "node:http";

import { expect, test as base } from "@playwright/test";

const proxyTargets = [
  { listenPort: 3000, targetHost: "web", targetPort: 3000 },
  { listenPort: 8000, targetHost: "master-agent", targetPort: 8000 },
  { listenPort: 8004, targetHost: "publisher-agent", targetPort: 8004 },
] as const;

interface Diagnostics {
  readonly amapNetwork: AmapNetworkHarness;
  readonly browserMessages: string[];
}

export interface AmapNetworkHarness {
  readonly requests: string[];
  readonly setMode: (mode: "offline" | "success") => void;
}

const amapRequestPattern = /^https:\/\/(?:[^/]+\.)?amap\.com\//;
const amapLoaderPattern = /^https:\/\/webapi\.amap\.com\/maps(?:\?|$)/;
const deterministicAmapScript = `
(() => {
  const lifecycle = {
    conversions: 0,
    mapsCreated: 0,
    mapsDestroyed: 0,
    lastConversion: null,
    securityServiceHost: window._AMapSecurityConfig?.serviceHost ?? null,
  };
  window.__HENNONXI_E2E_AMAP__ = lifecycle;

  class DeterministicRoadMap {
    constructor(container) {
      this.container = container;
      lifecycle.mapsCreated += 1;
      container.dataset.e2eAmapState = "ready";
      const roadSurface = document.createElement("div");
      roadSurface.dataset.e2eAmapRoadSurface = "true";
      roadSurface.setAttribute("aria-hidden", "true");
      roadSurface.style.cssText =
        "position:absolute;inset:0;background:#dce5da;" +
        "background-image:linear-gradient(32deg,transparent 47%,#fff 48%,#fff 51%,transparent 52%);";
      container.appendChild(roadSurface);
    }

    on(event, listener) {
      if (event === "complete") {
        queueMicrotask(listener);
      }
    }

    destroy() {
      lifecycle.mapsDestroyed += 1;
      this.container.replaceChildren();
      delete this.container.dataset.e2eAmapState;
    }
  }

  window.AMap = {
    Map: DeterministicRoadMap,
    convertFrom(point, type, callback) {
      lifecycle.conversions += 1;
      lifecycle.lastConversion = { point: [...point], type };
      callback("complete", {
        info: "ok",
        locations: [{ lng: 110.304, lat: 31.259 }],
      });
    },
  };
  window.___onAPILoaded();
})();
`;

export const test = base.extend<Diagnostics>({
  amapNetwork: [
    async ({ page }, use) => {
      const requests: string[] = [];
      let mode: "offline" | "success" = "success";
      await page.route(amapRequestPattern, async (route) => {
        const url = route.request().url();
        requests.push(url);
        if (mode === "success" && amapLoaderPattern.test(url)) {
          await route.fulfill({
            body: deterministicAmapScript,
            contentType: "application/javascript; charset=utf-8",
            status: 200,
          });
          return;
        }
        await route.abort("failed");
      });

      await use({
        requests,
        setMode(nextMode) {
          mode = nextMode;
        },
      });
    },
    { auto: true },
  ],
  browserMessages: async ({ page }, use, testInfo) => {
    const browserMessages: string[] = [];
    page.on("console", (message) => {
      if (
        (message.type() === "error" || message.type() === "warning") &&
        !isHeadlessWebGlDriverDiagnostic(message.text())
      ) {
        browserMessages.push(`${message.type()}: ${message.text()}`);
      }
    });
    page.on("pageerror", (error) => {
      browserMessages.push(`pageerror: ${error.message}`);
    });

    await use(browserMessages);

    if (browserMessages.length > 0 || testInfo.status !== testInfo.expectedStatus) {
      const diagnosticMessages =
        browserMessages.length > 0
          ? browserMessages.join("\n")
          : "未捕获到浏览器控制台错误或警告。";
      await testInfo.attach("browser-console.log", {
        body: Buffer.from(`${diagnosticMessages}\n`, "utf8"),
        contentType: "text/plain",
      });
    }
  },
  page: async ({ page }, use) => {
    const proxies = await Promise.all(proxyTargets.map(startProxy));
    try {
      await use(page);
    } finally {
      await Promise.all(proxies.map(closeServer));
    }
  },
});

export { expect };

function isHeadlessWebGlDriverDiagnostic(message: string): boolean {
  return message.includes("GL Driver Message") && message.includes("GPU stall due to ReadPixels");
}

async function startProxy(target: (typeof proxyTargets)[number]): Promise<Server> {
  const server = createServer((incoming, outgoing) => {
    const forwarded = request(
      {
        host: target.targetHost,
        port: target.targetPort,
        method: incoming.method,
        path: incoming.url,
        headers: incoming.headers,
      },
      (response) => {
        outgoing.writeHead(response.statusCode ?? 502, response.headers);
        response.pipe(outgoing);
      },
    );
    forwarded.on("error", () => {
      if (!outgoing.headersSent) {
        outgoing.writeHead(502, { "content-type": "text/plain; charset=utf-8" });
      }
      outgoing.end("E2E upstream unavailable");
    });
    incoming.pipe(forwarded);
  });

  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(target.listenPort, "127.0.0.1", () => {
      server.off("error", reject);
      resolve();
    });
  });
  return server;
}

async function closeServer(server: Server): Promise<void> {
  server.closeAllConnections();
  await new Promise<void>((resolve, reject) => {
    server.close((error) => {
      if (error === undefined) {
        resolve();
      } else {
        reject(error);
      }
    });
  });
}
