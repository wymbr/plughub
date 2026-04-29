/**
 * ContactsPage — /contacts
 *
 * Unified table of all contacts (active + closed), ordered by arrival.
 *
 * Primary filters (always visible): date range, session_id, channel, outcome.
 * Secondary filters ("Outros filtros"): pool_id, agent_id, ANI, DNIS,
 *   insight category, insight tags.
 *
 * Columns: session_id, canal, pool, origem (ANI), destino (DNIS),
 *          iniciado, encerrado, duração, status/outcome, segmentos.
 *
 * Drill-down: click any row → ContactDetail (Transcrição + Eventos de Negócio).
 */
import React, { useCallback, useEffect, useRef, useState } from 'react'
import { useAuth } from '@/auth/useAuth'
import { SessionTranscript } from '@/modules/atendimento/components/SessionTranscript'

// ─── Insight Types ────────────────────────────────────────────────────────────

interface InsightRow {
  insight_id:   string
  tenant_id:    string
  session_id:   string
  insight_type: string
  category:     string | null
  value:        string | null
  tags:         string[]
  agent_id:     string | null
  timestamp:    string
}

interface InsightsApiResponse {
  data: InsightRow[]
  meta?: { total?: number }
  error?: string
}

// ─── ContactInsightsPanel ─────────────────────────────────────────────────────

function ContactInsightsPanel({ tenantId, sessionId }: { tenantId: string; sessionId: string }) {
  const [rows,    setRows]    = useState<InsightRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState('')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError('')

    const params = new URLSearchParams({
      tenant_id:  tenantId,
      session_id: sessionId,
      page_size:  '200',
    })

    fetch(`/reports/contact-insights?${params}`)
      .then(r => r.json())
      .then((data: InsightsApiResponse) => {
        if (cancelled) return
        setRows(Array.isArray(data) ? (data as unknown as InsightRow[]) : (data.data ?? []))
        if (data.error) setError(data.error)
      })
      .catch(e => { if (!cancelled) setError(String(e)) })
      .finally(() => { if (!cancelled) setLoading(false) })

    return () => { cancelled = true }
  }, [tenantId, sessionId])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400 text-sm gap-2">
        <span className="animate-spin text-lg">⟳</span> Carregando eventos…
      </div>
    )
  }

  if (rows.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-gray-400 gap-2 p-8">
        <span className="text-3xl">📭</span>
        <p className="text-sm text-center">
          {error
            ? 'Falha ao carregar eventos de negócio.'
            : 'Nenhum evento de negócio registrado nesta sessão.'}
        </p>
        {error && <p className="text-xs text-red-400">{error}</p>}
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-2 p-4 overflow-y-auto">
      {rows.map(row => (
        <InsightCard key={row.insight_id} row={row} />
      ))}
    </div>
  )
}

