/**
 * lib/context-map.ts — o MAPA do ContextStore em runtime (V3 do arco ALLOWLIST).
 *
 * ── O que esta casa faz, e o que ela deliberadamente NÃO faz ─────────────────
 *
 * Faz três coisas, e as três são MEDIÇÃO:
 *
 *   1. resolve a tag observada contra o mapa, **na borda** (D3.1) — canônica,
 *      alias, dinâmica ou desconhecida;
 *   2. conta cada resolução em PAR (alias × canônica) e a DATA (D3.3);
 *   3. registra a população não-declarada, que é a lista com que a V4 decide.
 *
 * **Não esconde nada, não recusa nada, não altera nenhuma saída.** O `mode` do
 * mapa tem um valor só (`audit`) e não existe config capaz de ligar imposição
 * antes de a V4 escrever o código que a honra.
 *
 * ── Por que a falha aqui degrada ABERTO, contra a regra da casa ──────────────
 *
 * O `CLAUDE.md` manda masking recusar alto: em mascaramento, fallback mudo não é
 * opção. Esta casa é a exceção, e a exceção é medida, não conveniente: **ela não
 * toma decisão de política**. Se o mapa não carrega, a auditoria deixa de contar —
 * ninguém fica mais exposto do que já estava, porque a máscara continua sendo
 * decidida por `resolveContextMaskingRule`, noutra casa.
 *
 * Recusar alto aqui derrubaria a aba Contexto do Console por falha de um contador.
 * Trocar exposição por indisponibilidade seria pagar o preço da V4 sem receber a
 * proteção dela. A degradação, porém, **nunca é silenciosa**: loga nomeando o que
 * deixa de valer, que é o que faltou ao `"using default values"` que ninguém leu
 * por meses.
 *
 * ── O que a auditoria NÃO enxerga (limitação declarada) ──────────────────────
 *
 * A observação acontece na LEITURA, nas duas portas humanas. Um campo escrito por
 * um dos 12 `HSET` diretos (§1.7 do ADR) e **nunca lido** é invisível aqui.
 *
 * Isso é benigno para a decisão da V4, e vale dizer por quê em vez de deixar como
 * ressalva vaga: o que a V4 inverte é a política **R-humano** (D5) — o que o
 * operador vê. Um campo que ninguém lê por essa porta não perde nada ao deixar de
 * ser acessível por ela. A auditoria observa exatamente a população sobre a qual a
 * inversão age.
 */

import {
  ContextMapSchema,
  DEFAULT_CONTEXT_MAP,
  buildContextTagIndex,
  resolveContextTag,
  type ContextMap,
  type ContextTagIndex,
} from "@plughub/schemas"
import { createRedisClient, type RedisClient } from "../infra/redis"

const CONFIG_API_URL = process.env["CONFIG_API_URL"] ?? "http://localhost:3600"

// ── cache do mapa (mesmo TTL e mesmo motivo do de `context-masking.ts`) ───────
const CONTEXT_MAP_CACHE_TTL_MS = 60_000
interface CachedMap { map: ContextMap; index: ContextTagIndex; expiresAt: number }
const contextMapCache = new Map<string, CachedMap>()

export function invalidateContextMapCache(tenantId: string): void {
  contextMapCache.delete(tenantId)
}

/**
 * Carrega o mapa do config-api (`masking.context_map`), com cache de 60 s.
 *
 * Um valor que não passa no `ContextMapSchema` é DESCARTADO e o default embutido
 * assume — com log. É o mesmo desenho da guarda de runtime da T3 do `masked`
 * tipado: config malformada não pode virar comportamento novo, e `mode` fora do
 * enum (o caso que importa: alguém escrevendo `"enforce"` à mão) cai aqui.
 */
export async function getContextMap(tenantId: string): Promise<{ map: ContextMap; index: ContextTagIndex }> {
  const cached = contextMapCache.get(tenantId)
  if (cached && cached.expiresAt > Date.now()) return { map: cached.map, index: cached.index }

  let map: ContextMap = DEFAULT_CONTEXT_MAP
  try {
    const base = CONFIG_API_URL.replace(/\/$/, "")
    const url  = `${base}/config/masking?tenant_id=${encodeURIComponent(tenantId)}`
    const resp = await fetch(url, { signal: AbortSignal.timeout(5000) })
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const body     = await resp.json() as Record<string, unknown>
    const entries  = (body["entries"] ?? body) as Record<string, unknown>
    const rawEntry = entries["context_map"]
    const value    = (rawEntry && typeof rawEntry === "object" && "value" in (rawEntry as object))
      ? (rawEntry as { value: unknown }).value
      : rawEntry
    if (value == null) throw new Error("chave masking.context_map ausente")
    const parsed = ContextMapSchema.safeParse(value)
    if (!parsed.success) throw new Error(`mapa inválido: ${parsed.error.issues.map(i => i.path.join(".")).join(", ")}`)
    map = parsed.data
  } catch (err) {
    console.warn(
      `[context-map] tenant=${tenantId} usando o mapa EMBUTIDO (${String(err)}). ` +
      `Deixa de valer: a auditoria do ContextStore mede contra o default do código, ` +
      `não contra o mapa do tenant — aliases e campos declarados SÓ na config não ` +
      `são reconhecidos e aparecem como não-declarados. Nenhuma máscara muda.`,
    )
  }

  const index = buildContextTagIndex(map)
  contextMapCache.set(tenantId, { map, index, expiresAt: Date.now() + CONTEXT_MAP_CACHE_TTL_MS })
  return { map, index }
}

