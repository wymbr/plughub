/**
 * Shared types for the Contacts module (Lista | Monitor | Análise tabs).
 */

export interface ContactFilters {
  fromDt:          string
  toDt:            string
  sessionIdSearch: string
  channel:         string
  outcome:         string
  /** **"atendido por"** (D12) — qualquer pool que trabalhou no contato (subconsulta
   *  em `segments`). O nome do campo é histórico; o rótulo na tela nunca é "Pool". */
  poolId:          string
  /** F3.2 — **"entrou por"**: a PORTA (`sessions.pool_id`, first-write-wins desde a
   *  F1b). Compõe por AND com `poolId`, e é essa diferença que a tela expõe: 12
   *  contatos de cliente entram no `sac_ia` e são atendidos pelo `retencao_humano`. */
  entryPoolId:     string
  agentId:         string
  insightCategory: string
  insightTags:     string
  /** Arc 19: filter by session status (active | suspended | closed) */
  status?:         string
  /** Substrate isolation (ADR): origem do substrato (live=produção default | import | reeval).
   *  Usado só pelas telas de Analytics; ausente no Console (= produção). */
  origin?:         'live' | 'import' | 'reeval'
}

export interface ContactRow {
  session_id:     string
  tenant_id:      string
  channel:        string
  pool_id:        string | null
  customer_id:    string | null
  opened_at:      string | null
  closed_at:      string | null
  close_reason:   string | null
  outcome:        string | null
  wait_time_ms:   number | null
  /** ⚠️ ALIAS DE COMPAT (D9). Não ganhar leitor novo — use `elapsed_time_ms`. */
  handle_time_ms: number | null
  /** F3.1 / D9 — duração do CASO: wall-clock, **inclui as esperas**. Nunca
   *  `agent_time_ms` (agente × tempo) e **nunca** Σ segmentos (eles se SOBREPÕEM). */
  elapsed_time_ms?: number | null
  segment_count:  number
  /** Arc 19: session status — 'active' | 'suspended' | 'closed' | null (pre-Arc-19) */
  status?:        string | null
  /** Arc 19: origin_session_id — for webhook sessions, the intake session that triggered them */
  origin_session_id?: string | null
  /** ADR wrapup-detached-pull §7 (fatia 1b): linha de POOL INTERNO (wrap-up, dispatch).
   *  Veredicto computado no backend — a UI não reclassifica por `pool_id`. */
  is_internal?: boolean
  /** Journey T4 — POR QUE esta sessão nasceu. É a fonte da coluna de direção (D8):
   *  ausente/`null` = ninguém a criou ⇒ o cliente chegou. Ver `contactDirection`. */
  spawn_reason?: string | null
  /** Journey T1 — raiz LOCAL da árvore de proveniência (pré-merge). Para exibir use
   *  `journey_id`, que é a raiz CANÔNICA. */
  root_session_id?: string | null
  /** F3.3 — raiz CANÔNICA do processo (union-find sobre `journey_aliases`). É o que
   *  rotula o chip: usar `root_session_id` cru faria duas linhas do MESMO processo
   *  exibirem códigos diferentes depois de um `journey_merge`. */
  journey_id?: string | null
  /** F3.3 — nº de contatos do processo **INTEIRO**, deliberadamente fora do filtro de
   *  período. `null`/ausente = o backend não conseguiu contar ⇒ **não desenhar chip**
   *  (um `1` inventado afirmaria "não pertence a processo nenhum"). */
  journey_session_count?: number | null
  /** F3.1 — pools que TRABALHARAM no contato, em ordem de primeiro segmento. É o lado
   *  direito do par `entrou por → atendido por` (`pool_id` é o esquerdo). Lista vazia
   *  = nenhum segmento (abandono antes de qualquer agente entrar). */
  attended_pool_ids?: string[]
}

export interface ContactsApiResponse {
  data: ContactRow[]
  meta?: {
    total?: number
    page?: number
    page_size?: number
    /** ADR §7.2: DOIS domínios de contagem, nunca somados.
     *  `total` dimensiona a PAGINAÇÃO (linhas listadas); `total_contacts` é o
     *  cabeçalho, sempre no domínio de contato mesmo com a tabela expandida. */
    total_contacts?: number
    total_internal?: number
    scope?: string
    /** Tamanho do conjunto de pools internos que classificou estas linhas.
     *  É um FATO, não flag de saúde: `0` ⇒ nada a distinguir ⇒ não oferecer o toggle. */
    internal_pools_known?: number
    /** F3.3 — a janela de período INCIDIU sobre esta resposta? Drill (por
     *  `root_session_id`/`origin_session_id`) é isento. É o que condiciona o rodapé
     *  que explica o N do chip: sem recorte, a frase seria ruído. */
    window_applied?: boolean
  }
}

