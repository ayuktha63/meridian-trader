/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_URL?: string
  readonly VITE_WS_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

/** Injected by the Electron preload; absent in the browser build. */
interface Window {
  meridian?: {
    platform: string
    isDesktop: boolean
    version: string
  }
}
