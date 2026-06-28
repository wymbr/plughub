/**
 * routes/knowledge.ts
 * REST surface for the knowledge base (search + snippet CRUD), consumed by the
 * platform-ui KnowledgePage and by the evaluation-api CalibrationNote publish.
 *
 * Reuses the same db helpers as the MCP tools (single source of logic). Gated by
 * `requireKnowledgeAccess` (service-token OR Bearer+ABAC evaluation.gerir_rubrica).
 *
 *   GET    /v1/knowledge/search        ?tenant_id&query&namespace&top_k&min_score
 *   POST   /v1/knowledge/snippets      { tenant_id, namespace?, content, source_ref?, metadata? }
 *   DELETE /v1/knowledge/snippets/:id  ?tenant_id
 */
import { Router, type Request, type Response } from "express"
import type { Pool } from "pg"
import { upsertSnippet, deleteSnippet, searchSnippets } from "../db.js"
import { embedText, type KnowledgeConfig } from "../tools.js"
import { requireKnowledgeAccess, type KnowledgeGateOpts } from "../middleware/require-knowledge-access.js"

export function createKnowledgeRouter(
  pool: Pool,
  config: KnowledgeConfig,
  gate: KnowledgeGateOpts,
): Router {
  const router = Router()
  const readGate  = requireKnowledgeAccess(gate, false)
  const writeGate = requireKnowledgeAccess(gate, true)

  // ── GET /v1/knowledge/search ───────────────────────────────────────────────
  router.get("/v1/knowledge/search", readGate, async (req: Request, res: Response) => {
    try {
      const tenant_id = (req.query["tenant_id"] as string) ?? ""
      const query     = (req.query["query"]     as string) ?? ""
      const namespace = (req.query["namespace"] as string) || undefined
      const top_k     = Math.min(Math.max(parseInt((req.query["top_k"] as string) ?? "5", 10) || 5, 1), 50)
      const min_score = Math.min(Math.max(parseFloat((req.query["min_score"] as string) ?? "0") || 0, 0), 1)
      if (!tenant_id || !query) {
        res.status(400).json({ error: "validation_error", message: "tenant_id and query are required" })
        return
      }
      const embedding = await embedText(query, config)
      const snippets = await searchSnippets(pool, {
        tenant_id, namespace, embedding,
        query_text: embedding ? undefined : query, top_k, min_score,
      })
      res.json({
        query, tenant_id, namespace,
        search_mode:  embedding ? "vector" : "fulltext",
        result_count: snippets.length,
        results: snippets.map(s => ({
          snippet_id: s.snippet_id,
          content:    s.content,
          score:      Math.round((s.score ?? 0) * 10000) / 10000,
          source_ref: s.source_ref,
          namespace:  s.namespace,
          metadata:   s.metadata,
        })),
      })
    } catch (e) {
      res.status(500).json({ error: "internal_error", message: e instanceof Error ? e.message : String(e) })
    }
  })

  // ── POST /v1/knowledge/snippets (upsert) ───────────────────────────────────
  router.post("/v1/knowledge/snippets", writeGate, async (req: Request, res: Response) => {
    try {
      const body = (req.body ?? {}) as Record<string, unknown>
      const tenant_id  = String(body["tenant_id"] ?? "")
      const content    = String(body["content"] ?? "")
      const namespace  = String(body["namespace"] ?? "default")
      const source_ref = body["source_ref"] != null ? String(body["source_ref"]) : undefined
      const metadata   = (body["metadata"] as Record<string, unknown> | undefined) ?? undefined
      if (!tenant_id || !content) {
        res.status(400).json({ error: "validation_error", message: "tenant_id and content are required" })
        return
      }
      const embedding = await embedText(content, config)
      const result = await upsertSnippet(pool, {
        tenant_id, namespace, content, embedding: embedding ?? null, source_ref, metadata,
      })
      res.status(result.created ? 201 : 200).json({
        snippet_id: result.snippet_id,
        created:    result.created,
        tenant_id, namespace, content, source_ref: source_ref ?? null, metadata: metadata ?? {},
      })
    } catch (e) {
      res.status(500).json({ error: "internal_error", message: e instanceof Error ? e.message : String(e) })
    }
  })

  // ── DELETE /v1/knowledge/snippets/:id ──────────────────────────────────────
  router.delete("/v1/knowledge/snippets/:snippet_id", writeGate, async (req: Request, res: Response) => {
    try {
      const tenant_id  = (req.query["tenant_id"] as string) ?? ""
      const snippet_id = req.params["snippet_id"] ?? ""
      if (!tenant_id) {
        res.status(400).json({ error: "validation_error", message: "tenant_id is required" })
        return
      }
      const deleted = await deleteSnippet(pool, { tenant_id, snippet_id })
      res.json({ deleted, snippet_id, tenant_id })
    } catch (e) {
      res.status(500).json({ error: "internal_error", message: e instanceof Error ? e.message : String(e) })
    }
  })

  return router
}
