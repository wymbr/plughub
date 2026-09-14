/**
 * lib/capacity.ts
 * Capacity-governance — helpers compartilhados de validação contra o contratado.
 *
 * C (capacidade contratada) = {t}:quota:max_concurrent_sessions, gravada pelo
 * quota sync do pricing-api (item 1). Sem C / Redis fora → fail-open (sem pricing
 * configurado não há o que validar; o runtime segue protegido pela admissão).
 *
 * Consumidor: `routes/pool-slots.ts` (item 3b — Σ declarada nos deploys ≤ C).
 *
 * O item 3a (`routes/pools.ts`: Σ `session_reservation` ≤ C) saiu na fatia 3
 * (2026-08-02) junto com os baldes reservados. Sobrou o gate de PROVISIONAMENTO, que
 * responde outra pergunta — "cabe no contrato o que está deployado?" — e continua sendo
 * imposto com 422 no PUT de slot.
 *
 * Nota herdada, e que a fatia 3 torna mais aguda: `C` aqui é
 * `max_concurrent_sessions`, que soma licença humana com licença de IA. Como teto de
 * ADMISSÃO isso foi removido; como teto de PROVISIONAMENTO ele sobrevive e mistura as
 * moedas do mesmo jeito. É o defeito C do arco de capacidade, ainda aberto.
 */

import { prisma } from "../db"
import { getRedis } from "../infra/redis"

export async function contractedCapacity(tenantId: string): Promise<number | null> {
  try {
    const raw = await getRedis().get(`${tenantId}:quota:max_concurrent_sessions`)
    if (!raw) return null
    const n = parseInt(raw, 10)
    return Number.isFinite(n) && n > 0 ? n : null
  } catch {
    return null   // Redis fora → degrada para sem validação
  }
}

/** Concorrência declarada num slot de deploy (config_json.max_concurrent_sessions; default 1). */
export function slotDeclared(configJson: unknown): number {
  const cfg = (configJson ?? {}) as Record<string, unknown>
  const v = cfg["max_concurrent_sessions"]
  return typeof v === "number" && v >= 1 ? Math.floor(v) : 1
}

/** Declarada atual do próprio pool (slot `current`; 0 se não há deploy). */
export async function currentDeclared(tenantId: string, poolId: string): Promise<number> {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const slot = await (prisma as any).poolSkillSlot.findUnique({
    where: { pool_id_tenant_id_slot: { pool_id: poolId, tenant_id: tenantId, slot: "current" } },
  }) as { skill_id: string | null; config_json: unknown } | null
  return slot && slot.skill_id ? slotDeclared(slot.config_json) : 0
}

/**
 * Item 3b — valida a declaração de deploy contra C.
 * Mesmas regras do 3a: sem C → fail-open; REDUÇÕES/iguais sempre passam (heal de
 * legado não-conforme; re-sync idempotente do RegistrySyncer não quebra); só
 * AUMENTOS que façam Σ declarada > C retornam payload de erro (chamador → 422).
 * Retorna null quando permitido.
 */
export async function deployViolation(
  tenantId: string,
  poolId:   string,
  newDeclared: number,
): Promise<Record<string, unknown> | null> {
  return deployViolationBatch(tenantId, [{ poolId, declared: newDeclared }])
}

/**
 * PID-16 — a mesma regra para N pools de UMA vez, e é a única implementação dela.
 *
 * Julgar o lote pool a pool com `deployViolation` deixaria passar o que a regra existe
 * para recusar: cada pool lê os DEMAIS do banco, onde o vizinho do lote ainda tem a
 * declaração antiga — dois aumentos que cabem sozinhos e estouram juntos passariam os
 * dois. Aqui os "demais" são os pools FORA do lote, e o lote entra com os valores novos.
 *
 * Mesmas regras: sem C → fail-open; lote em que NENHUM pool aumenta sempre passa
 * (redução/igual, re-sync idempotente); com um aumento, vale a soma.
 */
export async function deployViolationBatch(
  tenantId: string,
  entries:  Array<{ poolId: string; declared: number }>,
): Promise<Record<string, unknown> | null> {
  const vivos = entries.filter(e => e.declared > 0)
  if (vivos.length === 0) return null
  const atuais = await Promise.all(vivos.map(e => currentDeclared(tenantId, e.poolId)))
  if (vivos.every((e, i) => e.declared <= atuais[i]!)) return null   // redução/igual sempre passa
  const contracted = await contractedCapacity(tenantId)
  if (contracted === null) return null
  const noLote = new Set(vivos.map(e => e.poolId))
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const slots = await (prisma as any).poolSkillSlot.findMany({
    where: { tenant_id: tenantId, slot: "current" },
  }) as Array<{ pool_id: string; skill_id: string | null; config_json: unknown }>
  const others = slots
    .filter(s => !!s.skill_id && !noLote.has(s.pool_id))
    .reduce((sum, s) => sum + slotDeclared(s.config_json), 0)
  const requested = vivos.reduce((sum, e) => sum + e.declared, 0)
  const total     = others + requested
  if (total <= contracted) return null
  return {
    error: "deploy declara concorrência acima da capacidade contratada",
    details: {
      contracted,
      declared_others:  others,
      requested,
      declared_total:   total,
      balance_would_be: contracted - total,
      ...(vivos.length > 1 ? { pools: vivos.map(e => ({ pool_id: e.poolId, requested: e.declared })) } : {}),
    },
  }
}
