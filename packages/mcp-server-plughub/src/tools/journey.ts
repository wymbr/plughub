/**
 * tools/journey.ts
 * Journey J3/J5 — merge de journeys por raiz.
 *
 * Tool:
 *   journey_merge — une duas journeys gravando uma aresta de alias entre as RAÍZES DAS
 *   COMPONENTES. Publica em journey.merges (a analytics-api persiste em journey_aliases e
 *   resolve por union-find na leitura) e mantém um espelho do mapa no Redis, que é o que
 *   permite ao produtor garantir o invariante — e ao bridge resolver a raiz canônica para
 *   o `@ctx.journey.*`.
 *
 * ─── Invariante: ACÍCLICO POR CONSTRUÇÃO (J5a-2) ─────────────────────────────────────
 *
 * A v1 tentava garantir a floresta ordenando as arestas por IDADE (novo→antigo) e
 * confiando no chamador quando os timestamps não resolviam. Duas falhas:
 *
 *   1. A idade vinha de `session:{root}:meta.started_at` — campo que o adapter **webchat**
 *      escreve e o **webhook** NÃO. Como as raízes de journey são justamente sessões
 *      webhook (o processo nasce de um trigger), o swap NUNCA acontecia: o chamador sempre
 *      decidia o sobrevivente. A regra existia no código e não rodava.
 *   2. Mesmo com a idade, "ordenar por timestamp" só evita ciclo se TODA aresta for
 *      ordenada — um único caso sem timestamp (ou dois relógios em desacordo) reabre o
 *      ciclo. O guard de ciclo vivia só na LEITURA (union-find da analytics), isto é:
 *      tolerava o dado inconsistente em vez de impedi-lo.
 *
 * Agora a aciclicidade é ESTRUTURAL e não depende de relógio nenhum:
 *
 *   rs = find(source)   ·   rc = find(canonical)      (raízes das componentes)
 *   rs === rc  →  no-op (já são a mesma journey)
 *   rs !== rc  →  aresta rs → rc
 *
 * Como `rs` era raiz (não tinha aresta de saída) e as componentes eram **disjuntas**, a
 * cadeia de `rc` não passa por `rs`. Logo a aresta não pode fechar ciclo. É união de
 * conjuntos disjuntos — o argumento clássico do union-find, aplicado na ESCRITA.
 *
 * A idade volta ao seu lugar: **política**, não invariante. Ela só escolhe QUAL das duas
 * raízes sobrevive (a mais antiga identifica melhor o processo); se não resolver, a
 * designação do chamador vale — e continua sendo acíclico de qualquer jeito.
 *
 * Fonte de idade (J5a-2): o **stream canônico** `session:{id}:stream`, cujo ID de entrada
 * do Redis Stream É o timestamp em ms. Existe para TODA sessão, em todo canal — ao
 * contrário do `meta.started_at`. O meta fica como fallback.
 *
 * Demais invariantes (inalterados):
 *   - NUNCA reescreve `root_session_id` das sessões — só grava a aresta.
 *   - Quem pode comandar é gateado pelas permissões do JWT (McpInterceptor). Auditado.
 *
 * (Reintroduz o nome journey_merge removido no Arc 19 Fase F, agora SEM entidade Journey —
 *  é apenas uma aresta de proveniência/alias.)
 */
import { z }             from "zod"
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import { JourneyMergedEventSchema } from "@plughub/schemas"
import type { RedisClient }   from "../infra/redis"
import type { KafkaProducer } from "../infra/kafka"
import { verifySessionToken, InvalidTokenError } from "../infra/jwt"

export interface JourneyDeps {
  redis: RedisClient
  kafka: KafkaProducer
}

const JourneyMergeInputSchema = z.object({
  /** JWT from agent_login — resolves tenant_id + actor. */
  session_token:  z.string().min(1),
  /** Raiz da journey a ser absorvida (por default, a mais nova). */
  source_root:    z.string().min(1),
  /** Raiz sobrevivente (por default, a mais antiga). */
  canonical_root: z.string().min(1),
})

type ToolResult = { isError?: true; content: Array<{ type: "text"; text: string }> }
function ok(data: unknown): ToolResult {
  return { content: [{ type: "text" as const, text: JSON.stringify(data) }] }
}
function mcpError(code: string, message: string): ToolResult {
  return { isError: true, content: [{ type: "text" as const, text: JSON.stringify({ error: code, message }) }] }
}

/** Espelho do mapa de aliases no Redis: field = raiz absorvida → value = raiz canônica. */
export function aliasKey(tenantId: string): string {
  return `${tenantId}:journey:aliases`
}

/** Hash do contexto compartilhado do processo (`@ctx.journey.*`). */
export function journeyCtxKey(tenantId: string, root: string): string {
  return `${tenantId}:ctx:journey:${root}`
}

