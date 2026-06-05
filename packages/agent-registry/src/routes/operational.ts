/**
 * routes/operational.ts
 * Real-time operational visibility — pool capacity and queue state.
 * Data sourced from Redis snapshots written by the Routing Engine.
 *
 * Endpoints (all require x-tenant-id header):
 *   GET /v1/operational/pools
 *     Returns all pools enriched with their live operational snapshot.
 *     Falls back gracefully when Redis has no snapshot yet.
 *
 *   GET /v1/operational/pools/:pool_id/queue
 *     Returns the list of sessions currently queued in a pool,
 *     with position, queued_at, and estimated_wait_ms.
 */

import { Router, Request, Response, NextFunction } from "express"
import { prisma }          from "../db"
import { getRedis, opKeys, getPoolSnapshot } from "../infra/redis"
import { config }          from "../config"

export const operationalRouter = Router()

function _getTenantId(req: Request): string {
  return (req.headers["x-tenant-id"] as string) ?? "tenant_default"
}

// ── Item 7a (capacity-governance) — agregador de admissão ─────────────────────
// Lê o estado dos buckets de admissão (Fase B + gates por tipo + fila de
// sistema) direto do Redis — read-only, sem tocar o hot path do routing.
// Per-pool: reserva usada / atribuição do shared (HASH shared_pools) /
// fila muda×atendida / admissível. Tenant: C, shared usado/limite, buffer.

async function _intOrNull(raw: string | null): Promise<number | null> {
  if (!raw) return null
  const n = parseInt(raw, 10)
  return Number.isFinite(n) && n > 0 ? n : null
}

/** Teto do buffer da fila gratuita (Config API routing.queue_max_total; cache 60s).
 *  Fix 2026-06-05: o GET /config/{ns} EXIGE ?tenant_id= (422 sem ele) — o fetch
 *  caía silenciosamente no default. Cache por tenant. Nota: list_namespace
 *  devolve entries {key: valor_resolvido} (flat), não envelopes {value}. */
const _maxQueueCache = new Map<string, { at: number; value: number }>()
async function _maxQueueTotal(tenantId: string): Promise<number> {
  const hit = _maxQueueCache.get(tenantId)
  if (hit && Date.now() - hit.at < 60_000) return hit.value
  let value = 100   // default hard-coded (espelha routing_config)
  try {
    const resp = await fetch(
      `${config.config_api_url}/config/routing?tenant_id=${encodeURIComponent(tenantId)}`
    )
    if (resp.ok) {
      const body = await resp.json() as { entries?: Record<string, unknown> }
      const raw  = body.entries?.["queue_max_total"]
      const v    = typeof raw === "number" ? raw
        : typeof (raw as { value?: unknown })?.value === "number"
          ? (raw as { value: number }).value
          : parseInt(String(raw ?? ""), 10)
      if (Number.isFinite(v) && v > 0) value = v
    }
  } catch { /* default */ }
  _maxQueueCache.set(tenantId, { at: Date.now(), value })
  return value
}

interface AdmissionState {
  contracted:    number | null            // C ({t}:quota:max_concurrent_sessions)
  aiCap:         number | null            // C_ai
  aiUsed:        number
  sharedUsed:    number                   // SCARD shared
  sharedLimit:   number | null            // C − Σ reservas
  sharedByPool:  Record<string, number>   // HASH shared_pools group-by (exato)
  reservedUsed:  Record<string, number>   // SCARD reserved:{pool}
  bufferUsed:    number                   // SCARD unadmitted
  bufferLimit:   number
  unadmitted:    Set<string>              // p/ split fila muda×atendida
}

async function _admissionState(
  tenantId: string,
  reservations: Record<string, number>,   // pool_id → session_reservation
): Promise<AdmissionState> {
  const redis = getRedis()
  const [cRaw, aiRaw, sharedUsed, aiUsed, bufferMembers, sharedHash] = await Promise.all([
    redis.get(`${tenantId}:quota:max_concurrent_sessions`),
    redis.get(`${tenantId}:quota:capacity:ai_agent`),
    redis.scard(`${tenantId}:admission:shared`),
    redis.scard(`${tenantId}:admission:kind:ai`),
    redis.smembers(`${tenantId}:queue:unadmitted`),
    redis.hgetall(`${tenantId}:admission:shared_pools`),
  ])
  const reservedUsed: Record<string, number> = {}
  await Promise.all(Object.keys(reservations).map(async (poolId) => {
    reservedUsed[poolId] = await redis.scard(`${tenantId}:admission:reserved:${poolId}`)
  }))
  const sharedByPool: Record<string, number> = {}
  for (const poolId of Object.values(sharedHash)) {
    sharedByPool[poolId] = (sharedByPool[poolId] ?? 0) + 1
  }
  const contracted = await _intOrNull(cRaw)
  const sumReservations = Object.values(reservations).reduce((s, v) => s + v, 0)
  return {
    contracted,
    aiCap:       await _intOrNull(aiRaw),
    aiUsed,
    sharedUsed,
    sharedLimit: contracted === null ? null : Math.max(0, contracted - sumReservations),
    sharedByPool,
    reservedUsed,
    bufferUsed:  bufferMembers.length,
    bufferLimit: await _maxQueueTotal(tenantId),
    unadmitted:  new Set(bufferMembers),
  }
}

