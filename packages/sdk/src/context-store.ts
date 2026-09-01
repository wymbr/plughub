/**
 * context-store.ts
 * ContextStore — camada única de persistência do estado observável de um contato.
 *
 * Dois hashes Redis por contato:
 *   {t}:ctx:{sessionId}             → TTL de sessão (padrão 4h)
 *                                     namespaces: caller, account, session, sla, queue,
 *                                                 insight.conversa, workflow
 *   {t}:ctx:customer:{customerId}   → TTL longo (padrão 90d)
 *                                     namespaces: insight.historico, pricing
 *
 * Uso via SDK direto (fontes não-MCP: AI Gateway, Routing Engine, Rules Engine):
 *
 *   const store = new ContextStore({ redis, tenantId: "tenant_demo" })
 *   await store.set(sessionId, "session.sentimento.current", {
 *     value: -0.4, confidence: 1.0, source: "ai:sentiment_emitter"
 *   })
 *   const entry = await store.get(sessionId, "session.sentimento.current")
 *   const snapshot = await store.getByPrefix(sessionId, ["session", "caller"])
 */

/**
 * Subset da interface Redis necessário pelo ContextStore.
 * Duck-typed para evitar dependência hard em ioredis.
 */
export interface RedisClient {
  hget(key: string, field: string): Promise<string | null>
  hset(key: string, field: string, value: string): Promise<unknown>
  hgetall(key: string): Promise<Record<string, string> | null>
  hdel(key: string, ...fields: string[]): Promise<unknown>
  expire(key: string, seconds: number): Promise<unknown>
  del(key: string): Promise<unknown>
}

import type {
  ContextEntry,
  ContextSnapshot,
  SkillRequiredContext,
  ContextGapsReport,
  ContextMergeStrategy,
} from "@plughub/schemas"
import { resolveContextStore, CONTEXT_ROUTE_PREFIXES } from "@plughub/schemas"

// ── roteamento de store: UMA casa, e ela é @plughub/schemas ───────────────────
//
// CNS-04 — as duas listas de prefixo que viviam aqui (`LONG_TTL_PREFIXES` e
// `JOURNEY_TTL_PREFIXES`) foram para `CONTEXT_ROUTE_PREFIXES`, no pacote base. Aqui
// elas eram metade da verdade: o oráculo do mapa aprovava roots contra um enum
// próprio (`session|journey|customer`) que NÃO casava com estes prefixos, e por isso
// admitia `customer` como root prometendo 90 dias enquanto `customer.x` caía no
// default de 4 horas. Duas listas para a mesma pergunta, e a resposta divergente era
// silenciosa.
//
// O que fica NESTE pacote é o que é dele: o mapeamento store → TTL, porque os TTLs
// são CONFIGURÁVEIS por instância (`ContextStoreConfig`) e a tabela de rotas não é.

/**
 * CNS-03/04 — **o escopo de uma tag do CORE é o seu SEGUNDO segmento**, e quem
 * responde isso é `resolveContextStore`, de `@plughub/schemas`.
 *
 * A CNS-02 reservou o root `core.*` para a plataforma, e com isso o primeiro segmento
 * passou a carregar PROPRIEDADE em vez de escopo. O escopo não desapareceu: desceu um
 * nível — `core.customer.` vai para o hash do CLIENTE (90 d), `core.journey.` para o
 * da JOURNEY (30 d), e o resto de `core.` é de sessão como qualquer outro root.
 *
 * ⚠️ **`core.customer.` não é declaração por antecipação — é migração.** Ele absorve
 * `insight.historico.*` e `pricing.*`, que já roteiam para o cliente com 90 dias. Sem
 * a rota, renomeá-los moveria dado de retenção trimestral para um hash de 4 horas,
 * **sem erro em lugar nenhum**.
 *
 * ⚠️ E o prefixo decide **DUAS** coisas, TTL *e* chave — por isso `isLongTtl` continua
 * existindo como predicado próprio, consultado pelos call sites que escolhem a CHAVE,
 * e não só pelo `ttlFor`.
 */

// ── TTLs padrão ───────────────────────────────────────────────────────────────

const SESSION_TTL_S  = 4 * 60 * 60        // 4h
const JOURNEY_TTL_S  = 30 * 24 * 60 * 60  // 30d — vida do processo, não da sessão
const LONG_TTL_S     = 90 * 24 * 60 * 60  // 90d

// ── Config ────────────────────────────────────────────────────────────────────

export interface ContextStoreConfig {
  redis:    RedisClient
  tenantId: string
  /** Sobrescreve TTL do hash de sessão (segundos). Default: SESSION_TTL_S */
  sessionTtlS?: number
  /** Sobrescreve TTL do hash da journey (segundos). Default: JOURNEY_TTL_S (30d) */
  journeyTtlS?: number
  /** Sobrescreve TTL do hash de longa duração (segundos). Default: LONG_TTL_S */
  longTtlS?: number
}

