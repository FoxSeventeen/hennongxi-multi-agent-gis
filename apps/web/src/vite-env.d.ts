/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_MASTER_BASE_URL?: string;
  readonly VITE_PUBLISHER_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
