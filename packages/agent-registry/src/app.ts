/**
 * app.ts
 * Express app do Agent Registry.
 */

import express, { Request, Response, NextFunction } from "express"
import { ZodError }           from "zod"
import { poolsRouter }            from "./routes/pools"
import { skillsRouter }           from "./routes/skills"
import { instancesRouter }        from "./routes/instances"
import { channelsRouter }         from "./routes/channels"
import { channelEndpointsRouter } from "./routes/channel-endpoints"
// Skill Versioning Fase E: per-skill SkillVersionSlot aposentado (duplicação do
// PoolSkillSlot, autoritativo). Rota desmontada; model removido do schema.
import { poolSlotsRouter }        from "./routes/pool-slots"
import { operationalRouter }      from "./routes/operational"
import { requireResourceWrite }   from "./middleware/require-resource-write"

export const app = express()

// ── CORS — allow browser requests from platform-ui ──
app.use((req: Request, res: Response, next: NextFunction) => {
  const origin = req.headers.origin
  // Allow any localhost origin (dev + demo) and any configured CORS_ORIGIN
  const allowed = process.env["CORS_ORIGIN"] || "http://localhost:5174"
  if (origin && (origin === allowed || origin.startsWith("http://localhost"))) {
    res.setHeader("Access-Control-Allow-Origin", origin)
  }
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
  res.setHeader("Access-Control-Allow-Headers", "Content-Type, x-tenant-id, x-user-id, Authorization")
  if (req.method === "OPTIONS") return res.sendStatus(204)
  next()
})

app.use(express.json())

// ── Rotas ──────────────────────────────────
// G-PROBE platform-wide: gate DUAL (service-token OU Bearer+ABAC config.resources) nas
// MUTAÇÕES dos routers de config que a UI (PoolsPage/registry.ts) edita diretamente.
// GET aberto (o middleware deixa passar). FORA do gate por ora (runtime/deploy interno,
// cadeia de callers maior): instances, operational e pool-slots (promote/rollback do
// Fluxo→Deploy, mediado por mcp-server) — fatia própria.
app.use("/v1/pools",              requireResourceWrite, poolsRouter)
app.use("/v1/pools/:pool_id",     poolSlotsRouter)   // slots sub-routes (deploy) — não gateado nesta fatia
app.use("/v1/skills",             requireResourceWrite, skillsRouter)
app.use("/v1/instances",          instancesRouter)
app.use("/v1/channels",           requireResourceWrite, channelsRouter)
app.use("/v1/channel-endpoints",  requireResourceWrite, channelEndpointsRouter)
app.use("/v1/operational",        operationalRouter)

// ── Healthcheck ────────────────────────────
app.get("/v1/health", (_req: Request, res: Response) => {
  res.json({ status: "ok", service: "agent-registry", version: "1.0.0" })
})

// ── Error handler ──────────────────────────
app.use((err: unknown, _req: Request, res: Response, _next: NextFunction) => {
  // Duck-type check for ZodError: handles dual-zod-instance case when schemas
  // package has its own zod copy (instanceof ZodError would fail cross-instance).
  const isZodError = err instanceof ZodError
    || (err !== null && typeof err === "object" && "issues" in err && Array.isArray((err as { issues: unknown }).issues))
  if (isZodError) {
    const zodErr = err as ZodError
    return res.status(422).json({
      error:  "validation_error",
      detail: zodErr.errors ?? (zodErr as unknown as { issues: unknown[] }).issues,
    })
  }
  if (err instanceof Error) {
    return res.status(500).json({
      error:  "internal_error",
      detail: process.env["NODE_ENV"] === "development" ? err.message : "Internal server error",
    })
  }
  return res.status(500).json({ error: "unknown_error" })
})
