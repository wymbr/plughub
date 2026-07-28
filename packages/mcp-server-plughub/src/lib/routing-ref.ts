/**
 * lib/routing-ref.ts
 * Leitor do registro de roteamento por-(sessão, instância).
 *
 * Chave: `session:{session_id}:routing:{instance_id}` (String JSON), escrita pelo
 * orchestrator-bridge (`_write_routing_ref`).
 *
 * É o ÚNICO lugar do sistema que guarda **qual pool esta instância serve NESTA
 * sessão** — o único fato por-(sessão, instância) do conjunto. Um humano tem UMA
 * instância (`human-{userId}`) e pode estar logado em N pools ao mesmo tempo, então
 * o `pool_id` do registro global da instância (`{tenant}:instance:{iid}`) NÃO responde
 * "qual pool este agente serve neste atendimento" — responde "qual foi o último pool
 * escrito", que é outra pergunta.
 *
 * ADR `docs/adr/adr-human-agent-pool-scoped-identity.md` (F3/F4/F5):
 * *fato de escopo mais estreito não se lê de campo de escopo mais largo.*
 *
 * Compatibilidade de formato: até a F4 a chave guardava um sub-documento
 * `snapshot` com a cópia congelada do registro da instância; hoje guarda
 * `{tenant_id, instance_id, pool_id}` no nível raiz. O leitor aceita os dois —
 * chaves antigas drenam sozinhas pelo TTL de 4 h.
 */

import type { RedisClient } from "../infra/redis"

export interface RoutingRef {
  tenant_id?:   string
  instance_id?: string
  pool_id:      string
}

/**
 * Lê o pool que `instanceId` serve em `sessionId`.
 *
 * Devolve `null` quando a chave não existe, está malformada ou não tem pool —
 * e LOGA o motivo. Degradação nunca é silenciosa: um `null` aqui significa
 * "não sei qual pool", e todo chamador precisa decidir explicitamente o que
 * fazer com isso (o que quase sempre é *não agir*, nunca *chutar um pool*).
 */
export async function readRoutingRefPool(
  redis:      RedisClient,
  sessionId:  string,
  instanceId: string,
  logPrefix = "[routing-ref]",
): Promise<string | null> {
  if (!sessionId || !instanceId) return null

  let raw: string | null = null
  try {
    raw = await redis.get(`session:${sessionId}:routing:${instanceId}`)
  } catch (err) {
    console.warn(
      `${logPrefix} read failed: session=${sessionId} instance=${instanceId} — ${String(err)}`
    )
    return null
  }

  if (!raw) {
    console.warn(
      `${logPrefix} no routing ref: session=${sessionId} instance=${instanceId} ` +
      `(instância nunca ativada nesta sessão, ou TTL de 4 h expirou)`
    )
    return null
  }

  try {
    const ref = JSON.parse(raw) as Record<string, unknown>
    // Formato atual (F4): pool_id na raiz.
    const flat = typeof ref["pool_id"] === "string" ? (ref["pool_id"] as string) : ""
    if (flat) return flat

    // Formato legado (pré-F4): pool_id dentro do sub-documento `snapshot`.
    const snapshot = ref["snapshot"] as Record<string, unknown> | undefined
    const nested = typeof snapshot?.["pool_id"] === "string"
      ? (snapshot["pool_id"] as string)
      : ""
    if (nested) return nested

    console.warn(
      `${logPrefix} routing ref sem pool_id: session=${sessionId} instance=${instanceId}`
    )
    return null
  } catch (err) {
    console.warn(
      `${logPrefix} routing ref malformado: session=${sessionId} instance=${instanceId} — ${String(err)}`
    )
    return null
  }
}

/** Prefixo de instância de agente humano — ver `HUMAN_INSTANCE_PREFIX` no routing-engine. */
const HUMAN_INSTANCE_PREFIX = "human-"

/**
 * Tipo de agente de uma instância **no escopo de uma sessão**.
 *
 * Porte em TypeScript do `resolve_agent_type(instance, pool_id)` do routing-engine
 * (`models.py`, F2 do mesmo ADR):
 *
 *   - **humano** → `human_agent_{pool}`, função pura do pool. Não existe um "tipo"
 *     único que o registro do recurso possa guardar, porque a MESMA instância
 *     atende N pools; o campo armazenado é resíduo do primeiro login.
 *   - **IA** → o `agent_type_id` do registro, que aí é identidade legítima (uma
 *     instância de IA pertence a um agent type e a um pool).
 *
 * Devolve `null` quando não dá para resolver — nunca um valor plausível inventado.
 */
export async function resolveAgentTypeForSession(
  redis:      RedisClient,
  tenantId:   string,
  sessionId:  string,
  instanceId: string,
  logPrefix = "[routing-ref]",
): Promise<string | null> {
  if (!instanceId) return null

  if (instanceId.startsWith(HUMAN_INSTANCE_PREFIX)) {
    const pool = await readRoutingRefPool(redis, sessionId, instanceId, logPrefix)
    return pool ? `human_agent_${pool}` : null
  }

  try {
    const raw = await redis.get(`${tenantId}:instance:${instanceId}`)
    if (!raw) {
      console.warn(`${logPrefix} instância ausente: ${tenantId}:instance:${instanceId}`)
      return null
    }
    const inst = JSON.parse(raw) as Record<string, unknown>
    const type = typeof inst["agent_type_id"] === "string" ? (inst["agent_type_id"] as string) : ""
    return type || null
  } catch (err) {
    console.warn(
      `${logPrefix} leitura de agent_type_id falhou: instance=${instanceId} — ${String(err)}`
    )
    return null
  }
}
