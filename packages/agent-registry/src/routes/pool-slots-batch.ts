/**
 * routes/pool-slots-batch.ts — PID-16 (2026-09-14)
 *
 * POST /v1/pool-slots/promote-batch — o MESMO snapshot de um skill promovido em N pools
 * nomeados, tudo ou nada.
 *
 * POR QUE EXISTE. Um skill de plataforma roda em várias portas (`skill_intake_runner_v1`
 * em `limite_ia` e `portabilidade_ia`: medido, UM snapshot e DUAS configs). O release era
 * um `set-next` + `promote` por pool, feito à mão, e isso tinha dois defeitos que o lote
 * fecha por construção:
 *   · cada `set-next` congela o `skill.flow` do SEU instante — uma edição entre dois deles
 *     deixa as portas rodando snapshots diferentes sem ninguém ter pedido;
 *   · uma recusa no meio (portão do pool 2) deixa o pool 1 já promovido: release pela metade.
 *
 * O CONTRATO (decisões do dono, 2026-09-14)
 *   · Endereço: lista EXPLÍCITA de pools. O servidor não deduz "todos que rodam o skill" —
 *     o pool é a unidade endereçável, e um pool novo não entra num lote sem ser nomeado.
 *   · Tudo ou nada: todos os portões são julgados em todos os pools ANTES de mexer em
 *     qualquer slot; um bloqueio devolve 422 nomeando CADA pool e motivo, e nada muda. As
 *     trocas de slot são UMA transação.
 *   · Rollback continua por pool (`POST /v1/pools/:id/rollback`), isento de portões. A
 *     resposta e o `SkillDeployment.notes` levam o `batch_id` e os pools, para quem precisar
 *     reverter saber onde.
 *
 * A CONFIG É DE CADA POOL. Sem `configs[pool]`, vale o `config_json` do `current` — mas só
 * se o `current` já roda ESTE skill; config de outro skill não serve, e adivinhar seria
 * promover um deploy que ninguém declarou (mesma recusa do `deploy_skill_to_slot.sh`).
 *
 * DOIS CASOS QUE NÃO SÃO SILÊNCIO
 *   · `next` pendente no pool: o lote NÃO o atropela. Recusa nomeando o que estava lá, a
 *     não ser que `replace_pending_next: true` diga que é de propósito.
 *   · `current` idêntico (mesmo snapshot E mesma config): o pool é `unchanged` e não é
 *     tocado. Promovê-lo empurraria o `current` para `previous` e APAGARIA o alvo de
 *     rollback com uma cópia de si mesmo — o dano que o `seed_deploy_lens_demo.sh` já teve
 *     de guardar à mão.
 */
import { Router, Request, Response, NextFunction } from "express"
import crypto from "node:crypto"
import { authorOf } from "../middleware/require-resource-write"
import { prisma } from "../db"
import { publishRegistryChanged } from "../infra/kafka"
import { deployViolationBatch, slotDeclared } from "../lib/capacity"
import { judgeSlotCandidate } from "../lib/slot-candidate"
import { promoteSlotsInTx, recordSkillDeployment, type SlotContent } from "../lib/slot-promotion"
import { requireDeployWrite, _getTenantId } from "./pool-slots"

export const poolSlotsBatchRouter = Router()

/** Teto do lote: além disto o release é outra operação (e a resposta, ilegível). */
export const BATCH_MAX_POOLS = 50