// ── ContextStore ──────────────────────────────────────────────────────────────

export class ContextStore {
  private readonly redis:      RedisClient
  private readonly tenantId:   string
  private readonly sessionTtl: number
  private readonly journeyTtl: number
  private readonly longTtl:    number

  constructor(config: ContextStoreConfig) {
    this.redis      = config.redis
    this.tenantId   = config.tenantId
    this.sessionTtl = config.sessionTtlS ?? SESSION_TTL_S
    this.journeyTtl = config.journeyTtlS ?? JOURNEY_TTL_S
    this.longTtl    = config.longTtlS    ?? LONG_TTL_S
  }

  // ── chaves Redis ────────────────────────────────────────────────────────────

  private sessionKey(sessionId: string): string {
    return `${this.tenantId}:ctx:${sessionId}`
  }

  private customerKey(customerId: string): string {
    return `${this.tenantId}:ctx:customer:${customerId}`
  }

  /**
   * Vai para o hash do CLIENTE? É o único store que muda a CHAVE, e por isso tem
   * predicado próprio — os quatro call sites perguntam isto, não o TTL.
   * `journey` também tem TTL longo mas mora no hash da JOURNEY (via o sessionId
   * virtual `journey:{id}`); confundir os dois mandaria contexto de processo para
   * o hash do cliente.
   */
  private isLongTtl(tag: string): boolean {
    return resolveContextStore(tag) === "customer"
  }

  /** TTL efetivo da tag, derivado do store. Ordem: cliente → journey → sessão. */
  private ttlFor(tag: string, override?: number): number {
    switch (resolveContextStore(tag)) {
      case "customer": return this.longTtl
      case "journey":  return this.journeyTtl
      default:         return override ?? this.sessionTtl
    }
  }

  // ── set ─────────────────────────────────────────────────────────────────────

  /**
   * Escreve ou atualiza uma tag no ContextStore.
   *
   * @param sessionId   ID da sessão ativa
   * @param tag         Caminho da tag: "caller.cpf", "session.sentimento.current"
   * @param entry       Dados a escrever (sem updated_at — gerado automaticamente)
   * @param merge       Estratégia de merge (default: highest_confidence)
   * @param customerId  Necessário apenas para tags de longa duração (pricing, insight.historico)
   */
  async set(
    sessionId:  string,
    tag:        string,
    entry:      Omit<ContextEntry, "updated_at">,
    merge:      ContextMergeStrategy = "highest_confidence",
    customerId?: string,
  ): Promise<void> {
    const key      = this.isLongTtl(tag) && customerId
      ? this.customerKey(customerId)
      : this.sessionKey(sessionId)

    const ttl = this.ttlFor(tag, entry.ttl_override_s)
    const now = new Date().toISOString()

    // Lê valor existente para aplicar merge strategy
    if (merge === "highest_confidence") {
      const existingRaw = await this.redis.hget(key, tag).catch(() => null)
      if (existingRaw) {
        try {
          const existing = JSON.parse(existingRaw) as ContextEntry
          if (existing.confidence >= entry.confidence) {
            // Não sobrescreve — existente tem confiança maior ou igual
            return
          }
        } catch { /* parse error → sobrescreve */ }
      }
    }

    if (merge === "append") {
      // Acumula em array — lê valor atual e adiciona
      const existingRaw = await this.redis.hget(key, tag).catch(() => null)
      let arr: unknown[] = []
      if (existingRaw) {
        try {
          const existing = JSON.parse(existingRaw) as ContextEntry
          arr = Array.isArray(existing.value) ? existing.value : [existing.value]
        } catch { /* sobrescreve */ }
      }
      arr.push(entry.value)
      const appended: ContextEntry = { ...entry, value: arr, updated_at: now }
      await this.redis.hset(key, tag, JSON.stringify(appended))
      await this.redis.expire(key, ttl)
      return
    }

    // overwrite ou highest_confidence que passou a verificação
    const full: ContextEntry = { ...entry, updated_at: now }
    await this.redis.hset(key, tag, JSON.stringify(full))
    await this.redis.expire(key, ttl)
  }

  // ── get ─────────────────────────────────────────────────────────────────────

  /**
   * Lê uma tag específica do ContextStore.
   * Tenta primeiro o hash de sessão; se a tag for de longa duração e
   * customerId for fornecido, tenta o hash de customer.
   */
  async get(
    sessionId:   string,
    tag:         string,
    customerId?: string,
  ): Promise<ContextEntry | null> {
    const key = this.isLongTtl(tag) && customerId
      ? this.customerKey(customerId)
      : this.sessionKey(sessionId)

    const raw = await this.redis.hget(key, tag).catch(() => null)
    if (!raw) return null

    try {
      return JSON.parse(raw) as ContextEntry
    } catch {
      return null
    }
  }

  // ── getValue ────────────────────────────────────────────────────────────────

