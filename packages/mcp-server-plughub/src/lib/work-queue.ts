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
  // Bug B fix: the delegate step enqueues the approval work-item WITH a
  // conference_id (the conference the caller opened on the session). The claim
  // MUST carry it back so work_task_claim attaches the human as the conference
  // participant (not a bare primary) — otherwise the occupant becomes
  // "{session}::" (empty conf), the routed event omits the conference, and the
  // Console cannot (re-)attach the package. Empty string = non-conference contact.
  conference_id: string | null
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
      const queuedAtMs = Number(contact?.["queued_at_ms"]) || 0
      out.push({
        session_id,
        pool_id,
        state:        "claimable",
        channel:      (contact?.["channel"] as string) ?? null,
        summary:      (contact?.["summary"] as string) ?? (contact?.["title"] as string) ?? null,
        queued_at_ms: queuedAtMs || null,
        age_ms:       queuedAtMs ? Math.max(nowMs - queuedAtMs, 0) : null,
        conference_id: (contact?.["conference_id"] as string) ?? null,
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
}

export function claimTask(
  routingUrl: string, adminToken: string | undefined, a: ClaimArgs,
): Promise<unknown> {
  return callRouting(routingUrl, adminToken, "/v1/work_queue/claim", {
    tenant_id:     a.tenant_id,
    pool_id:       a.pool_id,
    session_id:    a.session_id,
    instance_id:   a.instance_id,
    conference_id: a.conference_id ?? "",
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
