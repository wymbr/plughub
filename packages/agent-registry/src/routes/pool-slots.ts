/**
 * routes/pool-slots.ts
 * Pool-centric 3-slot deploy lifecycle.
 * Spec: Task #31 revised
 *
 * Rules:
 *   - Only "next" slot is writable — current/previous are immutable snapshots.
 *   - Promote: next→current, current→previous, next cleared.
 *   - Rollback: previous→current, previous cleared.
 *   - yaml_snapshot is automatically fetched from the skill when not provided.
 *
 * Endpoints (mounted at /v1/pools/:pool_id):
 *   GET  /slots          — return all 3 slots for the pool
 *   PUT  /slots/next     — developer sets the "next" slot (skill_id + config_json)
 *   POST /promote        — operator promotes: next→current, current→previous
 *   POST /rollback       — operator rolls back: previous→current
 */

import { Router, Request, Response, NextFunction } from "express"
import { authorOf, requireAbacWrite } from "../middleware/require-resource-write"
import { prisma, Prisma } from "../db"
import { publishRegistryChanged } from "../infra/kafka"
import { deployViolation, slotDeclared } from "../lib/capacity"
import { judgeMaskedDeploy } from "../lib/masked-deploy"
import { judgeProfileSteps } from "../lib/profile-steps"
import { judgeRequiredConfig } from "../lib/required-config"
import { judgeIdentityFloor } from "../lib/identity-floor"

export const poolSlotsRouter = Router({ mergeParams: true })

// PID-07 — o portão do DEPLOY é o campo da TELA de Deploy (`skill_flows.operacao`),
// declarado ROTA A ROTA. Até aqui estas rotas só tinham portão por ACIDENTE: o
// `app.use("/v1/pools", requireResourceWrite, …)` casa por prefixo e cobria
// `/v1/pools/:id/slots|promote|rollback` com `config.resources` (preset admin-only),
// enquanto o comentário do `app.ts` dizia "não gateado". Consequência medida: o
// devops via a tela de Deploy e tomava 403 em set-next e promote.
// Por rota, e não num `router.use`: este router é montado em `/v1/pools/:pool_id`, e
// um `use` aqui também pegaria o `PUT /v1/pools/:id` do pool — que é da tela Recursos.
const requireDeployWrite = requireAbacWrite("skill_flows", "operacao")

// ── Helpers ────────────────────────────────────────────────────────────────────

function _getTenantId(req: Request): string {
  return (req.headers["x-tenant-id"] as string) ?? "tenant_default"
}

function _formatSlot(row: Record<string, unknown> | null, slot: string) {
  if (!row) return { slot, set: false }
  const { id: _id, ...rest } = row
  return { ...rest, slot, set: true }
}

/** Fetch skill flow snapshot from the registry (used when developer omits yaml_snapshot).
 *
 *  UMA definição, sem rascunho (2026-07-13): o set-next congela a DEFINIÇÃO atual
 *  (`flow`) — a mesma que o editor grava e cujo `updated_at` a UI compara com o
 *  `set_at` do slot para mostrar "há alterações não implantadas".
 *
 *  O `flow_draft` foi eliminado (existia só para impedir vazamento à produção, o que
 *  hoje o snapshot de slot já garante — o bridge não roda mais definição viva). Fica
 *  o fallback de leitura para linhas antigas que ainda tenham um draft pendente. */
async function _fetchSkillSnapshot(skillId: string, tenantId: string): Promise<unknown | null> {
  try {
    const skill = await prisma.skill.findUnique({
      where: { skill_id_tenant_id: { skill_id: skillId, tenant_id: tenantId } },
    })
    const rec = skill as unknown as Record<string, unknown> | null
    return (rec?.["flow"] ?? rec?.["flow_draft"]) ?? null
  } catch {
    return null
  }
}

// ── GET /v1/pools/:pool_id/slots ───────────────────────────────────────────────

