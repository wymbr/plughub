/**
 * AnaliseJourneysPage — Vista Processos (Journey J2) — nível 2 de /analise/sessions
 *
 * Lente por PROVENIÊNCIA (agrupa sessions por root_session_id) — não a entidade
 * Journey do Arc 10 (removida). Drill por URL-param:
 *   L2: /analise/sessions?journey=:root            — sessões-membro da journey
 *
 * O nível de SESSÃO não mora mais aqui: clicar numa sessão-membro leva a
 * `?journey=:root&session_id=:s`, que o `SessionsPage` trata com o drill único
 * (renderer escolhido pelo canal) e com o processo no breadcrumb. `?…&session=:s`
 * é endereço legado e redireciona.
 *
 * ⚠️ **O L1 (lista livre de journeys) não é mais alcançável** (F3.3 / ADR D2): o
 * `SessionsPage` só monta este componente COM `?journey=`, e sem o parâmetro a rota
 * mostra a lista de CONTATOS — que é para onde o `onBack` daqui aponta. O código do
 * L1 (`JourneysList`) permanece porque a F4 o reenquadra como o pivô; ele não deve
 * ganhar entrada de menu no caminho. Processo é pivô, nunca navegação livre.
 *
 * Fonte: GET /reports/journeys + GET /reports/sessions?root_session_id= (analytics-api).
 * Sem alias/merge (isso é J3): cada grupo é uma árvore de proveniência.
 */
