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

function StatusBadge({ status }: { status: string }) {
  const cls = STATUS_COLORS[status] ?? 'bg-surface-alt text-muted border-border'
  return (
    <span className={`inline-block text-xs px-2 py-0.5 rounded border font-medium ${cls}`}>
      {status || '—'}
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
                      {truncateId(j.journey_id)}
                    </span>
                  </td>
                  <td className="px-3 py-2.5">
                    <span className={`font-medium ${j.session_count > 1 ? 'text-dark' : 'text-muted-light'}`}>
                      {j.session_count}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 text-muted-light">
                    {(j.channels ?? []).join(', ') || '—'}
                  </td>
                  <td className="px-3 py-2.5 text-muted-light whitespace-nowrap">{fmtDate(j.started_at)}</td>
                  <td className="px-3 py-2.5 text-muted-light whitespace-nowrap">{fmtDuration(j.started_at, j.last_activity_at)}</td>
                  <td className="px-3 py-2.5">
                    {j.business_outcome
                      ? <span className={`inline-block text-xs px-2 py-0.5 rounded border font-medium ${outcomeCls(j.business_outcome)}`}>
                          {j.business_outcome}
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

function JourneySessions({ tenantId, root, onBack, onSelectSession }:
  { tenantId: string; root: string; onBack: () => void; onSelectSession: (sid: string) => void }) {
  const { t } = useTranslation('contacts')
  const [rows, setRows] = useState<MemberSession[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!tenantId || !root) return
    let cancelled = false
    setLoading(true)
    const q = new URLSearchParams({ tenant_id: tenantId, root_session_id: root, page_size: '200' })
    fetch(`/reports/sessions?${q.toString()}`)
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then(d => { if (!cancelled) setRows(d.data ?? []) })
      .catch(() => { if (!cancelled) setRows([]) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [tenantId, root])

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Breadcrumb */}
      <div className="bg-white border-b border-border px-5 py-2.5 flex items-center gap-2 text-xs flex-shrink-0 sticky top-0 z-10">
        <button onClick={onBack} className="text-muted-light hover:text-dark transition-colors font-medium">
          {t('journeys.breadcrumb', { defaultValue: 'Processos' })}
        </button>
        <ChevronRight className="w-3.5 h-3.5 text-border-strong" aria-hidden="true" />
        <span className="text-dark font-medium font-mono" title={root}>{truncateId(root)}</span>
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
              {rows.map(s => (
                <tr key={s.session_id} onClick={() => onSelectSession(s.session_id)}
                  className={`border-t border-border hover:bg-surface-muted transition-colors cursor-pointer ${
                    s.session_id === root ? 'bg-primary-light/30' : ''}`}>
                  <td className="px-3 py-2.5 font-mono text-dark" title={s.session_id}>
                    {truncateId(s.session_id)}
                    {s.session_id === root && (
                      <span className="ml-1.5 text-[10px] text-primary uppercase tracking-wide">
                        {t('journeys.rootBadge', { defaultValue: 'raiz' })}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2.5 text-muted-light">{s.channel || '—'}</td>
                  <td className="px-3 py-2.5"><StatusBadge status={s.status} /></td>
                  <td className="px-3 py-2.5 text-muted-light whitespace-nowrap">{fmtDate(s.opened_at)}</td>
                  <td className="px-3 py-2.5 text-muted-light">{s.segment_count || 0}</td>
                  <td className="px-3 py-2.5 text-muted-light">{s.outcome || <span className="text-border-strong">—</span>}</td>
                </tr>
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
