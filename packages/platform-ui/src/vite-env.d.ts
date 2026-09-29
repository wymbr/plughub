/// <reference types="vite/client" />

// AUT-20 — sem VITE_*_URL de serviço: a UI fala só com a própria origem (borda/proxy).
// eslint-disable-next-line @typescript-eslint/no-empty-interface
interface ImportMetaEnv {}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