// ── Live fallback helpers ──────────────────────────────────────────────────────
// When no Routing Engine snapshot exists (no active traffic yet), read directly
// from the orchestrator-bridge Redis keys.
// Key: {tenant}:pool:{pool_id}:instances  — SET of all instance_ids in the pool
// Key: {tenant}:pool:{pool_id}:queue      — ZSET of queued session_ids

async function _liveCount(tenantId: string, poolId: string): Promise<{ instances: number; queue: number }> {
  try {
    const redis = getRedis()
    const [instances, queue] = await Promise.all([
      redis.scard(opKeys.poolInstances(tenantId, poolId)),
      redis.zcard(opKeys.poolQueue(tenantId, poolId)),
    ])
    return { instances, queue }
  } catch {
    return { instances: 0, queue: 0 }
  }
}

// ── GET /v1/operational/pools ──────────────────────────────────────────────────
//
// Returns all pools for the tenant joined with their live Redis data.
// Priority: Routing Engine snapshot → live Redis fallback (instances SET + queue ZSET)
// Pool config (description, sla_target_ms, channel_types) comes from PostgreSQL.

operationalRouter.get("/pools", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)

    // 1. Load pool configurations from PostgreSQL
    const pools = await prisma.pool.findMany({
      where:   { tenant_id: tenantId, status: "active" },
      orderBy: { pool_id: "asc" },
    })

    // 2. Load snapshots + live counts in parallel
    const [snapshots, liveCounts] = await Promise.all([
      Promise.all(pools.map(p => getPoolSnapshot(tenantId, p.pool_id))),
      Promise.all(pools.map(p => _liveCount(tenantId, p.pool_id))),
    ])

    // 2b. Item 7a — estado da admissão (reservado×shared×fila gratuita) +
    //     ativos e fila muda por pool.
    const reservations: Record<string, number> = {}
    for (const p of pools) {
      const r = (p as unknown as { session_reservation: number | null }).session_reservation
      if (r && r > 0) reservations[p.pool_id] = r
    }
    const adm = await _admissionState(tenantId, reservations)
    const redis = getRedis()
    const activeCounts = await Promise.all(pools.map(async (p) => {
      const raw = await redis.get(`${tenantId}:pool:${p.pool_id}:active_count`)
      const n = raw ? parseInt(raw, 10) : 0
      return Number.isFinite(n) && n > 0 ? n : 0
    }))
    const muteCounts = await Promise.all(pools.map(async (p) => {
      try {
        const members = await redis.zrange(opKeys.poolQueue(tenantId, p.pool_id), 0, -1)
        return members.filter(sid => adm.unadmitted.has(sid)).length
      } catch { return 0 }
    }))

    // 3. Merge: snapshot takes priority; fall back to live counts
    const result = pools.map((p, i) => {
      const snap      = snapshots[i]
      const live      = liveCounts[i]!
      const hasSnap   = snap !== null

      const snapshotAgeMs = hasSnap ? Date.now() - Date.parse(snap!.updated_at) : null

      // Use snapshot values if available, else live counts
      const available   = hasSnap ? snap!.available    : live.instances
      const queueLength = hasSnap ? snap!.queue_length : live.queue

      const status =
        available > 0   ? "available"
        : queueLength > 0 ? "queued"
        : "empty"

      // Estimated wait: each position waits ~sla*0.7 (p70 handle time heuristic)
      const slaMs           = p.sla_target_ms
      const avgHandleMs     = slaMs * 0.7
      const estimatedWaitMs = queueLength > 0 ? Math.round(queueLength * avgHandleMs) : null

      // ── Item 7a — regime de admissão e disponibilidade admissível ──────────
      const px          = p as unknown as {
        session_reservation: number | null; agent_kind: string | null; queue_config: unknown
      }
      const reservation = px.session_reservation && px.session_reservation > 0
        ? px.session_reservation : null
      const scope       = reservation ? "reserved" : "shared"
      const admitted    = reservation
        ? (adm.reservedUsed[p.pool_id] ?? 0)
        : (adm.sharedByPool[p.pool_id] ?? 0)
      // Admissível: fatia própria (reservado) ou shared restante; pools IA
      // também limitados por C_ai restante. null = sem teto configurado.
      let admissible: number | null
      if (reservation) {
        admissible = Math.max(0, reservation - admitted)
      } else {
        admissible = adm.sharedLimit === null
          ? null : Math.max(0, adm.sharedLimit - adm.sharedUsed)
      }
      if (px.agent_kind === "ai" && adm.aiCap !== null) {
        const aiLeft = Math.max(0, adm.aiCap - adm.aiUsed)
        admissible = admissible === null ? aiLeft : Math.min(admissible, aiLeft)
      }
      const queueMute = muteCounts[i] ?? 0
      const queueTier = px.queue_config ? "attended"
        : px.agent_kind === "human" ? "system" : "none"

      return {
        // Pool config
        pool_id:          p.pool_id,
        description:      p.description ?? null,
        sla_target_ms:    slaMs,
        channel_types:    p.channel_types,
        pool_status:      p.status,
        agent_kind:       px.agent_kind ?? null,

        // Operational state
        op_status:        status,
        available:        available,
        queue_length:     queueLength,
        estimated_wait_ms: estimatedWaitMs,
        snapshot_age_ms:     snapshotAgeMs,
        snapshot_updated_at: hasSnap ? snap!.updated_at : null,

        // Item 7a — admissão (físico × admissível, regime, fila por tier)
        admission_scope:  scope,                            // "reserved" | "shared"
        reservation,                                        // fatia própria (ou null)
        admitted,                                           // sessões debitando C neste pool
        active_sessions:  activeCounts[i] ?? 0,             // em atendimento agora
        queue_mute:       queueMute,                        // espera gratuita (fora de C)
        queue_attended:   Math.max(0, queueLength - queueMute),
        queue_tier:       queueTier,                        // attended | system | none
        admissible,                                         // o que a admissão deixa entrar
        admissible_shared: !reservation,                    // ⊕ — número compartilhado

        // Source flags
        has_snapshot:  hasSnap,
        live_fallback: !hasSnap,  // true = data from :instances/:queue keys directly
      }
    })

    // Item 7a — agregados do tenant (tiles + donuts)
    const inServiceTotal = activeCounts.reduce((s, v) => s + v, 0)
    const queueTotal     = result.reduce((s, r) => s + r.queue_length, 0)
    const queueMuteTotal = result.reduce((s, r) => s + r.queue_mute, 0)
    const reservedList   = Object.entries(reservations).map(([poolId, r]) => ({
      pool_id: poolId, reservation: r, used: adm.reservedUsed[poolId] ?? 0,
    }))
    const admittedTotal  = adm.sharedUsed
      + reservedList.reduce((s, r) => s + r.used, 0)
    const summary = {
      contracted:      adm.contracted,                       // C (null = sem pricing)
      admitted_total:  admittedTotal,
      headroom:        adm.contracted === null ? null : Math.max(0, adm.contracted - admittedTotal),
      in_service:      inServiceTotal,
      queue_total:     queueTotal,
      queue_attended:  Math.max(0, queueTotal - queueMuteTotal),
      queue_mute:      queueMuteTotal,
      shared: {
        used:    adm.sharedUsed,
        limit:   adm.sharedLimit,
        by_pool: adm.sharedByPool,                           // fatias exatas (HASH)
      },
      reserved: reservedList,
      buffer:   { used: adm.bufferUsed, limit: adm.bufferLimit },
      ai:       { cap: adm.aiCap, used: adm.aiUsed },
    }

    return res.json({ items: result, total: result.length, summary })
  } catch (err) {
    return next(err)
  }
})

