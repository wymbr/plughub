/**
 * routes/pools.ts
 * CRUD de pools — spec 4.5
 */

import { Router, Request, Response, NextFunction } from "express"
import { prisma, Prisma }    from "../db"
import { CreatePoolSchema, UpdatePoolSchema } from "../validators/pool"
import { ZodError }          from "zod"
import { publishRegistryEvent, publishRegistryChanged } from "../infra/kafka"
import { contractedCapacity } from "../lib/capacity"

export const poolsRouter = Router()

// ─────────────────────────────────────────────
// Capacity-governance item 3 — Σ session_reservation ≤ C (shared ≥ 0)
//
// C (capacidade contratada) = {t}:quota:max_concurrent_sessions, gravada pelo
// quota sync do pricing-api. A admissão híbrida deriva shared = C − Σ reservas;
// reserva acima de C tornaria o shared negativo (pool "rouba" capacidade que
// não existe). Regras:
//   - Sem C (pricing não configurado / Redis fora) → sem validação (fail-open;
//     o runtime continua protegido pela própria admissão).
//   - REDUÇÕES são sempre permitidas (heal gradual de estado legado
//     não-conforme; re-PUT do RegistrySyncer com valor igual também passa).
//   - AUMENTOS que façam Σ > C são rejeitados com 422 + detalhe.
// Conformidade é derivável (não persistida): GET /v1/pools/capacity/conformance.
// ─────────────────────────────────────────────

async function _reservedTotal(tenantId: string, excludePoolId: string | null): Promise<number> {
  const agg = await prisma.pool.aggregate({
    _sum:  { session_reservation: true },
    where: {
      tenant_id: tenantId,
      status:    "active" as never,
      ...(excludePoolId ? { NOT: { pool_id: excludePoolId } } : {}),
    },
  })
  return agg._sum.session_reservation ?? 0
}

/** Retorna o payload de erro 422, ou null quando a mutação é permitida. */
async function _reservationViolation(
  tenantId:      string,
  poolId:        string,
  newValue:      number | null | undefined,
  currentValue:  number | null,
): Promise<Record<string, unknown> | null> {
  if (newValue === undefined || newValue === null || newValue <= 0) return null
  if (currentValue !== null && newValue <= currentValue) return null   // redução/igual sempre passa
  const contracted = await contractedCapacity(tenantId)
  if (contracted === null) return null
  const reservedOthers = await _reservedTotal(tenantId, poolId)
  const reservedTotal  = reservedOthers + newValue
  if (reservedTotal <= contracted) return null
  return {
    error: "session_reservation excede a capacidade contratada (shared ficaria negativo)",
    details: {
      contracted,
      reserved_others:    reservedOthers,
      requested:          newValue,
      reserved_total:     reservedTotal,
      shared_would_be:    contracted - reservedTotal,
    },
  }
}

