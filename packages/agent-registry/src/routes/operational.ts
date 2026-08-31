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
import { verifyHs256 }     from "../middleware/require-resource-write"

export const operationalRouter = Router()

function _getTenantId(req: Request): string {
  return (req.headers["x-tenant-id"] as string) ?? "tenant_default"
}

// ── Segurança Fase D — pool-scoping do snapshot operacional ───────────────────
// O Monitor (superfície operacional) deve respeitar o DOMÍNIO de pools do usuário
// (`accessible_pools`, Arc 7c), como os relatórios /reports. Lê o Bearer do auth-api
// (mesmo segredo, PLUGHUB_JWT_SECRET) e devolve a lista permitida.
//   null  → sem restrição (todos os pools): sem segredo configurado, sem token, token
//           inválido (degrada aberto, como o optional_pool_principal), ou accessible_pools
//           vazio ([] = admin/todos por convenção do auth-api).
//   [...] → restrito a esses pool_ids.
// Escopo (não a fronteira dura de escrita): read-only, degradação graciosa — nunca 401.
function _accessiblePools(req: Request): string[] | null {
  const secret = config.jwt_secret
  if (!secret) return null
  const auth = (req.headers["authorization"] as string | undefined) ?? ""
  if (!auth.startsWith("Bearer ")) return null
  try {
    const claims = verifyHs256(auth.slice("Bearer ".length), secret)
    const raw = claims["accessible_pools"]
    // Ordem idêntica à do `resolve_scope` do py-authz — dois serviços que respondem
    // diferente a "qual é o meu domínio?" divergem no primeiro ajuste:
    //   1. lista não-vazia → escopado;
    //   2. senão → irrestrito LEGADO, contado. Some na AUT-03.
    //
    // ⚠️ O ramo `claim unrestricted → irrestrito` SAIU em 2026-08-31: sob ABAC total não
    // há porta larga por claim, porque pools são do TENANT e não da plataforma. Esta é
    // uma das DUAS cópias TS do resolvedor que nenhum censo contava — o
    // `probe_authz_single_verifier` conta quem DECODIFICA JWT, e estas consomem claims
    // já decodificados. Mantê-la em sincronia é obrigação, não estilo.
    if (Array.isArray(raw) && raw.length > 0) return raw.map((p) => String(p))
    console.warn(
      `[operational] LEGADO_POOLS_VAZIO — accessible_pools vazio. ` +
      `sub=${claims["sub"] ?? ""}`,
    )
    return null
  } catch (e) {
    // AUT-17 (2026-08-31): NAO degrada mais aberto. Antes um token ausente ou invalido
    // devolvia `null` = irrestrito — e depois da AUT-03 isso seria a maior porta que
    // sobra: escopo vazio recusaria, mas NENHUMA credencial liberaria o tenant inteiro.
    //
    // Devolve dominio VAZIO. E o log distingue as duas populacoes, porque elas nao sao
    // a mesma coisa: sem header e chamador que nunca se identificou (tela nao migrada
    // para o `apiFetch`); token invalido e sessao expirada ou adulterada.
    console.warn(
      `[operational] escopo VAZIO por credencial: ${auth ? "token invalido/expirado" : "sem header Authorization"}` +
      ` — ${e instanceof Error ? e.message : "erro"}`,
    )
    return []
  }
}

// ── F4b — rollup de capacidade do tenant (defeito C) ──────────────────────────
//
// `Σ available(pool)` conta o mesmo recurso uma vez por pool: um humano de 3 vagas
// logado em 3 pools soma 9 para 3 vagas. Não é corrigível somando melhor — a
// informação de sobreposição não está nas linhas. O Routing Engine publica o rollup
// deduplicado (sobre instâncias DISTINTAS, por TIPO de licença) e aqui apenas o
// repassamos. **Nunca reimplementar a agregação neste serviço**: seria a segunda
// implementação da mesma regra, e é assim que dois números divergem.
interface TenantCapacity {
  by_kind: Record<string, {
    total_capacity: number; used: number; available: number; instances: number
    by_channel: Record<string, { available: number; instances: number; pools_available: number }>
  }>
  computed_at: string
}

// Cache curto do rollup ESCOPADO. O recompute é O(pools + instâncias) no engine e o
// Monitor recarrega a cada 15 s; 5 s espelham o throttle do próprio rollup, então a
// resposta escopada nunca é mais velha que a global.
const _scopedCapCache = new Map<string, { at: number; value: TenantCapacity | null }>()

