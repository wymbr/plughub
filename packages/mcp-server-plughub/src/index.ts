/**
 * index.ts
 * Entry point do mcp-server-plughub.
 */

import { startServer } from "./server"

const PORT = parseInt(process.env["PORT"] ?? "3100", 10)
const HOST = process.env["HOST"] ?? "0.0.0.0"

startServer({ port: PORT, host: HOST }).catch((err) => {
  console.error("❌ Falha ao iniciar mcp-server-plughub:", err)
  process.exit(1)
})