// ── classificação (pura) ──────────────────────────────────────────────────────

export interface ContextTagClassification {
  /** grafia legada → canônica. A chave é a GRAFIA, para se saber qual matar. */
  alias:     Record<string, string>
  /** canônicas observadas já na forma nova. */
  canonical: string[]
  /** `agent.*`/`segment.*` — não declaráveis; terceiro balde, nunca "desconhecida". */
  dynamic:   string[]
  /** A população que autoriza (ou barra) a V4. */
  unknown:   string[]
}

/**
 * Classifica um conjunto de tags. Pura e sem I/O — testável sem Redis, e é ela
 * que o gate exercita.
 */
export function classifyContextTags(tags: string[], index: ContextTagIndex): ContextTagClassification {
  const out: ContextTagClassification = { alias: {}, canonical: [], dynamic: [], unknown: [] }
  for (const tag of tags) {
    const r = resolveContextTag(tag, index)
    switch (r.origin) {
      case "alias":     out.alias[tag] = r.canonical; break
      case "canonical": out.canonical.push(tag);      break
      case "dynamic":   out.dynamic.push(tag);        break
      case "unknown":   out.unknown.push(tag);        break
    }
  }
  return out
}

// ── registro (I/O, fire-and-forget) ───────────────────────────────────────────

/**
 * Teto de grafias DISTINTAS registradas por balde.
 *
 * Existe porque `unknown` é alimentado por campo autorado pelo tenant
 * (`delegate.context` deposita o que quiser em `session.*`), logo é ilimitado por
 * construção. Estourar o teto NÃO é silencioso: incrementa `__overflow__`, que a
 * leitura publica ao lado — truncar sem dizer faria a lista parecer completa, que
 * é justamente o que a V4 não pode acreditar.
 */
const MAX_DISTINCT_PER_BUCKET = 500

/**
 * ⚠️ **`{t}:ctx_audit:*`, e NÃO `{t}:ctx:audit:*`.** A primeira grafia colide com o
 * namespace das SESSÕES: `{t}:ctx:{sessionId}` é o hash de contexto, e todo scanner
 * de `*:ctx:*` passa a devolver `audit:counts` e `audit:seen` como se fossem ids de
 * sessão. Não é hipótese — foi medido: os probes da V1 e da V1b, que listam as
 * sessões com ContextStore vivo, passaram a exibir as duas chaves no meio dos UUIDs.
 *
 * `journey:` e `customer:` moram sob `:ctx:` legitimamente, porque são ESCOPOS do
 * próprio contexto. A auditoria não é contexto — é metadado SOBRE o contexto, e não
 * herda esse endereço.
 */
export const contextAuditKeys = {
  counts: (t: string) => `${t}:ctx_audit:counts`,
  seen:   (t: string) => `${t}:ctx_audit:seen`,
}

let lazyRedis: RedisClient | null = null
function auditRedis(): RedisClient {
  if (!lazyRedis) lazyRedis = createRedisClient()
  return lazyRedis
}

/**
 * Persiste a classificação. **Nunca lança** — o chamador está no caminho de uma
 * leitura de tela.
 *
 * Contadores em PAR (ADR §7): `alias:*` e `canon:*` vivem no mesmo hash e são
 * lidos juntos. Só o primeiro não distingue *"ninguém migrou"* de *"ninguém usa"* —
 * e, no estado de hoje, é exatamente o par que dá a resposta: as tags que o
 * routing-engine escreve (`session.pool.*`, `session.queue.*`) já nascem canônicas,
 * então `canon` > 0 enquanto `alias` > 0 significa migração PARCIAL, não ausência
 * de uso.
 *
 * ⚠️ O número conta **observações de leitura**, não escritas: o mesmo hash lido
 * duas vezes conta duas. Serve para responder *"esta grafia ainda aparece?"*
 * (e o `last_seen` responde *"quando?"*), que é o que a D3 pede para decidir a
 * remoção. Não serve para dimensionar volume de escrita, e não deve ser usado
 * para isso.
 */
