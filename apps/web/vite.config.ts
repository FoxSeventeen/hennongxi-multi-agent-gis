import react from "@vitejs/plugin-react";
import { loadEnv, type Plugin } from "vite";
import { defineConfig } from "vitest/config";

import { createAmapSecurityProxyMiddleware } from "./vite/amapSecurityProxy";

function amapSecurityProxyPlugin(securityCode?: string): Plugin {
  const middleware = createAmapSecurityProxyMiddleware({ securityCode });
  return {
    name: "hennongxi-amap-security-proxy",
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        void middleware(request, response, next);
      });
    },
  };
}

export default defineConfig(({ mode }) => {
  const environment = loadEnv(mode, process.cwd(), "");
  return {
    plugins: [react(), amapSecurityProxyPlugin(environment.AMAP_JS_API_SECURITY_CODE)],
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
      css: true,
      restoreMocks: true,
    },
  };
});