function InsightCard({ row }: { row: InsightRow }) {
  // Derive a colour from the insight_type prefix
  const isHistorico = row.insight_type?.startsWith('insight.historico')
  const isConvo     = row.insight_type?.startsWith('insight.conversa')
  const borderColor = isHistorico ? 'border-violet-400'
    : isConvo ? 'border-teal-400'
    : 'border-blue-300'
  const badgeBg     = isHistorico ? 'bg-violet-100 text-violet-700'
    : isConvo ? 'bg-teal-100 text-teal-700'
    : 'bg-blue-100 text-blue-700'

  const dt = row.timestamp
    ? new Date(row.timestamp).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' })
    : '—'

  return (
    <div className={`bg-white border-l-4 ${borderColor} rounded-lg shadow-sm px-4 py-3 space-y-1.5`}>
      {/* Header row */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${badgeBg}`}>
          {row.insight_type}
        </span>
        {row.category && (
          <span className="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-600">
            {row.category}
          </span>
        )}
        <span className="ml-auto text-xs text-gray-400 tabular-nums">{dt}</span>
      </div>

      {/* Value */}
      {row.value && (
        <p className="text-sm text-gray-700 font-medium leading-snug">{row.value}</p>
      )}

      {/* Tags */}
      {row.tags?.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {row.tags.map(tag => (
            <span
              key={tag}
              className="text-xs px-2 py-0.5 rounded border border-gray-200 text-gray-500 bg-gray-50"
            >
              #{tag}
            </span>
          ))}
        </div>
      )}

      {/* Agent */}
      {row.agent_id && (
        <p className="text-xs text-gray-400">
          Registrado por: <code className="bg-gray-100 rounded px-1">{row.agent_id}</code>
        </p>
      )}
    </div>
  )
}

// ─── ContactDetail ────────────────────────────────────────────────────────────

type DetailTab = 'transcript' | 'insights'

function ContactDetail({
  tenantId,
  sessionId,
  onBack,
}: {
  tenantId:  string
  sessionId: string
  onBack:    () => void
}) {
  const [tab, setTab] = useState<DetailTab>('transcript')

  return (
    <div className="flex flex-col h-full bg-gray-50">
      {/* Tab bar */}
      <div className="flex items-center gap-0 border-b border-gray-200 bg-white px-4 flex-shrink-0">
        <button
          onClick={onBack}
          className="mr-4 text-sm text-gray-500 hover:text-primary py-3 transition-colors"
        >
          ← Contatos
        </button>
        {(['transcript', 'insights'] as DetailTab[]).map(t => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
              tab === t
                ? 'border-primary text-primary'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            {t === 'transcript' ? '💬 Transcrição' : '📌 Eventos de Negócio'}
          </button>
        ))}
        <span className="ml-auto text-xs text-gray-400 font-mono py-3 truncate max-w-xs">
          {sessionId}
        </span>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-hidden">
        {tab === 'transcript' ? (
          <div style={{ height: '100%', backgroundColor: '#0f172a' }}>
            <SessionTranscript
              tenantId={tenantId}
              sessionId={sessionId}
              onBack={onBack}
            />
          </div>
        ) : (
          <ContactInsightsPanel tenantId={tenantId} sessionId={sessionId} />
        )}
      </div>
    </div>
  )
}

// ─── Contact Row Types ────────────────────────────────────────────────────────

interface ContactRow {
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
  handle_time_ms: number | null
  ani:            string | null
  dnis:           string | null
  segment_count:  number
}

interface ApiResponse {
  data: ContactRow[]
  meta?: { total?: number; page?: number; page_size?: number }
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function formatMs(ms: number | null): string {
  if (ms === null || ms === undefined) return '—'
  const s = Math.floor(ms / 1000)
  const m = Math.floor(s / 60)
  const h = Math.floor(m / 60)
  if (h > 0) return `${h}h ${m % 60}m`
  if (m > 0) return `${m}m ${s % 60}s`
  return `${s}s`
}

function formatDt(dt: string | null): string {
  if (!dt) return '—'
  try {
    return new Date(dt).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' })
  } catch { return dt }
}

function iso7dAgo(): string {
  const d = new Date(); d.setDate(d.getDate() - 7)
  return d.toISOString().slice(0, 10)
}
function isoToday(): string { return new Date().toISOString().slice(0, 10) }

const CHANNEL_ICONS: Record<string, string> = {
  webchat: '💬', whatsapp: '📱', voice: '📞', email: '✉️',
  sms: '📟', instagram: '📷', telegram: '✈️', webrtc: '🎥',
}

const OUTCOME_COLORS: Record<string, string> = {
  resolved:    '#059669',
  escalated:   '#d97706',
  transferred: '#2563eb',
  abandoned:   '#dc2626',
  timeout:     '#9333ea',
}

const PAGE_SIZE = 50

// ─── Main Page ────────────────────────────────────────────────────────────────

type DrillLevel = 'list' | 'detail'

export default function ContactsPage() {
  const { session } = useAuth()
  const tenantId = session?.tenantId ?? ''

  const [level,     setLevel]     = useState<DrillLevel>('list')
  const [sessionId, setSessionId] = useState<string | null>(null)

  function openDetail(sid: string) { setSessionId(sid); setLevel('detail') }
  function closeDetail()           { setSessionId(null); setLevel('list') }

  if (!tenantId) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400 text-sm">
        Sessão sem tenant. Faça login novamente.
      </div>
    )
  }

  if (level === 'detail' && sessionId) {
    return (
      <ContactDetail
        tenantId={tenantId}
        sessionId={sessionId}
        onBack={closeDetail}
      />
    )
  }

  return <ContactsList tenantId={tenantId} onOpen={openDetail} />
}

// ─── Contacts List ────────────────────────────────────────────────────────────

function ContactsList({ tenantId, onOpen }: { tenantId: string; onOpen: (sid: string) => void }) {
  const [rows,    setRows]    = useState<ContactRow[]>([])
  const [total,   setTotal]   = useState(0)
  const [page,    setPage]    = useState(1)
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState('')

  // ── Primary filter state (always visible) ──────────────────────────────
  const [sessionIdFilter, setSessionIdFilter] = useState('')
  const [fromDt,          setFromDt]          = useState(iso7dAgo)
  const [toDt,            setToDt]            = useState(isoToday)
  const [channelFilter,   setChannelFilter]   = useState('')
  const [outcomeFilter,   setOutcomeFilter]   = useState('')

  // ── Secondary filter state (collapsible "Outros filtros") ──────────────
  const [showExtra,       setShowExtra]       = useState(false)
  const [poolFilter,      setPoolFilter]      = useState('')
  const [agentId,         setAgentId]         = useState('')
  const [aniFilter,       setAniFilter]       = useState('')
  const [dnisFilter,      setDnisFilter]      = useState('')
  const [insightCategory, setInsightCategory] = useState('')
  const [insightTags,     setInsightTags]     = useState('')

  // ── Debounced values (400ms) ────────────────────────────────────────────
  const [dSessionId,  setDSessionId]  = useState('')
  const [dPool,       setDPool]       = useState('')
  const [dAgent,      setDAgent]      = useState('')
  const [dAni,        setDAni]        = useState('')
  const [dDnis,       setDDnis]       = useState('')
  const [dCategory,   setDCategory]   = useState('')
  const [dTags,       setDTags]       = useState('')

  function useDebounce(value: string, setter: (v: string) => void) {
    useEffect(() => {
      const t = setTimeout(() => setter(value.trim()), 400)
      return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [value])
  }

  useDebounce(sessionIdFilter, setDSessionId)
  useDebounce(poolFilter,      setDPool)
  useDebounce(agentId,         setDAgent)
  useDebounce(aniFilter,       setDAni)
  useDebounce(dnisFilter,      setDDnis)
  useDebounce(insightCategory, setDCategory)
  useDebounce(insightTags,     setDTags)

  const pendingRef = useRef(false)

  const load = useCallback(async (p: number) => {
    if (pendingRef.current) return
    pendingRef.current = true
    setLoading(true)
    setError('')
    try {
      const params = new URLSearchParams({
        tenant_id: tenantId,
        page:      String(p),
        page_size: String(PAGE_SIZE),
      })
      if (fromDt)     params.set('from_dt',          fromDt + 'T00:00:00')
      if (toDt)       params.set('to_dt',            toDt  + 'T23:59:59')
      if (dSessionId) params.set('session_id',       dSessionId)
      if (channelFilter) params.set('channel',       channelFilter)
      if (outcomeFilter) params.set('outcome',       outcomeFilter)
      if (dPool)      params.set('pool_id',          dPool)
      if (dAgent)     params.set('agent_id',         dAgent)
      if (dAni)       params.set('ani',              dAni)
      if (dDnis)      params.set('dnis',             dDnis)
      if (dCategory)  params.set('insight_category', dCategory)
      if (dTags)      params.set('insight_tags',     dTags)

      const res = await fetch(`/reports/sessions?${params}`)
      if (!res.ok) { setError(`Erro HTTP ${res.status}`); return }
      const data: ApiResponse = await res.json()
      const items = Array.isArray(data) ? (data as unknown as ContactRow[]) : (data.data ?? [])
      setRows(items)
      setTotal(data.meta?.total ?? items.length)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
      pendingRef.current = false
    }
  }, [
    tenantId, fromDt, toDt,
    dSessionId, channelFilter, outcomeFilter,
    dPool, dAgent, dAni, dDnis, dCategory, dTags,
  ])

  useEffect(() => { setPage(1); load(1) }, [load])

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  function changePage(p: number) { setPage(p); load(p) }

  function clearFilters() {
    setSessionIdFilter(''); setFromDt(iso7dAgo); setToDt(isoToday)
    setChannelFilter(''); setOutcomeFilter('')
    setPoolFilter(''); setAgentId('')
    setAniFilter(''); setDnisFilter('')
    setInsightCategory(''); setInsightTags('')
  }

  const hasExtra  = poolFilter || agentId || aniFilter || dnisFilter || insightCategory || insightTags
  const hasFilters = sessionIdFilter || channelFilter || outcomeFilter || hasExtra

  // input class shorthand
  const inp = "text-sm border border-gray-300 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary/30"

  return (
    <div className="flex flex-col h-full bg-gray-50 overflow-hidden">

      {/* ── Page header ─────────────────────────────────────────────────── */}
      <div className="flex items-center gap-3 bg-white border-b border-gray-200 px-4 py-3 flex-shrink-0">
        <span className="font-bold text-dark text-base">📋 Contatos</span>
        <span className="text-xs text-gray-400 font-medium ml-1">
          {total.toLocaleString('pt-BR')} contato{total !== 1 ? 's' : ''}
        </span>
        {loading && <span className="text-gray-400 text-sm animate-spin">⟳</span>}
        <div className="flex-1" />
        {hasFilters && (
          <button
            onClick={clearFilters}
            className="text-xs text-gray-400 hover:text-red-500 px-2 py-1 rounded-lg border border-gray-200 hover:border-red-300 transition-colors"
          >
            ✕ Limpar filtros
          </button>
        )}
      </div>

      {/* ── Filter bar ──────────────────────────────────────────────────── */}
      <div className="bg-white border-b border-gray-200 px-4 py-2.5 flex-shrink-0">

        {/* Primary row: date range + session_id + channel + outcome + "Outros" toggle */}
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-1.5 text-xs text-gray-500">
            <span>De</span>
            <input
              type="date" value={fromDt}
              onChange={e => setFromDt(e.target.value)}
              className={inp}
            />
            <span>até</span>
            <input
              type="date" value={toDt}
              onChange={e => setToDt(e.target.value)}
              className={inp}
            />
          </div>

          <input
            type="text" value={sessionIdFilter}
            onChange={e => setSessionIdFilter(e.target.value)}
            placeholder="Session ID…"
            className={`${inp} w-44`}
          />

          <select
            value={channelFilter}
            onChange={e => setChannelFilter(e.target.value)}
            className={`${inp} bg-white text-dark`}
          >
            <option value="">Todos os canais</option>
            {['webchat','whatsapp','voice','email','sms','instagram','telegram','webrtc'].map(c => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>

          <select
            value={outcomeFilter}
            onChange={e => setOutcomeFilter(e.target.value)}
            className={`${inp} bg-white text-dark`}
          >
            <option value="">Todos os outcomes</option>
            {['resolved','escalated','transferred','abandoned','timeout'].map(o => (
              <option key={o} value={o}>{o}</option>
            ))}
          </select>

          {/* "Outros filtros" toggle */}
          <button
            onClick={() => setShowExtra(v => !v)}
            className={`text-xs px-3 py-1.5 rounded-lg border transition-colors flex items-center gap-1 ${
              showExtra || hasExtra
                ? 'bg-primary/10 text-primary border-primary/30 font-semibold'
                : 'text-gray-500 border-gray-300 hover:border-primary hover:text-primary'
            }`}
          >
            {showExtra ? '▲' : '▼'} Outros filtros
            {hasExtra && (
              <span className="ml-1 inline-flex items-center justify-center w-4 h-4 rounded-full bg-primary text-white text-[10px] font-bold">
                {[poolFilter, agentId, aniFilter, dnisFilter, insightCategory, insightTags].filter(Boolean).length}
              </span>
            )}
          </button>
        </div>

        {/* Secondary row: collapsible extra filters */}
        {showExtra && (
          <div className="flex flex-wrap items-center gap-2 mt-2 pt-2 border-t border-gray-100">

            <div className="flex items-center gap-1">
              <span className="text-xs text-gray-400 whitespace-nowrap">Pool:</span>
              <input
                type="text" value={poolFilter}
                onChange={e => setPoolFilter(e.target.value)}
                placeholder="ex: sac_ia"
                className={`${inp} w-36`}
              />
            </div>

            <div className="flex items-center gap-1">
              <span className="text-xs text-gray-400 whitespace-nowrap">Agente:</span>
              <input
                type="text" value={agentId}
                onChange={e => setAgentId(e.target.value)}
                placeholder="participant_id…"
                className={`${inp} w-44`}
              />
            </div>

            <div className="flex items-center gap-1">
              <span className="text-xs text-gray-400 whitespace-nowrap">Origem (ANI):</span>
              <input
                type="text" value={aniFilter}
                onChange={e => setAniFilter(e.target.value)}
                placeholder="+5511…"
                className={`${inp} w-36`}
              />
            </div>

            <div className="flex items-center gap-1">
              <span className="text-xs text-gray-400 whitespace-nowrap">Destino (DNIS):</span>
              <input
                type="text" value={dnisFilter}
                onChange={e => setDnisFilter(e.target.value)}
                placeholder="+5511…"
                className={`${inp} w-36`}
              />
            </div>

            <div className="flex items-center gap-1">
              <span className="text-xs text-gray-400 whitespace-nowrap">Evento:</span>
              <input
                type="text" value={insightCategory}
                onChange={e => setInsightCategory(e.target.value)}
                placeholder="categoria…"
                className={`${inp} w-40`}
              />
            </div>

            <div className="flex items-center gap-1">
              <span className="text-xs text-gray-400 whitespace-nowrap">Tags:</span>
              <input
                type="text" value={insightTags}
                onChange={e => setInsightTags(e.target.value)}
                placeholder="tag1,tag2 (AND)"
                className={`${inp} w-36`}
              />
            </div>

          </div>
        )}
      </div>

      {/* ── Error banner ─────────────────────────────────────────────────── */}
      {error && (
        <div className="px-4 py-2 bg-red-50 border-b border-red-200 text-red-700 text-xs flex-shrink-0">
          {error}
        </div>
      )}

      {/* ── Table ───────────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-auto">
        {rows.length === 0 && !loading ? (
          <div className="flex flex-col items-center justify-center h-full text-gray-400 gap-2">
            <span className="text-3xl">📂</span>
            <span className="text-sm">Nenhum contato encontrado.</span>
            {hasFilters && (
              <button onClick={clearFilters} className="text-xs text-primary underline">
                Limpar filtros
              </button>
            )}
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-gray-50 border-b border-gray-200 z-10">
              <tr>
                {[
                  'Session ID', 'Canal', 'Pool',
                  'Origem', 'Destino',
                  'Iniciado', 'Encerrado', 'Duração',
                  'Status / Outcome', 'Segmentos',
                ].map(col => (
                  <th
                    key={col}
                    className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wide px-4 py-2.5 whitespace-nowrap"
                  >
                    {col}
                  </th>
                ))}
                <th className="px-4 py-2.5" />
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {rows.map(row => (
                <ContactRowItem key={row.session_id} row={row} onClick={() => onOpen(row.session_id)} />
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* ── Pagination ──────────────────────────────────────────────────── */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between px-4 py-2 bg-white border-t border-gray-200 flex-shrink-0 text-sm">
          <span className="text-gray-500 text-xs">
            Página {page} de {totalPages}
          </span>
          <div className="flex gap-1">
            <button
              disabled={page <= 1}
              onClick={() => changePage(page - 1)}
              className="px-3 py-1 rounded border border-gray-200 text-xs text-gray-600 disabled:opacity-40 hover:border-primary hover:text-primary transition-colors"
            >
              ← Anterior
            </button>
            {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
              const start = Math.max(1, Math.min(page - 2, totalPages - 4))
              return start + i
            }).map(p => (
              <button
                key={p}
                onClick={() => changePage(p)}
                className={`px-3 py-1 rounded border text-xs transition-colors ${
                  p === page
                    ? 'bg-primary text-white border-primary'
                    : 'border-gray-200 text-gray-600 hover:border-primary hover:text-primary'
                }`}
              >
                {p}
              </button>
            ))}
            <button
              disabled={page >= totalPages}
              onClick={() => changePage(page + 1)}
              className="px-3 py-1 rounded border border-gray-200 text-xs text-gray-600 disabled:opacity-40 hover:border-primary hover:text-primary transition-colors"
            >
              Próxima →
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// ─── Contact Row ─────────────────────────────────────────────────────────────

function ContactRowItem({ row, onClick }: { row: ContactRow; onClick: () => void }) {
  const isActive = !row.closed_at
  const outcome  = row.outcome
  const outColor = outcome ? (OUTCOME_COLORS[outcome] ?? '#6b7280') : null
  const shortId  = row.session_id.length > 16
    ? '…' + row.session_id.slice(-14)
    : row.session_id

  return (
    <tr onClick={onClick} className="hover:bg-primary/5 cursor-pointer transition-colors">

      {/* Session ID */}
      <td className="px-4 py-3 font-mono text-xs text-gray-700 whitespace-nowrap">
        {shortId}
      </td>

      {/* Canal */}
      <td className="px-4 py-3 text-gray-600 whitespace-nowrap">
        {CHANNEL_ICONS[row.channel] ?? '⬡'} {row.channel}
      </td>

      {/* Pool */}
      <td className="px-4 py-3 text-gray-500 text-xs whitespace-nowrap max-w-[120px] truncate" title={row.pool_id ?? ''}>
        {row.pool_id?.replace(/_/g, ' ') ?? '—'}
      </td>

      {/* Origem (ANI) */}
      <td className="px-4 py-3 text-gray-500 text-xs whitespace-nowrap tabular-nums">
        {row.ani ? (
          <span className="font-mono">{row.ani}</span>
        ) : (
          <span className="text-gray-300">—</span>
        )}
      </td>

      {/* Destino (DNIS) */}
      <td className="px-4 py-3 text-gray-500 text-xs whitespace-nowrap tabular-nums">
        {row.dnis ? (
          <span className="font-mono">{row.dnis}</span>
        ) : (
          <span className="text-gray-300">—</span>
        )}
      </td>

      {/* Iniciado */}
      <td className="px-4 py-3 text-gray-500 text-xs tabular-nums whitespace-nowrap">
        {formatDt(row.opened_at)}
      </td>

      {/* Encerrado */}
      <td className="px-4 py-3 text-gray-500 text-xs tabular-nums whitespace-nowrap">
        {isActive
          ? <span className="text-green-600 font-medium">Em andamento</span>
          : formatDt(row.closed_at)
        }
      </td>

      {/* Duração */}
      <td className="px-4 py-3 text-gray-700 tabular-nums whitespace-nowrap text-xs">
        {formatMs(row.handle_time_ms)}
      </td>

      {/* Status / Outcome */}
      <td className="px-4 py-3 whitespace-nowrap">
        {isActive ? (
          <span className="inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full bg-green-100 text-green-700">
            <span className="w-1.5 h-1.5 rounded-full bg-green-500 inline-block" />
            ativo
          </span>
        ) : outcome && outColor ? (
          <span
            className="inline-block text-xs font-semibold px-2 py-0.5 rounded-full"
            style={{ backgroundColor: outColor + '20', color: outColor }}
          >
            {outcome}
          </span>
        ) : (
          <span className="text-gray-400 text-xs">—</span>
        )}
      </td>

      {/* Segmentos */}
      <td className="px-4 py-3 text-center">
        {row.segment_count > 0 ? (
          <span className="inline-block text-xs font-semibold px-2 py-0.5 rounded-full bg-blue-50 text-blue-600 tabular-nums">
            {row.segment_count}
          </span>
        ) : (
          <span className="text-gray-300 text-xs">—</span>
        )}
      </td>

      {/* Drill-down arrow */}
      <td className="px-4 py-3 text-gray-400 text-right">›</td>
    </tr>
  )
}
