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
import { JourneyMergedEventSchema, stampContextEntry, type ContextEntryStamp } from "@plughub/schemas"
import { getContextMap } from "../lib/context-map"
import type { RedisClient }   from "../infra/redis"
import type { KafkaProducer } from "../infra/kafka"
import { verifySessionToken, InvalidTokenError } from "../infra/jwt"

export interface JourneyDeps {
  redis: RedisClient
  kafka: KafkaProducer
}

const JourneyMergeInputSchema = z.object({
  /**
   * JWT from agent_login — resolves tenant_id + actor. This is the path for callers that
   * already HAVE a minted JWT (Console operators, evaluator/reviewer agents explicitly
   * bootstrapped with one — see agente_revisor_v1.yaml header). Optional now: one of
   * `session_token` / `tenant_id` is required, checked manually in the handler below.
   */
  session_token:  z.string().min(1).optional(),
  /**
   * Plain tenant id — alternative auth path for callers with NO session_token, which is
   * every ORDINARY native-agent skill-flow invoke step (customer-facing pools like
   * skill_limite_entrada_v1: channel-gateway → routing-engine → orchestrator-bridge →
   * skill-flow-service `/execute` never mints or forwards a JWT into the pipeline
   * context — only workflows that explicitly inject one, like skill_revisao_treplica_v1,
   * do). Same calling convention as every OTHER tool this kind of skill already uses
   * (customer_resolve, pending_workflow_get, context_set, workflow_trigger all take
   * `tenant_id` as plain data). Authorization for these callers is already enforced
   * upstream by McpInterceptor (in-process for native agents) — re-deriving it from a
   * session_token here would mean minting a JWT for every conversational skill that has
   * no other use for one, just to satisfy this one tool.
   */
  tenant_id:      z.string().min(1).optional(),
  /** Free-form actor label for the `tenant_id` auth path (audit trail only — the
   *  `session_token` path derives this from the JWT instead). Defaults to "skill_flow". */
  actor:          z.string().min(1).optional(),
  /** Raiz da journey a ser absorvida (por default, a mais nova). */
  source_root:    z.string().min(1),
  /** Raiz sobrevivente (por default, a mais antiga). */
  canonical_root: z.string().min(1),
})
// NOTE: no `.refine()` here on purpose — `JourneyMergeInputSchema.shape` (below, in
// registerJourneyTools) needs a plain ZodRawShape for the MCP SDK's `server.tool()`
// call, and `.refine()` would turn this into a ZodEffects wrapper that has no `.shape`.
// The "session_token OR tenant_id" invariant is checked manually in the handler instead
// (same style already used there for the self_merge check).

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
 * Metade RUNTIME da D9.1: o publish RECUSA, o runtime **nunca rejeita** — grava, resolve
 * para o mais restritivo e **LOGA nomeando**. Sem esta metade a primeira troca um
 * vazamento por uma queda, e a § Postura de Engenharia já cataloga o padrão.
 *
 * ── `unknown` e `fallback` são fatos DIFERENTES, e a mensagem os separa ──────────
 *
 * `origem: "unknown"` diz *"esta tag não está no mapa"*. Mas se o mapa em uso é o
 * **fallback embutido** — porque o config-api não respondeu —, `unknown` deixa de ser
 * evidência de cadastro faltando e passa a ser evidência de que o mapa não carregou: a
 * tag pode estar perfeitamente registrada no mapa vivo.
 *
 * Emitir as duas na mesma frase manda alguém cadastrar o que já existe. Medido em
 * 2026-09-02 nessa forma exata: o censo lia o mapa SEMENTE e publicava 18 não-declaradas
 * onde a resposta era 2 — 16 nomes já cadastrados enviados de volta para a fila.
 *
 * `dynamic` fica FORA de propósito: `agent.*`/`segment.*` são famílias declaradas como
 * abertas, e avisar sobre elas ensinaria a ignorar o aviso.
 */
function warnUnregisteredTag(
  tenantId:  string,
  sessionId: string,
  tag:       string,
  atributo:  ContextEntryStamp,
  fallback:  boolean,
): void {
  if (atributo.origem !== "unknown") return
  if (fallback) {
    console.warn(
      `[ctx-writer] tenant=${tenantId} session=${sessionId}: tag "${tag}" sem ` +
      `correspondência no mapa, MAS o mapa em uso é o FALLBACK EMBUTIDO — o config-api ` +
      `não respondeu. NÃO conclua que falta cadastro: ela pode estar no mapa vivo. ` +
      `Conserte o transporte antes de cadastrar qualquer coisa.`,
    )
    return
  }
  console.warn(
    `[ctx-writer] tenant=${tenantId} session=${sessionId}: tag "${tag}" NÃO CADASTRADA ` +
    `no mapa do ContextStore, gravada mesmo assim (o runtime nunca rejeita — D9.1). Ela ` +
    `resolve para o mais RESTRITIVO na leitura, então um operador legítimo pode não ver o ` +
    `valor. Cadastre em /config/context-map.`,
  )
}

