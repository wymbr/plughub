/**
 * EventsPage — /contacts/events
 *
 * Flat event stream from the canonical session stream.
 * Useful for debugging, audit, and point investigations.
 * Key filter: session_id for exact-match lookups.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react'
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

const inp = 'text-sm border border-gray-300 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary/30 bg-white'

function FilterBar({ filters, setFilters }: {
  filters: Filters
  setFilters: React.Dispatch<React.SetStateAction<Filters>>
}) {
  function set<K extends keyof Filters>(key: K, val: Filters[K]) {
    setFilters(prev => ({ ...prev, [key]: val }))
  }
  const hasAny = !!(filters.sessionId || filters.poolId || filters.channel || filters.eventType
    || filters.fromDt !== DEFAULT_FILTERS.fromDt || filters.toDt !== DEFAULT_FILTERS.toDt)

  return (
    <div className="bg-white border-b border-gray-200 px-4 py-2.5 flex-shrink-0">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1.5 text-xs text-gray-500">
          <span>De</span>
          <input type="date" value={filters.fromDt} onChange={e => set('fromDt', e.target.value)} className={inp} />
          <span>Até</span>
          <input type="date" value={filters.toDt}   onChange={e => set('toDt',   e.target.value)} className={inp} />
        </div>

        <input type="text" value={filters.sessionId}
          onChange={e => set('sessionId', e.target.value)}
          placeholder="Session ID (busca exata)"
          className={`${inp} w-56`} />

        <select value={filters.channel} onChange={e => set('channel', e.target.value)} className={inp}>
          <option value="">Todos os canais</option>
          {['webchat','whatsapp','voice','email','sms','instagram','telegram','webrtc'].map(c => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>

        <select value={filters.eventType} onChange={e => set('eventType', e.target.value)} className={inp}>
          <option value="">Todos os tipos</option>
          {EVENT_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
        </select>

        <input type="text" value={filters.poolId}
          onChange={e => set('poolId', e.target.value)}
          placeholder="Pool ID"
          className={`${inp} w-36`} />

        {hasAny && (
          <button onClick={() => setFilters(DEFAULT_FILTERS)}
            className="text-xs text-gray-400 hover:text-red-500 px-2 py-1.5 rounded-lg border border-gray-200 hover:border-red-300 transition-colors ml-auto">
            Limpar filtros
          </button>
        )}
      </div>
    </div>
  )
}

// ── Role badge ────────────────────────────────────────────────────────────────

const ROLE_COLORS: Record<string, string> = {
  primary:    'bg-blue-100 text-blue-700',
  specialist: 'bg-purple-100 text-purple-700',
  supervisor: 'bg-amber-100 text-amber-700',
  evaluator:  'bg-teal-100 text-teal-700',
  reviewer:   'bg-green-100 text-green-700',
  system:     'bg-gray-100 text-gray-600',
}

// ── EventsPage ────────────────────────────────────────────────────────────────

const PAGE_SIZE = 100

export default function EventsPage() {
  const { tenantId } = useAuth()
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
    <div className="flex items-center justify-center h-full text-gray-400 text-sm">
      Nenhum tenant selecionado.
    </div>
  )

  return (
    <div className="flex flex-col h-full overflow-hidden bg-gray-50">
      <div className="bg-white flex-shrink-0 border-b border-gray-200">
        <div className="px-4 pt-3 pb-2">
          <span className="font-bold text-gray-800 text-base">Eventos</span>
        </div>
      </div>

      <FilterBar filters={filters} setFilters={setFilters} />

      {/* Count bar */}
      <div className="flex items-center gap-2 px-4 py-2 bg-white border-b border-gray-100 flex-shrink-0 text-xs text-gray-400">
        {loading
          ? <><span className="animate-spin">⟳</span> Carregando…</>
          : error
            ? <span className="text-red-500">{error}</span>
            : <><strong className="text-gray-700">{total.toLocaleString('pt-BR')}</strong> evento{total !== 1 ? 's' : ''}</>
        }
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto">
        {rows.length === 0 && !loading ? (
          <div className="flex flex-col items-center justify-center h-full text-gray-400 gap-2">
            <span className="text-3xl">📭</span>
            <span className="text-sm">Nenhum evento encontrado com os filtros aplicados.</span>
            {error && <span className="text-xs text-red-400">{error}</span>}
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-gray-50 border-b border-gray-200 z-10">
              <tr>
                {['Timestamp','Session ID','Tipo','Canal','Pool','Autor','Role'].map(col => (
                  <th key={col} className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wide px-4 py-2.5 whitespace-nowrap">
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {rows.map(row => {
                const dt = row.timestamp
                  ? new Date(row.timestamp).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'medium' })
                  : '—'
                const shortSession = row.session_id.length > 14 ? '…' + row.session_id.slice(-12) : row.session_id
                const roleBadge    = row.author_role ? (ROLE_COLORS[row.author_role] ?? 'bg-gray-100 text-gray-600') : ''
                return (
                  <tr key={row.event_id} className="hover:bg-primary/5 transition-colors">
                    <td className="px-4 py-2.5 font-mono text-xs text-gray-500 whitespace-nowrap tabular-nums">{dt}</td>
                    <td className="px-4 py-2.5 font-mono text-xs text-blue-600 whitespace-nowrap" title={row.session_id}>
                      {shortSession}
                    </td>
                    <td className="px-4 py-2.5">
                      <span className="inline-block text-xs font-medium px-2 py-0.5 rounded-full bg-gray-100 text-gray-700 whitespace-nowrap">
                        {row.type}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-gray-500 text-xs whitespace-nowrap">{row.channel ?? '—'}</td>
                    <td className="px-4 py-2.5 text-gray-500 text-xs whitespace-nowrap max-w-[120px] truncate" title={row.pool_id ?? ''}>
                      {row.pool_id?.replace(/_/g, ' ') ?? '—'}
                    </td>
                    <td className="px-4 py-2.5 text-gray-600 text-xs font-mono whitespace-nowrap max-w-[160px] truncate" title={row.author_id ?? ''}>
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
        <div className="flex items-center justify-between px-4 py-2 bg-white border-t border-gray-200 flex-shrink-0 text-sm">
          <span className="text-gray-500 text-xs">Página {page} de {totalPages}</span>
          <div className="flex gap-1">
            <button disabled={page <= 1} onClick={() => { setPage(page - 1); load(page - 1) }}
              className="px-3 py-1 rounded border border-gray-200 text-xs text-gray-600 disabled:opacity-40 hover:border-primary hover:text-primary transition-colors">
              ← Anterior
            </button>
            <button disabled={page >= totalPages} onClick={() => { setPage(page + 1); load(page + 1) }}
              className="px-3 py-1 rounded border border-gray-200 text-xs text-gray-600 disabled:opacity-40 hover:border-primary hover:text-primary transition-colors">
              Próxima →
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
