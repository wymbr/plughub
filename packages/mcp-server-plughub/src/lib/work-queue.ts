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

// ─── I5 / D7b — pendências de wrap-up (leitura do ledger) ─────────────────────
//
// O ledger `{t}:work_task:{session}` nasce no despacho do delegate e morre no
// resume — seu tempo de vida É o intervalo da pendência. O claim NÃO o apaga,
// então UMA linha cobre as duas formas (nunca reivindicada e reivindicada-não-
// submetida), que é justamente o que o relatório precisava e a fila pull não
// dava (`work_queue_list` não lista item já reivindicado).
//
// ESCOPO: só wrap-up. O ledger é genérico (cobre aprovação e delegate a pool
// push), mas o relatório da D4 é de trabalho AUTHOR-BOUND — aprovação é pooled,
// tem transbordo por `fallback_to_pool_after_s`, e portanto ninguém fica preso
// nela. O discriminador é o sufixo `-int` do pool, que a D6 tornou garantia por
// CONSTRUÇÃO (o registry rejeita criação manual de pool com esse sufixo), não
// convenção — `endsWith` aqui é seguro pela mesma razão que a derivação de
// acesso da D2 é.

/** Sufixo reservado (D6) das filas internas author-bound. */
export const INTERNAL_POOL_SUFFIX = "-int"

/**
 * Estado de um item parqueado, derivado do cruzamento ledger × ZSET × lease ×
 * registro durável de posse.
 *
 * `orphaned` é o estado que NÃO existia no desenho e que a leitura de código
 * revelou: pool pull, item fora do ZSET e sem dono. **A Fase A (D6) o estreitou
 * e com isso o tornou mais grave.** Antes, ele englobava o caso comum de "a lease
 * de 180 s venceu" — que não é orfandade nenhuma, o dono está lá. Agora que a posse
 * tem registro durável (`claimRecord`, TTL do prazo do item), item com dono aparece
 * como `claimed` (via `record`), e `orphaned` passou a significar o que o nome diz:
 * fora da fila, sem lease E sem registro — ninguém detém e ninguém re-enfileirou.
 * Ver `orphaned` na tela = investigar, não é mais ruído esperado.
 */
export type WorkTaskState =
  | "unclaimed"    // no ZSET, sem lease — nunca reivindicada
  | "claimed"      // fora do ZSET, com lease — reivindicada, não submetida
  | "orphaned"     // pool pull, fora do ZSET e sem lease — lease venceu sem reaper
  | "not_queued"   // pool push — não é item de fila (o expire é no-op)
  | "unknown"      // sem pool_config no cache — infra ausente, NÃO presumir push

export interface PendingWorkTask {
  /** Sessão que o RESUME resolve (chave do ledger) — a que o expire recebe. */
  session_id:       string
  /** Id que está DE FATO no ZSET (== session_id em conferência). */
  queue_session_id: string
  pool_id:          string
  /** user_id do dono (Camada B). Vazio sob `-int` é ANOMALIA, não normalidade. */
  assigned_to:      string | null
  step_id:          string | null
  state:            WorkTaskState
  /** dispatch_mode do pool lido de `{t}:pool_config:{pool}`; null = ausente. */
  dispatch_mode:    string | null
  created_at:       string | null
  /** Prazo do delegate (ISO). */
  deadline:         string | null
  /** Idade desde o despacho. */
  age_ms:           number | null
  /** ms até o prazo (negativo = vencido). */
  time_to_deadline_ms: number | null
  /**
   * Prazo no passado com a chave AINDA VIVA. No caminho normal isso não ocorre:
   * o timeout scanner resume no prazo e apaga o ledger. Ver = o scanner não
   * passou (serviço fora, ou o intervalo de 60 s). Sinal, não decoração.
   */
  overdue:          boolean
  /** Instância que detém o item (só em `claimed`). */
  claimed_by:       string | null
  claimed_at:       string | null
  /** Qual fonte nomeou o dono: `lease` (fresca) ou `record` (durável, Fase A).
   *  `record` num item antigo é normal — a lease tem TTL de 180 s. */
  claimed_via:      "lease" | "record" | null
}

export interface PendingWorkTasksResult {
  items: PendingWorkTask[]
  /** Chaves varridas antes do teto — não é o total do Redis quando truncado. */
  scanned: number
  /**
   * Bateu o teto do SCAN. Explícito de propósito: resultado parcial mudo é a
   * mentira tranquila que a § Postura proíbe. A UI PRECISA exibir isto.
   */
  truncated: boolean
}

