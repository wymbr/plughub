/**
 * EventsPage — /contacts/events
 *
 * Flat event stream from the canonical session stream.
 * Useful for debugging, audit, and point investigations.
 * Key filter: session_id for exact-match lookups.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, SearchX } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'

// ── Types ─────────────────────────────────────────────────────────────────────

interface EventRow {
  event_id:   string
  session_id: string
  tenant_id:  string
  type:       string
  timestamp:  string
  channel:    string | null
  pool_id:    string | null
  author_id:  string | null
  author_role: string | null
  content:    string | null
}

interface EventsResponse {
  data: EventRow[]
  meta?: { total?: number; page?: number; page_size?: number }
}

// ── Event type options ────────────────────────────────────────────────────────

const EVENT_TYPES = [
  'session_opened', 'session_closed', 'message_sent', 'agent_done',
  'agent_pause', 'agent_ready', 'agent_login', 'agent_logout',
  'workflow_triggered', 'workflow_completed',
]

// ── Filter bar ────────────────────────────────────────────────────────────────

function iso7dAgo() { const d = new Date(); d.setDate(d.getDate() - 7); return d.toISOString().slice(0, 10) }
function isoToday() { return new Date().toISOString().slice(0, 10) }

interface Filters {
  fromDt:    string
  toDt:      string
  sessionId: string
  poolId:    string
  channel:   string
  eventType: string
}

const DEFAULT_FILTERS: Filters = {
  fromDt:    iso7dAgo(),
  toDt:      isoToday(),
  sessionId: '',
  poolId:    '',
  channel:   '',
  eventType: '',
}

const inp = 'text-sm border border-border-strong rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary/30 bg-white'

function FilterBar({ filters, setFilters }: {
  filters: Filters
  setFilters: React.Dispatch<React.SetStateAction<Filters>>
}) {
  const { t } = useTranslation('contacts')

  function set<K extends keyof Filters>(key: K, val: Filters[K]) {
    setFilters(prev => ({ ...prev, [key]: val }))
  }
  const hasAny = !!(filters.sessionId || filters.poolId || filters.channel || filters.eventType
    || filters.fromDt !== DEFAULT_FILTERS.fromDt || filters.toDt !== DEFAULT_FILTERS.toDt)

  return (
    <div className="bg-white border-b border-border px-4 py-2.5 flex-shrink-0">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1.5 text-xs text-muted">
          <span>{t('filter.from')}</span>
          <input type="date" value={filters.fromDt} onChange={e => set('fromDt', e.target.value)} className={inp} />
          <span>{t('filter.to')}</span>
          <input type="date" value={filters.toDt}   onChange={e => set('toDt',   e.target.value)} className={inp} />
        </div>

        <input type="text" value={filters.sessionId}
          onChange={e => set('sessionId', e.target.value)}
          placeholder={t('events.sessionIdPlaceholder')}
          className={`${inp} w-56`} />

        <select value={filters.channel} onChange={e => set('channel', e.target.value)} className={inp}>
          <option value="">{t('filter.allChannels')}</option>
          {['webchat','whatsapp','voice','email','sms','instagram','telegram','webrtc'].map(c => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>

        <select value={filters.eventType} onChange={e => set('eventType', e.target.value)} className={inp}>
          <option value="">{t('events.allTypes')}</option>
          {EVENT_TYPES.map(ev => <option key={ev} value={ev}>{ev}</option>)}
        </select>

        <input type="text" value={filters.poolId}
          onChange={e => set('poolId', e.target.value)}
          placeholder={t('events.poolIdPlaceholder')}
          className={`${inp} w-36`} />

        {hasAny && (
          <button onClick={() => setFilters(DEFAULT_FILTERS)}
            className="text-xs text-muted-light hover:text-red px-2 py-1.5 rounded-lg border border-border hover:border-red/30 transition-colors ml-auto">
            {t('filter.clearFilters')}
          </button>
        )}
      </div>
    </div>
  )
}

// ── Role badge ────────────────────────────────────────────────────────────────

const ROLE_COLORS: Record<string, string> = {
  primary:    'bg-primary-light text-primary',
  specialist: 'bg-ai-light text-ai-text',
  supervisor: 'bg-warning-light text-warning-text',
  evaluator:  'bg-revised-light text-revised-text',
  reviewer:   'bg-green-light text-green-text',
  system:     'bg-surface-alt text-muted',
}

// ── Column keys ───────────────────────────────────────────────────────────────

const COLUMN_KEYS = [
  'events.columns.timestamp',
  'events.columns.sessionId',
  'events.columns.type',
  'events.columns.channel',
  'events.columns.pool',
  'events.columns.author',
  'events.columns.role',
] as const

// ── EventsPage ────────────────────────────────────────────────────────────────

const PAGE_SIZE = 100

export default function EventsPage() {
  const { tenantId } = useAuth()
  const { t, i18n } = useTranslation('contacts')
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS)
  const [rows,    setRows]    = useState<EventRow[]>([])
  const [total,   setTotal]   = useState(0)
  const [page,    setPage]    = useState(1)
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState('')
  const pending = useRef(false)

  const load = useCallback(async (p: number) => {
    if (!tenantId || pending.current) return
    pending.current = true
    setLoading(true); setError('')
    try {
      const qs = new URLSearchParams({
        tenant_id: tenantId,
        from_dt:   filters.fromDt + 'T00:00:00',
        to_dt:     filters.toDt   + 'T23:59:59',
        page:      String(p),
        page_size: String(PAGE_SIZE),
      })
      if (filters.sessionId) qs.set('session_id', filters.sessionId)
      if (filters.poolId)    qs.set('pool_id',    filters.poolId)
      if (filters.channel)   qs.set('channel',    filters.channel)
      if (filters.eventType) qs.set('event_type', filters.eventType)

      const res = await fetch(`/reports/events?${qs}`)
      if (!res.ok) { setError(`HTTP ${res.status}`); return }
      const data: EventsResponse = await res.json()
      const items = Array.isArray(data) ? (data as unknown as EventRow[]) : (data.data ?? [])
      setRows(items)
      setTotal(data.meta?.total ?? items.length)
    } catch (e) { setError(String(e)) }
    finally { setLoading(false); pending.current = false }
  }, [tenantId, filters])

  useEffect(() => { setPage(1); load(1) }, [load])

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  if (!tenantId) return (
    <div className="flex items-center justify-center h-full text-muted-light text-sm">
      {t('noTenant')}
    </div>
  )

  return (
    <div className="flex flex-col h-full overflow-hidden bg-surface-muted">
      <FilterBar filters={filters} setFilters={setFilters} />

      {/* Count bar */}
      <div className="flex items-center gap-2 px-4 py-2 bg-white border-b border-border flex-shrink-0 text-xs text-muted-light">
        {loading
          ? <><span className="animate-spin">⟳</span> {t('events.loading')}</>
          : error
            ? <span className="text-red">{error}</span>
            : <><strong className="text-dark">{total.toLocaleString(i18n.language)}</strong> {t('events.eventLabel', { count: total })}</>
        }
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto">
        {rows.length === 0 && !loading ? (
          error ? (
            <div className="flex flex-col items-center justify-center h-full text-red gap-2">
              <AlertTriangle className="w-8 h-8" aria-hidden="true" />
              <span className="text-sm font-medium">{t('events.serviceUnavailable')}</span>
              <span className="text-xs text-red-text">{error}</span>
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center h-full text-muted-light gap-2">
              <SearchX className="w-8 h-8" aria-hidden="true" />
              <span className="text-sm">{t('events.empty')}</span>
            </div>
          )
        ) : (
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-surface-muted border-b border-border z-10">
              <tr>
                {COLUMN_KEYS.map(col => (
                  <th key={col} className="text-left text-xs font-semibold text-muted uppercase tracking-wide px-4 py-2.5 whitespace-nowrap">
                    {t(col)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {rows.map(row => {
                const dt = row.timestamp
                  ? new Date(row.timestamp).toLocaleString(i18n.language, { dateStyle: 'short', timeStyle: 'medium' })
                  : '—'
                const shortSession = row.session_id.length > 14 ? '…' + row.session_id.slice(-12) : row.session_id
                const roleBadge    = row.author_role ? (ROLE_COLORS[row.author_role] ?? 'bg-surface-alt text-muted') : ''
                return (
                  <tr key={row.event_id} className="hover:bg-primary/5 transition-colors">
                    <td className="px-4 py-2.5 font-mono text-xs text-muted whitespace-nowrap tabular-nums">{dt}</td>
                    <td className="px-4 py-2.5 font-mono text-xs text-secondary whitespace-nowrap" title={row.session_id}>
                      {shortSession}
                    </td>
                    <td className="px-4 py-2.5">
                      <span className="inline-block text-xs font-medium px-2 py-0.5 rounded-full bg-surface-alt text-dark whitespace-nowrap">
                        {row.type}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-muted text-xs whitespace-nowrap">{row.channel ?? '—'}</td>
                    <td className="px-4 py-2.5 text-muted text-xs whitespace-nowrap max-w-[120px] truncate" title={row.pool_id ?? ''}>
                      {row.pool_id?.replace(/_/g, ' ') ?? '—'}
                    </td>
                    <td className="px-4 py-2.5 text-muted text-xs font-mono whitespace-nowrap max-w-40 truncate" title={row.author_id ?? ''}>
                      {row.author_id ?? '—'}
                    </td>
                    <td className="px-4 py-2.5">
                      {row.author_role && (
                        <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${roleBadge}`}>
                          {row.author_role}
                        </span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between px-4 py-2 bg-white border-t border-border flex-shrink-0 text-sm">
          <span className="text-muted text-xs">{t('lista.page', { page, total: totalPages })}</span>
          <div className="flex gap-1">
            <button disabled={page <= 1} onClick={() => { setPage(page - 1); load(page - 1) }}
              className="px-3 py-1 rounded border border-border text-xs text-muted disabled:opacity-40 hover:border-primary hover:text-primary transition-colors">
              {t('lista.prev')}
            </button>
            <button disabled={page >= totalPages} onClick={() => { setPage(page + 1); load(page + 1) }}
              className="px-3 py-1 rounded border border-border text-xs text-muted disabled:opacity-40 hover:border-primary hover:text-primary transition-colors">
              {t('lista.next')}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