poolSlotsRouter.get("/slots", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const poolId   = req.params["pool_id"]!

    const pool = await prisma.pool.findUnique({
      where: { pool_id_tenant_id: { pool_id: poolId, tenant_id: tenantId } },
    })
    if (!pool) return res.status(404).json({ error: "Pool não encontrado" })

    const rows = await (prisma as any).poolSkillSlot.findMany({
      where: { pool_id: poolId, tenant_id: tenantId },
    }) as Record<string, unknown>[]

    const bySlot = Object.fromEntries(rows.map(r => [r["slot"], r]))

    return res.json({
      pool_id: poolId,
      slots: {
        previous: _formatSlot(bySlot["previous"] ?? null, "previous"),
        current:  _formatSlot(bySlot["current"]  ?? null, "current"),
        next:     _formatSlot(bySlot["next"]      ?? null, "next"),
      },
    })
  } catch (err) {
    return next(err)
  }
})

// ── PUT /v1/pools/:pool_id/slots/next ─────────────────────────────────────────
// Only "next" is writable. Returns 403 for previous/current.

poolSlotsRouter.put("/slots/:slot", requireDeployWrite, async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const userId   = authorOf(req)
    const poolId   = req.params["pool_id"]!
    const slot     = req.params["slot"]

    if (slot !== "next") {
      return res.status(403).json({
        error: `Slot '${slot}' é imutável. Apenas o slot 'next' pode ser editado.`,
      })
    }

    const pool = await prisma.pool.findUnique({
      where: { pool_id_tenant_id: { pool_id: poolId, tenant_id: tenantId } },
    })
    if (!pool) return res.status(404).json({ error: "Pool não encontrado" })

    const { skill_id, config_json, yaml_snapshot } = req.body as {
      skill_id?:     string
      config_json?:  Record<string, unknown>
      yaml_snapshot?: unknown
    }

    if (!skill_id) {
      return res.status(400).json({ error: "skill_id é obrigatório para configurar o slot next" })
    }

    // Verify skill exists
    const skill = await prisma.skill.findUnique({
      where: { skill_id_tenant_id: { skill_id: skill_id, tenant_id: tenantId } },
    })
    if (!skill) return res.status(404).json({ error: `Skill '${skill_id}' não encontrada` })

    // Capacity-governance item 2: deploy de skill só em pool 'ai' — pool humano
    // não pré-instancia agentes (a fila atendida vive no pool de fila, IA).
    if ((pool as { agent_kind?: string | null }).agent_kind === "human") {
      return res.status(422).json({
        error: "pool agent_kind 'human' não recebe deploy de skill — deploys são para pools IA",
      })
    }

    // Capacity-governance item 3b: Σ declarada nos deploys ≤ C.
    // Feedback cedo, na declaração (re-checada no promote — C pode mudar entre
    // os dois). Reduções/iguais sempre passam (re-sync idempotente do
    // RegistrySyncer não quebra); sem C → fail-open.
    const violation = await deployViolation(tenantId, poolId, slotDeclared(config_json))
    if (violation) return res.status(422).json(violation)

    // Auto-fetch yaml_snapshot if not provided
    const snapshot = yaml_snapshot != null
      ? yaml_snapshot
      : await _fetchSkillSnapshot(skill_id, tenantId)

    // Um slot SEM snapshot é inexecutável: o bridge roda exclusivamente o snapshot do
    // slot `current`, então promovê-lo produz um pool que parece deployado e não roda
    // nada — e o erro sai lá na ponta como "pool sem slot", que é o diagnóstico errado.
    // Falhar aqui, onde a causa está visível (o skill não tem definição).
    if (snapshot == null) {
      return res.status(422).json({
        error: "skill_sem_definicao",
        message:
          `O skill '${skill_id}' não tem definição (flow) gravada — não há o que congelar no slot. ` +
          `Salve o fluxo no editor (ou deixe o RegistrySyncer semeá-lo do YAML) antes de fazer o deploy.`,
      })
    }

    // NIV-03 (deploy) — skill que MASCARA em pool sem canal capaz. Mesma forma do
    // `deployViolation` acima: feedback cedo aqui, re-checado no promote, porque
    // `Pool.channel_types` pode mudar entre a declaração e a promoção.
    const vereditoNext = judgeMaskedDeploy(
      snapshot,
      (pool as { channel_types?: unknown }).channel_types,
      { poolId, skillId: skill_id },
    )
    if (vereditoNext.kind === "block") {
      return res.status(422).json({ error: vereditoNext.error, message: vereditoNext.message })
    }
    if (vereditoNext.kind === "warn") {
      console.warn(`[pool-slots:set-next] ${vereditoNext.warning}`)
    }

    // CTR-01 (G1) — step que o PERFIL do pool não admite. Mesma casa e mesma forma
    // que as duas checagens acima: o perfil é fato do POOL (Arc 19), então o
    // publish do skill não tem como saber. Re-checado no promote porque
    // `Pool.channel_types` pode mudar entre a declaração e a promoção — e é
    // justamente essa mudança que vira o perfil.
    const perfilNext = judgeProfileSteps(
      snapshot,
      (pool as { channel_types?: unknown }).channel_types,
      { poolId, skillId: skill_id },
    )
    if (perfilNext.kind === "block") {
      return res.status(422).json({ error: perfilNext.error, message: perfilNext.message })
    }

    // Parâmetro de deploy OBRIGATÓRIO que o slot não preencheu. Mesma casa e mesma
    // forma das três checagens acima, e re-checado no promote pelo mesmo motivo
    // delas: o `config_params` do skill pode GANHAR um obrigatório entre a
    // declaração e a promoção — e aí quem estava conforme deixa de estar sem que
    // ninguém toque no slot.
    const configNext = judgeRequiredConfig(
      (skill as unknown as Record<string, unknown>)["config_params"],
      config_json ?? {},
      { poolId, skillId: skill_id },
    )
    if (configNext.kind === "block") {
      return res.status(422).json({ error: configNext.error, message: configNext.message })
    }

    // PID-06 (D7) — a exigência de retomada que o slot declara contém o PISO do skill?
    // Mesma casa e mesmo motivo das anteriores; re-checado no promote porque o piso vem
    // do snapshot e a exigência da config, e os dois podem mudar entre os momentos.
    const pisoNext = judgeIdentityFloor(snapshot, config_json ?? {}, { poolId, skillId: skill_id })
    if (pisoNext.kind === "block") {
      return res.status(422).json({ error: pisoNext.error, message: pisoNext.message })
    }

    const row = await (prisma as any).poolSkillSlot.upsert({
      where:  { pool_id_tenant_id_slot: { pool_id: poolId, tenant_id: tenantId, slot: "next" } },
      update: {
        skill_id,
        config_json:   config_json ?? {},
        yaml_snapshot: snapshot != null ? (snapshot as Prisma.InputJsonValue) : Prisma.DbNull,
        set_at:        new Date(),
        set_by:        userId,
      },
      create: {
        pool_id:       poolId,
        tenant_id:     tenantId,
        slot:          "next",
        skill_id,
        config_json:   config_json ?? {},
        yaml_snapshot: snapshot != null ? (snapshot as Prisma.InputJsonValue) : Prisma.DbNull,
        set_by:        userId,
      },
    }) as Record<string, unknown>

    // O aviso viaja no CORPO e no log. Duas casas de propósito: a UI pode não
    // renderizar o corpo, e o log pode não ser lido — se depender de uma só, o
    // fato some, que é como a MSK-01 sobreviveu.
    return res.json({
      ..._formatSlot(row, "next"),
      ...(vereditoNext.kind === "warn" ? { warnings: [vereditoNext.warning] } : {}),
    })
  } catch (err) {
    return next(err)
  }
})

