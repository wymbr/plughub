/**
 * tools/operational.ts
 * Operational visibility tools — real-time queue and pool status.
 * Spec: PlugHub v24.0 section 3.3c
 *
 * These tools enable AI agents to query live operational data before deciding
 * whether to offer a customer a channel switch, inform wait time, or escalate.
 *
 * Data source: Redis snapshots written by the Routing Engine after every
 * routing event ({tenant_id}:pool:{pool_id}:snapshot, **TTL 3600s** — a nota
 * "TTL 120s" aqui estava desatualizada e é justamente o número que decide se uma
 * linha antiga é suspeita).
 *
 * ⚠️ **DOIS produtores escrevem esta chave, e a linha diz qual** (`model`):
 *   · `resource_semaphore`    — Routing Engine, dirigido por evento, linha completa;
 *   · `bootstrap_placeholder` — orchestrator-bridge (NX, TTL 60s), que OMITE
 *     `busy`/`busy_elsewhere`/`untagged` e **omite `available` de propósito**
 *     quando a soma seria parcial (`capacity_unknown: "unmanaged_members"`).
 * Medido em 2026-08-21 no tenant demo: **30 das 36 linhas vivas eram
 * `bootstrap_placeholder`** — o produtor "de exceção" é a maioria em pool ocioso.
 * Ler `snapshot.available` como se fosse sempre número é, portanto, ler um campo
 * OPCIONAL como obrigatório.
 *
 * Tools:
 *   queue_context_get         — queue position + estimated wait for a session
 *   pool_status_get           — pool availability (agents ready, queue depth)
 *   system_availability_check — channel availability across pools for a tenant
 */

import { McpServer }  from "@modelcontextprotocol/sdk/server/mcp.js"
import { z }          from "zod"
import type { RedisClient } from "../infra/redis"
import { keys }             from "../infra/redis"

// ─────────────────────────────────────────────
// Dependencies
// ─────────────────────────────────────────────

export interface OperationalDeps {
  redis: RedisClient
}

// ─────────────────────────────────────────────
// Internal types
// ─────────────────────────────────────────────

interface PoolSnapshot {
  pool_id:       string
  tenant_id:     string
  /** OPCIONAL no contrato. O tipo dizia `number` e afirmava uma garantia que
   *  produtor nenhum dá: o bootstrap OMITE o campo quando só saberia somar uma
   *  parcela. Em TypeScript `undefined > 0` é `false` — a omissão virava "não há
   *  agente", o oposto exato do que o produtor quis dizer. Use `publishedAvailable`. */
  available?:       number
  /** Vagas fora de circulação por PAUSA (2026-08-21). Ausente na linha do
   *  bootstrap. Serve para o chamador distinguir "não há agente" de "há agente,
   *  pausado" — decisões de produto diferentes. */
  paused_capacity?: number
  /** Discriminador de PRODUTOR: `resource_semaphore` | `bootstrap_placeholder`. */
  model?:           string
  /** Motivo pelo qual a linha não afirma capacidade (ex.: `unmanaged_members`). */
  capacity_unknown?: string
  queue_length:  number
  sla_target_ms: number
  channel_types: string[]
  updated_at:    string
}

/** Rollup de capacidade do tenant (F4). Sem campo `available` no topo, de propósito:
 *  humano e IA são moedas não-fungíveis e um escalar único as somaria. */
interface TenantCapacity {
  tenant_id?:  string
  by_kind:     Record<string, {
    total_capacity: number
    used:           number
    available:      number
    instances:      number
    by_channel:     Record<string, {
      available:       number
      instances:       number
      pools_available: number
    }>
  }>
  computed_at: string
}

// ─────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────

async function getSnapshot(
  redis: RedisClient,
  tenantId: string,
  poolId: string,
): Promise<PoolSnapshot | null> {
  const raw = await redis.get(keys.poolQueueSnapshot(tenantId, poolId))
  if (!raw) return null
  try {
    return JSON.parse(raw) as PoolSnapshot
  } catch {
    return null
  }
}

/** Lê o rollup do tenant. `null` = DESCONHECIDO — e o chamador precisa tratá-lo como
 *  tal. Nunca reconstruir somando as linhas de pool: essa soma é o defeito C, não um
 *  fallback dele. */
async function getTenantCapacity(
  redis: RedisClient,
  tenantId: string,
): Promise<TenantCapacity | null> {
  try {
    const raw = await redis.get(keys.tenantCapacity(tenantId))
    if (!raw) return null
    const parsed = JSON.parse(raw) as TenantCapacity
    return parsed?.by_kind ? parsed : null
  } catch {
    return null
  }
}