import React, { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { apiFetch } from '@/api/apiFetch'
import { ChevronRight, GitBranch, FileText, Route, X } from 'lucide-react'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'
// F4 — o rótulo do processo e a leitura da direção vêm da visão 1, não de cópias
// locais: o chip que trouxe o operador até aqui e o cabeçalho que ele encontra têm
// de dizer o MESMO código, e a classe da linha tem de usar a MESMA direção que a
// coluna de lá. Ver `journeyLabel` e `contactDirection` em `modules/contacts/types`.
import { contactDirection, DIRECTION_ICONS, journeyLabel } from '@/modules/contacts/types'

// ── Types ───────────────────────────────────────────────────────────────────

interface JourneyRow {
  journey_id:           string
  session_count:        number
  started_at:           string
  last_activity_at:     string
  channels:             string[]
  pool_ids:             string[]
  open_count:           number
  significant:          number
  // J4 — métricas de processo + sinal de qualidade N3
  business_outcome?:    string | null
  business_duration_ms?: number | null
  signal_count?:        number
  nps_avg?:             number | null
  csat_avg?:            number | null
  ces_avg?:             number | null
}

interface MemberSession {
  session_id:      string
  channel:         string
  pool_id:         string
  status:          string
  opened_at:       string
  closed_at:       string | null
  outcome:         string | null
  close_reason:    string | null
  segment_count:   number
  root_session_id: string
  // T1/T4 — a ARESTA (quem me criou) e o seu RÓTULO (por quê).
  origin_session_id: string | null
  spawn_reason:      string | null   // trigger | delegate | collect | null (topo)
  /** F4/D8 — direção do acesso, DERIVADA pelo backend. `''`/ausente = não
   *  classificada. É o discriminador das duas classes de linha (D4). */
  direction?:        string | null
}

// ── T5: árvore de proveniência ───────────────────────────────────────────────
//
// A sessão é um NÓ RECURSIVO (pode gerar outras sessões); a journey é a ÁRVORE. A lista
// plana escondia isso — e escondia porque a aresta (`origin_session_id`) nem chegava à
// tabela (T1). Com ela, a hierarquia se monta sozinha.
//
// Um nó cujo pai NÃO está no conjunto é raiz LOCAL (a raiz da journey, ou um órfão de
// dados antigos — a árvore não é retroativa). Renderizar órfãos no topo, em vez de
// escondê-los, é deliberado: dado antigo aparece achatado, não some.
interface TreeNode { session: MemberSession; children: TreeNode[]; depth: number }

function buildTree(rows: MemberSession[]): TreeNode[] {
  const byId = new Map(rows.map(s => [s.session_id, s]))
  const childrenOf = new Map<string, MemberSession[]>()
  const roots: MemberSession[] = []

  for (const s of rows) {
    const parent = s.origin_session_id
    if (parent && byId.has(parent)) {
      const list = childrenOf.get(parent) ?? []
      list.push(s)
      childrenOf.set(parent, list)
    } else {
      roots.push(s)          // raiz da journey, ou órfão (dado pré-T1)
    }
  }

  // Guard de ciclo: dado corrompido não pode travar o render.
  const seen = new Set<string>()
  const walk = (s: MemberSession, depth: number): TreeNode => {
    seen.add(s.session_id)
    const kids = (childrenOf.get(s.session_id) ?? [])
      .filter(k => !seen.has(k.session_id))
      .sort((a, b) => a.opened_at.localeCompare(b.opened_at))
    return { session: s, depth, children: kids.map(k => walk(k, depth + 1)) }
  }
  return roots
    .sort((a, b) => a.opened_at.localeCompare(b.opened_at))
    .map(r => walk(r, 0))
}

function flattenTree(nodes: TreeNode[]): TreeNode[] {
  return nodes.flatMap(n => [n, ...flattenTree(n.children)])
}

// ── T6: rastro forense (proveniência bidirecional) ───────────────────────────
//
// A Vista Processos MEDE uma journey (tem fronteira); o rastro PERCORRE o grafo
// (sem fronteira — ninguém mede). Vem do endpoint bidirecional: ancestrais (sobe
// por origin_session_id) + descendentes (BFS) EM VOLTA de uma sessão, atravessando
// fronteiras de journey. Cada nó cuja journey canônica difere da do foco é marcado
// `journey_boundary` — a fronteira que o `journey: new` cria, exibida como link.
interface TraceApiNode {
  session_id:        string
  origin_session_id: string | null
  spawn_reason:      string | null
  root_session_id:   string
  journey_id:        string
  channel:           string
  pool_id:           string
  status:            string
  outcome:           string | null
  opened_at:         string
  closed_at:         string | null
  depth:             number        // relativo ao foco: <0 ancestral, 0 foco, >0 descendente
  is_focus:          boolean
  journey_boundary:  boolean
}
interface TraceResp {
  focus_session_id: string
  focus_journey_id: string | null
  focus:            TraceApiNode | null
  nodes:            TraceApiNode[]
  meta:             { node_count: number; truncated: boolean }
}
interface TraceTreeNode { node: TraceApiNode; children: TraceTreeNode[]; depth: number }

// Monta a árvore por origin_session_id (a cadeia sobe em linha, desce em árvore). A
// profundidade AQUI é a posição no render (a partir do nó de topo), não o `depth`
// relativo ao foco do backend — o topo é o ancestral mais alto do conjunto.
function buildTraceTree(nodes: TraceApiNode[]): TraceTreeNode[] {
  const byId = new Map(nodes.map(n => [n.session_id, n]))
  const childrenOf = new Map<string, TraceApiNode[]>()
  const roots: TraceApiNode[] = []
  for (const n of nodes) {
    const parent = n.origin_session_id
    if (parent && byId.has(parent)) {
      const list = childrenOf.get(parent) ?? []
      list.push(n); childrenOf.set(parent, list)
    } else {
      roots.push(n)   // topo do conjunto (pai fora do rastro ou raiz de topo)
    }
  }
  const seen = new Set<string>()
  const walk = (n: TraceApiNode, depth: number): TraceTreeNode => {
    seen.add(n.session_id)
    const kids = (childrenOf.get(n.session_id) ?? [])
      .filter(k => !seen.has(k.session_id))
      .sort((a, b) => a.opened_at.localeCompare(b.opened_at))
    return { node: n, depth, children: kids.map(k => walk(k, depth + 1)) }
  }
  return roots
    .sort((a, b) => a.opened_at.localeCompare(b.opened_at))
    .map(r => walk(r, 0))
}
function flattenTraceTree(nodes: TraceTreeNode[]): TraceTreeNode[] {
  return nodes.flatMap(n => [n, ...flattenTraceTree(n.children)])
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function isoToday(): string { return new Date().toISOString().slice(0, 10) }
function iso30DaysAgo(): string {
  const d = new Date(); d.setDate(d.getDate() - 29)
  return d.toISOString().slice(0, 10)
}
function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' }) }
  catch { return iso }
}
function fmtDurationMs(ms: number | null | undefined): string {
  if (ms == null || ms < 0) return '—'
  if (ms < 60_000) return `${Math.round(ms / 1000)}s`
  const m = Math.floor(ms / 60_000); const s = Math.round((ms % 60_000) / 1000)
  return s > 0 ? `${m}m ${s}s` : `${m}m`
}
function fmtDuration(from: string, to?: string | null): string {
  if (!from || !to) return '—'
  return fmtDurationMs(new Date(to).getTime() - new Date(from).getTime())
}
function truncateId(id: string | undefined | null): string {
  if (!id) return '—'
  return id.length > 16 ? `…${id.slice(-12)}` : id
}

// T5 — o rótulo da journey (`PRC-…`) mora em `modules/contacts/types` e é IMPORTADO.
//
// Ele era duplicado aqui, e as duas cópias divergiam: o chip da visão 1 cortava o id
// em 4 caracteres e este cabeçalho em 8, então o mesmo processo tinha dois códigos —
// um no lugar de onde se clica e outro no lugar aonde se chega. A prosa da outra
// cópia ainda afirmava que a convenção era a mesma. Unificado em 8 na F4.
//
// (O modelo não muda: a journey é identificada pela RAIZ CANÔNICA, e o `journey_id`
// **é** um `session_id` — como um branch do git é identificado por um hash. O
// prefixo conserta a APRESENTAÇÃO, para que ninguém leia "o processo é a sessão".)

// ── F4 — as DUAS classes de linha (ADR D4) ──────────────────────────────────
//
// Tratar as sessões-membro como pares é o que fazia o processo parecer que não
// respondia à pergunta. Um processo de 3 sessões pode ter 1 acesso do cliente e 2
// etapas de maquinaria — e o cabeçalho que diz "3 sessões" está certo sobre a
// tabela e errado sobre o caso.
//
//   · **acesso do cliente** — protagonista. `inbound` (o cliente procurou) ou
//     `outbound` (nós procuramos). É o que o cliente PERCEBE.
//   · **etapa interna**     — maquinaria entre acessos. Dobrada por default (D11).
//   · **não classificada**  — `spawn_reason` que o backend não classificou. NÃO é
//     um terceiro balde de conveniência: aparece como linha, nunca é dobrada e
//     nunca entra na contagem de acessos. Somá-la a "acessos" inflaria o número
//     protagonista com uma linha que ninguém sabe ler.
type LineClass = 'access' | 'internal' | 'unknown'
type Lens      = 'tree' | 'chrono'

function lineClass(s: MemberSession): LineClass {
  const d = contactDirection(s)
  if (d === null) return 'unknown'
  return d === 'internal' ? 'internal' : 'access'
}

/** Grupo das etapas internas sem NENHUM acesso acima delas na proveniência. */
const ORPHAN_GROUP = '__orphan__'

/**
 * Para cada sessão, o ACESSO a que ela pertence: o ancestral de proveniência mais
 * próximo que é acesso do cliente (ou ela mesma, se for um). É o que permite dobrar
 * a maquinaria SOB o acesso que a originou, em vez de escondê-la numa lista à parte.
 *
 * Etapa interna sem acesso ancestral cai em `ORPHAN_GROUP` e é EXIBIDA como grupo
 * próprio — pendurá-la no primeiro acesso qualquer afirmaria uma origem que o dado
 * não tem.
 */
function buildGroups(tree: TreeNode[]): Map<string, string> {
  const groups = new Map<string, string>()
  const walk = (n: TreeNode, currentAccess: string | null) => {
    const cls = lineClass(n.session)
    const isInternal = cls === 'internal'
    groups.set(n.session.session_id, isInternal ? (currentAccess ?? ORPHAN_GROUP) : n.session.session_id)
    const nextAccess = isInternal ? currentAccess : n.session.session_id
    n.children.forEach(c => walk(c, nextAccess))
  }
  tree.forEach(r => walk(r, null))
  return groups
}

/**
 * Deslocamento desde a abertura do PROCESSO (`+7m54s`).
 *
 * Refinamento barato de leitura (ADR D6): dois timestamps absolutos em níveis de
 * indentação diferentes são difíceis de comparar de olho, e o aninhamento salta
 * quando o offset está ao lado. A base é o MENOR `opened_at` do conjunto, não o da
 * raiz — depois de um merge a raiz canônica é a de outro contato, e usar a raiz
 * produziria offsets negativos sem explicação.
 */
function fmtOffset(base: string | null, iso: string | null): string {
  if (!base || !iso) return ''
  const ms = new Date(iso).getTime() - new Date(base).getTime()
  if (!Number.isFinite(ms) || ms < 0) return ''
  return `+${fmtDurationMs(ms)}`
}

// J4 — cor do badge de desfecho do processo (business_outcome).
function outcomeCls(o: string): string {
  if (o === 'resolved') return 'bg-green-light text-green border-green/30'
  if (o === 'failed' || o === 'no_resource') return 'bg-red-light text-red border-red/30'
  if (o === 'timeout' || o === 'escalated') return 'bg-warning-light text-warning border-warning/30'
  return 'bg-surface-alt text-muted border-border'
}

const STATUS_COLORS: Record<string, string> = {
  active:    'bg-primary-light text-primary border-primary/30',
  suspended: 'bg-warning-light text-warning border-warning/30',
  closed:    'bg-surface-alt text-muted border-border',
}

// ── J5b: rótulos dos ENUMS vindos do backend ────────────────────────────────
//
// `status`, `outcome`, `business_outcome` e `channels` chegam da analytics-api como
// valores CRUS (`resolved`, `suspended`, `webhook`, …) e eram renderizados assim — o
// operador via inglês técnico mesmo em pt-BR. As chaves da moldura (títulos, colunas)
// já passavam por `t()`; o que faltava eram os valores.
//
// `defaultValue: raw` é deliberado: um enum novo no backend aparece com o valor cru em
// vez de quebrar a tela ou mostrar a chave i18n. Degrada, não falha.
//
// `t` entra por PARÂMETRO — a regra do projeto proíbe `useTranslation` fora de um
// componente/hook, e estes helpers são chamados de dentro do render.
type TFunc = (key: string, opts?: Record<string, unknown>) => string

/** Status da sessão — reusa `sessions.status.*`, que já existia no namespace. */
function statusLabel(t: TFunc, status: string): string {
  return status ? t(`sessions.status.${status}`, { defaultValue: status }) : '—'
}

function outcomeLabel(t: TFunc, outcome: string): string {
  return outcome ? t(`enums.outcome.${outcome}`, { defaultValue: outcome }) : '—'
}

function channelsLabel(t: TFunc, channels: string[] | undefined): string {
  const list = (channels ?? []).map(c => t(`enums.channel.${c}`, { defaultValue: c }))
  return list.length > 0 ? list.join(', ') : '—'
}

function StatusBadge({ t, status }: { t: TFunc; status: string }) {
  const cls = STATUS_COLORS[status] ?? 'bg-surface-alt text-muted border-border'
  return (
    <span className={`inline-block text-xs px-2 py-0.5 rounded border font-medium ${cls}`}
      title={status}>
      {statusLabel(t, status)}
    </span>
  )
}

// ── Level 1: journeys list ──────────────────────────────────────────────────

function JourneysList({ tenantId, onSelect }: { tenantId: string; onSelect: (j: JourneyRow) => void }) {
  const { t } = useTranslation('contacts')
  const [fromDt,     setFromDt]     = useState(iso30DaysAgo)
  const [toDt,       setToDt]       = useState(isoToday)
  const [significant, setSignificant] = useState(true)
  const [rows,   setRows]   = useState<JourneyRow[]>([])
  const [total,  setTotal]  = useState(0)
  const [loading, setLoading] = useState(false)
  const [error,  setError]  = useState<string | null>(null)

  const load = useCallback(() => {
    if (!tenantId) return
    setLoading(true); setError(null)
    const q = new URLSearchParams({
      tenant_id: tenantId,
      from_dt: fromDt,
      to_dt: toDt,
      significant_only: String(significant),
      page_size: '200',
    })
    apiFetch(`/reports/journeys?${q.toString()}`)
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then(d => { setRows(d.data ?? []); setTotal(d.meta?.total ?? (d.data?.length ?? 0)) })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }, [tenantId, fromDt, toDt, significant])

  useEffect(() => { load() }, [load])

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Filter bar */}
      <div className="bg-white border-b border-border px-5 py-2.5 flex items-center gap-3 flex-shrink-0 flex-wrap">
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-muted">{t('journeys.filters.from', { defaultValue: 'De' })}</label>
          <input type="date" value={fromDt} onChange={e => setFromDt(e.target.value)}
            className="text-xs border border-border-strong rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-primary/40" />
        </div>
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-muted">–</label>
          <input type="date" value={toDt} onChange={e => setToDt(e.target.value)}
            className="text-xs border border-border-strong rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-primary/40" />
        </div>
        <label className="flex items-center gap-1.5 text-xs text-muted cursor-pointer select-none">
          <input type="checkbox" checked={significant} onChange={e => setSignificant(e.target.checked)}
            className="accent-primary" />
          {t('journeys.filters.significantOnly', { defaultValue: 'Só significativas' })}
        </label>
        <div className="flex-1" />
        {loading
          ? <Spinner />
          : <button onClick={load} className="text-xs text-muted-light hover:text-muted transition-colors px-2 py-1">
              {t('journeys.refresh', { defaultValue: 'Atualizar' })}
            </button>}
      </div>

      {/* Count */}
      <div className="flex items-center px-5 py-2 bg-white border-b border-border flex-shrink-0 text-xs">
        <span className="text-muted-light">
          {loading
            ? <span className="animate-spin inline-block">⟳</span>
            : <strong className="text-dark">{t('journeys.totalCount', { count: total, defaultValue: `${total} processos` })}</strong>}
        </span>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto px-5 py-4">
        {error ? (
          <div className="flex flex-col items-center justify-center py-20 text-red gap-2">
            <FileText className="w-10 h-10 opacity-30" aria-hidden="true" />
            <span className="text-sm font-medium">{t('journeys.error', { defaultValue: 'Erro ao carregar processos' })}</span>
            <span className="text-xs text-muted font-mono max-w-lg text-center break-all">{error}</span>
          </div>
        ) : rows.length === 0 && !loading ? (
          <div className="flex flex-col items-center justify-center py-20 text-muted-light gap-2">
            <GitBranch className="w-10 h-10 opacity-30" aria-hidden="true" />
            <span className="text-sm">{t('journeys.empty', { defaultValue: 'Nenhum processo no período' })}</span>
          </div>
        ) : (
          <table className="w-full text-xs bg-white border border-border rounded-lg overflow-hidden border-separate border-spacing-0">
            <thead className="sticky top-0 z-10 bg-surface-muted border-b border-border">
              <tr>
                <th className="px-3 py-2.5 text-left text-muted font-medium">{t('journeys.columns.journey', { defaultValue: 'Processo (raiz)' })}</th>
                <th className="px-3 py-2.5 text-left text-muted font-medium">{t('journeys.columns.sessions', { defaultValue: 'Sessões' })}</th>
                <th className="px-3 py-2.5 text-left text-muted font-medium">{t('journeys.columns.channels', { defaultValue: 'Canais' })}</th>
                <th className="px-3 py-2.5 text-left text-muted font-medium">{t('journeys.columns.started', { defaultValue: 'Início' })}</th>
                <th className="px-3 py-2.5 text-left text-muted font-medium">{t('journeys.columns.duration', { defaultValue: 'Duração' })}</th>
                <th className="px-3 py-2.5 text-left text-muted font-medium">{t('journeys.columns.outcome', { defaultValue: 'Desfecho' })}</th>
                <th className="px-3 py-2.5 text-left text-muted font-medium">{t('journeys.columns.nps', { defaultValue: 'NPS' })}</th>
                <th className="px-3 py-2.5 text-left text-muted font-medium">{t('journeys.columns.open', { defaultValue: 'Abertas' })}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(j => (
                <tr key={j.journey_id} onClick={() => onSelect(j)}
                  className="border-t border-border hover:bg-surface-muted transition-colors cursor-pointer">
                  <td className="px-3 py-2.5 font-mono text-primary" title={j.journey_id}>
                    <span className="inline-flex items-center gap-1.5">
                      <GitBranch className="w-3.5 h-3.5 opacity-60" aria-hidden="true" />
                      {journeyLabel(j.journey_id)}
                    </span>
                  </td>
                  <td className="px-3 py-2.5">
                    <span className={`font-medium ${j.session_count > 1 ? 'text-dark' : 'text-muted-light'}`}>
                      {j.session_count}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 text-muted-light" title={(j.channels ?? []).join(', ')}>
                    {channelsLabel(t, j.channels)}
                  </td>
                  <td className="px-3 py-2.5 text-muted-light whitespace-nowrap">{fmtDate(j.started_at)}</td>
                  <td className="px-3 py-2.5 text-muted-light whitespace-nowrap">{fmtDuration(j.started_at, j.last_activity_at)}</td>
                  {/* T2 — o desfecho é o da RAIZ (o processo), não o da última sessão
                      aberta (que numa journey de survey seria o da PESQUISA).
                      Enquanto houver sessão aberta ele é PROVISÓRIO: antes, a tela dizia
                      "Resolvido" e "Abertas: 1" ao mesmo tempo — uma contradição. */}
                  <td className="px-3 py-2.5">
                    {j.business_outcome
                      ? <span className="inline-flex items-center gap-1">
                          <span className={`inline-block text-xs px-2 py-0.5 rounded border font-medium ${outcomeCls(j.business_outcome)} ${j.open_count > 0 ? 'opacity-60 border-dashed' : ''}`}
                            title={j.open_count > 0
                              ? t('journeys.provisionalHint', { defaultValue: 'Provisório: o processo ainda tem sessões abertas' })
                              : j.business_outcome}>
                            {outcomeLabel(t, j.business_outcome)}
                          </span>
                          {j.open_count > 0 && (
                            <span className="text-[10px] text-muted-light italic">
                              {t('journeys.provisional', { defaultValue: 'provisório' })}
                            </span>
                          )}
                        </span>
                      : <span className="text-border-strong">—</span>}
                  </td>
                  <td className="px-3 py-2.5 whitespace-nowrap">
                    {j.nps_avg != null
                      ? <span className="text-dark font-medium">{j.nps_avg}
                          <span className="ml-1 text-muted-light font-normal">({j.signal_count ?? 0})</span>
                        </span>
                      : (j.signal_count
                          ? <span className="text-muted-light">({j.signal_count})</span>
                          : <span className="text-border-strong">—</span>)}
                  </td>
                  <td className="px-3 py-2.5 text-muted-light">{j.open_count > 0 ? j.open_count : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

// ── J4 / Item 1: painel de sinal N3 (o processo como um todo) ────────────────
//
// O sinal N3 — desfecho do processo (raiz) + NPS/CSAT/CES da pesquisa grão=journey —
// já vinha do /reports/journeys, mas só aparecia na LISTA (L1) e na Voz do Cliente,
// nunca DENTRO do drill. Aqui ele fica pendurado no cabeçalho do L2, sobre a mesma
// JourneyRow que a lista já carregou (fluxo por clique). `csat_avg`/`ces_avg` passam
// a ser renderizados — antes chegavam no tipo mas nenhum JSX os lia.
function SignalStat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <span className="inline-flex items-baseline gap-1">
      <span className="text-muted-light">{label}</span>
      <span className="text-dark font-medium">{value}</span>
    </span>
  )
}

function JourneySignalPanel({ t, j }: { t: TFunc; j: JourneyRow }) {
  const hasSignal = j.nps_avg != null || j.csat_avg != null || j.ces_avg != null || (j.signal_count ?? 0) > 0
  // Nada a mostrar (nem desfecho, nem duração, nem sinal) → não polui o cabeçalho.
  if (!j.business_outcome && j.business_duration_ms == null && !hasSignal) return null
  return (
    <div className="bg-white border-b border-border px-5 py-2 flex items-center gap-4 flex-wrap text-xs flex-shrink-0">
      <span className="text-[10px] uppercase tracking-wide text-muted font-medium">
        {t('journeys.signal.title', { defaultValue: 'Sinal do processo' })}
      </span>
      {j.business_outcome && (
        <span className="inline-flex items-center gap-1">
          <span className="text-muted-light">{t('journeys.signal.outcome', { defaultValue: 'Desfecho' })}</span>
          <span className={`inline-block px-2 py-0.5 rounded border font-medium ${outcomeCls(j.business_outcome)} ${j.open_count > 0 ? 'opacity-60 border-dashed' : ''}`}
            title={j.open_count > 0
              ? t('journeys.provisionalHint', { defaultValue: 'Provisório: o processo ainda tem sessões abertas' })
              : j.business_outcome}>
            {outcomeLabel(t, j.business_outcome)}
          </span>
          {j.open_count > 0 && (
            <span className="text-[10px] text-muted-light italic">{t('journeys.provisional', { defaultValue: 'provisório' })}</span>
          )}
        </span>
      )}
      {j.business_duration_ms != null && (
        <SignalStat label={t('journeys.signal.duration', { defaultValue: 'Duração' })} value={fmtDurationMs(j.business_duration_ms)} />
      )}
      {j.nps_avg  != null && <SignalStat label="NPS"  value={j.nps_avg} />}
      {j.csat_avg != null && <SignalStat label="CSAT" value={j.csat_avg} />}
      {j.ces_avg  != null && <SignalStat label="CES"  value={j.ces_avg} />}
      <span className="text-muted-light">
        {(j.signal_count ?? 0) > 0
          ? t('journeys.signal.count', { count: j.signal_count ?? 0, defaultValue: `${j.signal_count ?? 0} sinais` })
          : t('journeys.signal.none', { defaultValue: 'Sem sinais de pesquisa' })}
      </span>
    </div>
  )
}

// ── Level 2: member sessions of a journey ───────────────────────────────────

function JourneySessions({ tenantId, root, journey, onBack, onSelectSession, onSelectJourney }:
  { tenantId: string; root: string; journey?: JourneyRow | null; onBack: () => void; onSelectSession: (sid: string) => void;
    onSelectJourney: (root: string) => void }) {
  const { t } = useTranslation('contacts')
  const [rows, setRows] = useState<MemberSession[]>([])
  // T5 — filhos que NASCERAM desta journey mas pertencem a OUTRA (`journey: new`).
  // NÃO expandem: viram links. Expandir desfaria o corte que o operador pediu — e a
  // árvore completa (todas as criações, transitivamente) não tem fronteira, logo não é
  // mensurável. A journey se MEDE; a árvore completa se RASTREIA.
  const [spawned, setSpawned] = useState<MemberSession[]>([])
  const [loading, setLoading] = useState(false)
  // T6 — sessão cujo rastro forense está aberto (drawer). null = fechado.
  const [traceFor, setTraceFor] = useState<string | null>(null)
  // F4/D6 — as lentes A e B **não são dois modelos**: com `opened_at` na linha (custo
  // zero, já vem no shape), são o mesmo componente com dois eixos de ordenação.
  // Árvore responde "quem criou quem"; cronologia responde "o que aconteceu quando".
  const [lens, setLens] = useState<Lens>('tree')
  // F4/D11 — etapas internas escondidas por default. **Visibilidade, nunca contagem**:
  // o cabeçalho reporta os dois domínios em qualquer estado do toggle. Foi por
  // misturar as duas coisas que a divergência "cabeçalho diz 3, tabela mostra 4"
  // existia — e ela some sem inventar um quarto número.
  const [showAllInternal, setShowAllInternal] = useState(false)
  // …e o desdobramento LOCAL, por acesso. O toggle global é o de D11; este é o que
  // permite abrir a maquinaria de UM acesso sem encher a tela com a dos outros.
  const [openGroups, setOpenGroups] = useState<Set<string>>(new Set())
  // Item 1 / Fatia 2 — deep-link ao L2: a JourneyRow não veio do L1. Rebusca a própria
  // linha via /reports/journeys?root_session_id= (fetch direcionado, resolve canônico,
  // ignora janela+significant). Se o L1 já passou a `journey`, não busca nada.
  const [fetchedJourney, setFetchedJourney] = useState<JourneyRow | null>(null)
  useEffect(() => {
    if (journey || !tenantId || !root) { setFetchedJourney(null); return }
    let cancelled = false
    apiFetch(`/reports/journeys?${new URLSearchParams({
      tenant_id: tenantId, root_session_id: root, significant_only: 'false', page_size: '1',
    })}`)
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then(d => { if (!cancelled) setFetchedJourney(d.data?.[0] ?? null) })
      .catch(() => { if (!cancelled) setFetchedJourney(null) })
    return () => { cancelled = true }
  }, [journey, tenantId, root])
  const shownJourney = journey ?? fetchedJourney

  useEffect(() => {
    if (!tenantId || !root) return
    let cancelled = false
    setLoading(true)

    const members = apiFetch(`/reports/sessions?${new URLSearchParams({
      tenant_id: tenantId, root_session_id: root, page_size: '200',
    })}`).then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))

    const crossing = apiFetch(`/reports/sessions?${new URLSearchParams({
      tenant_id: tenantId, spawned_from_root: root, page_size: '50',
    })}`).then(r => r.ok ? r.json() : { data: [] }).catch(() => ({ data: [] }))

    Promise.all([members, crossing])
      .then(([m, c]) => {
        if (cancelled) return
        setRows(m.data ?? [])
        setSpawned(c.data ?? [])
      })
      .catch(() => { if (!cancelled) { setRows([]); setSpawned([]) } })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [tenantId, root])

  const tree = buildTree(rows)
  const treeNodes = flattenTree(tree)
  const groups = buildGroups(tree)

  // ── F4 — os DOIS domínios de contagem, nunca somados (D4) ─────────────────
  // O cabeçalho conta ACESSOS DO CLIENTE. As etapas internas são reportadas ao
  // lado, e as não classificadas só aparecem quando existem — um "· 0 não
  // classificadas" fixo seria ruído com cara de informação.
  const accessCount  = rows.filter(s => lineClass(s) === 'access').length
  const internalCount = rows.filter(s => lineClass(s) === 'internal').length
  const unknownCount  = rows.filter(s => lineClass(s) === 'unknown').length

  // Base do offset: o menor `opened_at` do conjunto. Ver `fmtOffset`.
  const baseOpenedAt = rows.reduce<string | null>(
    (min, s) => (!min || (s.opened_at && s.opened_at < min) ? (s.opened_at ?? min) : min), null)

  // Quantas internas cada acesso esconde — é o número do rótulo dobrado.
  const hiddenByGroup = new Map<string, number>()
  for (const s of rows) {
    if (lineClass(s) !== 'internal') continue
    const g = groups.get(s.session_id) ?? ORPHAN_GROUP
    hiddenByGroup.set(g, (hiddenByGroup.get(g) ?? 0) + 1)
  }
  const groupOpen = (g: string) => showAllInternal || openGroups.has(g)
  const toggleGroup = (g: string) => setOpenGroups(prev => {
    const next = new Set(prev)
    if (next.has(g)) next.delete(g)
    else next.add(g)
    return next
  })

  // ── F4/D6 — a ORDEM, que é a única coisa que separa as duas lentes ─────────
  //
  // `tree`   — caminhada de PROVENIÊNCIA, indentada por profundidade. Responde
  //            *quem criou quem*, e por isso agrupa a maquinaria sob o acesso que a
  //            originou. Esconde a SOBREPOSIÇÃO: um acesso que rodou dentro da
  //            janela de outro aparece como irmão — é por isso que o offset ao lado
  //            do horário deixou de ser opcional.
  // `chrono` — **ordem global estrita por `opened_at`, sem agrupamento nenhum.**
  //            Responde *o que aconteceu quando*, e nada mais.
  //
  // ⚠️ **Emenda ao ADR D6, medida na tela (2026-08-25).** O desenho dizia
  // *"ordenação por started_at, contato como cabeçalho de grupo"* — as duas coisas
  // juntas. Implementado assim, a maquinaria ficava presa ao acesso de origem e
  // uma etapa das 17:11:31 era renderizada ACIMA de um acesso das 17:10:52. Pior:
  // com as internas dobradas (o default), as duas lentes produziam **exatamente as
  // mesmas linhas**, porque os acessos já saem em ordem na árvore. Um controle que
  // não muda nada no caso comum é indistinguível de um controle quebrado — a mesma
  // família do seletor «Inbound/Outbound» que a F3 removeu por não filtrar nada.
  //
  // O agrupamento por acesso NÃO se perde: ele é a lente árvore, do lado. O que se
  // ganha é uma lente que faz o que o nome promete, e a diferença entre as duas
  // passa a ser visível na primeira olhada.
  interface RenderRow { s: MemberSession; depth: number; cls: LineClass; group: string }
  const byOpened = (a: MemberSession, b: MemberSession) =>
    (a.opened_at ?? '').localeCompare(b.opened_at ?? '')

  const isTree = lens === 'tree'
  const ordered: RenderRow[] = isTree
    ? treeNodes.map(n => ({
        s: n.session, depth: n.depth, cls: lineClass(n.session),
        group: groups.get(n.session.session_id) ?? ORPHAN_GROUP,
      }))
    : [...rows].sort(byOpened).map(s => ({
        s, depth: 0, cls: lineClass(s),
        group: groups.get(s.session_id) ?? ORPHAN_GROUP,
      }))
  // As órfãs precisam de um dono visível para pendurar o rótulo dobrado. Só na
  // ÁRVORE: na cronologia não há grupo a rotular, e o marcador seria uma promessa
  // de agrupamento que aquela lente não faz. `null` = não há órfã.
  const firstOrphanId = isTree
    ? (ordered.find(r => r.group === ORPHAN_GROUP)?.s.session_id ?? null)
    : null

  // Arestas cruzando, agrupadas pelo PAI — para pendurar o marcador na linha certa.
  const crossingByParent = new Map<string, MemberSession[]>()
  for (const s of spawned) {
    if (!s.origin_session_id) continue
    const list = crossingByParent.get(s.origin_session_id) ?? []
    list.push(s)
    crossingByParent.set(s.origin_session_id, list)
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Breadcrumb */}
      <div className="bg-white border-b border-border px-5 py-2.5 flex items-center gap-2 text-xs flex-shrink-0 sticky top-0 z-10">
        <button onClick={onBack} className="text-muted-light hover:text-dark transition-colors font-medium">
          {t('journeys.breadcrumb', { defaultValue: 'Processos' })}
        </button>
        <ChevronRight className="w-3.5 h-3.5 text-border-strong" aria-hidden="true" />
        <span className="text-dark font-medium font-mono" title={root}>{journeyLabel(root)}</span>

        {/* ── F4/D4 — os DOIS domínios, e por que não há um total ──────────────
            "3 sessões" está certo sobre a tabela e errado sobre o caso: um
            processo de 3 sessões pode ter 1 acesso do cliente e 2 etapas de
            maquinaria. O número protagonista é o de ACESSOS; as etapas internas
            aparecem ao lado, nunca somadas — e é essa separação (não um toggle)
            que dissolve o "cabeçalho diz 3, tabela mostra 4" registrado no D11. */}
        <span className="ml-1 text-dark font-medium" title={t('journeys.countsHint')}>
          · {t('journeys.accessCount', { count: accessCount })}
        </span>
        {internalCount > 0 && (
          <span className="text-muted-light">· {t('journeys.internalCount', { count: internalCount })}</span>
        )}
        {/* Só aparece quando existe: um "· 0 não classificadas" fixo seria ruído
            com cara de informação. Quando aparece, é para ser lido. */}
        {unknownCount > 0 && (
          <span className="text-warning" title={t('journeys.unclassifiedHint')}>
            · {t('journeys.unclassifiedCount', { count: unknownCount })}
          </span>
        )}

        <span className="flex-1" />

        {/* D6 — mesmo componente, dois eixos de ordenação. */}
        <div className="inline-flex rounded border border-border overflow-hidden" role="group"
          title={t('journeys.lens.hint')}>
          {(['tree', 'chrono'] as Lens[]).map(l => (
            <button key={l} onClick={() => setLens(l)}
              aria-pressed={lens === l}
              className={`px-2 py-0.5 transition-colors ${
                lens === l ? 'bg-primary text-white' : 'bg-white text-muted hover:text-dark'}`}>
              {t(`journeys.lens.${l}`)}
            </button>
          ))}
        </div>

        {/* D11 — visibilidade, nunca contagem: o cabeçalho acima não muda com ele. */}
        {internalCount > 0 && (
          <label className="flex items-center gap-1.5 text-muted cursor-pointer select-none"
            title={t('journeys.showInternalHint')}>
            <input type="checkbox" checked={showAllInternal}
              onChange={e => { setShowAllInternal(e.target.checked); setOpenGroups(new Set()) }}
              className="accent-primary cursor-pointer" />
            {t('journeys.showInternal')}
          </label>
        )}
      </div>

      {/* Item 1 — sinal N3 do processo. Vem do L1 (clique) ou é rebuscado no deep-link (Fatia 2). */}
      {shownJourney && <JourneySignalPanel t={t} j={shownJourney} />}

      <div className="flex-1 overflow-auto px-5 py-4">
        {loading ? (
          <div className="flex items-center gap-2 text-muted-light text-xs py-6"><Spinner /></div>
        ) : rows.length === 0 ? (
          <p className="text-xs text-muted-light py-6">{t('journeys.sessions.empty', { defaultValue: 'Sem sessões' })}</p>
        ) : (
          <table className="w-full text-xs bg-white border border-border rounded-lg overflow-hidden border-separate border-spacing-0">
            <thead className="sticky top-0 z-10 bg-surface-muted border-b border-border">
              <tr>
                <th className="px-3 py-2.5 text-left text-muted font-medium">{t('journeys.sessions.columns.session', { defaultValue: 'Sessão' })}</th>
                <th className="px-3 py-2.5 text-left text-muted font-medium">{t('journeys.sessions.columns.channel', { defaultValue: 'Canal' })}</th>
                <th className="px-3 py-2.5 text-left text-muted font-medium">{t('journeys.sessions.columns.status', { defaultValue: 'Status' })}</th>
                <th className="px-3 py-2.5 text-left text-muted font-medium">{t('journeys.sessions.columns.opened', { defaultValue: 'Aberta' })}</th>
                <th className="px-3 py-2.5 text-left text-muted font-medium">{t('journeys.sessions.columns.segments', { defaultValue: 'Segs' })}</th>
                <th className="px-3 py-2.5 text-left text-muted font-medium">{t('journeys.sessions.columns.outcome', { defaultValue: 'Desfecho' })}</th>
              </tr>
            </thead>
            <tbody>
              {ordered.map(({ s, depth, cls, group }) => {
                const isInternal = cls === 'internal'
                // Na cronologia não há grupo: o único controle é o toggle global.
                // Desdobrar "só este acesso" é uma pergunta de PROVENIÊNCIA, e a
                // lente que a responde é a árvore.
                const open   = isTree ? groupOpen(group) : showAllInternal
                const hidden = hiddenByGroup.get(group) ?? 0
                // Etapa interna dobrada: some da tabela e vira o contador do grupo.
                if (isInternal && !open
                    && !(isTree && group === ORPHAN_GROUP && s.session_id === firstOrphanId)) {
                  return null
                }
                const direction = contactDirection(s)
                return (
                <React.Fragment key={s.session_id}>
                {/* Grupo ÓRFÃO — etapas internas sem acesso ancestral. Ganha rótulo
                    próprio em vez de ser pendurado no primeiro acesso: a origem que
                    o dado não tem não se inventa. */}
                {isTree && group === ORPHAN_GROUP && s.session_id === firstOrphanId && (
                  <tr className="border-t border-border bg-surface-muted/60">
                    <td colSpan={6} className="px-3 py-1.5">
                      <button onClick={() => toggleGroup(ORPHAN_GROUP)}
                        className="inline-flex items-center gap-1.5 text-[11px] text-muted hover:text-dark transition-colors">
                        <span aria-hidden="true">{open ? '▾' : '▸'}</span>
                        {t('journeys.orphanGroup')}
                        <span className="tabular-nums">({hidden})</span>
                      </button>
                    </td>
                  </tr>
                )}
                {(!isInternal || open) && (
                <tr onClick={() => onSelectSession(s.session_id)}
                  className={`group border-t border-border hover:bg-surface-muted transition-colors cursor-pointer ${
                    s.session_id === root ? 'bg-primary-light/30' : ''} ${
                    isInternal ? 'text-muted-light bg-surface-muted/30' : ''}`}>
                  <td className="px-3 py-2.5 font-mono text-dark" title={s.session_id}>
                    {/* T5 — indentação = profundidade (proveniência na lente árvore,
                        pertença ao acesso na cronologia). O rótulo (T4) diz POR QUE o
                        filho existe: sem ele, vê-se a hierarquia mas não o motivo. */}
                    <span style={{ paddingLeft: `${depth * 16}px` }} className="inline-flex items-center gap-1.5">
                      {depth > 0 && (
                        <span className="text-border-strong select-none" aria-hidden="true">└─</span>
                      )}
                      {/* D4/D8 — a CLASSE da linha, no mesmo ícone da visão 1. Direção
                          não classificada mostra `—` e nomeia o valor cru no título:
                          um `spawn_reason` novo não pode cair num balde plausível. */}
                      {direction ? (
                        <span title={t(`lista.direction.${direction}`, { ns: 'contacts' })}
                          aria-label={t(`lista.direction.${direction}`, { ns: 'contacts' })}>
                          {DIRECTION_ICONS[direction]}
                        </span>
                      ) : (
                        <span className="text-warning" title={t('lista.direction.unknownHint', {
                          ns: 'contacts', value: s.spawn_reason ?? '' })}>—</span>
                      )}
                      {truncateId(s.session_id)}
                      {depth > 0 && s.spawn_reason && (
                        <span className="text-[10px] px-1 py-0.5 rounded bg-surface-alt text-muted border border-border"
                          title={s.spawn_reason}>
                          {t(`journeys.spawn.${s.spawn_reason}`, { defaultValue: s.spawn_reason })}
                        </span>
                      )}
                    </span>
                    {s.session_id === root && (
                      <span className="ml-1.5 text-[10px] text-primary uppercase tracking-wide">
                        {t('journeys.rootBadge', { defaultValue: 'raiz' })}
                      </span>
                    )}
                    {/* T6 — abre o rastro forense (proveniência bidirecional) desta sessão. */}
                    <button
                      onClick={e => { e.stopPropagation(); setTraceFor(s.session_id) }}
                      title={t('journeys.trace.open', { defaultValue: 'Rastro' })}
                      className="ml-2 inline-flex items-center align-middle text-muted-light hover:text-primary transition-colors opacity-0 group-hover:opacity-100 focus:opacity-100">
                      <Route className="w-3.5 h-3.5" aria-hidden="true" />
                    </button>
                  </td>
                  <td className="px-3 py-2.5 text-muted-light" title={s.channel}>
                    {s.channel ? t(`enums.channel.${s.channel}`, { defaultValue: s.channel }) : '—'}
                  </td>
                  <td className="px-3 py-2.5"><StatusBadge t={t} status={s.status} /></td>
                  {/* D6 — absoluto + OFFSET desde a abertura do processo. Dois
                      horários absolutos em indentações diferentes não se comparam de
                      olho; com o `+7m54s` ao lado, o aninhamento salta. É também o
                      único lugar em que a SOBREPOSIÇÃO fica legível na lente árvore,
                      que mostra como irmão o acesso que rodou dentro de outro. */}
                  <td className="px-3 py-2.5 text-muted-light whitespace-nowrap">
                    {fmtDate(s.opened_at)}
                    {baseOpenedAt && s.opened_at && s.opened_at !== baseOpenedAt && (
                      <span className="ml-1.5 text-border-strong tabular-nums"
                        title={t('journeys.offsetHint')}>
                        {fmtOffset(baseOpenedAt, s.opened_at)}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2.5 text-muted-light">{s.segment_count || 0}</td>
                  <td className="px-3 py-2.5 text-muted-light" title={s.outcome ?? ''}>
                    {s.outcome
                      ? outcomeLabel(t, s.outcome)
                      : <span className="text-border-strong">—</span>}
                  </td>
                </tr>
                )}

                {/* D4/D11 — a maquinaria DOBRADA sob o acesso que a originou.
                    Contada, nunca omitida em silêncio: sem este rótulo, um processo
                    com duas etapas internas escondidas seria indistinguível de um
                    que não teve nenhuma. Clicar abre só este grupo. */}
                {isTree && !isInternal && hidden > 0 && !open && (
                  <tr className="border-t border-border/50 bg-surface-muted/40 hover:bg-surface-muted cursor-pointer"
                    onClick={() => toggleGroup(group)}>
                    <td colSpan={6} className="px-3 py-1.5">
                      <span style={{ paddingLeft: `${(depth + 1) * 16}px` }}
                        className="inline-flex items-center gap-1.5 text-[11px] text-muted"
                        title={t('journeys.foldedHint')}>
                        <span className="text-border-strong select-none" aria-hidden="true">⋯</span>
                        {t('journeys.internalCount', { count: hidden })}
                      </span>
                    </td>
                  </tr>
                )}

                {/* T5 — ARESTAS QUE ATRAVESSAM A FRONTEIRA (`journey: new`).
                    Este atendimento originou OUTRO processo. Vira um LINK, nunca uma
                    expansão: expandir traria a subárvore de outra journey para dentro
                    desta, desfazendo o corte que o operador pediu — e a árvore completa
                    (todas as criações, transitivamente) não tem fronteira, logo não é
                    mensurável. Percorre-se navegando; não se encara de uma vez. */}
                {(crossingByParent.get(s.session_id) ?? []).map(child => (
                  <tr key={`x-${child.session_id}`}
                    onClick={e => { e.stopPropagation(); onSelectJourney(child.root_session_id) }}
                    className="border-t border-border/50 bg-surface-muted/40 hover:bg-surface-muted cursor-pointer">
                    <td colSpan={6} className="px-3 py-1.5">
                      <span style={{ paddingLeft: `${(depth + 1) * 16}px` }}
                        className="inline-flex items-center gap-1.5 text-[11px] text-muted">
                        <span className="text-border-strong select-none" aria-hidden="true">└─</span>
                        <span>↗</span>
                        <span>{t('journeys.spawnedProcess', { defaultValue: 'originou o processo' })}</span>
                        <span className="font-mono text-primary">{journeyLabel(child.root_session_id)}</span>
                        {child.spawn_reason && (
                          <span className="text-[10px] px-1 py-0.5 rounded bg-white text-muted border border-border">
                            {t(`journeys.spawn.${child.spawn_reason}`, { defaultValue: child.spawn_reason })}
                          </span>
                        )}
                      </span>
                    </td>
                  </tr>
                ))}
                </React.Fragment>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {traceFor && (
        <TraceDrawer
          tenantId={tenantId}
          initialFocus={traceFor}
          onClose={() => setTraceFor(null)}
          onOpenJourney={r => { setTraceFor(null); onSelectJourney(r) }}
        />
      )}
    </div>
  )
}

// ── T6: rastro forense — drawer ──────────────────────────────────────────────
//
// Abre a partir de uma sessão na Vista Processos: a cadeia de proveniência em
// volta dela, ATRAVESSANDO fronteiras de journey. O foco é re-centrável (clicar
// num nó re-ancora o rastro nele → percorre-se o grafo por navegação, §6 da
// spec: "percorre-se navegando pelos links; nenhuma tela precisa renderizá-lo").
// O selo da journey de um nó de fronteira leva à Vista Processos daquela journey.
function TraceDrawer({ tenantId, initialFocus, onClose, onOpenJourney }:
  { tenantId: string; initialFocus: string; onClose: () => void; onOpenJourney: (root: string) => void }) {
  const { t } = useTranslation('contacts')
  const [focus, setFocus]     = useState(initialFocus)
  const [resp, setResp]       = useState<TraceResp | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!tenantId || !focus) return
    let cancelled = false
    setLoading(true)
    apiFetch(`/reports/sessions/${encodeURIComponent(focus)}/trace?${new URLSearchParams({ tenant_id: tenantId })}`)
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then(d => { if (!cancelled) setResp(d) })
      .catch(() => { if (!cancelled) setResp(null) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [tenantId, focus])

  const flat = resp ? flattenTraceTree(buildTraceTree(resp.nodes)) : []

  return (
    <div className="fixed inset-0 z-40 flex justify-end" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-dark/30" onClick={onClose} aria-hidden="true" />
      <div className="relative w-full max-w-2xl h-full bg-white shadow-xl flex flex-col animate-in slide-in-from-right">
        {/* Header */}
        <div className="flex items-start justify-between px-5 py-3 border-b border-border flex-shrink-0">
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 text-sm font-medium text-dark">
              <Route className="w-4 h-4 text-primary" aria-hidden="true" />
              {t('journeys.trace.title', { defaultValue: 'Rastro de proveniência' })}
            </div>
            <p className="text-xs text-muted-light mt-0.5 pr-4">
              {t('journeys.trace.subtitle', { defaultValue: 'O que esta sessão gerou — e de onde veio — atravessando fronteiras de processo' })}
            </p>
          </div>
          <button onClick={onClose} title={t('journeys.trace.close', { defaultValue: 'Fechar' })}
            className="text-muted-light hover:text-dark transition-colors flex-shrink-0 -mr-1">
            <X className="w-4 h-4" aria-hidden="true" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-auto px-3 py-3">
          {loading ? (
            <div className="flex items-center gap-2 text-muted-light text-xs py-8 px-2">
              <Spinner /> {t('journeys.trace.loading', { defaultValue: 'Carregando rastro…' })}
            </div>
          ) : !resp || resp.nodes.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 text-muted-light gap-2">
              <Route className="w-9 h-9 opacity-30" aria-hidden="true" />
              <span className="text-xs">{t('journeys.trace.empty', { defaultValue: 'Sem proveniência registrada para esta sessão' })}</span>
            </div>
          ) : (
            <>
              {resp.meta?.truncated && (
                <div className="text-[11px] text-warning bg-warning-light border border-warning/30 rounded px-2 py-1 mb-2">
                  {t('journeys.trace.truncated', { defaultValue: 'Rastro truncado (limite atingido)' })}
                </div>
              )}
              <ul className="space-y-0.5">
                {flat.map(({ node: n, depth }) => (
                  <li key={n.session_id}>
                    <div
                      className={`group flex items-center gap-2 rounded px-2 py-1.5 border ${
                        n.is_focus
                          ? 'bg-primary-light/40 border-primary/30'
                          : n.journey_boundary
                            ? 'bg-surface-muted/60 border-dashed border-border'
                            : 'border-transparent hover:bg-surface-muted'
                      }`}
                      style={{ marginLeft: `${depth * 18}px` }}
                    >
                      {depth > 0 && (
                        <span className="text-border-strong select-none text-xs" aria-hidden="true">└─</span>
                      )}
                      {/* Clicar no id RE-CENTRA o rastro neste nó (percorrer o grafo). */}
                      <button
                        onClick={() => { if (n.session_id !== focus) setFocus(n.session_id) }}
                        title={n.session_id === focus
                          ? n.session_id
                          : t('journeys.trace.recenter', { defaultValue: 'Centralizar o rastro aqui' })}
                        className="font-mono text-xs text-dark hover:text-primary transition-colors truncate">
                        {truncateId(n.session_id)}
                      </button>

                      {n.is_focus && (
                        <span className="text-[10px] text-primary uppercase tracking-wide flex-shrink-0">
                          {t('journeys.trace.focus', { defaultValue: 'foco' })}
                        </span>
                      )}

                      {/* Rótulo da aresta (T4) — por que este nó existe. */}
                      {n.spawn_reason && (
                        <span className="text-[10px] px-1 py-0.5 rounded bg-surface-alt text-muted border border-border flex-shrink-0"
                          title={n.spawn_reason}>
                          {t(`journeys.spawn.${n.spawn_reason}`, { defaultValue: n.spawn_reason })}
                        </span>
                      )}

                      {n.channel && (
                        <span className="text-[11px] text-muted-light flex-shrink-0" title={n.channel}>
                          {t(`enums.channel.${n.channel}`, { defaultValue: n.channel })}
                        </span>
                      )}
                      <StatusBadge t={t} status={n.status} />

                      <span className="flex-1" />

                      {/* Fronteira de journey → selo PRC- que LEVA àquela journey. */}
                      {n.journey_boundary ? (
                        <button
                          onClick={() => onOpenJourney(n.journey_id)}
                          title={t('journeys.trace.openJourney', { defaultValue: 'Abrir processo' })}
                          className="inline-flex items-center gap-1 text-[10px] font-mono px-1.5 py-0.5 rounded border border-accent/40 bg-accent/5 text-accent hover:bg-accent/10 transition-colors flex-shrink-0">
                          <span aria-hidden="true">↗</span>
                          {journeyLabel(n.journey_id)}
                        </button>
                      ) : (
                        <span className="text-[10px] font-mono text-muted-light flex-shrink-0" title={n.journey_id}>
                          {journeyLabel(n.journey_id)}
                        </span>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
              <div className="text-[11px] text-muted-light mt-3 px-2">
                {t('journeys.trace.nodeCount', { count: resp.meta?.node_count ?? resp.nodes.length,
                   defaultValue: `${resp.nodes.length} sessões` })}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Main page ───────────────────────────────────────────────────────────────

export default function AnaliseJourneysPage() {
  const { tenantId } = useAuth()
  const { t } = useTranslation('contacts')
  const [searchParams, setSearchParams] = useSearchParams()
  // Item 1 — a JourneyRow selecionada no L1, para o painel de sinal N3 do L2 sem
  // re-fetch. Deep-link direto ao L2 (sem passar pelo L1) fica null → painel oculto
  // (Fatia 2 = filtro root no /reports/journeys resolveria isso).
  const [selectedJourney, setSelectedJourney] = useState<JourneyRow | null>(null)

  const root      = searchParams.get('journey')
  const sessionId = searchParams.get('session')

  // Redirect do endereço LEGADO `?journey=…&session=…` (ver o bloco do L3 removido,
  // abaixo). Em efeito, não no render: `setSearchParams` durante o render é efeito
  // colateral em fase de render, e o React pode reexecutá-la.
  useEffect(() => {
    if (root && sessionId) {
      setSearchParams({ journey: root, session_id: sessionId }, { replace: true })
    }
  }, [root, sessionId])   // eslint-disable-line react-hooks/exhaustive-deps

  if (!tenantId) {
    return (
      <div className="flex items-center justify-center h-full text-muted-light text-sm">
        {t('journeys.noTenant', { defaultValue: 'Sem tenant' })}
      </div>
    )
  }

  // ── L3 REMOVIDO — o nível de sessão é UM só (2026-08-25) ──────────────────
  //
  // Aqui vivia um `SessionTranscript` **incondicional**, e era a segunda
  // implementação do nível de sessão: pela lista, `?session_id=` escolhe o
  // renderer pelo CANAL (`webhook` → trace de workflow; demais → segmentos);
  // por aqui, tudo virava transcrição. A mesma sessão interna aparecia como
  // "Workflow trace" por um caminho e como transcrição VAZIA pelo outro, e
  // nada ficava vermelho — o operador só via uma tela sem eventos e concluía
  // que o dado não existia.
  //
  // Hoje o clique numa sessão-membro vai para `?journey=…&session_id=…`, que o
  // `SessionsPage` trata com o MESMO drill da lista, carregando o processo no
  // breadcrumb. Mesma correção que a F4 fez com a direção do acesso: uma casa.
  //
  // `?journey=…&session=…` (o endereço antigo) sobrevive como REDIRECT — pode
  // haver favorito e há histórico de navegador apontando para ele.
  if (root && sessionId) return null   // o efeito acima já reescreveu a URL

  // L2 — member sessions
  if (root) {
    return (
      <JourneySessions
        tenantId={tenantId}
        root={root}
        journey={selectedJourney}
        onBack={() => setSearchParams({})}
        // O clique numa sessão-membro leva ao drill ÚNICO (o mesmo da lista),
        // preservando o processo na URL — é o `journey` que o breadcrumb de lá usa
        // para oferecer a volta. Não existe mais um nível de sessão daqui.
        onSelectSession={sid => setSearchParams({ journey: root, session_id: sid })}
        // T5: navegar para a journey vizinha (a que nasceu daqui com `journey: new`).
        // O grafo completo é PERCORRIDO por links, não renderizado de uma vez. A linha
        // dessa journey vizinha não está em memória → limpa o painel (não mostra dado
        // da journey errada); reaparece se vier de um clique no L1.
        onSelectJourney={r => { setSelectedJourney(null); setSearchParams({ journey: r }) }}
      />
    )
  }

  // L1 — journeys list
  return (
    <div className="flex flex-col h-full overflow-hidden bg-surface-muted">
      <JourneysList tenantId={tenantId}
        onSelect={row => { setSelectedJourney(row); setSearchParams({ journey: row.journey_id }) }} />
    </div>
  )
}
