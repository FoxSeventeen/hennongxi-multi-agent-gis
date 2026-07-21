import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { createMasterClient } from "./api/client";
import { App } from "./app/App";

const rootElement = document.getElementById("root");
if (rootElement === null) {
  throw new Error("找不到应用挂载节点。");
}

const configuredBaseUrl = import.meta.env.VITE_MASTER_BASE_URL?.trim();
const configuredPublisherBaseUrl = import.meta.env.VITE_PUBLISHER_BASE_URL?.trim();
const client = createMasterClient({
  baseUrl: configuredBaseUrl && configuredBaseUrl.length > 0 ? configuredBaseUrl : "http://localhost:8000",
});

createRoot(rootElement).render(
  <StrictMode>
    <App
      client={client}
      publisherBaseUrl={
        configuredPublisherBaseUrl && configuredPublisherBaseUrl.length > 0
          ? configuredPublisherBaseUrl
          : "http://localhost:8004"
      }
    />
  </StrictMode>,
);
