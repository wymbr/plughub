/**
 * AnaliseJourneysPage — Vista Processos (Journey J2) — /analise/processos
 *
 * Lente por PROVENIÊNCIA (agrupa sessions por root_session_id) — não a entidade
 * Journey do Arc 10 (removida). Drill 3 níveis por URL-param:
 *   L1: /analise/processos                          — lista de journeys
 *   L2: /analise/processos?journey=:root            — sessões-membro da journey
 *   L3: /analise/processos?journey=:root&session=:s — SessionTranscript
 *
 * Fonte: GET /reports/journeys + GET /reports/sessions?root_session_id= (analytics-api).
 * Sem alias/merge (isso é J3): cada grupo é uma árvore de proveniência.
 */
import React, { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ChevronRight, GitBranch, FileText } from 'lucide-react'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'
import { SessionTranscript } from '@/modules/service/components/SessionTranscript'

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
function fmtDuration(from: string, to?: string | null): string {
  if (!from || !to) return '—'
  const ms = new Date(to).getTime() - new Date(from).getTime()
  if (ms < 0) return '—'
  if (ms < 60_000) return `${Math.round(ms / 1000)}s`
  const m = Math.floor(ms / 60_000); const s = Math.round((ms % 60_000) / 1000)
  return s > 0 ? `${m}m ${s}s` : `${m}m`
}
function truncateId(id: string | undefined | null): string {
  if (!id) return '—'
  return id.length > 16 ? `…${id.slice(-12)}` : id
}

/**
 * T5 — rótulo da journey com prefixo.
 *
 * A journey é identificada pela RAIZ CANÔNICA, ou seja, o `journey_id` **é** um
 * `session_id`. Isso é correto no modelo (como um branch do git é identificado por um
 * hash de commit), mas exibir o UUID cru, idêntico ao da sessão, convida exatamente à
 * confusão "o processo é a mesma coisa que a sessão?".
 *
 * O prefixo conserta a APRESENTAÇÃO sem tocar no modelo — nada de entidade Journey
 * (Arc 10), nada de id cunhado, nada para sincronizar.
 */
function journeyLabel(id: string | undefined | null): string {
  if (!id) return '—'
  return `PRC-${id.slice(0, 8)}`
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

function JourneysList({ tenantId, onSelect }: { tenantId: string; onSelect: (root: string) => void }) {
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
    fetch(`/reports/journeys?${q.toString()}`)
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
                <tr key={j.journey_id} onClick={() => onSelect(j.journey_id)}
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

// ── Level 2: member sessions of a journey ───────────────────────────────────

function JourneySessions({ tenantId, root, onBack, onSelectSession, onSelectJourney }:
  { tenantId: string; root: string; onBack: () => void; onSelectSession: (sid: string) => void;
    onSelectJourney: (root: string) => void }) {
  const { t } = useTranslation('contacts')
  const [rows, setRows] = useState<MemberSession[]>([])
  // T5 — filhos que NASCERAM desta journey mas pertencem a OUTRA (`journey: new`).
  // NÃO expandem: viram links. Expandir desfaria o corte que o operador pediu — e a
  // árvore completa (todas as criações, transitivamente) não tem fronteira, logo não é
  // mensurável. A journey se MEDE; a árvore completa se RASTREIA.
  const [spawned, setSpawned] = useState<MemberSession[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!tenantId || !root) return
    let cancelled = false
    setLoading(true)

    const members = fetch(`/reports/sessions?${new URLSearchParams({
      tenant_id: tenantId, root_session_id: root, page_size: '200',
    })}`).then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))

    const crossing = fetch(`/reports/sessions?${new URLSearchParams({
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
  const nodes = flattenTree(tree)
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
        <span className="ml-1 text-muted-light">· {t('journeys.memberCount', { count: rows.length, defaultValue: `${rows.length} sessões` })}</span>
      </div>

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
              {nodes.map(({ session: s, depth }) => (
                <React.Fragment key={s.session_id}>
                <tr onClick={() => onSelectSession(s.session_id)}
                  className={`border-t border-border hover:bg-surface-muted transition-colors cursor-pointer ${
                    s.session_id === root ? 'bg-primary-light/30' : ''}`}>
                  <td className="px-3 py-2.5 font-mono text-dark" title={s.session_id}>
                    {/* T5 — indentação = profundidade na árvore de proveniência.
                        O rótulo (T4) diz POR QUE o filho existe: sem ele, vê-se a
                        hierarquia mas não o motivo de cada nó estar ali. */}
                    <span style={{ paddingLeft: `${depth * 16}px` }} className="inline-flex items-center gap-1.5">
                      {depth > 0 && (
                        <span className="text-border-strong select-none" aria-hidden="true">└─</span>
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
                  </td>
                  <td className="px-3 py-2.5 text-muted-light" title={s.channel}>
                    {s.channel ? t(`enums.channel.${s.channel}`, { defaultValue: s.channel }) : '—'}
                  </td>
                  <td className="px-3 py-2.5"><StatusBadge t={t} status={s.status} /></td>
                  <td className="px-3 py-2.5 text-muted-light whitespace-nowrap">{fmtDate(s.opened_at)}</td>
                  <td className="px-3 py-2.5 text-muted-light">{s.segment_count || 0}</td>
                  <td className="px-3 py-2.5 text-muted-light" title={s.outcome ?? ''}>
                    {s.outcome
                      ? outcomeLabel(t, s.outcome)
                      : <span className="text-border-strong">—</span>}
                  </td>
                </tr>

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
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

// ── Main page ───────────────────────────────────────────────────────────────

export default function AnaliseJourneysPage() {
  const { tenantId } = useAuth()
  const { t } = useTranslation('contacts')
  const [searchParams, setSearchParams] = useSearchParams()

  const root      = searchParams.get('journey')
  const sessionId = searchParams.get('session')

  if (!tenantId) {
    return (
      <div className="flex items-center justify-center h-full text-muted-light text-sm">
        {t('journeys.noTenant', { defaultValue: 'Sem tenant' })}
      </div>
    )
  }

  // L3 — transcript
  if (root && sessionId) {
    return (
      <div className="h-full overflow-hidden">
        <SessionTranscript
          tenantId={tenantId}
          sessionId={sessionId}
          canJoin={false}
          onBack={() => setSearchParams({ journey: root })}
        />
      </div>
    )
  }

  // L2 — member sessions
  if (root) {
    return (
      <JourneySessions
        tenantId={tenantId}
        root={root}
        onBack={() => setSearchParams({})}
        onSelectSession={sid => setSearchParams({ journey: root, session: sid })}
        // T5: navegar para a journey vizinha (a que nasceu daqui com `journey: new`).
        // O grafo completo é PERCORRIDO por links, não renderizado de uma vez.
        onSelectJourney={r => setSearchParams({ journey: r })}
      />
    )
  }

  // L1 — journeys list
  return (
    <div className="flex flex-col h-full overflow-hidden bg-surface-muted">
      <JourneysList tenantId={tenantId} onSelect={r => setSearchParams({ journey: r })} />
    </div>
  )
}