  /** Atalho que retorna apenas o value da entry, ou null se ausente. */
  async getValue(
    sessionId:   string,
    tag:         string,
    customerId?: string,
  ): Promise<unknown> {
    const entry = await this.get(sessionId, tag, customerId)
    return entry?.value ?? null
  }

  // ── getAll ──────────────────────────────────────────────────────────────────

  /** Retorna snapshot completo do hash de sessão. */
  async getAll(sessionId: string): Promise<ContextSnapshot> {
    return this._readHash(this.sessionKey(sessionId))
  }

  // ── getByPrefix ─────────────────────────────────────────────────────────────

  /**
   * Retorna snapshot filtrado por um ou mais prefixos de namespace.
   *
   * @param prefixes  ex: ["session", "caller", "account"]
   *
   * Para prefixos de longa duração (pricing, insight.historico),
   * fornece customerId para ler do hash correto.
   */
  async getByPrefix(
    sessionId:   string,
    prefixes:    string[],
    customerId?: string,
  ): Promise<ContextSnapshot> {
    const sessionSnapshot = await this._readHash(this.sessionKey(sessionId))

    // Filtra session hash por prefixos
    const result: ContextSnapshot = {}
    for (const [tag, entry] of Object.entries(sessionSnapshot)) {
      if (prefixes.some(p => tag === p || tag.startsWith(`${p}.`))) {
        result[tag] = entry
      }
    }

    // Se algum prefixo é de longa duração e customerId fornecido, lê do customer hash.
    //
    // ⚠️ QUINTO call site da tabela de rotas — os outros quatro perguntam por uma TAG
    // inteira (`resolveContextStore`), este pergunta por um PREFIXO DE CONSULTA, que
    // pode ser mais curto que a rota (`insight` para alcançar `insight.historico.*`)
    // ou mais longo (`insight.historico.resumo`). Daí o casamento nas DUAS direções,
    // que `resolveContextStore` não faz nem deve fazer: ele responde "onde esta tag
    // mora", e aqui a pergunta é "esta consulta pode alcançar o hash do cliente".
    // Confundir as duas faria a consulta curta parar de ler o hash do cliente — e o
    // sintoma seria histórico do cliente sumindo do snapshot, sem erro.
    const customerRoutes = CONTEXT_ROUTE_PREFIXES.filter(r => r.store === "customer").map(r => r.prefix)
    const longPrefixes = prefixes.filter(p => customerRoutes.some(lp => lp.startsWith(p) || p.startsWith(lp)))
    if (longPrefixes.length > 0 && customerId) {
      const customerSnapshot = await this._readHash(this.customerKey(customerId))
      for (const [tag, entry] of Object.entries(customerSnapshot)) {
        if (longPrefixes.some(p => tag === p || tag.startsWith(`${p}.`))) {
          result[tag] = entry
        }
      }
    }

    return result
  }

  // ── getMissing ──────────────────────────────────────────────────────────────

  /**
   * Compara required_context com o ContextStore atual e retorna um GapsReport.
   * Usado para computar @ctx.__gaps__ na entrada de um fluxo.
   */
  async getMissing(
    sessionId:       string,
    requiredContext: SkillRequiredContext[],
    customerId?:     string,
  ): Promise<ContextGapsReport> {
    const missing:        string[]                                        = []
    const low_confidence: ContextGapsReport["low_confidence"]            = []

    for (const req of requiredContext) {
      if (!req.required) continue

      const entry = await this.get(sessionId, req.tag, customerId)

      if (!entry) {
        missing.push(req.tag)
        continue
      }

      const minConf = req.confidence_min ?? 0.7
      if (entry.confidence < minConf) {
        low_confidence.push({
          tag:        req.tag,
          confidence: entry.confidence,
          required:   minConf,
        })
      }
    }

    return {
      missing,
      low_confidence,
      complete: missing.length === 0 && low_confidence.length === 0,
    }
  }

  // ── delete ──────────────────────────────────────────────────────────────────

  /** Remove uma tag específica do store. */
  async delete(sessionId: string, tag: string, customerId?: string): Promise<void> {
    const key = this.isLongTtl(tag) && customerId
      ? this.customerKey(customerId)
      : this.sessionKey(sessionId)
    await this.redis.hdel(key, tag).catch(() => null)
  }

  /** Remove todas as tags de sessão (chamado em session_close). */
  async clearSession(sessionId: string): Promise<void> {
    await this.redis.del(this.sessionKey(sessionId)).catch(() => null)
  }

  // ── helpers ─────────────────────────────────────────────────────────────────

  private async _readHash(key: string): Promise<ContextSnapshot> {
    const raw = await this.redis.hgetall(key).catch(() => null)
    if (!raw) return {}

    const result: ContextSnapshot = {}
    for (const [tag, json] of Object.entries(raw)) {
      try {
        result[tag] = JSON.parse(json as string) as ContextEntry
      } catch { /* skip malformed entry */ }
    }
    return result
  }
}
