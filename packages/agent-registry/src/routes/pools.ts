/**
 * routes/pools.ts
 * CRUD de pools — spec 4.5
 */

import { Router, Request, Response, NextFunction } from "express"
import { prisma, Prisma }    from "../db"
import { CreatePoolSchema, UpdatePoolSchema } from "../validators/pool"
import { ZodError }          from "zod"
import { publishRegistryEvent, publishRegistryChanged } from "../infra/kafka"
// `contractedCapacity` saiu daqui na fatia 3 junto com o endpoint de conformidade de
// reservas. Ela continua viva em `lib/capacity.ts`, usada por `pool-slots.ts` para o
// gate de PROVISIONAMENTO (Σ declarada ≤ C) — que é uma pergunta diferente.
import {
  INTERNAL_QUEUE_SUFFIX,
  isMirrorPoolId,
  syncInternalQueueMirror,
  detachedHookViolation,
} from "../lib/internal-queue"

export const poolsRouter = Router()

// ─────────────────────────────────────────────
// `_reservedTotal` / `_reservationViolation` REMOVIDAS (fatia 3, 2026-08-02).
//
// Eram a validação "capacity-governance item 3": Σ `session_reservation` ≤ C, para que
// `shared = C − Σ reservas` não ficasse negativo. Os dois termos dessa conta morreram
// na fatia 3 — o balde `shared` não existe mais (era o pote misto `C_ai + C_human`, que
// recusava sessão humana contra uma soma de moedas não-fungíveis) e os baldes reservados
// saíram com ele. Validar uma soma que não limita nada, contra um `C` que soma moedas,
// é pior que não validar: era a ÚLTIMA coisa no sistema a afirmar que reservas de sessão
// existem, e quem lesse o código por ela reconstruiria o modelo removido.
//
// O que continua valendo é o gate de PROVISIONAMENTO (`lib/capacity.ts`,
// `deployViolation`: Σ declarada nos slots ≤ C), que responde outra pergunta — "cabe no
// contrato o que está deployado?" — e não passa por aqui.
//
// Endpoint `GET /v1/pools/capacity/conformance` derivava desta mesma conta; ver abaixo.
// ─────────────────────────────────────────────
// POST /v1/pools
// ─────────────────────────────────────────────
poolsRouter.post("/", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId  = _getTenantId(req)
    const createdBy = _getUserId(req)
    const body      = CreatePoolSchema.parse(req.body)

    // ADR internal-work-queue (D6): o sufixo `-int` só é garantia enquanto NINGUÉM
    // além do auto-provisionamento o usa. Se um pool manual pudesse tomá-lo, a
    // derivação de acesso (`p ∪ p+"-int"`) viraria adivinhação e o supervisor podia
    // acabar enxergando — ou deixando de enxergar — um pool que não é espelho de nada.
    if (isMirrorPoolId(body.pool_id)) {
      return res.status(422).json({
        error:
          `o sufixo "${INTERNAL_QUEUE_SUFFIX}" é reservado ao espelho de fila interna, ` +
          "criado automaticamente por `internal_queue_enabled` no pool de origem",
      })
    }

    // Verificar duplicata
    const existing = await prisma.pool.findUnique({
      where: { pool_id_tenant_id: { pool_id: body.pool_id, tenant_id: tenantId } },
    })
    if (existing) {
      return res.status(409).json({ error: "pool_id já registrado neste tenant" })
    }

    // Validar evaluation_template_id se fornecido
    // TODO: consultar tabela evaluation_templates

    // Capacity-governance item 2: queue_config ⇒ agent_kind 'human' (fila
    // atendida só para recurso escasso/lento; para IA o slot da fila
    // instanciaria o próprio agente solicitado).
    if (body.queue_config && body.agent_kind === "ai") {
      return res.status(422).json({
        error: "queue_config exige agent_kind 'human' — pool IA não tem fila atendida",
      })
    }

    // ADR internal-work-queue: hook detached agent-side exige a fila interna ligada.
    const hookViolation = detachedHookViolation(body.hooks, body.internal_queue_enabled === true)
    if (hookViolation) return res.status(422).json(hookViolation)

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
        internal_queue_enabled:  body.internal_queue_enabled ?? false,
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

    // Espelho de fila interna (ADR internal-work-queue, D1). Depois do create para
    // que o espelho nunca exista sem o pai.
    if (body.internal_queue_enabled === true) {
      await syncInternalQueueMirror(
        tenantId,
        { pool_id: body.pool_id, channel_types: body.channel_types, description: body.description ?? null },
        true,
        createdBy,
      )
    }

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
// `GET /v1/pools/capacity/conformance` REMOVIDO (fatia 3, 2026-08-02).
//
// Publicava `{contracted, reserved_total, shared, conform, pools}` — e três desses cinco
// campos deixaram de ter referente: `shared = C − Σ reservas` era o pote misto, `Σ
// reservas` os baldes por pool, e `conform` a comparação entre os dois. Manter o
// endpoint significaria a tela de Billing exibir "conforme/não-conforme" sobre uma regra
// que nenhum caminho de execução aplica — afirmação ao operador, plausível e falsa.
//
// A conformidade que SOBREVIVE é outra e já é imposta: Σ declarada nos slots de deploy
// ≤ C (`lib/capacity.ts` → `deployViolation`, 422 no PUT de slot). Na tela ela aparece
// como `contratado × alocado × saldo`, que continua de pé.
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

    // ── ADR internal-work-queue — estado RESULTANTE da flag e dos hooks ──────────
    const exIq = existing as { internal_queue_enabled?: boolean; hooks: unknown }
    const resultingIq = body.internal_queue_enabled !== undefined
      ? body.internal_queue_enabled
      : (exIq.internal_queue_enabled ?? false)
    const resultingHooks = body.hooks !== undefined ? body.hooks : exIq.hooks

    const hookViolation = detachedHookViolation(resultingHooks, resultingIq)
    if (hookViolation) return res.status(422).json(hookViolation)

    // D6 — desligar a flag NUNCA é silencioso. O registry não enxerga a fila (ela vive
    // no Redis do routing-engine, e o invariante proíbe acesso direto daqui), então a
    // impossibilidade de VERIFICAR pendência é tratada como pendência: recusa por
    // default, e o desligamento exige confirmação explícita.
    //
    // O que se evita: desativar o espelho tira o pool de `availablePools`, a inbox
    // deixa de listá-lo e os itens somem da vista do agente — falha por AUSÊNCIA, que
    // é a que ninguém percebe. Melhor recusar barulhento do que orfanar quieto.
    const disabling = (exIq.internal_queue_enabled ?? false) && !resultingIq
    const forceDisable = String(req.query["force_disable"] ?? "") === "true"
    if (disabling && !forceDisable) {
      return res.status(422).json({
        error:
          "desligar `internal_queue_enabled` desativa a fila interna e qualquer item " +
          "pendente nela deixa de ser reivindicável. O registry não consegue verificar " +
          "a fila (ela é do routing-engine). Drene a fila e repita com ?force_disable=true.",
        details: { mirror_pool_id: `${req.params["pool_id"]!}${INTERNAL_QUEUE_SUFFIX}` },
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
        ...(body.internal_queue_enabled  !== undefined && { internal_queue_enabled:  body.internal_queue_enabled }),
        // Campos limpáveis via PUT null (schema .nullable()): escalares aceitam
        // null direto no Prisma; JSONB exige Prisma.DbNull.
        ...(body.max_concurrent_sessions !== undefined && { max_concurrent_sessions: body.max_concurrent_sessions }),
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

    // Espelho de fila interna — reconcilia SEMPRE (idempotente): a flag pode ter mudado
    // e os campos derivados (canais) podem ter mudado com a flag já ligada. Espelho não
    // espelha espelho.
    const up = updated as unknown as { pool_id: string; channel_types: string[]; description: string | null }
    if (!isMirrorPoolId(up.pool_id)) {
      await syncInternalQueueMirror(
        tenantId,
        { pool_id: up.pool_id, channel_types: up.channel_types, description: up.description },
        resultingIq,
        _getUserId(req),
      )
    }

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