// ── GET /v1/operational/pools/:pool_id/queue ───────────────────────────────────
//
// Returns the ordered list of sessions currently waiting in the pool's queue.
// ZSET score = queued_at_ms (Unix timestamp in milliseconds).

operationalRouter.get("/pools/:pool_id/queue", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const poolId   = req.params["pool_id"]!

    // Verify pool exists
    const pool = await prisma.pool.findUnique({
      where: { pool_id_tenant_id: { pool_id: poolId, tenant_id: tenantId } },
    })
    if (!pool) return res.status(404).json({ error: "Pool não encontrado" })

    const redis  = getRedis()
    const qKey   = opKeys.poolQueue(tenantId, poolId)

    // ZRANGE with WITHSCORES — returns [member, score, member, score, ...]
    // Use ZRANGE key 0 -1 WITHSCORES (oldest first = lowest score first)
    let raw: string[] = []
    try {
      raw = await redis.zrange(qKey, 0, -1, "WITHSCORES") as string[]
    } catch {
      // Redis unreachable — return empty queue
      return res.json({ pool_id: poolId, queue: [], total: 0 })
    }

    // Parse pairs into queue entries
    const slaMs       = pool.sla_target_ms
    const avgHandleMs = slaMs * 0.7
    const now         = Date.now()

    const queue: {
      position:          number
      session_id:        string
      queued_at:         string
      wait_ms:           number
      estimated_wait_ms: number
    }[] = []

    for (let i = 0; i < raw.length; i += 2) {
      const sessionId  = raw[i]!
      const score      = parseFloat(raw[i + 1]!)
      const queuedAt   = new Date(score).toISOString()
      const waitMs     = now - score
      const position   = (i / 2) + 1

      // Estimated remaining wait: ahead agents * avg handle time
      const aheadCount = position - 1
      const estimatedWaitMs = Math.max(0, Math.round(aheadCount * avgHandleMs))

      queue.push({ position, session_id: sessionId, queued_at: queuedAt, wait_ms: Math.round(waitMs), estimated_wait_ms: estimatedWaitMs })
    }

    return res.json({ pool_id: poolId, queue, total: queue.length })
  } catch (err) {
    return next(err)
  }
})
