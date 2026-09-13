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
import { contextMapRouter }       from "./routes/context-map"
import { requireResourceWrite, requireAbacWrite } from "./middleware/require-resource-write"

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
// G-PROBE platform-wide: gate DUAL (service-token OU Bearer+ABAC) nas MUTAÇÕES dos
// routers de config que a UI (PoolsPage/registry.ts/editor de fluxo) edita direto.
// GET aberto (o middleware deixa passar). FORA do gate: instances e operational
// (runtime interno). Com Bearer, o tenant e o autor saem do TOKEN (PID-07).
// MOD-06 (corte #3): o campo é POR ROUTER, e cada um é o que a TELA daquele
// backend já declara no menu. Antes, os quatro exigiam `config.resources` — e a
// medição ao vivo mostrou o developer sem conseguir salvar um flow que o menu lhe
// oferece, e `config.channels` recusado pela API da tela de Canais.
// PID-07: o router de DEPLOY é montado ANTES do de pools, e a ordem é o mecanismo.
// `/v1/pools` casa por prefixo, então montado primeiro o `requireResourceWrite` julgava
// também `/v1/pools/:id/slots|promote|rollback` com `config.resources` — o campo errado,
// e o único portão que essas rotas tinham. Aqui cada rota de deploy traz o seu
// (`skill_flows.operacao`, no próprio router) e responde; o que não é deploy cai no
// `next()` e chega ao router de pools com o portão dele.
app.use("/v1/pools/:pool_id",     poolSlotsRouter)
app.use("/v1/pools",              requireResourceWrite, poolsRouter)
app.use("/v1/skills",             requireAbacWrite("skill_flows", "editar"), skillsRouter)
app.use("/v1/instances",          instancesRouter)
app.use("/v1/channels",           requireAbacWrite("config", "channels"), channelsRouter)
app.use("/v1/channel-endpoints",  requireAbacWrite("config", "channels"), channelEndpointsRouter)
app.use("/v1/operational",        operationalRouter)
// D6 — vocabulário do seletor de `context_visibility`. Somente LEITURA e
// derivado do mapa; não escreve nada, por isso fora do `requireResourceWrite`.
app.use("/v1/context-map",        contextMapRouter)

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
  // Erros que JÁ CARREGAM status HTTP são de CLIENTE, não do servidor: o
  // `express.json()` marca `status: 400` + `type: "entity.parse.failed"` em corpo
  // malformado, e o `http-errors` faz o mesmo. Ignorar esse campo devolvia
  // `500 internal_error` para um JSON inválido — o que manda quem depura procurar
  // o defeito no serviço em vez de na requisição, e ainda esconde a mensagem
  // (só visível em NODE_ENV=development). Erro de cliente rotulado como falha do
  // servidor é degradação silenciosa com sinal trocado.
  const httpStatus = (err !== null && typeof err === "object")
    ? Number((err as { status?: number; statusCode?: number }).status
             ?? (err as { statusCode?: number }).statusCode)
    : NaN
  if (Number.isInteger(httpStatus) && httpStatus >= 400 && httpStatus < 500) {
    const parseFailed =
      (err as { type?: string }).type === "entity.parse.failed"
    return res.status(httpStatus).json({
      error:  parseFailed ? "invalid_json" : "bad_request",
      // A mensagem do body-parser descreve o corpo enviado pelo CLIENTE (posição do
      // token inválido) — não vaza estado interno, então pode ir sempre.
      detail: err instanceof Error ? err.message : "Bad request",
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