async function _tenantCapacity(
  tenantId: string,
  accessible: string[] | null,
): Promise<{ capacity: TenantCapacity | null; capacity_unavailable: string | null }> {
  // Sem escopo: lê a chave publicada, direto.
  if (!accessible) {
    try {
      const raw = await getRedis().get(opKeys.tenantCapacity(tenantId))
      if (!raw) return { capacity: null, capacity_unavailable: "no_rollup" }
      const parsed = JSON.parse(raw) as TenantCapacity
      if (!parsed?.by_kind) return { capacity: null, capacity_unavailable: "no_rollup" }
      return { capacity: parsed, capacity_unavailable: null }
    } catch {
      return { capacity: null, capacity_unavailable: "no_rollup" }
    }
  }

  // Com escopo: a deduplicação **não projeta**. Depois de agregar, a informação de qual
  // instância pertence a qual pool foi consumida — do `available: 353` do tenant não há
  // como derivar quanto 2 pools alcançam. A conta restrita precisa ser REFEITA, e é
  // refeita no Routing Engine (`GET /v1/capacity?pools=…`), que é o dono da regra.
  // Reimplementá-la aqui, onde o `accessible_pools` está à mão, criaria a segunda
  // implementação da mesma regra — e é assim que dois números divergem.
  const key = `${tenantId}::${[...accessible].sort().join(",")}`
  const hit = _scopedCapCache.get(key)
  if (hit && Date.now() - hit.at < 5_000) {
    return hit.value
      ? { capacity: hit.value, capacity_unavailable: null }
      : { capacity: null, capacity_unavailable: "no_rollup" }
  }
  try {
    const url = `${config.routing_engine_url}/v1/capacity`
      + `?tenant_id=${encodeURIComponent(tenantId)}`
      + `&pools=${encodeURIComponent(accessible.join(","))}`
    const resp = await fetch(url, {
      headers: config.routing_admin_token
        ? { "X-Admin-Token": config.routing_admin_token } : {},
      signal: AbortSignal.timeout(2_000),
    })
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const body = await resp.json() as { capacity: TenantCapacity | null }
    _scopedCapCache.set(key, { at: Date.now(), value: body.capacity ?? null })
    return body.capacity
      ? { capacity: body.capacity, capacity_unavailable: null }
      : { capacity: null, capacity_unavailable: "no_rollup" }
  } catch {
    // Engine fora / timeout → desconhecido. NUNCA cair para `Σ available` das linhas:
    // a soma é o defeito C, não um fallback dele.
    return { capacity: null, capacity_unavailable: "engine_unreachable" }
  }
}

// ── Item 7a (capacity-governance) — agregador de admissão ─────────────────────
// Lê o estado dos buckets de admissão (Fase B + gates por tipo + fila de
// sistema) direto do Redis — read-only, sem tocar o hot path do routing.
// Per-pool: atribuição da licença de IA (HASH ai_pools) / fila muda×atendida /
// admissível. Tenant: C_ai usado/limite, buffer. Fatia 3 (2026-08-02): reserva por
// pool e pote compartilhado saíram — eram baldes de SESSÃO de um C que soma moedas.

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

/**
 * Estado da admissão. **Fatia 3 (2026-08-02): sobrou UM balde.**
 *
 * Saíram `sharedUsed`/`sharedLimit`/`sharedByPool` (o pote misto
 * `max_concurrent_sessions = C_ai + C_human`, que gateava sessão HUMANA contra uma
 * soma de moedas não-fungíveis) e `reservedUsed` (fatia de sessão por pool, do mesmo
 * pote, sem nenhum pool usando). O que resta é o teto de IA, na moeda certa.
 *
 * `contracted` continua sendo lido, mas NÃO é mais teto de admissão: é o número de
 * PROVISIONAMENTO que `lib/capacity.ts` cobra dos deploys (Σ declarada ≤ C). Fica
 * fora do bloco de admissão do `summary` por isso.
 */
interface AdmissionState {
  contracted:    number | null            // C ({t}:quota:max_concurrent_sessions) — provisionamento
  aiCap:         number | null            // C_ai — ÚNICO teto de admissão
  aiUsed:        number                   // SCARD kind:ai
  aiByPool:      Record<string, number>   // HASH ai_pools group-by (exato)
  bufferUsed:    number                   // SCARD unadmitted
  bufferLimit:   number
  unadmitted:    Set<string>              // p/ split fila muda×atendida
}