// ── POST /v1/pools/:pool_id/promote ───────────────────────────────────────────
// next → current, current → previous, next cleared.

poolSlotsRouter.post("/promote", requireDeployWrite, async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const userId   = authorOf(req)
    const poolId   = req.params["pool_id"]!

    const pool = await prisma.pool.findUnique({
      where: { pool_id_tenant_id: { pool_id: poolId, tenant_id: tenantId } },
    })
    if (!pool) return res.status(404).json({ error: "Pool não encontrado" })

    const rows = await (prisma as any).poolSkillSlot.findMany({
      where: { pool_id: poolId, tenant_id: tenantId },
    }) as Record<string, unknown>[]

    const bySlot      = Object.fromEntries(rows.map((r: Record<string, unknown>) => [r["slot"], r]))
    const nextSlot    = bySlot["next"]    as Record<string, unknown> | undefined
    const currentSlot = bySlot["current"] as Record<string, unknown> | undefined

    if (!nextSlot) {
      return res.status(409).json({ error: "Slot 'next' não está configurado — configure antes de promover" })
    }

    // Capacity-governance item 3b: o promote é o momento em que a declaração
    // vira efetiva (next → current) — revalida contra o C vigente.
    // Rollback fica ISENTO (operação de emergência nunca bloqueia).
    const violation = await deployViolation(
      tenantId, poolId, slotDeclared(nextSlot["config_json"]),
    )
    if (violation) return res.status(422).json(violation)

    // NIV-03 (deploy) — re-julga masked × canais. NÃO é redundante com o set-next:
    // `Pool.channel_types` pode ter perdido o canal capaz entre uma coisa e outra, e
    // o promote é o instante em que o snapshot passa a atender contato de verdade.
    // O ROLLBACK fica isento pelo mesmo motivo que o de capacidade: operação de
    // emergência nunca bloqueia.
    const vereditoProm = judgeMaskedDeploy(
      nextSlot["yaml_snapshot"],
      (pool as { channel_types?: unknown }).channel_types,
      { poolId, skillId: (nextSlot["skill_id"] as string) || "(sem skill)" },
    )
    if (vereditoProm.kind === "block") {
      return res.status(422).json({ error: vereditoProm.error, message: vereditoProm.message })
    }
    if (vereditoProm.kind === "warn") {
      console.warn(`[pool-slots:promote] ${vereditoProm.warning}`)
    }

    // CTR-01 (G1) — re-julga perfil × steps. O ROLLBACK fica isento, pelo mesmo
    // motivo das outras duas: operação de emergência nunca bloqueia.
    const perfilProm = judgeProfileSteps(
      nextSlot["yaml_snapshot"],
      (pool as { channel_types?: unknown }).channel_types,
      { poolId, skillId: (nextSlot["skill_id"] as string) || "(sem skill)" },
    )
    if (perfilProm.kind === "block") {
      return res.status(422).json({ error: perfilProm.error, message: perfilProm.message })
    }

    // PID-06 (D7) — piso de identidade × config do slot que está sendo PROMOVIDO.
    const pisoProm = judgeIdentityFloor(
      nextSlot["yaml_snapshot"],
      nextSlot["config_json"],
      { poolId, skillId: (nextSlot["skill_id"] as string) || "(sem skill)" },
    )
    if (pisoProm.kind === "block") {
      return res.status(422).json({ error: pisoProm.error, message: pisoProm.message })
    }

    // Parâmetro obrigatório × config_json do slot que está sendo PROMOVIDO — nunca
    // o `current`, que descreve o que já rodava. É aqui que a declaração vira
    // efetiva, então é aqui que a ausência tem de doer: no runtime ela vira um
    // pool saudável que escala todo contato, sem nada vermelho.
    const skillProm = (nextSlot["skill_id"] as string) || ""
    if (skillProm) {
      const skillRow = await prisma.skill.findUnique({
        where: { skill_id_tenant_id: { skill_id: skillProm, tenant_id: tenantId } },
      })
      const configProm = judgeRequiredConfig(
        (skillRow as unknown as Record<string, unknown> | null)?.["config_params"],
        nextSlot["config_json"],
        { poolId, skillId: skillProm },
      )
      if (configProm.kind === "block") {
        return res.status(422).json({ error: configProm.error, message: configProm.message })
      }
    }

    const now = new Date()

    await prisma.$transaction(async (tx: any) => {
      // current → previous
      if (currentSlot) {
        await tx.poolSkillSlot.upsert({
          where:  { pool_id_tenant_id_slot: { pool_id: poolId, tenant_id: tenantId, slot: "previous" } },
          update: {
            skill_id:      currentSlot["skill_id"] ?? null,
            config_json:   currentSlot["config_json"] ?? {},
            yaml_snapshot: (currentSlot["yaml_snapshot"] ?? Prisma.DbNull) as Prisma.InputJsonValue,
            set_at:        now,
            set_by:        userId,
          },
          create: {
            pool_id:       poolId,
            tenant_id:     tenantId,
            slot:          "previous",
            skill_id:      currentSlot["skill_id"] as string ?? null,
            config_json:   currentSlot["config_json"] ?? {},
            yaml_snapshot: (currentSlot["yaml_snapshot"] ?? Prisma.DbNull) as Prisma.InputJsonValue,
            set_by:        userId,
          },
        })
      }

      // next → current
      await tx.poolSkillSlot.upsert({
        where:  { pool_id_tenant_id_slot: { pool_id: poolId, tenant_id: tenantId, slot: "current" } },
        update: {
          skill_id:      nextSlot["skill_id"] ?? null,
          config_json:   nextSlot["config_json"] ?? {},
          yaml_snapshot: (nextSlot["yaml_snapshot"] ?? Prisma.DbNull) as Prisma.InputJsonValue,
          set_at:        now,
          set_by:        userId,
        },
        create: {
          pool_id:       poolId,
          tenant_id:     tenantId,
          slot:          "current",
          skill_id:      nextSlot["skill_id"] as string ?? null,
          config_json:   nextSlot["config_json"] ?? {},
          yaml_snapshot: (nextSlot["yaml_snapshot"] ?? Prisma.DbNull) as Prisma.InputJsonValue,
          set_by:        userId,
        },
      })

      // clear next
      await tx.poolSkillSlot.deleteMany({
        where: { pool_id: poolId, tenant_id: tenantId, slot: "next" },
      })
    })

    // Skill Versioning Fase C: o promote É o deploy → registra um SkillDeployment
    // (append-log) com deployed_at = set_at (now). A identidade da versão é o `now`
    // (carimbado pelo bridge em segments.deploy_version via slot.set_at); `version`
    // guarda o RÓTULO (skill.version) para o display do epoch (rótulo + data).
    const promotedSkillId = (nextSlot["skill_id"] as string) || ""
    if (promotedSkillId) {
      let versionLabel = ""
      try {
        const sk = await prisma.skill.findUnique({
          where: { skill_id_tenant_id: { skill_id: promotedSkillId, tenant_id: tenantId } },
        })
        versionLabel = ((sk as unknown as Record<string, unknown>)?.["version"] as string) || ""
      } catch { /* sem rótulo → epoch cai para a data do deploy */ }
      try {
        await (prisma as any).skillDeployment.create({
          data: {
            skill_id:      promotedSkillId,
            tenant_id:     tenantId,
            version:       versionLabel,
            pool_ids:      [poolId],
            yaml_snapshot: (nextSlot["yaml_snapshot"] ?? null) as Prisma.InputJsonValue,
            deployed_by:   userId,
            deployed_at:   now,
            notes:         "promote",
          },
        })
      } catch (err) {
        // Não-fatal (o deploy já foi efetivado pelos slots), mas NUNCA mudo: desde a
        // PID-08 este é o ÚNICO escritor de SkillDeployment, então engolir a falha
        // apagaria o marker da lente e o rótulo do epoch sem rastro nenhum.
        console.error(
          `[pool-slots:promote] SkillDeployment NÃO registrado — pool=${poolId} skill=${promotedSkillId}: ` +
          `${err instanceof Error ? err.message : String(err)}. O slot foi promovido; a lente de deploy fica sem este marker.`,
        )
      }
    }

    await publishRegistryChanged(tenantId, "pool", poolId, "updated")

    const updated = await (prisma as any).poolSkillSlot.findMany({
      where: { pool_id: poolId, tenant_id: tenantId },
    }) as Record<string, unknown>[]
    const updatedBySlot = Object.fromEntries(updated.map((r: Record<string, unknown>) => [r["slot"], r]))

    return res.json({
      pool_id: poolId,
      action:  "promoted",
      slots: {
        previous: _formatSlot(updatedBySlot["previous"] ?? null, "previous"),
        current:  _formatSlot(updatedBySlot["current"]  ?? null, "current"),
        next:     _formatSlot(updatedBySlot["next"]      ?? null, "next"),
      },
    })
  } catch (err) {
    return next(err)
  }
})

