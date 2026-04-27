/**
 * app.ts
 * Express app do Agent Registry.
 */

import express, { Request, Response, NextFunction } from "express"
import { ZodError }           from "zod"
import { poolsRouter }        from "./routes/pools"
import { agentTypesRouter }   from "./routes/agent-types"
import { skillsRouter }       from "./routes/skills"
import { importRouter }       from "./routes/import"
import { instancesRouter }    from "./routes/instances"
import { channelsRouter }     from "./routes/channels"

export const app = express()

app.use(express.json())

// ── Rotas ──────────────────────────────────
app.use("/v1/pools",              poolsRouter)
app.use("/v1/agent-types/import", importRouter)   // antes de /v1/agent-types para não colidir
app.use("/v1/agent-types",        agentTypesRouter)
app.use("/v1/skills",             skillsRouter)
app.use("/v1/instances",          instancesRouter)
app.use("/v1/channels",           channelsRouter)

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