export async function recordContextTagAudit(
  tenantId: string,
  cls:      ContextTagClassification,
  redis?:   RedisClient,
): Promise<void> {
  try {
    const r    = redis ?? auditRedis()
    const now  = new Date().toISOString()
    const cKey = contextAuditKeys.counts(tenantId)
    const sKey = contextAuditKeys.seen(tenantId)

    const bump: Array<[string, string]> = []
    for (const legado of Object.keys(cls.alias)) bump.push([`alias:${legado}`, now])
    for (const canon of cls.canonical)           bump.push([`canon:${canon}`,  now])
    for (const tag of cls.unknown)               bump.push([`unknown:${tag}`,  now])
    if (cls.dynamic.length > 0)                  bump.push([`dynamic:__all__`, now])
    if (bump.length === 0) return

    // O teto se aplica a grafias NOVAS: uma já registrada continua contando.
    const known = new Set(await r.hkeys(cKey))
    const pipe  = r.pipeline()
    let overflow = 0
    for (const [field, ts] of bump) {
      if (!known.has(field) && known.size >= MAX_DISTINCT_PER_BUCKET) { overflow++; continue }
      known.add(field)
      pipe.hincrby(cKey, field, 1)
      pipe.hset(sKey, field, ts)
    }
    if (overflow > 0) pipe.hincrby(cKey, "__overflow__", overflow)
    await pipe.exec()
  } catch (err) {
    console.warn(
      `[context-map] tenant=${tenantId} auditoria NÃO registrada (${String(err)}). ` +
      `Deixa de valer: o par alias × canônica e a lista de campos não-declarados ` +
      `ficam desatualizados para esta leitura — a V4 não deve ser autorizada por ` +
      `uma janela que contém este erro. Nenhuma máscara muda.`,
    )
  }
}

/**
 * Observa um hash de ContextStore em modo AUDITORIA. Devolve a classificação (para
 * quem quiser exibi-la) e registra em segundo plano.
 *
 * Chamada pelas DUAS portas humanas — `applyContextMaskingDynamic` (endpoint da
 * Console) e `maskContextForPersistence` (tool MCP + snapshot durável). É o mesmo
 * par que a V1b unificou, e pelo mesmo motivo: instrumentar uma só deixaria a
 * outra medindo nada, sem nada ficar vermelho.
 */
export async function observeContextTags(
  rawHash:  Record<string, string>,
  tenantId: string,
  redis?:   RedisClient,
): Promise<ContextTagClassification> {
  const { index } = await getContextMap(tenantId)
  const cls = classifyContextTags(Object.keys(rawHash), index)
  void recordContextTagAudit(tenantId, cls, redis)
  return cls
}

// ── leitura da auditoria ──────────────────────────────────────────────────────

export interface ContextAuditReport {
  /** Testemunha de PRESENÇA — sem ela, "zero não-declarados" é serviço parado. */
  declared_in_map: number
  aliases_in_map:  number
  /** O PAR do ADR §7. */
  alias:     Array<{ tag: string; canonical: string | null; count: number; last_seen: string | null }>
  canonical: Array<{ tag: string; count: number; last_seen: string | null }>
  unknown:   Array<{ tag: string; count: number; last_seen: string | null }>
  dynamic_observations: number
  /** Grafias distintas descartadas pelo teto. `> 0` ⇒ as listas estão INCOMPLETAS. */
  overflow:  number
}

export async function readContextAudit(
  tenantId: string,
  redis?:   RedisClient,
): Promise<ContextAuditReport> {
  const r     = redis ?? auditRedis()
  const [counts, seen] = await Promise.all([
    r.hgetall(contextAuditKeys.counts(tenantId)),
    r.hgetall(contextAuditKeys.seen(tenantId)),
  ])
  const { index } = await getContextMap(tenantId)

  const pick = (prefix: string) => Object.entries(counts)
    .filter(([f]) => f.startsWith(prefix))
    .map(([f, c]) => ({ tag: f.slice(prefix.length), count: Number(c) || 0, last_seen: seen[f] ?? null }))
    .sort((a, b) => b.count - a.count)

  return {
    declared_in_map: index.canonical.size,
    aliases_in_map:  index.alias.size,
    alias:     pick("alias:").map(e => ({ ...e, canonical: index.alias.get(e.tag) ?? null })),
    canonical: pick("canon:"),
    unknown:   pick("unknown:"),
    dynamic_observations: Number(counts["dynamic:__all__"]) || 0,
    overflow:             Number(counts["__overflow__"])    || 0,
  }
}