/**
 * J5a + D9.6 — o CHOKE POINT de escrita de UMA tag de contexto.
 *
 * Faz DUAS coisas, e a segunda chegou na ALW-02 (2026-09-02):
 *
 *   1. **Roteia** — uma tag `journey.*` não pertence ao hash da sessão: pertence ao
 *      PROCESSO, que vive além dela (TTL 30d) e é compartilhado por N contatos da mesma
 *      journey. Cair no hash da sessão a faria evaporar em 4 h sem erro nenhum.
 *   2. **Carimba o `atributo`** a partir do cadastro (`masking.context_map`). O escritor
 *      não declara tipo — não tem o que errar —, e o dado guardado fica autodescritivo
 *      para o snapshot durável (F5) e para export LGPD.
 *
 * ⚠️ **A assinatura recebe o OBJETO, nunca o JSON já serializado.** Foi assim que o
 * carimbo deixou de ser evitável: com `entryJson: string` qualquer chamador podia
 * serializar por fora e passar ao largo, e o furo seria mudo — que é exatamente o que a
 * D9.6 avisa. Remover a alternativa custa menos que lembrar de não usá-la.
 *
 * Resolve a raiz canônica pela MESMA via que o bridge e o `journey_merge`: raiz de
 * proveniência = `core.contact.root_session_id` do ctx (o que o bridge carimba; fallback =
 * a própria sessão) → `resolveJourneyRoot` (find da componente na floresta de aliases).
 *
 * Degradação do carimbo é BOUNDED e barulhenta: `getContextMap` nunca levanta — cai no
 * mapa embutido, loga o que deixa de valer, e o carimbo sai com `fallback: true`. A
 * escrita acontece de qualquer jeito; recusá-la deixaria o ContextStore refém do
 * config-api, o que é uma troca pior que carimbar com o mapa do código.
 */
export async function writeContextTag(
  redis:     RedisClient,
  tenantId:  string,
  sessionId: string,
  tag:       string,
  entry:     Record<string, unknown>,
): Promise<{ scope: "journey" | "session"; journeyRoot?: string; atributo: ContextEntryStamp }> {
  const { index, fallback } = await getContextMap(tenantId)
  const stamped   = stampContextEntry(entry, tag, index, fallback)
  const entryJson = JSON.stringify(stamped)
  const atributo  = stamped["atributo"] as ContextEntryStamp
  warnUnregisteredTag(tenantId, sessionId, tag, atributo, fallback)
  // CNS-03 — `core.journey.*` roteia igual a `journey.*`: o escopo de uma tag do core é
  // o SEGUNDO segmento. Terceira das três casas que roteiam por prefixo; as outras duas
  // são `sdk/context-store.ts` (TTL + chave) e `skill-flow-engine/interpolate.ts` (leitura).
  if (tag.startsWith("journey.") || tag.startsWith("core.journey.")) {
    let provenanceRoot = sessionId
    try {
      const raw = await redis.hget(`${tenantId}:ctx:${sessionId}`, "core.contact.root_session_id")
      if (raw) {
        const parsed = JSON.parse(raw)
        if (parsed?.value) provenanceRoot = String(parsed.value)
      }
    } catch { /* fallback: raiz de proveniência = a própria sessão */ }

    const canonicalRoot = await resolveJourneyRoot(redis, tenantId, provenanceRoot)
    const key = journeyCtxKey(tenantId, canonicalRoot)
    await redis.hset(key, tag, entryJson)
    // TTL do processo (30d) — igual ao migrateJourneyContext do merge.
    await redis.expire(key, 30 * 24 * 3600)
    return { scope: "journey", journeyRoot: canonicalRoot, atributo }
  }

  await redis.hset(`${tenantId}:ctx:${sessionId}`, tag, entryJson)
  return { scope: "session", atributo }
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
    "merging two roots already in the same journey is a no-op. Auth: pass EITHER " +
    "session_token (JWT, for callers that already have one) OR tenant_id (+ optional actor " +
    "label) — the latter is for ordinary native-agent skill-flow invoke steps, which never " +
    "receive a JWT in their pipeline context.",
    JourneyMergeInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      try {
        const { session_token, tenant_id: tenantIdInput, actor: actorInput, source_root, canonical_root } =
          JourneyMergeInputSchema.parse(input)

        let tenant_id: string
        let actor:     string
        if (session_token) {
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
        } else if (tenantIdInput) {
          // Plain tenant_id path (see schema comment above) — auth already enforced
          // upstream by McpInterceptor for native-agent invoke steps.
          tenant_id = tenantIdInput
          actor     = actorInput || "skill_flow"
        } else {
          return mcpError("missing_auth", "either session_token or tenant_id is required")
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