// ─────────────────────────────────────────────
// POST /v1/pools
// ─────────────────────────────────────────────
poolsRouter.post("/", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId  = _getTenantId(req)
    const createdBy = _getUserId(req)
    const body      = CreatePoolSchema.parse(req.body)

    // Verificar duplicata
    const existing = await prisma.pool.findUnique({
      where: { pool_id_tenant_id: { pool_id: body.pool_id, tenant_id: tenantId } },
    })
    if (existing) {
      return res.status(409).json({ error: "pool_id já registrado neste tenant" })
    }

    // Validar evaluation_template_id se fornecido
    // TODO: consultar tabela evaluation_templates

    // Capacity-governance item 3: Σ reservas ≤ C
    const violation = await _reservationViolation(
      tenantId, body.pool_id, body.session_reservation, null,
    )
    if (violation) return res.status(422).json(violation)

    // Capacity-governance item 2: queue_config ⇒ agent_kind 'human' (fila
    // atendida só para recurso escasso/lento; para IA o slot da fila
    // instanciaria o próprio agente solicitado).
    if (body.queue_config && body.agent_kind === "ai") {
      return res.status(422).json({
        error: "queue_config exige agent_kind 'human' — pool IA não tem fila atendida",
      })
    }

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const pool = await prisma.pool.create({
      data: {
        pool_id:               body.pool_id,
        tenant_id:             tenantId,
        agent_kind:            body.agent_kind ?? null,   // null → backfill infere no boot
        description:           body.description ?? null,
        channel_types:         body.channel_types,
        sla_target_ms:           body.sla_target_ms,
        webhook_skill_id:        body.webhook_skill_id ?? null,
        max_concurrent_sessions: body.max_concurrent_sessions ?? null,
        dispatch_mode:           body.dispatch_mode ?? "push",
        purpose:                 body.purpose ?? "contact",
        session_reservation:     body.session_reservation ?? null,
        max_reply_time_ms:       body.max_reply_time_ms ?? null,
        routing_expression:      body.routing_expression ?? Prisma.DbNull,
        evaluation_template_id: body.evaluation_template_id ?? null,
        supervisor_config:     body.supervisor_config ?? Prisma.DbNull,
        queue_config:          body.queue_config ?? Prisma.DbNull,
        mentionable_pools:     body.mentionable_pools ?? Prisma.DbNull,
        agent_groups:          body.agent_groups ?? [],
        llm_account_ids:       body.llm_account_ids ?? [],
        hooks:                 body.hooks ?? Prisma.DbNull,
        calendar_id:             body.calendar_id ?? null,
        context_visibility:      body.context_visibility ?? Prisma.DbNull,
        created_by:              createdBy,
      } as any,
    })

    const formatted = _formatPool(pool)

    // Publica evento para o Routing Engine atualizar o cache Redis (pool_config)
    await publishRegistryEvent({
      event:     "pool.registered",
      tenant_id: tenantId,
      pool:      formatted,
    })
    // Notifica o orchestrator-bridge para reconciliar imediatamente — senão o
    // heartbeat dele reescreve o pool_config a partir do cache em memória velho.
    await publishRegistryChanged(tenantId, "pool", body.pool_id, "created")

    return res.status(201).json(formatted)
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// GET /v1/pools
// ─────────────────────────────────────────────
poolsRouter.get("/", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const status   = (req.query["status"] as string) ?? "active"

    const pools = await prisma.pool.findMany({
      where:   { tenant_id: tenantId, status: status as never },
      orderBy: { created_at: "asc" },
    })

    // Attach the live deploy slot (PoolSkillSlot.current) so consumers like the
    // bootstrap can provision instances from the deploy — skill_id + concurrent
    // sessions — instead of from legacy agent_types. Fase 3b.
    const currentSlots = await (prisma as any).poolSkillSlot.findMany({
      where: { tenant_id: tenantId, slot: "current" },
    }) as Array<{ pool_id: string; skill_id: string | null; config_json: unknown }>
    const slotByPool = new Map(
      currentSlots
        .filter((s) => !!s.skill_id)
        .map((s) => [s.pool_id, s] as const),
    )

    const formatted = pools.map((p) => {
      const base = _formatPool(p as unknown as Record<string, unknown>)
      const slot = slotByPool.get((p as { pool_id: string }).pool_id)
      if (slot && slot.skill_id) {
        const cfg = (slot.config_json ?? {}) as Record<string, unknown>
        const mcs = cfg["max_concurrent_sessions"]
        base["deployed_skill_id"] = slot.skill_id
        base["deployed_max_concurrent_sessions"] =
          typeof mcs === "number" && mcs >= 1 ? Math.floor(mcs) : 1
      }
      return base
    })

    return res.json({ pools: formatted, total: formatted.length })
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// GET /v1/pools/capacity/conformance
// Conformidade derivada (não persistida) — capacity-governance item 3.
// Reflete mudanças de contrato na hora (revalidação implícita: C é lido do
// Redis a cada chamada). conform=false → alerta na UI (item 4 do arco).
// ─────────────────────────────────────────────
poolsRouter.get("/capacity/conformance", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId   = _getTenantId(req)
    const contracted = await contractedCapacity(tenantId)
    const pools = await prisma.pool.findMany({
      where:  { tenant_id: tenantId, status: "active" as never, session_reservation: { gt: 0 } },
      select: { pool_id: true, session_reservation: true },
      orderBy: { session_reservation: "desc" },
    })
    const reservedTotal = pools.reduce((s, p) => s + (p.session_reservation ?? 0), 0)
    return res.json({
      contracted,                                            // null = sem pricing configurado
      reserved_total: reservedTotal,
      shared:         contracted === null ? null : contracted - reservedTotal,
      conform:        contracted === null ? true : reservedTotal <= contracted,
      pools,
    })
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// GET /v1/pools/:pool_id
// ─────────────────────────────────────────────
poolsRouter.get("/:pool_id", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const pool     = await prisma.pool.findUnique({
      where:   { pool_id_tenant_id: { pool_id: req.params["pool_id"]!, tenant_id: tenantId } },
    })

    if (!pool) return res.status(404).json({ error: "Pool não encontrado" })
    return res.json(_formatPool(pool))
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// PUT /v1/pools/:pool_id
// ─────────────────────────────────────────────
poolsRouter.put("/:pool_id", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const body     = UpdatePoolSchema.parse(req.body)

    const existing = await prisma.pool.findUnique({
      where: { pool_id_tenant_id: { pool_id: req.params["pool_id"]!, tenant_id: tenantId } },
    })
    if (!existing) return res.status(404).json({ error: "Pool não encontrado" })

    // Capacity-governance item 3: Σ reservas ≤ C (só aumentos são bloqueados)
    const violation = await _reservationViolation(
      tenantId,
      req.params["pool_id"]!,
      body.session_reservation,
      (existing as { session_reservation: number | null }).session_reservation,
    )
    if (violation) return res.status(422).json(violation)

    // Capacity-governance item 2: queue_config ⇒ agent_kind 'human' — valida o
    // ESTADO RESULTANTE (campo novo ou existente, fila nova ou existente).
    const ex = existing as { agent_kind: string | null; queue_config: unknown }
    const resultingKind  = body.agent_kind !== undefined ? body.agent_kind : ex.agent_kind
    const resultingQueue = body.queue_config !== undefined ? body.queue_config : ex.queue_config
    if (resultingQueue != null && resultingKind === "ai") {
      return res.status(422).json({
        error: "queue_config exige agent_kind 'human' — pool IA não tem fila atendida",
      })
    }

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const updated = await prisma.pool.update({
      where: { id: existing.id },
      data: {
        ...(body.agent_kind            !== undefined && { agent_kind:            body.agent_kind }),
        ...(body.description           !== undefined && { description:           body.description }),
        ...(body.channel_types         !== undefined && { channel_types:         body.channel_types }),
        ...(body.sla_target_ms           !== undefined && { sla_target_ms:           body.sla_target_ms }),
        ...(body.webhook_skill_id        !== undefined && { webhook_skill_id:        body.webhook_skill_id }),
        ...(body.dispatch_mode           !== undefined && { dispatch_mode:           body.dispatch_mode }),
        ...(body.purpose                 !== undefined && { purpose:                 body.purpose }),
        // Campos limpáveis via PUT null (schema .nullable()): escalares aceitam
        // null direto no Prisma; JSONB exige Prisma.DbNull.
        ...(body.max_concurrent_sessions !== undefined && { max_concurrent_sessions: body.max_concurrent_sessions }),
        ...(body.session_reservation     !== undefined && { session_reservation:     body.session_reservation }),
        ...(body.max_reply_time_ms       !== undefined && { max_reply_time_ms:       body.max_reply_time_ms }),
        ...(body.routing_expression      !== undefined && { routing_expression:      body.routing_expression }),
        ...(body.evaluation_template_id !== undefined && { evaluation_template_id: body.evaluation_template_id }),
        ...(body.supervisor_config     !== undefined && { supervisor_config:     body.supervisor_config }),
        ...(body.queue_config          !== undefined && { queue_config:          body.queue_config ?? Prisma.DbNull }),
        ...(body.mentionable_pools     !== undefined && { mentionable_pools:     body.mentionable_pools }),
        ...(body.agent_groups          !== undefined && { agent_groups:          body.agent_groups }),
        ...(body.llm_account_ids       !== undefined && { llm_account_ids:       body.llm_account_ids }),
        ...(body.hooks                 !== undefined && { hooks:                 body.hooks }),
        ...(body.calendar_id              !== undefined && { calendar_id:              body.calendar_id }),
        ...(body.context_visibility       !== undefined && { context_visibility:       body.context_visibility }),
      } as any,
    })

    const formatted = _formatPool(updated)

    // Publica evento de atualização para o Routing Engine invalidar/atualizar cache
    await publishRegistryEvent({
      event:     "pool.updated",
      tenant_id: tenantId,
      pool:      formatted,
    })
    // Notifica o orchestrator-bridge para reconciliar imediatamente — senão o
    // heartbeat dele reescreve o pool_config a partir do cache em memória velho
    // (ex.: agent_kind ai→human ou dispatch_mode push↔pull não propagavam).
    await publishRegistryChanged(tenantId, "pool", req.params["pool_id"]!, "updated")

    return res.json(formatted)
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// GET /v1/pools/:pool_id/mentionable-agents
// Returns AI agent types available for @mention / Delegar in this pool,
// derived from pool.mentionable_pools: Record<alias, pool_id>.
// Response: { agents: { agent_type_id, pool_id, description? }[] }
// ─────────────────────────────────────────────
poolsRouter.get("/:pool_id/mentionable-agents", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const poolId   = req.params["pool_id"]!

    const pool = await prisma.pool.findUnique({
      where: { pool_id_tenant_id: { pool_id: poolId, tenant_id: tenantId } },
    })
    if (!pool) return res.status(404).json({ error: "Pool não encontrado" })

    // mentionable_pools: Record<alias, pool_id>  e.g. { copilot: "copilot_sac", auth: "auth_ia" }
    const mentionablePools = pool.mentionable_pools as Record<string, string> | null
    if (!mentionablePools || Object.keys(mentionablePools).length === 0) {
      return res.json({ agents: [] })
    }

    const targetPoolIds = Object.values(mentionablePools)

    // AgentType retired: source each specialist from its target pool's current
    // deploy slot (PoolSkillSlot.current → skill_id). The synthesized agent_type_id
    // equals the skill_id (deploy-driven), so it still matches the live participant
    // and formats to a readable name in the UI.
    const currentSlots = await (prisma as any).poolSkillSlot.findMany({
      where: { tenant_id: tenantId, slot: "current", pool_id: { in: targetPoolIds } },
    }) as Array<{ pool_id: string; skill_id: string | null }>
    const skillByPool = new Map<string, string>(
      currentSlots.filter(s => !!s.skill_id).map(s => [s.pool_id, s.skill_id as string]),
    )

    // Human pools have NO deploy skill (humans don't deploy a skill_id). Resolve the
    // target pools' agent_kind so a HUMAN pool can also be invited as a specialist into
    // the conference (Arc 11 — AI and humans are symmetric co-participants). The @mention
    // dispatch (mcp-server) is already agent-kind-agnostic: it routes to the target pool
    // with conference_id, so the Routing Engine allocates a human there just like an AI.
    const targetPools = await (prisma as any).pool.findMany({
      where: { tenant_id: tenantId, pool_id: { in: targetPoolIds } },
      select: { pool_id: true, agent_kind: true },
    }) as Array<{ pool_id: string; agent_kind: string | null }>
    const kindByPool = new Map<string, string | null>(
      targetPools.map(p => [p.pool_id, p.agent_kind]),
    )

    // Build alias-indexed response: one entry per alias (key in mentionable_pools)
    // so the UI can construct @alias mentions (not @agent_type_id which is not a valid alias)
    const agents: Array<{
      alias:         string;
      agent_type_id: string;
      pool_id:       string;
    }> = []

    for (const [alias, targetPoolId] of Object.entries(mentionablePools)) {
      const skillId = skillByPool.get(targetPoolId)
      if (skillId) {
        // AI specialist — deploy-driven agent_type_id == skill_id (formats to a name in UI).
        agents.push({ alias, agent_type_id: skillId, pool_id: targetPoolId })
      } else if (kindByPool.get(targetPoolId) === "human") {
        // Human pool (no deploy skill) — invite a HUMAN specialist into the conference.
        // Placeholder agent_type_id so the UI can render it.
        agents.push({ alias, agent_type_id: "human_agent", pool_id: targetPoolId })
      }
      // else: undeployed AI pool (no current skill, not human) → still skipped.
    }

    return res.json({ agents })
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// GET /v1/pools/:pool_id/deployments
// Timeline de deploys que atingiram este pool (Arc 6 Fase 2 — lente `deploy`
// ancorada no pool). Um deploy (SkillDeployment) lista vários pools em pool_ids;
// aqui filtramos os que incluem este pool. Newest-first. Cada entrada carrega
// skill_id + version → a lente desenha o marcador de versão na curva do pool.
// ─────────────────────────────────────────────
poolsRouter.get("/:pool_id/deployments", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const poolId   = req.params["pool_id"]!
    const limit    = Math.min(parseInt((req.query["limit"] as string) ?? "200", 10), 500)

    const pool = await prisma.pool.findUnique({
      where: { pool_id_tenant_id: { pool_id: poolId, tenant_id: tenantId } },
    })
    if (!pool) return res.status(404).json({ error: "Pool não encontrado" })

    const deployments = await (prisma as any).skillDeployment.findMany({
      where:   { tenant_id: tenantId, pool_ids: { has: poolId } },
      orderBy: { deployed_at: "desc" },
      take:    limit,
    }) as Array<Record<string, unknown>>

    return res.json({
      deployments: deployments.map(d => ({
        id:          d["id"],
        skill_id:    d["skill_id"],
        version:     d["version"],
        deployed_at: d["deployed_at"],
        deployed_by: d["deployed_by"],
        pool_ids:    d["pool_ids"],
        notes:       d["notes"],
      })),
      total: deployments.length,
    })
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────
function _getTenantId(req: Request): string {
  // Em produção: extraído do JWT via middleware de autenticação
  return (req.headers["x-tenant-id"] as string) ?? "tenant_default"
}

function _getUserId(req: Request): string {
  return (req.headers["x-user-id"] as string) ?? "system"
}

function _formatPool(pool: Record<string, unknown>): Record<string, unknown> {
  const { id: _id, ...rest } = pool
  return rest
}