async function _admissionState(tenantId: string): Promise<AdmissionState> {
  const redis = getRedis()
  const [cRaw, aiRaw, aiUsed, bufferMembers, aiHash] = await Promise.all([
    redis.get(`${tenantId}:quota:max_concurrent_sessions`),
    redis.get(`${tenantId}:quota:capacity:ai_agent`),
    redis.scard(`${tenantId}:admission:kind:ai`),
    redis.smembers(`${tenantId}:queue:unadmitted`),
    redis.hgetall(`${tenantId}:admission:ai_pools`),
  ])
  const aiByPool: Record<string, number> = {}
  for (const poolId of Object.values(aiHash)) {
    aiByPool[poolId] = (aiByPool[poolId] ?? 0) + 1
  }
  return {
    contracted:  await _intOrNull(cRaw),
    aiCap:       await _intOrNull(aiRaw),
    aiUsed,
    aiByPool,
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
    const allPools = await prisma.pool.findMany({
      where:   { tenant_id: tenantId, status: "active" },
      orderBy: { pool_id: "asc" },
    })

    // Segurança Fase D — filtra ao DOMÍNIO do chamador. Filtrando a lista AQUI,
    // tudo a jusante (snapshots, live counts, admissão por pool, active/mute, items
    // e os agregados do summary derivados de items) já sai escopado.
    //
    // ⚠️ Três valores, e o do meio é novo (AUT-17, 2026-08-31):
    //   `null` → sem filtro (só o ramo LEGADO de lista vazia, que cai na AUT-03);
    //   `[]`   → NENHUM pool — credencial ausente ou inválida. Antes isto era `null`,
    //            e depois da AUT-03 seria a maior porta que sobra;
    //   lista  → recorte.
    // O `[]` funciona porque array vazio é truthy em JS: vira `Set` vazio e filtra tudo.
    // Não trocar por `accessible?.length ? … : null` — seria refazer aqui o `if not x`
    // que fundiria de novo "nenhum" com "todos".
    const accessible    = _accessiblePools(req)
    const accessibleSet = accessible ? new Set(accessible) : null
    const pools = accessibleSet ? allPools.filter(p => accessibleSet.has(p.pool_id)) : allPools

    // 2. Load snapshots + live counts in parallel
    const [snapshots, liveCounts] = await Promise.all([
      Promise.all(pools.map(p => getPoolSnapshot(tenantId, p.pool_id))),
      Promise.all(pools.map(p => _liveCount(tenantId, p.pool_id))),
    ])

    // 2b. Item 7a — estado da admissão (licença de IA × fila gratuita) +
    //     ativos e fila muda por pool.
    const adm = await _admissionState(tenantId)
    const cap = await _tenantCapacity(tenantId, accessible)
    const redis = getRedis()
    // Capacidade compartilhada, fatia 2 — `active_sessions` vinha de
    // `{t}:pool:{p}:active_count`, contador POR POOL de uma capacidade que é do
    // RECURSO (e agora sem escritor: foi removido). Passa a vir do `busy` do
    // snapshot, que é a projeção pela TAG do membro do semáforo — um ocupante tem
    // exatamente uma tag, então somar entre pools não conta o mesmo atendimento
    // duas vezes. `null` quando o snapshot não traz o campo (linha do bootstrap):
    // desconhecido, NÃO zero — zero é indistinguível de "ninguém em atendimento".
    const activeCounts: (number | null)[] = pools.map((_p, i) => {
      const s = snapshots[i]
      return s && typeof s.busy === "number" ? s.busy : null
    })
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

      // Use snapshot values if available, else live counts.
      //
      // `available` pode vir AUSENTE (2026-08-02): o bootstrap omite capacidade quando
      // o pool tem membro que ele não gerencia — humano logado é o caso normal, e antes
      // disso ele publicava `0`, afirmando que um pool com agente pronto não tinha
      // vaga. `undefined` aqui é DESCONHECIDO, e a diferença importa: `undefined > 0`
      // é `false` em JS, então tratar por omissão devolveria "empty" — o mesmo zero
      // plausível, só que produzido do lado do leitor.
      //
      // O live fallback (`live.instances`) é PERTENCIMENTO, não vaga — o mesmo modelo
      // que a F5 removeu do `available_agents`. Mantido só onde já estava (sem
      // snapshot nenhum) e marcado, para não crescer.
      const snapAvail   = hasSnap && typeof snap!.available === "number" ? snap!.available : null
      const available   = hasSnap ? snapAvail : live.instances
      const queueLength = hasSnap ? snap!.queue_length : live.queue

      const status =
        available === null  ? "unknown"
        : available > 0     ? "available"
        : queueLength > 0   ? "queued"
        : "empty"

      // Estimated wait: each position waits ~sla*0.7 (p70 handle time heuristic)
      const slaMs           = p.sla_target_ms
      const avgHandleMs     = slaMs * 0.7
      const estimatedWaitMs = queueLength > 0 ? Math.round(queueLength * avgHandleMs) : null

      // ── Item 7a — regime de admissão e disponibilidade admissível ──────────
      // Fatia 3: o regime não é mais "reservado × compartilhado" (baldes de sessão
      // do pote misto) e sim **licenciado × não-licenciado por sessão**. Pool de IA
      // debita `C_ai`; pool humano não debita nada aqui — sua licença é por LOGIN, e
      // o `agent_login` já a cobra. `admissible: null` num pool humano é ausência
      // honesta ("não há teto de sessão a informar"), não infinito otimista.
      const px          = p as unknown as {
        agent_kind: string | null; queue_config: unknown
      }
      const licensed  = px.agent_kind === "ai"
      const admitted  = licensed ? (adm.aiByPool[p.pool_id] ?? 0) : 0
      const admissible: number | null = licensed && adm.aiCap !== null
        ? Math.max(0, adm.aiCap - adm.aiUsed)
        : null
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
        admission_scope:  licensed ? "licensed" : "unlicensed",  // debita C_ai por sessão?
        admitted,                                           // sessões debitando C_ai neste pool
        active_sessions:  activeCounts[i] ?? null,          // em atendimento NESTE pool (null = desconhecido)
        // Vagas do MESMO recurso consumidas por pools IRMÃOS. Sem isto a linha fica
        // aritmeticamente inexplicável (`available < total − active_sessions`) e o
        // leitor conclui que há um bug — quando o que há é capacidade compartilhada.
        busy_elsewhere:   snap && typeof snap.busy_elsewhere === "number" ? snap.busy_elsewhere : null,
        total_capacity:   snap && typeof snap.total_instances === "number" ? snap.total_instances : null,
        capacity_model:   snap?.model ?? null,              // resource_semaphore | bootstrap_placeholder
        // POR QUE a capacidade é desconhecida, quando é. Hoje só `unmanaged_members`
        // (pool com humano logado numa janela em que o bootstrap escreveu a linha).
        // Sem isto a tela mostraria "—" sem conseguir dizer se é falha ou desenho.
        capacity_unknown: (snap as { capacity_unknown?: string } | null)?.capacity_unknown ?? null,
        queue_mute:       queueMute,                        // espera gratuita (fora de C)
        queue_attended:   Math.max(0, queueLength - queueMute),
        queue_tier:       queueTier,                        // attended | system | none
        admissible,                                         // o que a admissão deixa entrar (null = sem teto)
        admissible_shared: licensed,                        // ⊕ — número compartilhado (teto de tenant, não do pool)

        // Source flags
        has_snapshot:  hasSnap,
        live_fallback: !hasSnap,  // true = data from :instances/:queue keys directly
      }
    })

    // Item 7a — agregados do tenant (tiles + donuts).
    // `active_sessions` é aditivo entre pools por construção (um ocupante carrega
    // UMA tag), diferente de `available`, que não é — ver §3 do desenho de
    // capacidade compartilhada. Pools sem `busy` no snapshot ficam de fora da soma
    // em vez de entrar como 0.
    const inServiceTotal = activeCounts.reduce<number>((s, v) => s + (v ?? 0), 0)
    const queueTotal     = result.reduce((s, r) => s + r.queue_length, 0)
    const queueMuteTotal = result.reduce((s, r) => s + r.queue_mute, 0)
    // Segurança Fase D — admitted_total derivado dos items ESCOPADOS (não do
    // SCARD tenant-global). Escopa corretamente e equivale ao global quando irrestrito.
    const admittedTotal  = result.reduce((s, r) => s + r.admitted, 0)
    // by_pool nomeia pools → restringe ao domínio (o resto é agregado do tenant).
    const aiByPoolScoped = accessibleSet
      ? Object.fromEntries(Object.entries(adm.aiByPool).filter(([pid]) => accessibleSet.has(pid)))
      : adm.aiByPool
    const summary = {
      // `contracted` é PROVISIONAMENTO (Σ declarada nos deploys ≤ C — lib/capacity.ts),
      // NÃO teto de admissão. Deixou de ser denominador de `admitted_total` na fatia 3:
      // aquele par respondia "quantas sessões cabem no contrato", pergunta que exigia
      // somar licença humana com licença de IA. O teto que existe está em `ai.cap`.
      contracted:      adm.contracted,                       // C (null = sem pricing)
      admitted_total:  admittedTotal,                        // sessões debitando C_ai (escopado)
      headroom:        adm.aiCap === null ? null : Math.max(0, adm.aiCap - adm.aiUsed),
      in_service:      inServiceTotal,
      queue_total:     queueTotal,
      queue_attended:  Math.max(0, queueTotal - queueMuteTotal),
      queue_mute:      queueMuteTotal,
      buffer:   { used: adm.bufferUsed, limit: adm.bufferLimit },
      ai:       { cap: adm.aiCap, used: adm.aiUsed, by_pool: aiByPoolScoped },
      // F4b — capacidade DEDUPLICADA por tipo de licença. Substitui `Σ available`
      // na UI. `null` + motivo quando indisponível: o consumidor mostra ausência,
      // nunca cai de volta na soma (a soma É o defeito, não o fallback dele).
      capacity:             cap.capacity,
      capacity_unavailable: cap.capacity_unavailable,
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
