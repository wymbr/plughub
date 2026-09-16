/**
 * human-liveness.ts — o agente humano sai do pool quando a CONEXÃO some, e não só quando ela
 * FECHA (AGH-02).
 *
 * O desregistro do humano vivia inteiro no `close` do `/agent/ws`. Processo recriado (deploy,
 * `up -d`, crash) derruba todos os sockets sem que o timer de graça chegue a disparar: a
 * instância `human-*` ficava `ready` em todos os pools, sem TTL e sem ninguém do outro lado —
 * recebendo contato. Medido em 2026-09-15: o admin ficou em 12 pools e engoliu os contatos de
 * dois probes, e um fantasma de probe seguia no pool 150 s depois do reinício (TTL -1).
 *
 * ⚠️ A cura NÃO é pôr TTL na chave da instância. Isso já foi feito e desfeito: sem renovação
 * confiável, o agente CONECTADO some do roteamento sem nenhum DEL (2026-07-28, ver o
 * `LUA_JOIN_POOL`). A permanência da instância continua sendo afirmada pelo login; o que ganha
 * TTL é um fato SEPARADO e menor — *"há uma conexão viva deste recurso neste pool"* —, de escopo
 * (instância, pool) porque o Console abre uma conexão POR POOL.
 *
 *   escrita   login (`registerHumanAgent`) · pong de aplicação · pong de protocolo
 *   leitura   o varredor, e o `LUA_LEAVE_POOL`, que a relê DENTRO do script: a decisão de sair
 *             é tomada sobre o estado do instante da escrita, não sobre o que o varredor viu
 *
 * O varredor mora no mcp-server porque é ele o dono do ciclo de vida do registro humano, e sai
 * pelo MESMO `unregisterHumanAgent` do `close` — um caminho de saída, não dois.
 */

/** ~3 ciclos do ping de 30 s: uma perda de pong não derruba ninguém, três derrubam. */
export const HUMAN_LIVENESS_TTL_S = 90

/** Cadência do varredor. */
export const HUMAN_SWEEP_INTERVAL_MS = 15_000

export const livenessKey = (tenantId: string, instanceId: string, poolId: string) =>
  `${tenantId}:human_liveness:${instanceId}:${poolId}`

/**
 * Carência de boot: um processo recém-subido não varre antes de um TTL inteiro. Os Consoles
 * derrubados pelo próprio reinício precisam desse tempo para reconectar e reafirmar a conexão —
 * e instância anterior a este mecanismo não tem chave nenhuma, então sem a carência seria
 * varrida no primeiro tique com o agente a caminho.
 */
export function sweepAllowed(bootAtMs: number, nowMs: number, ttlS = HUMAN_LIVENESS_TTL_S): boolean {
  return nowMs - bootAtMs >= ttlS * 1000
}

/** `{t}:instance:human-x` sim; `{t}:instance:human-x:sessions` (o semáforo) não. */
export function humanInstanceIdFromKey(tenantId: string, key: string): string | null {
  const prefix = `${tenantId}:instance:`
  if (!key.startsWith(prefix)) return null
  const id = key.slice(prefix.length)
  if (!id.startsWith("human-") || id.includes(":")) return null
  return id
}

/**
 * Sessões do semáforo `{t}:instance:{iid}:sessions` servidas NESTE pool. Membro é
 * `"{session_id}::{conference_id}::{pool_id}"` (o pool é sempre o 3º campo). Membro sem pool
 * (anterior à F1) é atribuído a qualquer pool: o `agent_disconnect` duplicado é inócuo, o
 * bridge o descarta pelo SREM de `human_agents`. A vaga segura de wrap-up não é contato.
 */
export function occupantSessionsOfPool(members: string[], poolId: string): string[] {
  const out = new Set<string>()
  for (const m of members) {
    if (m.startsWith("__wrapup_hold__")) continue
    const [sid, , pool] = m.split("::")
    if (!sid) continue
    if (!pool || pool === poolId) out.add(sid)
  }
  return [...out]
}

/** O recorte de ioredis que o varredor usa — fácil de trocar por `ioredis-mock` no teste. */
export interface SweepRedis {
  scan(cursor: string, ...args: (string | number)[]): Promise<[string, string[]]>
  get(key: string): Promise<string | null>
  exists(key: string): Promise<number>
  set(key: string, value: string, ex: "EX", ttl: number): Promise<unknown>
  smembers(key: string): Promise<string[]>
  sismember(key: string, member: string): Promise<number>
}

