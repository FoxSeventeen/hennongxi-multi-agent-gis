import { createServer, request, type Server } from "node:http";

import { expect, test as base } from "@playwright/test";

const proxyTargets = [
  { listenPort: 3000, targetHost: "web", targetPort: 3000 },
  { listenPort: 8000, targetHost: "master-agent", targetPort: 8000 },
  { listenPort: 8004, targetHost: "publisher-agent", targetPort: 8004 },
] as const;

interface Diagnostics {
  readonly browserMessages: string[];
}

export const test = base.extend<Diagnostics>({
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

    if (browserMessages.length > 0) {
      await testInfo.attach("browser-console.log", {
        body: Buffer.from(`${browserMessages.join("\n")}\n`, "utf8"),
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
