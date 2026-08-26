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
  /** F3 (resíduo) — direção do ACESSO (D8), filtrada NO BACKEND pela mesma
   *  expressão que devolve a coluna. Vazio = todas. Nunca filtrar direção no
   *  cliente: a paginação é do servidor, e recortar a página já entregue diria
   *  "3 contatos" onde há 300. */
  direction?:      '' | ContactDirection
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
  /** Journey T4 — POR QUE esta sessão nasceu. É a MATÉRIA-PRIMA da direção, não a
   *  direção: quem classifica é o backend (`direction`). Exibido cru só no título
   *  da célula `—`, para nomear o valor que não foi classificado. */
  spawn_reason?: string | null
  /** D8 — direção do acesso, DERIVADA pelo backend (`inbound|outbound|internal`).
   *  `''`/ausente = não classificada ⇒ `—`. Mesma expressão que o filtro
   *  `?direction=` usa, e é essa identidade que impede a linha e o filtro de
   *  discordarem. Ver `contactDirection`. */
  direction?: string | null
  /** Journey T1 — raiz LOCAL da árvore de proveniência (pré-merge). Para exibir use
   *  `journey_id`, que é a raiz CANÔNICA. */
  root_session_id?: string | null
  /** F3.3 — raiz CANÔNICA do processo (union-find sobre `journey_aliases`). É o que
   *  rotula o chip: usar `root_session_id` cru faria duas linhas do MESMO processo
   *  exibirem códigos diferentes depois de um `journey_merge`. */
  journey_id?: string | null
  /** F3.3 — nº de sessões do processo **INTEIRO**, deliberadamente fora do filtro de
   *  período. `null`/ausente = o backend não conseguiu contar ⇒ **não desenhar chip**
   *  (um `1` inventado afirmaria "não pertence a processo nenhum"). */
  journey_session_count?: number | null
  /** Quebra do total acima por classe de linha (2026-08-26) — os MESMOS baldes do
   *  cabeçalho da visão 2, para onde o chip pivota. `acesso + interna + não
   *  classificada === total`, por construção: os três saem de `countIf` sobre a
   *  mesma `_DIRECTION_EXPR`, que é exaustiva.
   *
   *  ⚠️ **A quebra é da APRESENTAÇÃO, não da população.** O chip continua contando o
   *  processo inteiro; o que mudou é publicar em dois domínios em vez de um número
   *  sob o rótulo "contatos". Recortar o TOTAL por direção (chip contar só acessos)
   *  faria o chip dizer `·3` e a tela para onde ele leva mostrar 5 linhas.
   *
   *  ⚠️ **`journey_internal_step_count` é ETAPA interna** (`direction='internal'`,
   *  de `spawn_reason`), que entra no escopo de contato. Não confundir com o
   *  `internal_session_count` do card de `/reports/journeys`, que conta sessão de
   *  POOL interno (wrap-up, dispatch) — essa é EXCLUÍDA destes números.
   *
   *  Ausentes juntos (backend antigo, ou journey sem linha no agregado) ⇒ a UI cai
   *  no número único; nunca zerar só a quebra. */
  journey_access_count?: number | null
  journey_internal_step_count?: number | null
  journey_unclassified_count?: number | null
  /** 2026-08-26 — **existência, nunca tamanho.** Os quatro números acima são contados
   *  SOB a ABAC do usuário (deliberado: contar os membros que ele não alcança
   *  revelaria o tamanho de um processo que toca pools fora do escopo dele). A
   *  consequência não prevista era a tela AFIRMAR o oposto: processo do qual só uma
   *  sessão é alcançável dava `1`, o chip sumia por `> 1`, e a linha passava a dizer
   *  "este contato não pertence a processo nenhum".
   *
   *  Este booleano é o dado que faltava — `true` = *"há membros que você não
   *  alcança"*, sem dizer quantos nem quais. Com ele o chip volta a aparecer.
   *
   *  ⚠️ **Três valores, três proposições.** `true` = há mais · `false` = **medi e não
   *  há** (é o caso do usuário irrestrito) · `null`/ausente = **não medi** (falha da
   *  agregação, ou backend antigo). Tratar `null` como `false` é a mesma família do
   *  `1` inventado: afirma completude que ninguém verificou. */
  journey_has_scoped_out_members?: boolean | null
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
 * Direção do acesso, lida do campo `direction` que o backend DERIVA
 * (`reports_query._DIRECTION_EXPR`). A regra continua sendo a do D8 — `collect` →
 * outbound, `trigger`/`delegate` → interno, ausente → o canal desempata —, mas ela
 * mora num lugar só.
 *
 * ⚠️ **Esta função já foi a derivação, e deixou de ser na F4.** Enquanto o filtro
 * por direção não existia, ter a regra aqui era inofensivo; no instante em que o
 * `WHERE` precisou dela em SQL, manter a cópia em TypeScript criaria duas respostas
 * para a mesma pergunta — a linha diria `interno` e o filtro `inbound` a devolveria,
 * sem nada ficar vermelho. Quem sabe a resposta decide; quem exibe apenas exibe.
 *
 * Valor ausente ou desconhecido devolve `null` ⇒ a célula mostra `—`. Vale para as
 * duas causas, que são diferentes e ambas honestas: backend antigo (campo ausente)
 * e `spawn_reason` novo (o backend não classificou). Nenhuma vira balde plausível.
 */
export function contactDirection(
  row: Pick<ContactRow, 'direction'>,
): ContactDirection | null {
  const d = row.direction ?? ''
  return d === 'inbound' || d === 'outbound' || d === 'internal' ? d : null
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
  direction:       '',
}

/**
 * Rótulo curto e estável do processo: `PRC-` + 8 primeiros do id da raiz CANÔNICA.
 *
 * ⚠️ **Eram DOIS rótulos, e o comentário afirmava que eram um.** Esta função cortava
 * em 4 caracteres e dizia *"mesma convenção do cabeçalho da visão 2 (`PRC-3f9c`)"*;
 * o cabeçalho da visão 2 cortava em 8. O mesmo processo aparecia como `PRC-3f9c` no
 * chip e `PRC-3f9c1234` no cabeçalho para onde o chip levava — e a prosa dizia que
 * não. Unificado em 8 na F4, com a visão 2 passando a IMPORTAR daqui.
 *
 * Por que 8 e não 4: 4 hex são 65 mil valores, e a colisão de aniversário chega a
 * 50% perto de 300 processos — dois processos diferentes exibindo o mesmo código é
 * pior do que um rótulo mais largo. O id inteiro continua no `title` de toda
 * superfície que o mostra.
 */
export function journeyLabel(journeyId: string | null | undefined): string {
  if (!journeyId) return '—'
  return `PRC-${journeyId.slice(0, 8)}`
}