// ── Direção do acesso (D8) ─────────────────────────────────────────────────
//
// ⚠️ **Nunca chamar esta coluna de `origin`.** `lista.columns.origin` já significa
// ANI nesta mesma tabela; reusar o nome faria o operador ler um e receber o outro —
// exatamente o erro que este arco existe para corrigir, cometido de novo na tela que
// o corrige. A chave é `lista.columns.direction`.

export type ContactDirection = 'inbound' | 'outbound' | 'internal'

/**
 * Direção do acesso, DERIVADA de `spawn_reason` + canal — nunca armazenada.
 *
 * - `collect`            → **outbound**: nós procuramos o cliente.
 * - `trigger`/`delegate` → **interno**: uma etapa da maquinaria criou a sessão.
 * - ausente/`null`       → ninguém a criou. Aí o CANAL desempata: `webhook` é
 *   máquina falando com máquina (**interno**); qualquer outro é o cliente
 *   chegando (**inbound**).
 *
 * Valor desconhecido devolve `null` ⇒ a célula mostra `—`. É de propósito: um
 * `spawn_reason` novo cairia num balde plausível e passaria despercebido, e a regra
 * deste projeto é que valor ausente denuncie enquanto valor plausível esconde.
 */
export function contactDirection(row: ContactRow): ContactDirection | null {
  const sr = row.spawn_reason ?? ''
  if (sr === 'collect')                     return 'outbound'
  if (sr === 'trigger' || sr === 'delegate') return 'internal'
  if (sr === '')                            return row.channel === 'webhook' ? 'internal' : 'inbound'
  return null
}

export const DIRECTION_ICONS: Record<ContactDirection, string> = {
  inbound: '⇣', outbound: '⇡', internal: '⚙',
}

// ── Visualization format for Monitor + Análise ─────────────────────────────

export type VizFormat = 'heatmap' | 'bars' | 'donut' | 'tiles' | 'table'

// ── Helpers ────────────────────────────────────────────────────────────────

export function formatMs(ms: number | null): string {
  if (ms === null || ms === undefined) return '—'
  const s = Math.floor(ms / 1000)
  const m = Math.floor(s / 60)
  const h = Math.floor(m / 60)
  if (h > 0) return `${h}h ${m % 60}m`
  if (m > 0) return `${m}m ${s % 60}s`
  return `${s}s`
}

export function formatDt(dt: string | null): string {
  if (!dt) return '—'
  try {
    return new Date(dt).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' })
  } catch { return dt }
}

export function iso7dAgo(): string {
  const d = new Date(); d.setDate(d.getDate() - 7)
  return d.toISOString().slice(0, 10)
}
export function isoToday(): string { return new Date().toISOString().slice(0, 10) }

export const CHANNEL_ICONS: Record<string, string> = {
  webchat: '💬', whatsapp: '📱', voice: '📞', email: '✉️',
  sms: '📟', instagram: '📷', telegram: '✈️', webrtc: '🎥', webhook: '🔗',
}

export const OUTCOME_COLORS: Record<string, string> = {
  resolved:    '#059669',
  escalated:   '#d97706',
  transferred: '#2563eb',
  abandoned:   '#dc2626',
  timeout:     '#9333ea',
}

export const DEFAULT_FILTERS: ContactFilters = {
  fromDt:          iso7dAgo(),
  toDt:            isoToday(),
  sessionIdSearch: '',
  channel:         '',
  outcome:         '',
  poolId:          '',
  entryPoolId:     '',
  agentId:         '',
  insightCategory: '',
  insightTags:     '',
}

/** F3.3 — rótulo curto e estável do processo: `PRC-` + 4 primeiros do id da raiz
 *  CANÔNICA. Mesma convenção do cabeçalho da visão 2 (`PRC-3f9c`). */
export function journeyLabel(journeyId: string): string {
  return `PRC-${journeyId.slice(0, 4)}`
}