export interface PendingWorkTasksOpts {
  /** Teto de chaves varridas. Default 2000. */
  maxKeys?:     number
  /** Filtros (conveniência: o SCAN varre tudo e filtra depois — não há índice). */
  assignedTo?:  string
  poolId?:      string
  state?:       WorkTaskState
  /** false = não filtra por `-int` (diagnóstico). Default true. */
  internalOnly?: boolean
}

/**
 * Varre o ledger e classifica as pendências. SCAN (nunca KEYS — KEYS bloqueia o
 * Redis inteiro, e este é o mesmo Redis do routing em produção).
 */
export async function listPendingWorkTasks(
  redis:    RedisClient,
  tenantId: string,
  opts:     PendingWorkTasksOpts = {},
): Promise<PendingWorkTasksResult> {
  const maxKeys      = Math.max(1, Math.min(opts.maxKeys ?? 2000, 20000))
  const internalOnly = opts.internalOnly !== false
  const prefix       = `${tenantId}:work_task:`
  const nowMs        = Date.now()

  // ── 1. SCAN com teto ───────────────────────────────────────────────────────
  const rawKeys: string[] = []
  let cursor    = "0"
  let truncated = false
  do {
    const [next, batch] = await redis.scan(cursor, "MATCH", `${prefix}*`, "COUNT", 500)
    cursor = next
    for (const k of batch) {
      if (rawKeys.length >= maxKeys) { truncated = true; break }
      rawKeys.push(k)
    }
    if (truncated) break
  } while (cursor !== "0")

  if (rawKeys.length === 0) return { items: [], scanned: 0, truncated }

  // ── 2. Ledger de cada chave (pipeline) ─────────────────────────────────────
  const ledgerPipe = redis.pipeline()
  for (const k of rawKeys) ledgerPipe.get(k)
  const ledgerRes = await ledgerPipe.exec()

  interface Row { sessionId: string; led: Record<string, unknown> }
  const rows: Row[] = []
  for (let i = 0; i < rawKeys.length; i++) {
    const raw = ledgerRes?.[i]?.[1]
    if (typeof raw !== "string") continue          // expirou entre o SCAN e o GET
    let led: Record<string, unknown>
    try { led = JSON.parse(raw) as Record<string, unknown> } catch { continue }
    const poolId = String(led["pool_id"] ?? "")
    if (!poolId) continue
    if (internalOnly && !poolId.endsWith(INTERNAL_POOL_SUFFIX)) continue
    if (opts.poolId && poolId !== opts.poolId) continue
    const assignedTo = String(led["assigned_to"] ?? "")
    if (opts.assignedTo && assignedTo !== opts.assignedTo) continue
    rows.push({ sessionId: rawKeys[i]!.slice(prefix.length), led })
  }

  if (rows.length === 0) return { items: [], scanned: rawKeys.length, truncated }

  // ── 3. dispatch_mode por pool DISTINTO (uma leitura por pool, não por item) ─
  const pools = [...new Set(rows.map(r => String(r.led["pool_id"])))]
  const cfgPipe = redis.pipeline()
  for (const p of pools) cfgPipe.get(`${tenantId}:pool_config:${p}`)
  const cfgRes = await cfgPipe.exec()
  const dispatchByPool = new Map<string, string | null>()
  for (let i = 0; i < pools.length; i++) {
    const raw = cfgRes?.[i]?.[1]
    let mode: string | null = null
    if (typeof raw === "string") {
      try { mode = String((JSON.parse(raw) as Record<string, unknown>)["dispatch_mode"] ?? "push") }
      catch { mode = null }
    }
    dispatchByPool.set(pools[i]!, mode)
  }

  // ── 4. ZSET + lease + registro durável por item ────────────────────────────
  // O registro (Fase A / D6) entra aqui porque sem ele este relatório e o árbitro
  // discordam sobre o MESMO fato: passados 180 s a lease some, e a classificação
  // caía em `orphaned` ("ninguém detém") enquanto `work_task_holder` continuava
  // nomeando o dono. Valor plausível e errado num relatório de pendências.
  const STRIDE = 3
  const stPipe = redis.pipeline()
  for (const r of rows) {
    const poolId = String(r.led["pool_id"])
    const qsid   = String(r.led["queue_session_id"] ?? r.sessionId)
    stPipe.zscore(keys.poolQueue(tenantId, poolId), qsid)
    stPipe.get(keys.claimLease(tenantId, poolId, qsid))
    stPipe.get(keys.claimRecord(tenantId, poolId, qsid))
  }
  const stRes = await stPipe.exec()

  // ── 5. Classificação ───────────────────────────────────────────────────────
  const items: PendingWorkTask[] = []
  for (let i = 0; i < rows.length; i++) {
    const { sessionId, led } = rows[i]!
    const poolId  = String(led["pool_id"])
    const qsid    = String(led["queue_session_id"] ?? sessionId)
    const inQueue  = stRes?.[i * STRIDE]?.[1] != null
    const leaseRaw  = stRes?.[i * STRIDE + 1]?.[1]
    const recordRaw = stRes?.[i * STRIDE + 2]?.[1]

    let claimedBy: string | null = null
    let claimedAt: string | null = null
    let claimedVia: "lease" | "record" | null = null
    // Lease primeiro (mais fresca); registro como segunda via — mesma ordem do
    // `work_task_holder`, para que os dois leitores nunca divirjam.
    if (typeof leaseRaw === "string") {
      try {
        const lease = JSON.parse(leaseRaw) as Record<string, unknown>
        claimedBy  = (lease["instance_id"] as string) ?? null
        claimedAt  = (lease["claimed_at"]  as string) ?? null
        claimedVia = "lease"
      } catch { claimedBy = "?"; claimedVia = "lease" }
    }
    if (!claimedBy && typeof recordRaw === "string") {
      try {
        const rec  = JSON.parse(recordRaw) as Record<string, unknown>
        claimedBy  = (rec["instance_id"] as string) ?? null
        claimedAt  = (rec["claimed_at"]  as string) ?? null
        claimedVia = "record"
      } catch { claimedBy = "?"; claimedVia = "record" }
    }

    const mode = dispatchByPool.get(poolId) ?? null
    let state: WorkTaskState
    if (inQueue)          state = "unclaimed"
    else if (claimedBy)   state = "claimed"
    else if (mode === null) state = "unknown"     // sem config: não presumir nada
    else if (mode === "pull") state = "orphaned"  // lease venceu sem reaper
    else                  state = "not_queued"

    if (opts.state && state !== opts.state) continue

    const createdAt = (led["created_at"] as string) ?? null
    const deadline  = (led["deadline"]   as string) ?? null
    const createdMs = createdAt ? Date.parse(createdAt) : NaN
    const deadlineMs = deadline ? Date.parse(deadline) : NaN
    const assignedTo = String(led["assigned_to"] ?? "")

    items.push({
      session_id:       sessionId,
      queue_session_id: qsid,
      pool_id:          poolId,
      assigned_to:      assignedTo || null,
      step_id:          (led["step_id"] as string) || null,
      state,
      dispatch_mode:    mode,
      created_at:       createdAt,
      deadline,
      age_ms:              Number.isFinite(createdMs)  ? Math.max(nowMs - createdMs, 0) : null,
      time_to_deadline_ms: Number.isFinite(deadlineMs) ? deadlineMs - nowMs : null,
      overdue:             Number.isFinite(deadlineMs) ? deadlineMs < nowMs : false,
      claimed_by:       claimedBy,
      claimed_at:       claimedAt,
      claimed_via:      claimedVia,
    })
  }

  // Mais antigas primeiro — a pendência que mais dói é a que mais esperou.
  items.sort((a, b) => (b.age_ms ?? 0) - (a.age_ms ?? 0))
  return { items, scanned: rawKeys.length, truncated }
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

/** Veredicto de posse do árbitro (Fase A/D6). Ver `Router.work_task_holder`. */
export interface WorkTaskHolder {
  found:             boolean
  instance_id?:      string
  claimant_user_id?: string
  claimed_at?:       string
  /** Qual fonte provou a posse: "lease" | "record" | "none". */
  via:               string
  /**
   * A sessão é membro do ZSET da fila AGORA. Fato POSITIVO, e é o que torna o
   * veredicto fechável: o claim é um ZREM, então item na fila não tem dono.
   * `found=false, in_queue=true` = "ninguém detém"; `found=false, in_queue=false`
   * = ausência honesta (push, encerrado, claim pré-Fase A).
   */
  in_queue:          boolean
}

/**
 * Fase D (D5) — pergunta ao ÁRBITRO quem detém um item.
 *
 * O Console precisa disto para derivar *"eu detenho"* do CLAIM em vez de confiar
 * num `conversation.assigned` republicado na reconexão. `null` = desconhecido
 * (árbitro inalcançável ou resposta ilegível) e **nunca** deve ser lido como
 * "ninguém detém": os dois exigem condutas opostas do chamador.
 */
export async function workTaskHolder(
  routingUrl: string,
  adminToken: string | undefined,
  a: { tenant_id: string; pool_id: string; session_id: string },
): Promise<WorkTaskHolder | null> {
  try {
    const raw = await callRouting(routingUrl, adminToken, "/v1/work_queue/holder", {
      tenant_id:  a.tenant_id,
      pool_id:    a.pool_id,
      session_id: a.session_id,
    }) as Record<string, unknown>
    if (!raw || typeof raw !== "object" || typeof raw["found"] !== "boolean") return null
    return {
      found:            raw["found"] as boolean,
      instance_id:      (raw["instance_id"]      as string) ?? undefined,
      claimant_user_id: (raw["claimant_user_id"] as string) ?? undefined,
      claimed_at:       (raw["claimed_at"]       as string) ?? undefined,
      via:              String(raw["via"] ?? "none"),
      in_queue:         raw["in_queue"] === true,
    }
  } catch {
    return null
  }
}

export interface ReleaseArgs {
  tenant_id:   string
  pool_id:     string
  session_id:  string
  instance_id: string
}

/** Produtor mínimo de Kafka de que o release precisa (mesma forma que `server.ts` usa). */
export interface WorkQueuePublisher {
  publish: (topic: string, payload: Record<string, unknown>) => Promise<void>
}

/**
 * Devolve um item claimado à fila.
 *
 * **Duas metades, e a segunda é o conserto do achado 2 de 2026-08-04.** O árbitro
 * devolve a VAGA e re-enfileira o item; a PRESENÇA do humano na sessão é fato do
 * orchestrator-bridge (`session:{sid}:human_agent` + `session:{sid}:human_agents`),
 * e ninguém a desfazia. O marcador sobrevivente fazia o guard de dedup do bridge
 * descartar o `conversations.routed` do re-claim — a vaga era concedida, a
 * ocupação subia, e nenhum cartão nascia. Reproduzido 4×, com controle positivo
 * (após um F5, que passa pelo desmonte completo, os mesmos 3 claims viram 3
 * cartões).
 *
 * O anúncio é `contact_closed(reason=agent_release_item)` em `conversations.events`
 * — mesmo par tópico/evento que o `session_transfer` já usa para "o segmento deste
 * agente acabou, o contato continua". **O bridge é quem apaga as chaves**, e é por
 * isso que este arquivo não fala Redis de presença: quem escreveu o fato é quem o
 * desfaz. O ramo do bridge não re-rota, não fecha o contato e não chama o release
 * de novo.
 *
 * **Ordem load-bearing:** publica só depois do `released: true` do árbitro. Anunciar
 * antes derrubaria a presença de um item que o árbitro pode recusar a soltar
 * (posse de outro agente, 403) — e o humano perderia a tela de um item que
 * continua sendo dele.
 *
 * Falha do publish NÃO derruba o release (a vaga já voltou), mas nunca é muda:
 * sem o anúncio o defeito volta para ESTA sessão, e o log é a única coisa que
 * distingue isso de "consertado".
 */
export async function releaseTask(
  routingUrl: string,
  adminToken: string | undefined,
  a: ReleaseArgs,
  kafka: WorkQueuePublisher,
): Promise<unknown> {
  const result = await callRouting(routingUrl, adminToken, "/v1/work_queue/release", {
    tenant_id:   a.tenant_id,
    pool_id:     a.pool_id,
    session_id:  a.session_id,
    instance_id: a.instance_id,
  })

  const released = (result as Record<string, unknown> | null)?.["released"] === true
  if (!released) {
    console.warn(
      `[work_queue] release NÃO confirmado pelo árbitro — presença do humano ` +
      `preservada de propósito: session=${a.session_id} pool=${a.pool_id} ` +
      `instance=${a.instance_id} resposta=${JSON.stringify(result)}`,
    )
    return result
  }

  try {
    await kafka.publish("conversations.events", {
      event_type:  "contact_closed",
      session_id:  a.session_id,
      instance_id: a.instance_id,
      reason:      "agent_release_item",
      // Sem `outcome`: devolver não é desfecho. Um placeholder aqui viraria a
      // disposição do segmento no acumulador de sinais do bridge.
    })
  } catch (err) {
    console.error(
      `[work_queue] release OK mas o anúncio contact_closed(agent_release_item) ` +
      `FALHOU — a presença do humano fica no bridge e o próximo claim desta sessão ` +
      `será engolido pelo guard de dedup: session=${a.session_id} ` +
      `pool=${a.pool_id} instance=${a.instance_id}:`, err,
    )
  }
  return result
}