// ── POST /v1/pools/:pool_id/rollback ──────────────────────────────────────────
// previous → current, previous cleared. Does NOT touch "next".

poolSlotsRouter.post("/rollback", requireDeployWrite, async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const userId   = authorOf(req)
    const poolId   = req.params["pool_id"]!

    const pool = await prisma.pool.findUnique({
      where: { pool_id_tenant_id: { pool_id: poolId, tenant_id: tenantId } },
    })
    if (!pool) return res.status(404).json({ error: "Pool não encontrado" })

    const rows = await (prisma as any).poolSkillSlot.findMany({
      where: { pool_id: poolId, tenant_id: tenantId },
    }) as Record<string, unknown>[]

    const bySlot       = Object.fromEntries(rows.map((r: Record<string, unknown>) => [r["slot"], r]))
    const previousSlot = bySlot["previous"] as Record<string, unknown> | undefined

    if (!previousSlot) {
      return res.status(409).json({ error: "Slot 'previous' não está configurado — nada para fazer rollback" })
    }

    const now = new Date()

    await prisma.$transaction(async (tx: any) => {
      // previous → current
      await tx.poolSkillSlot.upsert({
        where:  { pool_id_tenant_id_slot: { pool_id: poolId, tenant_id: tenantId, slot: "current" } },
        update: {
          skill_id:      previousSlot["skill_id"] ?? null,
          config_json:   previousSlot["config_json"] ?? {},
          yaml_snapshot: (previousSlot["yaml_snapshot"] ?? Prisma.DbNull) as Prisma.InputJsonValue,
          set_at:        now,
          set_by:        userId,
        },
        create: {
          pool_id:       poolId,
          tenant_id:     tenantId,
          slot:          "current",
          skill_id:      previousSlot["skill_id"] as string ?? null,
          config_json:   previousSlot["config_json"] ?? {},
          yaml_snapshot: (previousSlot["yaml_snapshot"] ?? Prisma.DbNull) as Prisma.InputJsonValue,
          set_by:        userId,
        },
      })

      // clear previous
      await tx.poolSkillSlot.deleteMany({
        where: { pool_id: poolId, tenant_id: tenantId, slot: "previous" },
      })
    })

    await publishRegistryChanged(tenantId, "pool", poolId, "updated")

    const updated = await (prisma as any).poolSkillSlot.findMany({
      where: { pool_id: poolId, tenant_id: tenantId },
    }) as Record<string, unknown>[]
    const updatedBySlot = Object.fromEntries(updated.map((r: Record<string, unknown>) => [r["slot"], r]))

    return res.json({
      pool_id: poolId,
      action:  "rolled_back",
      slots: {
        previous: _formatSlot(updatedBySlot["previous"] ?? null, "previous"),
        current:  _formatSlot(updatedBySlot["current"]  ?? null, "current"),
        next:     _formatSlot(updatedBySlot["next"]      ?? null, "next"),
      },
    })
  } catch (err) {
    return next(err)
  }
})
