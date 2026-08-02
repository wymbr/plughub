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

/** Σ declarada nos slots `current` dos demais pools do tenant (pools com skill deployada). */
export async function declaredTotalOthers(tenantId: string, excludePoolId: string): Promise<number> {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const slots = await (prisma as any).poolSkillSlot.findMany({
    where: { tenant_id: tenantId, slot: "current", NOT: { pool_id: excludePoolId } },
  }) as Array<{ skill_id: string | null; config_json: unknown }>
  return slots
    .filter(s => !!s.skill_id)
    .reduce((sum, s) => sum + slotDeclared(s.config_json), 0)
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
  if (newDeclared <= 0) return null
  const current = await currentDeclared(tenantId, poolId)
  if (newDeclared <= current) return null            // redução/igual sempre passa
  const contracted = await contractedCapacity(tenantId)
  if (contracted === null) return null
  const others = await declaredTotalOthers(tenantId, poolId)
  const total  = others + newDeclared
  if (total <= contracted) return null
  return {
    error: "deploy declara concorrência acima da capacidade contratada",
    details: {
      contracted,
      declared_others:  others,
      requested:        newDeclared,
      declared_total:   total,
      balance_would_be: contracted - total,
    },
  }
}
