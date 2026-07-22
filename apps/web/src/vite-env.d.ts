/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_AMAP_JS_API_KEY?: string;
  readonly VITE_AMAP_LOAD_TIMEOUT_MS?: string;
  readonly VITE_MASTER_BASE_URL?: string;
  readonly VITE_PUBLISHER_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
