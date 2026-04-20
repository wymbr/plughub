import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:3100",  // mcp-server-plughub REST bridge
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
      "/agent-ws": {
        target: "ws://localhost:3100",
        ws: true,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/agent-ws/, "/agent/ws"),
        // ECONNABORTED / ECONNRESET are expected in dev: React StrictMode opens
        // and immediately closes the first WebSocket during double-mount.
        configure: (proxy) => {
          proxy.on("error", (err: NodeJS.ErrnoException) => {
            if (err.code === "ECONNABORTED" || err.code === "ECONNRESET") return
            console.error("[ws proxy]", err.message)
          })
        },
      },
    },
  },
  build: {
    outDir: "dist",
  },
});