/**
 * Teto de saltos no `find`. Numa floresta com path compression a cadeia é curta; um teto
 * existe só para que DADO CORROMPIDO (um ciclo que tenha entrado por outra via) não vire
 * loop infinito no caminho quente. Ultrapassar o teto é bug, não regime normal.
 */
const MAX_ALIAS_DEPTH = 32

/**
 * Raiz da componente de `root` (union-find `find`, com path compression).
 *
 * Exportado porque é a MESMA resolução que o bridge precisa para alimentar
 * `@ctx.journey.*` com a raiz canônica (J5a-1) — a definição de "qual journey é esta"
 * tem de ser uma só, ou o contexto compartilhado se parte no merge.
 */
export async function resolveJourneyRoot(
  redis:    RedisClient,
  tenantId: string,
  root:     string,
): Promise<string> {
  const key  = aliasKey(tenantId)
  const path: string[] = []
  let cur = root

  for (let i = 0; i < MAX_ALIAS_DEPTH; i++) {
    const next = await redis.hget(key, cur)
    if (!next || next === cur) break        // raiz da componente (ou self-edge defensivo)
    path.push(cur)
    cur = next
  }

  // Path compression: aponta todo mundo do caminho direto para a raiz. Mantém o `find`
  // O(1) amortizado e encurta a cadeia para o próximo leitor (o bridge, no caminho quente).
  if (path.length > 1) {
    const pipe = redis.pipeline()
    for (const node of path) {
      if (node !== cur) pipe.hset(key, node, cur)
    }
    await pipe.exec()
  }
  return cur
}

/**
 * Idade (ms epoch) de uma raiz. Fonte primária: o ID da PRIMEIRA entrada do stream
 * canônico (`session:{id}:stream`), que no Redis Streams é o timestamp em ms.
 *
 * Por que não o `meta`: `session:{id}:meta.started_at` só é escrito pelo adapter webchat.
 * Sessões webhook — que são exatamente as raízes de journey — não têm o campo, então a
 * ordenação por idade nunca disparava. O stream é invariante de plataforma: toda sessão
 * tem um. O meta fica como fallback para sessões sem stream (não deveria haver).
 */
async function readStartedAt(redis: RedisClient, root: string): Promise<number | null> {
  try {
    const entries = await redis.xrange(`session:${root}:stream`, "-", "+", "COUNT", 1)
    const id = entries?.[0]?.[0]
    if (typeof id === "string") {
      const ms = Number.parseInt(id.split("-")[0] ?? "", 10)
      if (Number.isFinite(ms) && ms > 0) return ms
    }
  } catch { /* cai no fallback */ }

  try {
    const raw = await redis.get(`session:${root}:meta`)
    if (!raw) return null
    const meta = JSON.parse(raw) as Record<string, unknown>
    for (const k of ["started_at", "opened_at", "created_at"]) {
      const v = meta[k]
      if (typeof v === "string") {
        const t = Date.parse(v)
        if (!Number.isNaN(t)) return t
      }
    }
  } catch { /* ignore */ }
  return null
}

/**
 * Migra o contexto compartilhado da componente absorvida para a canônica.
 *
 * Sem isto, o merge PARTIRIA o `@ctx.journey.*` exatamente no momento em que as journeys
 * se unem: os agentes passariam a ler o hash da raiz canônica, e tudo que a componente
 * absorvida acumulou ficaria órfão. Merge sem migração é união no relatório e divórcio no
 * contexto.
 *
 * Semântica: a canônica VENCE em colisão de tag (é a journey que sobrevive). Best-effort —
 * uma falha aqui não invalida o merge (que já foi publicado); só empobrece o contexto.
 */
async function migrateJourneyContext(
  redis:    RedisClient,
  tenantId: string,
  fromRoot: string,
  toRoot:   string,
): Promise<number> {
  try {
    const src = journeyCtxKey(tenantId, fromRoot)
    const dst = journeyCtxKey(tenantId, toRoot)
    const entries = await redis.hgetall(src)
    const tags = Object.keys(entries ?? {})
    if (tags.length === 0) return 0

    const existing = await redis.hgetall(dst)
    const toCopy: Record<string, string> = {}
    for (const [tag, value] of Object.entries(entries)) {
      if (existing?.[tag] === undefined) toCopy[tag] = value   // canônica vence
    }
    if (Object.keys(toCopy).length > 0) {
      await redis.hset(dst, toCopy)
      // TTL longo — o contexto do processo vive além da sessão (ver LONG_TTL_PREFIXES).
      await redis.expire(dst, 30 * 24 * 3600)
    }
    await redis.del(src)
    return Object.keys(toCopy).length
  } catch {
    return 0
  }
}

