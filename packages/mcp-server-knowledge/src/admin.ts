/**
 * admin.ts
 * Admin REST endpoints for mcp-server-knowledge.
 *
 * All endpoints are protected by X-Admin-Token header.
 * These are NOT MCP tools — they are direct HTTP endpoints for operators.
 *
 * Routes:
 *   GET    /admin/namespaces          — list namespaces + stats for a tenant
 *   GET    /admin/snippets            — paginated snippet list (no content)
 *   DELETE /admin/namespaces/:ns      — bulk-delete all snippets in a namespace
 *   GET    /health                    — liveness probe
 */

import type { Request, Response, Router } from "express"
import { Router as createRouter }          from "express"
import { listNamespaces, deleteNamespace, type DbClient } from "./db.js"

// ─── Types ────────────────────────────────────────────────────────────────────

export interface AdminConfig {
  adminToken: string  // empty string = no auth (dev mode)
}

// ─── Auth middleware ──────────────────────────────────────────────────────────

function requireAdmin(adminToken: string) {
  return (req: Request, res: Response, next: () => void): void => {
    if (!adminToken) {
      next()
      return
    }
    const provided = req.headers["x-admin-token"] as string | undefined
    if (provided !== adminToken) {
      res.status(401).json({ error: "unauthorized", message: "X-Admin-Token mismatch" })
      return
    }
    next()
  }
}

// ─── Router factory ───────────────────────────────────────────────────────────

export function createAdminRouter(db: DbClient, config: AdminConfig): Router {
  const router   = createRouter()
  const authGuard = requireAdmin(config.adminToken)

  // ── GET /admin/namespaces?tenant_id=… ────────────────────────────────────
  router.get("/admin/namespaces", authGuard, async (req: Request, res: Response) => {
    const tenant_id = req.query["tenant_id"] as string | undefined
    if (!tenant_id) {
      res.status(400).json({ error: "tenant_id query param required" })
      return
    }
    try {
      const namespaces = await listNamespaces(db, tenant_id)
      res.json({ tenant_id, namespaces })
    } catch (err) {
      console.error("[mcp-server-knowledge] GET /admin/namespaces error:", err)
      res.status(500).json({ error: "internal_error" })
    }
  })

  // ── GET /admin/snippets?tenant_id=…&namespace=…&limit=…&offset=… ─────────
  router.get("/admin/snippets", authGuard, async (req: Request, res: Response) => {
    const tenant_id  = req.query["tenant_id"]  as string | undefined
    const namespace  = req.query["namespace"]  as string | undefined
    const limitRaw   = parseInt(req.query["limit"]  as string ?? "50", 10)
    const offsetRaw  = parseInt(req.query["offset"] as string ?? "0",  10)

    if (!tenant_id) {
      res.status(400).json({ error: "tenant_id query param required" })
      return
    }

    const limit  = Math.min(Math.max(1, limitRaw), 200)
    const offset = Math.max(0, offsetRaw)

    try {
      const nsFilter = namespace ? "AND namespace = $3" : ""
      const args: unknown[] = [tenant_id, limit, offset]
      if (namespace) args.splice(2, 0, namespace)

      // Return metadata only (no content) to keep payload small
      const sql = namespace
        ? `SELECT snippet_id, tenant_id, namespace, source_ref, metadata,
                  created_at, updated_at,
                  embedding IS NOT NULL AS has_embedding
             FROM knowledge.snippets
            WHERE tenant_id = $1
              AND namespace  = $3
            ORDER BY created_at DESC
            LIMIT $2 OFFSET $4`
        : `SELECT snippet_id, tenant_id, namespace, source_ref, metadata,
                  created_at, updated_at,
                  embedding IS NOT NULL AS has_embedding
             FROM knowledge.snippets
            WHERE tenant_id = $1
            ORDER BY created_at DESC
            LIMIT $2 OFFSET $3`

      const { rows } = await db.query(sql, namespace
        ? [tenant_id, limit, namespace, offset]
        : [tenant_id, limit, offset]
      )

      res.json({ tenant_id, namespace: namespace ?? null, limit, offset, rows })
    } catch (err) {
      console.error("[mcp-server-knowledge] GET /admin/snippets error:", err)
      res.status(500).json({ error: "internal_error" })
    }
  })

  // ── DELETE /admin/namespaces/:namespace?tenant_id=… ──────────────────────
  router.delete("/admin/namespaces/:namespace", authGuard, async (req: Request, res: Response) => {
    const tenant_id = req.query["tenant_id"] as string | undefined
    const namespace = req.params["namespace"]

    if (!tenant_id) {
      res.status(400).json({ error: "tenant_id query param required" })
      return
    }
    if (!namespace) {
      res.status(400).json({ error: "namespace path param required" })
      return
    }

    try {
      const deleted_count = await deleteNamespace(db, { tenant_id, namespace })
      res.json({ deleted_count, tenant_id, namespace })
    } catch (err) {
      console.error("[mcp-server-knowledge] DELETE /admin/namespaces error:", err)
      res.status(500).json({ error: "internal_error" })
    }
  })

  return router
}
