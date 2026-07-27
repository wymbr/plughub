/**
 * lib/work-queue.ts — Frente 1 (dispatch pull): lógica COMPARTILHADA das operações
 * de fila pull, usada tanto pela tool MCP (tools/work_queue.ts — clientes MCP/IA)
 * quanto pelas rotas HTTP /api/work_queue/* (server.ts — consumidas pela inbox do
 * Console humano).
 *
 * LEITURA (listQueue) é Redis-direta. ESCRITA (claim/release) vai por HTTP ao
 * Routing Engine — o único árbitro (ZREM/claim_instance/mark_busy/lease/routed
 * acontecem DENTRO do engine).
 */

import type { RedisClient } from "../infra/redis"
import { keys }             from "../infra/redis"

export interface QueueContact {
  session_id:   string
  pool_id:      string
  state:        "claimable"
  channel:      string | null
  summary:      string | null
  queued_at_ms: number | null
  age_ms:       number | null
  // P3 — epoch ms do PRIMEIRO enqueue (preservado através de re-enfileiramentos).
  // A idade (age_ms) é derivada dele quando presente; senão cai em queued_at_ms.
  first_queued_ms: number | null
  // Bug B fix: the delegate step enqueues the approval work-item WITH a
  // conference_id (the conference the caller opened on the session). The claim
  // MUST carry it back so work_task_claim attaches the human as the conference
  // participant (not a bare primary) — otherwise the occupant becomes
  // "{session}::" (empty conf), the routed event omits the conference, and the
  // Console cannot (re-)attach the package. Empty string = non-conference contact.
  conference_id: string | null
  // Camada B (pull direcionado / "ramal"): reserva a um recurso preferido.
  //   assigned_to              — user_id preferido (null = fila compartilhada).
  //   fallback_to_pool_after_s — janela da reserva (s); após → claimable por todos
  //                              do pool. null = reserva permanente.
  //   assigned_at_ms           — âncora da janela (preservada no re-enqueue).
  // A elegibilidade DURA é aplicada pelo árbitro (routing-engine); estes campos
  // servem o filtro/rótulo do inbox. INVARIANTE: filtro de claim sobre trabalho
  // pooled, nunca alvo de roteamento que bypassa o pool.
  assigned_to:              string | null
  fallback_to_pool_after_s: number | null
  assigned_at_ms:           number | null
  // Wrap-up unificado (Camada E2) — quando true, o Console AUTO-REIVINDICA o item
  // (auto-atendimento, entrega inline) em vez de esperar o claim manual da inbox.
  auto_attend:              boolean
}

export async function listQueue(
  redis: RedisClient,
  tenantId: string,
  pools: string[],
  topN = 20,
): Promise<QueueContact[]> {
  const limit = Math.max(1, Math.min(topN, 100))
  const nowMs = Date.now()
  const out: QueueContact[] = []
  for (const pool_id of pools) {
    let sessions: string[] = []
    try {
      sessions = await redis.zrevrange(keys.poolQueue(tenantId, pool_id), 0, limit - 1)
    } catch { sessions = [] }
    for (const session_id of sessions) {
      let contact: Record<string, unknown> | null = null
      try {
        const raw = await redis.get(keys.queueContact(tenantId, session_id))
        if (raw) contact = JSON.parse(raw)
      } catch { /* ignore */ }
      // P3 — a idade real vem do primeiro enqueue (preservado no re-enfileiramento);
      // fallback para o score do sorted set (queued_at_ms, reordenado no re-enqueue).
      let firstQueuedMs = 0
      try {
        const fq = await redis.get(keys.firstQueued(tenantId, session_id))
        if (fq) firstQueuedMs = Number(fq) || 0
      } catch { /* ignore */ }
      const queuedAtMs = Number(contact?.["queued_at_ms"]) || 0
      const ageBaseMs  = firstQueuedMs || queuedAtMs
      out.push({
        session_id,
        pool_id,
        state:        "claimable",
        channel:      (contact?.["channel"] as string) ?? null,
        summary:      (contact?.["summary"] as string) ?? (contact?.["title"] as string) ?? null,
        queued_at_ms: queuedAtMs || null,
        age_ms:       ageBaseMs ? Math.max(nowMs - ageBaseMs, 0) : null,
        first_queued_ms: firstQueuedMs || null,
        conference_id: (contact?.["conference_id"] as string) ?? null,
        // Camada B — reserva/ramal (null quando não direcionado).
        assigned_to:              (contact?.["assigned_to"] as string) ?? null,
        fallback_to_pool_after_s: (contact?.["fallback_to_pool_after_s"] as number) ?? null,
        assigned_at_ms:           (contact?.["assigned_at_ms"] as number) ?? null,
        auto_attend:              contact?.["auto_attend"] === true,
      })
    }
  }
  return out
}

async function callRouting(
  routingUrl: string,
  adminToken: string | undefined,
  path: string,
  body: unknown,
): Promise<unknown> {
  const res = await fetch(`${routingUrl}${path}`, {
    method:  "POST",
    headers: {
      "content-type": "application/json",
      ...(adminToken ? { "X-Admin-Token": adminToken } : {}),
    },
    body: JSON.stringify(body),
  })
  return res.json()
}

export interface ClaimArgs {
  tenant_id:      string
  pool_id:        string
  session_id:     string
  instance_id:    string
  conference_id?: string
  // Camada B — identidade do claimant p/ casar com assigned_to (ramal). Ausente
  // → o engine deriva de instance_id (`human-{userId}`).
  claimant_user_id?: string
}

export function claimTask(
  routingUrl: string, adminToken: string | undefined, a: ClaimArgs,
): Promise<unknown> {
  return callRouting(routingUrl, adminToken, "/v1/work_queue/claim", {
    tenant_id:        a.tenant_id,
    pool_id:          a.pool_id,
    session_id:       a.session_id,
    instance_id:      a.instance_id,
    conference_id:    a.conference_id ?? "",
    claimant_user_id: a.claimant_user_id ?? "",
  })
}

export interface ReleaseArgs {
  tenant_id:   string
  pool_id:     string
  session_id:  string
  instance_id: string
}

export function releaseTask(
  routingUrl: string, adminToken: string | undefined, a: ReleaseArgs,
): Promise<unknown> {
  return callRouting(routingUrl, adminToken, "/v1/work_queue/release", {
    tenant_id:   a.tenant_id,
    pool_id:     a.pool_id,
    session_id:  a.session_id,
    instance_id: a.instance_id,
  })
}
