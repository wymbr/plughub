/**
 * vite.mock.config.ts
 * Configuração Vite para testes manuais com o mock-agent-ws (porta 3101).
 *
 * Uso:
 *   npm run mock-dev
 *   → equivale a: vite --config vite.mock.config.ts
 *
 * Diferença em relação ao vite.config.ts padrão:
 *   proxy /api    → http://localhost:3101  (mock)
 *   proxy /agent-ws → ws://localhost:3101  (mock)
 */

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const MOCK_PORT = 3101;

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: `http://localhost:${MOCK_PORT}`,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
      "/analytics": {
        target: "http://localhost:3500",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/analytics/, ""),
      },
      "/agent-ws": {
        target: `ws://localhost:${MOCK_PORT}`,
        ws: true,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/agent-ws/, "/agent/ws"),
        configure: (proxy) => {
          proxy.on("error", (err: NodeJS.ErrnoException) => {
            if (err.code === "ECONNABORTED" || err.code === "ECONNRESET") return;
            console.error("[ws proxy]", err.message);
          });
        },
      },
    },
  },
  build: {
    outDir: "dist",
  },
});