/**
 * Capacidade que a linha AFIRMA, ou `null` quando ela se abstém.
 *
 * Existe para que a abstenção do produtor sobreviva até o consumidor. Sem ela,
 * `snapshot.available > 0` transforma "não sei" em "não há" — e este é o grupo
 * de tools que decide **oferta de canal ao cliente**, o pior lugar possível para
 * um número plausível. Mesmo princípio da F5b, que já removeu do vizinho o
 * fallback por `SCARD` de membership.
 */
function publishedAvailable(s: PoolSnapshot): number | null {
  return typeof s.available === "number" ? s.available : null
}

function mcpOk(data: unknown) {
  return { content: [{ type: "text" as const, text: JSON.stringify(data) }] }
}

function mcpError(code: string, message: string) {
  return {
    isError: true,
    content: [{ type: "text" as const, text: JSON.stringify({ error: code, message }) }],
  }
}

// ─────────────────────────────────────────────
// Tool registration
// ─────────────────────────────────────────────

export function registerOperationalTools(
  server: McpServer,
  deps:   OperationalDeps,
): void {
  const { redis } = deps

  // ── queue_context_get ─────────────────────────────────────────────────────
  //
  // Returns the real-time queue context for a session already waiting in a pool.
  // Useful for AI agents that want to inform the customer of their estimated wait
  // time or offer a channel switch when the queue is long.

  server.tool(
    "queue_context_get",
    "Returns the real-time queue context for a session: position, estimated wait time, pool status. " +
    "Use before offering the customer an alternative channel or informing wait time.",
    {
      tenant_id:  z.string().describe("Tenant ID"),
      pool_id:    z.string().describe("Pool ID where the session is queued"),
      session_id: z.string().describe("Session ID of the queued contact"),
    } as any,
    async ({ tenant_id, pool_id, session_id }: { tenant_id: string; pool_id: string; session_id: string }) => {
      const snapshot = await getSnapshot(redis, tenant_id, pool_id)
      if (!snapshot) {
        return mcpError(
          "snapshot_unavailable",
          `No operational snapshot found for pool ${pool_id}. ` +
          "The Routing Engine may not have processed any event for this pool yet.",
        )
      }

      // Look up queue position (rank in ZSET, 0-based from oldest)
      let position: number | null = null
      try {
        const rank = await redis.zrank(keys.poolQueue(tenant_id, pool_id), session_id)
        if (rank !== null) position = rank + 1  // 1-based for the customer
      } catch { /* non-fatal — position stays null */ }

      // Estimated wait time based on queue depth and SLA target
      // Simple heuristic: queue_length * avg_handle_time
      // avg_handle_time estimated at SLA × 0.7 (p70 handle time)
      const avgHandleMs    = snapshot.sla_target_ms * 0.7
      const estimatedWaitMs = position !== null
        ? Math.round((position - 1) * avgHandleMs)
        : Math.round(snapshot.queue_length * avgHandleMs)

      const availAgents = publishedAvailable(snapshot)
      return mcpOk({
        session_id,
        pool_id,
        position:           position,
        queue_length:       snapshot.queue_length,
        // `null` quando a linha se abstém — nunca 0. Zero diria ao agente "não há
        // ninguém para atender", e ele informaria espera indefinida ao cliente
        // com base numa omissão.
        available_agents:   availAgents,
        capacity_unknown:   availAgents === null
          ? (snapshot.capacity_unknown ?? "snapshot_sem_capacidade")
          : null,
        paused_capacity:    snapshot.paused_capacity ?? null,
        estimated_wait_ms:  estimatedWaitMs,
        sla_target_ms:      snapshot.sla_target_ms,
        snapshot_model:     snapshot.model ?? null,
        snapshot_age_ms:    Date.now() - Date.parse(snapshot.updated_at),
      })
    },
  )

  // ── pool_status_get ───────────────────────────────────────────────────────
  //
  // Returns the current operational status of a pool: how many agents are
  // available, how many contacts are queued, and the configured SLA target.

  server.tool(
    "pool_status_get",
    "Returns the current operational status of a pool: available agents, queue depth, SLA target. " +
    "Use to decide whether to route to this pool or suggest an alternative.",
    {
      tenant_id: z.string().describe("Tenant ID"),
      pool_id:   z.string().describe("Pool ID"),
    } as any,
    async ({ tenant_id, pool_id }: { tenant_id: string; pool_id: string }) => {
      const snapshot = await getSnapshot(redis, tenant_id, pool_id)
      if (!snapshot) {
        // F5b — sem snapshot, "não sei" é a resposta CORRETA.
        //
        // O fallback anterior devolvia `available = SCARD(pool:instances)`: contagem de
        // PERTENCIMENTO ao pool, o modelo abandonado quando `max_concurrent > 1` passou
        // a existir. Ele conta instância lotada como disponível, ignora a capacidade
        // consumida em pools irmãos (a vaga é do RECURSO) e não filtra pausa nem
        // wrap-up. E fazia isso no tool que o Skill Flow usa para decidir oferta de
        // canal e tempo de espera AO CLIENTE — o pior lugar para um número plausível.
        //
        // `available: null` obriga o chamador a tratar o desconhecido; zero teria dito
        // "não há agente", e o SCARD dizia "há", ambos sem base. A fila é fato do pool
        // (ZSET), então ela continua sendo respondida.
        const queueLengthRaw = await redis.zcard(keys.poolQueue(tenant_id, pool_id))
        return mcpOk({
          pool_id,
          available:     null,
          queue_length:  queueLengthRaw,
          sla_target_ms: null,
          channel_types: [],
          status:        "unknown",
          snapshot_age_ms: null,
          live_fallback:   true,
          reason: "no_snapshot: capacidade desconhecida (o bootstrap publica a primeira " +
                  "linha de todo pool configurado em ~15s; SCARD de membership NÃO é capacidade)",
        })
      }

      // 2026-08-21 — a lição da F5b valia só para snapshot AUSENTE. Ela vale
      // igual para snapshot PRESENTE que se abstém de afirmar capacidade: o
      // bootstrap omite `available` de propósito quando a soma seria parcial, e
      // `undefined > 0` levava o ramo a `queued`/`empty` — publicando "não há
      // agente" a partir de um "não sei". Mesmo defeito, outro caminho.
      const avail  = publishedAvailable(snapshot)
      const status =
        avail === null                    ? "unknown"
        : avail > 0                       ? "available"
        : snapshot.queue_length > 0       ? "queued"
        : "empty"

      return mcpOk({
        pool_id:        snapshot.pool_id,
        available:      avail,
        // Distingue "não há agente" de "há agente, pausado" — duas decisões de
        // produto diferentes que `available: 0` sozinho colapsa numa só.
        paused_capacity: snapshot.paused_capacity ?? null,
        queue_length:   snapshot.queue_length,
        sla_target_ms:  snapshot.sla_target_ms,
        channel_types:  snapshot.channel_types,
        status,
        reason: avail === null
          ? (snapshot.capacity_unknown
              ? `capacity_unknown: ${snapshot.capacity_unknown}`
              : "snapshot presente mas SEM o campo `available` — a linha não afirma capacidade")
          : null,
        // Qual dos DOIS produtores escreveu esta linha, e há quanto tempo. Ambos
        // são DIAGNÓSTICO, não condição: não existe limiar de idade defensável
        // aqui — pool ocioso tem linha antiga e CORRETA, e reprovar por idade
        // faria um pool saudável se declarar desconhecido. Quem quiser condicionar
        // agora tem com quê.
        snapshot_model:  snapshot.model ?? null,
        snapshot_age_ms: Date.now() - Date.parse(snapshot.updated_at),
        live_fallback:   false,
      })
    },
  )

  // ── system_availability_check ─────────────────────────────────────────────
  //
  // Checks the availability of all channels across all pools for a tenant.
  // Returns a map of channel → { pools_available, total_queued, available_by_kind, status }.
  // NÃO existe `total_available_agents`: humano e IA são moedas não-fungíveis (F4).
  // Useful for offer-channel-switch logic in Skill Flows.

  server.tool(
    "system_availability_check",
    "Checks the real-time availability of all channels across a tenant's pools. " +
    "Returns per-channel availability status. Use to offer the customer an alternative " +
    "channel (e.g. switch from voice queue to chat) when one channel is saturated.",
    {
      tenant_id: z.string().describe("Tenant ID"),
      channels:  z.array(z.string()).optional().describe(
        "Optional list of channels to check. If omitted, checks all known channels.",
      ),
    } as any,
    async ({ tenant_id, channels: filterChannels }: { tenant_id: string; channels?: string[] }) => {
      // Enumerate all pool_ids for tenant from the tenant pools set
      const poolIds: string[] = []
      try {
        const members = await redis.smembers(`${tenant_id}:pools`)
        for (const m of members) poolIds.push(m.toString())
      } catch { /* empty */ }

      if (poolIds.length === 0) {
        return mcpOk({ tenant_id, channels: {}, message: "No pools found for tenant" })
      }

      // Load snapshots for all pools
      const snapshots = await Promise.all(
        poolIds.map(pid => getSnapshot(redis, tenant_id, pid))
      )

      // F4 — disponibilidade por canal vem do ROLLUP DO TENANT, não da soma das linhas.
      //
      // `Σ snap.available` era o defeito C: a vaga é do RECURSO, então um humano de 3
      // vagas logado em 3 pools contribuía 3 por linha e o total dizia 9 para 3 vagas.
      // O erro NÃO é corrigível aqui — a informação de sobreposição não está nas linhas
      // de pool. O rollup agrega sobre instâncias DISTINTAS, e por isso é a única fonte
      // que responde "quantos agentes existem de fato".
      //
      // E responde POR TIPO DE LICENÇA. Humano e IA não se substituem: um número único
      // repetiria a falácia de aditividade um nível acima — em vez de contar o mesmo
      // recurso duas vezes, somaria recursos que não são intercambiáveis. Por isso não
      // existe `total_available_agents` escalar na resposta; existe `by_kind`.
      const rollup = await getTenantCapacity(redis, tenant_id)

      // `pools_available` e `total_queued` SOBREVIVEM como soma: contagem de pools com
      // vaga e profundidade de fila são grandezas aditivas legítimas — respondem "há por
      // onde entrar?" e "quanto está esperando?", que são outras perguntas.
      const channelMap: Record<string, {
        pools_available:       number
        pools_capacity_unknown: number
        total_queued:          number
        pools:                 string[]
      }> = {}

      for (const snap of snapshots) {
        if (!snap) continue
        const a = publishedAvailable(snap)
        for (const ch of snap.channel_types) {
          if (filterChannels && !filterChannels.includes(ch)) continue
          if (!channelMap[ch]) {
            channelMap[ch] = {
              pools_available: 0, pools_capacity_unknown: 0,
              total_queued: 0, pools: [],
            }
          }
          channelMap[ch]!.total_queued += snap.queue_length
          channelMap[ch]!.pools.push(snap.pool_id)
          // TRÊS baldes, não dois: pool que AFIRMA vaga, pool que afirma não ter,
          // e pool que se ABSTÉM. Antes o terceiro caía no segundo em silêncio
          // (`undefined > 0` é false), e `pools_available` passava a significar
          // "pools que sabidamente têm vaga" sem que nada dissesse quantos não
          // foram consultáveis.
          if (a === null)     channelMap[ch]!.pools_capacity_unknown += 1
          else if (a > 0)     channelMap[ch]!.pools_available += 1
        }
      }

      const result: Record<string, unknown> = {}
      for (const [ch, data] of Object.entries(channelMap)) {
        // available por tipo, para ESTE canal, do rollup deduplicado.
        const byKind: Record<string, number> = {}
        let known = false
        if (rollup?.by_kind) {
          for (const [kind, k] of Object.entries(rollup.by_kind)) {
            const n = k.by_channel?.[ch]?.available
            if (typeof n === "number") { byKind[kind] = n; known = true }
          }
        }
        const anyAgent = Object.values(byKind).some(n => n > 0)
        result[ch] = {
          ...data,
          // Rollup ausente (TTL expirado / routing-engine sem escrever) → `null`, não 0.
          // Zero afirmaria "não há agente" e faria o Skill Flow desviar o cliente de um
          // canal que pode estar saudável; `null` obriga a tratar o desconhecido. Nunca
          // cair de volta na soma das linhas: a soma é o defeito, não o fallback dele.
          available_by_kind: known ? byKind : null,
          capacity_unknown:  !known,
          // 2026-08-21 — o ramo sem rollup dizia `pools_available > 0 ?
          // "available" : "unknown"`, e o comentário TRÊS LINHAS ACIMA já
          // proibia: *"nunca cair de volta na soma das linhas: a soma é o
          // defeito, não o fallback dele"*. A prosa estava certa e o código
          // fazia o contrário — publicava o veredicto "available" a partir do
          // número que ele mesmo acabara de declarar não confiável, no tool que
          // decide oferta de canal AO CLIENTE.
          //
          // Sem o rollup deduplicado a resposta é **`"unknown"`, sempre**.
          // `pools_available` continua no payload como DADO (é contagem aditiva
          // legítima); o que ele não pode mais é virar sentença.
          status: known
            ? (anyAgent ? "available" : data.total_queued > 0 ? "queued" : "no_agents")
            : "unknown",
        }
      }

      return mcpOk({
        tenant_id,
        channels: result,
        capacity_computed_at: rollup?.computed_at ?? null,
      })
    },
  )
}