export function registerJourneyTools(server: McpServer, deps: JourneyDeps): void {
  const { redis, kafka } = deps

  server.tool(
    "journey_merge",
    "Journey — merge two journeys by root. Unites the two COMPONENTS: resolves each root to " +
    "its component root and records an alias edge between them (acyclic by construction). " +
    "Publishes journey.merges; never rewrites root_session_id. Survivor defaults to the OLDER " +
    "root when both ages resolve; otherwise the caller's designation stands. Idempotent: " +
    "merging two roots already in the same journey is a no-op.",
    JourneyMergeInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      try {
        const { session_token, source_root, canonical_root } = JourneyMergeInputSchema.parse(input)

        let tenant_id: string
        let actor:     string
        try {
          const payload = verifySessionToken(session_token)
          tenant_id = payload.tenant_id
          actor     = payload.instance_id || payload.agent_type_id || "agent"
        } catch (e) {
          if (e instanceof InvalidTokenError) {
            return mcpError("invalid_token", "session_token is invalid or expired")
          }
          throw e
        }

        if (source_root === canonical_root) {
          return mcpError("self_merge", "source_root and canonical_root are the same — nothing to merge")
        }

        // ── 1. Resolve cada raiz até a RAIZ DA SUA COMPONENTE ────────────────────────
        // A aresta tem de ligar raízes, não nós internos: é isso que torna o merge
        // acíclico por construção (união de componentes disjuntas).
        const [rootA, rootB] = await Promise.all([
          resolveJourneyRoot(redis, tenant_id, source_root),
          resolveJourneyRoot(redis, tenant_id, canonical_root),
        ])

        // ── 2. Já são a mesma journey? No-op idempotente ─────────────────────────────
        if (rootA === rootB) {
          return ok({
            merged:         false,
            reason:         "already_same_journey",
            canonical_root: rootA,
          })
        }

        // ── 3. Sobrevivente = a mais ANTIGA (política, best-effort) ──────────────────
        // Se as idades não resolverem, mantém a designação do chamador. Em qualquer
        // caso a aresta liga raiz→raiz, então a aciclicidade não depende disto.
        let src   = rootA   // absorvida
        let canon = rootB   // sobrevivente
        const [tsA, tsB] = await Promise.all([
          readStartedAt(redis, rootA),
          readStartedAt(redis, rootB),
        ])
        if (tsA !== null && tsB !== null) {
          const aIsOlder = tsA < tsB || (tsA === tsB && rootA < rootB)
          if (aIsOlder) { src = rootB; canon = rootA }
        }

        const event = {
          event_id:       crypto.randomUUID(),
          tenant_id,
          source_root:    src,
          canonical_root: canon,
          merged_at:      new Date().toISOString(),
          actor,
        }
        const parsed = JourneyMergedEventSchema.safeParse(event)
        if (!parsed.success) {
          return mcpError("validation_error", parsed.error.errors.map(e => e.message).join("; "))
        }

        // ── 4. Espelho no Redis ANTES do Kafka ───────────────────────────────────────
        // O mapa do Redis é o que o PRÓPRIO produtor consulta no próximo merge (para
        // manter a floresta) e o que o bridge lê para resolver a raiz canônica do
        // `@ctx.journey.*`. Se falhar aqui, o merge não acontece — publicar no Kafka com
        // o espelho fora de sincronia deixaria o produtor cego para a aresta que ele
        // mesmo acabou de criar, e o próximo merge poderia formar o ciclo que este
        // desenho existe para impedir.
        try {
          await redis.hset(aliasKey(tenant_id), src, canon)
        } catch (redisErr) {
          return mcpError("alias_write_failed", `Failed to record alias edge: ${String(redisErr)}`)
        }

        try {
          await kafka.publish("journey.merges", parsed.data)
        } catch (kafkaErr) {
          // Rollback do espelho: sem o evento, a analytics nunca saberá da aresta, e um
          // espelho que sabe do que o relatório não sabe é pior que nenhum.
          try { await redis.hdel(aliasKey(tenant_id), src) } catch { /* best-effort */ }
          return mcpError("publish_failed", `Failed to publish journey.merges: ${String(kafkaErr)}`)
        }

        // ── 5. Contexto compartilhado segue a journey sobrevivente ───────────────────
        const migrated = await migrateJourneyContext(redis, tenant_id, src, canon)

        return ok({
          merged:            true,
          source_root:       src,
          canonical_root:    canon,
          merged_at:         event.merged_at,
          context_tags_moved: migrated,
        })
      } catch (e) {
        if (e instanceof z.ZodError) {
          return mcpError("validation_error", e.errors.map(er => er.message).join("; "))
        }
        if (e instanceof InvalidTokenError) {
          return mcpError("invalid_token", "session_token is invalid or expired")
        }
        return mcpError("internal_error", String(e))
      }
    },
  )
}