export type LeaveStatus = "absent" | "corrupt" | "no_membership" | "full_logout" | "partial" | "alive"

export interface SweepDeps {
  redis:    SweepRedis
  kafka:    { publish: (topic: string, payload: Record<string, unknown>) => Promise<void> }
  tenantId: string
  /** Conexão viva NESTE processo — cinto de segurança se a renovação falhou no Redis. */
  hasLiveConnection: (userId: string, poolId: string) => boolean
  /** `unregisterHumanAgent` com a chave de liveness como guarda atômica. */
  leave: (poolId: string, userId: string, guardKey: string) => Promise<LeaveStatus>
  log?: (line: string) => void
}

export interface SweepReport {
  scanned:       number
  swept:         Array<{ instance_id: string; pool_id: string; status: LeaveStatus; disconnected: string[] }>
  renewed_local: Array<{ instance_id: string; pool_id: string }>
}

export async function sweepHumanGhosts(deps: SweepDeps): Promise<SweepReport> {
  const { redis, kafka, tenantId } = deps
  const log = deps.log ?? (() => {})
  const report: SweepReport = { scanned: 0, swept: [], renewed_local: [] }

  const ids = new Set<string>()
  let cursor = "0"
  do {
    const [next, keys] = await redis.scan(cursor, "MATCH", `${tenantId}:instance:human-*`, "COUNT", 200)
    for (const k of keys) {
      const id = humanInstanceIdFromKey(tenantId, k)
      if (id) ids.add(id)
    }
    cursor = next
  } while (cursor !== "0")

  for (const instanceId of ids) {
    report.scanned++
    const raw = await redis.get(`${tenantId}:instance:${instanceId}`)
    if (!raw) continue
    let inst: Record<string, unknown>
    try { inst = JSON.parse(raw) as Record<string, unknown> } catch {
      log(`[human-sweep] registro ilegivel instance=${instanceId} — nao varro o que nao sei ler`)
      continue
    }
    const pools  = Array.isArray(inst["pools"]) ? (inst["pools"] as unknown[]).filter((p): p is string => typeof p === "string") : []
    const userId = typeof inst["user_id"] === "string" && inst["user_id"]
      ? (inst["user_id"] as string)
      : instanceId.slice("human-".length)

    for (const poolId of pools) {
      const key = livenessKey(tenantId, instanceId, poolId)
      if (await redis.exists(key)) continue
      if (deps.hasLiveConnection(userId, poolId)) {
        // A conexão existe e a chave não: a renovação falhou (Redis fora num pong). Não é
        // fantasma — reafirma e diz que reafirmou.
        await redis.set(key, "1", "EX", HUMAN_LIVENESS_TTL_S)
        report.renewed_local.push({ instance_id: instanceId, pool_id: poolId })
        log(`[human-sweep] liveness AUSENTE com conexao viva local — reafirmada instance=${instanceId} pool=${poolId}`)
        continue
      }

      const status = await deps.leave(poolId, userId, key)
      const disconnected: string[] = []
      if (status === "full_logout" || status === "partial") {
        // O contato que o fantasma atendia não pode ficar preso a ele: mesmo evento que o
        // `close` publica, pelo mesmo critério (o humano ainda está em `human_agents`).
        const members = await redis.smembers(`${tenantId}:instance:${instanceId}:sessions`)
        for (const sid of occupantSessionsOfPool(members, poolId)) {
          if (!(await redis.sismember(`session:${sid}:human_agents`, instanceId))) continue
          await kafka.publish("conversations.events", {
            event_type:  "contact_closed",
            session_id:  sid,
            instance_id: instanceId,
            reason:      "agent_disconnect",
          })
          disconnected.push(sid)
        }
      }
      report.swept.push({ instance_id: instanceId, pool_id: poolId, status, disconnected })
      log(
        `[human-sweep] FANTASMA instance=${instanceId} pool=${poolId} — sem conexao viva ha mais de ` +
        `${HUMAN_LIVENESS_TTL_S}s; saida=${status} agent_disconnect=[${disconnected.join(",")}]`
      )
    }
  }
  return report
}
