/**
 * mcp-server-knowledge/src/index.ts
 *
 * Domain MCP Server — RAG knowledge base for Arc 6 (Evaluation Platform).
 *
 * Tools (registered per SSE connection):
 *   knowledge_search  — hybrid vector + full-text search
 *   knowledge_upsert  — insert / update snippet with optional embedding
 *   knowledge_delete  — delete snippet by ID
 *
 * Admin REST (shared, HTTP only — not MCP):
 *   GET    /admin/namespaces   — list namespaces + stats
 *   GET    /admin/snippets     — paginated snippet metadata (no content)
 *   DELETE /admin/namespaces/:ns — bulk delete
 *   GET    /health             — liveness probe
 *
 * Transport: MCP SSE (same pattern as mcp-server-auth)
 *   GET  /sse      — open SSE connection
 *   POST /messages — receive MCP JSON-RPC
 *
 * Configuration (environment variables):
 *   DATABASE_URL         — PostgreSQL connection string (required)
 *   EMBEDDING_PROVIDER   — "" | "openai"  (default: "")
 *   OPENAI_API_KEY       — required when EMBEDDING_PROVIDER=openai
 *   EMBEDDING_MODEL      — default: text-embedding-3-small
 *   EMBEDDING_DIM        — default: 1536
 *   ADMIN_TOKEN          — X-Admin-Token for admin endpoints (empty = no auth)
 *   PORT                 — default: 3200
 */

import express, { type Request, type Response } from "express"
import { McpServer }          from "@modelcontextprotocol/sdk/server/mcp.js"
import { SSEServerTransport } from "@modelcontextprotocol/sdk/server/sse.js"
import { Pool }               from "pg"
import { ensureSchema, createPool } from "./db.js"
import { registerKnowledgeTools, type KnowledgeConfig } from "./tools.js"
import { createAdminRouter } from "./admin.js"

// ─── Configuration ────────────────────────────────────────────────────────────

const DATABASE_URL       = process.env["DATABASE_URL"] ?? "postgresql://plughub:plughub@localhost:5432/plughub"
const EMBEDDING_PROVIDER = (process.env["EMBEDDING_PROVIDER"] ?? "") as "" | "openai"
const OPENAI_API_KEY     = process.env["OPENAI_API_KEY"] ?? ""
const EMBEDDING_MODEL    = process.env["EMBEDDING_MODEL"] ?? "text-embedding-3-small"
const EMBEDDING_DIM      = parseInt(process.env["EMBEDDING_DIM"] ?? "1536", 10)
const ADMIN_TOKEN        = process.env["ADMIN_TOKEN"] ?? ""
const PORT               = parseInt(process.env["PORT"] ?? "3200", 10)

const knowledgeConfig: KnowledgeConfig = {
  embeddingProvider: EMBEDDING_PROVIDER,
  openaiApiKey:      OPENAI_API_KEY,
  embeddingModel:    EMBEDDING_MODEL,
  embeddingDim:      EMBEDDING_DIM,
}

// ─── Database pool ────────────────────────────────────────────────────────────

let pool: Pool

// ─── Express app ─────────────────────────────────────────────────────────────

const app = express()
app.use(express.json())

// ── SSE connections ────────────────────────────────────────────────────────
const transports = new Map<string, SSEServerTransport>()

app.get("/sse", async (_req: Request, res: Response) => {
  const server = new McpServer({ name: "mcp-server-knowledge", version: "1.0.0" })
  registerKnowledgeTools(server, { db: pool, config: knowledgeConfig })

  const transport = new SSEServerTransport("/messages", res)
  transports.set(transport.sessionId, transport)
  console.log(`[mcp-server-knowledge] SSE session opened: ${transport.sessionId}`)

  res.on("close", () => {
    transports.delete(transport.sessionId)
    console.log(`[mcp-server-knowledge] SSE session closed: ${transport.sessionId}`)
  })

  await server.connect(transport)
})

app.post("/messages", async (req: Request, res: Response) => {
  const sessionId = req.query["sessionId"] as string
  const transport = transports.get(sessionId)
  if (!transport) {
    res.status(400).json({ error: "Session not found", sessionId })
    return
  }
  await transport.handlePostMessage(req, res, req.body)
})

// ── Admin routes ───────────────────────────────────────────────────────────
app.use(createAdminRouter(pool!, { adminToken: ADMIN_TOKEN }))

// ── Health probe ───────────────────────────────────────────────────────────
app.get("/health", (_req: Request, res: Response) => {
  res.json({
    status:   "ok",
    service:  "mcp-server-knowledge",
    sessions: transports.size,
    embedding_provider: knowledgeConfig.embeddingProvider || "none",
  })
})

// ─── Startup ──────────────────────────────────────────────────────────────────

async function main(): Promise<void> {
  pool = createPool(DATABASE_URL)

  // Ensure schema on startup (idempotent)
  try {
    await ensureSchema(pool)
    console.log("[mcp-server-knowledge] Schema ready")
  } catch (err) {
    console.error("[mcp-server-knowledge] Schema init failed:", err)
    process.exit(1)
  }

  // Wire pool into admin router after pool is created
  // (app.use above captures a reference that is now initialized)
  app.listen(PORT, () => {
    console.log(`[mcp-server-knowledge] Listening on port ${PORT}`)
    console.log(`[mcp-server-knowledge] Tools: knowledge_search, knowledge_upsert, knowledge_delete`)
    console.log(`[mcp-server-knowledge] Embedding: ${knowledgeConfig.embeddingProvider || "none (full-text fallback)"}`)
    console.log(`[mcp-server-knowledge] Admin token: ${ADMIN_TOKEN ? "set" : "disabled (dev mode)"}`)
  })
}

main().catch(err => {
  console.error("[mcp-server-knowledge] Fatal startup error:", err)
  process.exit(1)
})