/** JSON canônico (chaves ordenadas) — `jsonb` e objetos JS não garantem a mesma ordem. */
export function canonicalJson(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(canonicalJson).join(",")}]`
  if (v !== null && typeof v === "object") {
    const o = v as Record<string, unknown>
    return `{${Object.keys(o).sort().map(k => `${JSON.stringify(k)}:${canonicalJson(o[k])}`).join(",")}}`
  }
  return JSON.stringify(v ?? null)
}

interface PoolProblem { pool_id: string; error: string; message?: string; details?: unknown }

poolSlotsBatchRouter.post("/promote-batch", requireDeployWrite, async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const userId   = authorOf(req)
    const body     = (req.body ?? {}) as {
      skill_id?:             unknown
      pools?:                unknown
      configs?:              unknown
      replace_pending_next?: unknown
    }

    // ── forma do pedido ───────────────────────────────────────────────────────
    const skillId = typeof body.skill_id === "string" ? body.skill_id : ""
    if (!skillId) return res.status(400).json({ error: "skill_id é obrigatório" })
    if (!Array.isArray(body.pools) || body.pools.length === 0
        || !body.pools.every(p => typeof p === "string" && p.length > 0)) {
      return res.status(400).json({ error: "pools deve ser uma lista não vazia de pool_id" })
    }
    const poolIds = body.pools as string[]
    const repetidos = poolIds.filter((p, i) => poolIds.indexOf(p) !== i)
    if (repetidos.length) {
      return res.status(400).json({ error: "pool repetido no lote", pools: [...new Set(repetidos)] })
    }
    if (poolIds.length > BATCH_MAX_POOLS) {
      return res.status(400).json({ error: `lote acima de ${BATCH_MAX_POOLS} pools` })
    }
    const configs = (body.configs ?? {}) as Record<string, unknown>
    if (typeof configs !== "object" || Array.isArray(configs)) {
      return res.status(400).json({ error: "configs deve ser um objeto {pool_id: config_json}" })
    }
    const foraDoLote = Object.keys(configs).filter(p => !poolIds.includes(p))
    if (foraDoLote.length) {
      return res.status(400).json({ error: "configs nomeia pool fora do lote", pools: foraDoLote })
    }
    const substituirNext = body.replace_pending_next === true

    // ── UM snapshot para o lote inteiro ───────────────────────────────────────
    const skill = await prisma.skill.findUnique({
      where: { skill_id_tenant_id: { skill_id: skillId, tenant_id: tenantId } },
    })
    if (!skill) return res.status(404).json({ error: `Skill '${skillId}' não encontrada` })
    const skillRec = skill as unknown as Record<string, unknown>
    const snapshot = (skillRec["flow"] ?? skillRec["flow_draft"]) ?? null

    const pools = await prisma.pool.findMany({
      where: { tenant_id: tenantId, pool_id: { in: poolIds } },
    }) as unknown as Array<Record<string, unknown>>
    const poolById = new Map(pools.map(p => [p["pool_id"] as string, p]))
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const slotRows = await (prisma as any).poolSkillSlot.findMany({
      where: { tenant_id: tenantId, pool_id: { in: poolIds } },
    }) as Array<Record<string, unknown>>
    const slotOf = (poolId: string, slot: string) =>
      slotRows.find(r => r["pool_id"] === poolId && r["slot"] === slot)

    // ── julgamento de TODOS os pools antes de tocar em qualquer um ────────────
    const problemas: PoolProblem[] = []
    const plano: Array<{ poolId: string; config: unknown; current?: Record<string, unknown> | undefined; unchanged: boolean; warnings: string[] }> = []

    for (const poolId of poolIds) {
      const pool = poolById.get(poolId)
      if (!pool) { problemas.push({ pool_id: poolId, error: "pool_nao_encontrado" }); continue }

      const current = slotOf(poolId, "current")
      const pendente = slotOf(poolId, "next")

      let config: unknown
      if (Object.prototype.hasOwnProperty.call(configs, poolId)) {
        config = configs[poolId]
        if (config === null || typeof config !== "object" || Array.isArray(config)) {
          problemas.push({ pool_id: poolId, error: "config_invalida", message: "configs[pool] deve ser um objeto" })
          continue
        }
      } else if (current && current["skill_id"] === skillId) {
        config = current["config_json"] ?? {}
      } else {
        problemas.push({
          pool_id: poolId,
          error:   "config_indefinida",
          message:
            `O pool '${poolId}' não roda '${skillId}' hoje (current: ${String(current?.["skill_id"] ?? "vazio")}), ` +
            `então não há config dele para herdar. Declare-a em configs['${poolId}'] — adivinhar seria promover ` +
            `um deploy que ninguém declarou.`,
        })
        continue
      }

      const unchanged = !!current && current["skill_id"] === skillId
        && canonicalJson(current["yaml_snapshot"]) === canonicalJson(snapshot)
        && canonicalJson(current["config_json"] ?? {}) === canonicalJson(config)

      if (pendente && !substituirNext && !unchanged) {
        problemas.push({
          pool_id: poolId,
          error:   "next_pendente",
          message:
            `O pool '${poolId}' tem um 'next' pendente (${String(pendente["skill_id"])}, ` +
            `${String(pendente["set_at"])}). O lote não o atropela: promova-o, ou reenvie com ` +
            `replace_pending_next: true se substituí-lo é de propósito.`,
        })
        continue
      }

      if (unchanged) { plano.push({ poolId, config, current, unchanged: true, warnings: [] }); continue }

      const veredito = judgeSlotCandidate({
        pool, skill: skillRec, snapshot, configJson: config, poolId, skillId, stage: "promote-batch",
      })
      if (veredito.kind === "block") {
        problemas.push({ pool_id: poolId, error: veredito.error, ...(veredito.message ? { message: veredito.message } : {}) })
        continue
      }
      plano.push({ poolId, config, current, unchanged: false, warnings: veredito.warnings })
    }

    const mudam = plano.filter(p => !p.unchanged)
    if (problemas.length === 0 && mudam.length > 0) {
      const violation = await deployViolationBatch(
        tenantId, mudam.map(p => ({ poolId: p.poolId, declared: slotDeclared(p.config) })),
      )
      if (violation) {
        for (const p of mudam) problemas.push({ pool_id: p.poolId, error: "capacidade", details: violation })
      }
    }

    if (problemas.length > 0) {
      return res.status(422).json({
        error:   "lote_recusado",
        message: `Nenhum pool foi alterado: ${problemas.length} de ${poolIds.length} recusado(s).`,
        skill_id: skillId,
        pools:    problemas,
      })
    }

    // ── efetivação: UMA transação para todas as trocas de slot ────────────────
    const batchId = crypto.randomUUID()
    const now     = new Date()
    const candidato: SlotContent = { skill_id: skillId, config_json: {}, yaml_snapshot: snapshot }

    if (mudam.length > 0) {
      await prisma.$transaction(async (tx: unknown) => {
        for (const p of mudam) {
          await promoteSlotsInTx(tx, {
            tenantId, poolId: p.poolId, userId, now,
            candidate: { ...candidato, config_json: p.config },
            current:   p.current as unknown as SlotContent | undefined,
          })
        }
      })
    }

    const notes = `promote-batch:${batchId} pools=${mudam.map(p => p.poolId).join(",")}`
    const resultado = []
    for (const p of plano) {
      if (p.unchanged) {
        resultado.push({ pool_id: p.poolId, action: "unchanged" })
        continue
      }
      const registrado = await recordSkillDeployment({
        tenantId, poolId: p.poolId, skillId, snapshot, userId, now, notes,
      })
      await publishRegistryChanged(tenantId, "pool", p.poolId, "updated")
      resultado.push({
        pool_id:             p.poolId,
        action:              "promoted",
        previous_skill_id:   (p.current?.["skill_id"] as string | undefined) ?? null,
        deployment_recorded: registrado,
        ...(p.warnings.length ? { warnings: p.warnings } : {}),
      })
    }

    return res.json({
      batch_id: batchId,
      skill_id: skillId,
      promoted: mudam.length,
      unchanged: plano.length - mudam.length,
      pools:    resultado,
      rollback: "por pool: POST /v1/pools/:pool_id/rollback",
    })
  } catch (err) {
    return next(err)
  }
})
