/**
 * SessionsPage — /contacts/sessions
 *
 * Unified view of all contact sessions (inbound + outbound),
 * live and historical. Active sessions show with a green badge.
 * Drill-down into a session overlays the full page.
 */
import React, { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import { SessionTranscript } from '@/modules/service/components/SessionTranscript'
import { SegmentList }       from '@/modules/service/components/SegmentList'
import type { ContactSegment } from '@/modules/service/types'
import type { ContactFilters } from './types'
import { DEFAULT_FILTERS }   from './types'
import { ListaTab }          from './tabs/ListaTab'

// ── Extended filters for sessions ─────────────────────────────────────────────

interface SessionFilters extends ContactFilters {
  sessionType:   string   // inbound | outbound | ''
  sessionStatus: string   // active | closed | abandoned | ''
}

const DEFAULT_SESSION_FILTERS: SessionFilters = {
  ...DEFAULT_FILTERS,
  sessionType:   '',
  sessionStatus: '',
}

// ── Insight panel ─────────────────────────────────────────────────────────────

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

function ContactInsightsPanel({ tenantId, sessionId }: { tenantId: string; sessionId: string }) {
  const { t } = useTranslation('contacts')
  const [rows,    setRows]    = useState<InsightRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState('')

  useEffect(() => {
    let cancelled = false
    setLoading(true); setError('')
    const params = new URLSearchParams({ tenant_id: tenantId, session_id: sessionId, page_size: '200' })
    fetch(`/reports/contact-insights?${params}`)
      .then(r => r.json())
      .then((data: any) => {
        if (cancelled) return
        setRows(Array.isArray(data) ? data : (data.data ?? []))
        if (data.error) setError(data.error)
      })
      .catch(e => { if (!cancelled) setError(String(e)) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [tenantId, sessionId])

  if (loading) return (
    <div className="flex items-center justify-center h-full text-gray-400 text-sm gap-2">
      <span className="animate-spin text-lg">⟳</span> {t('insights.loading')}
    </div>
  )

  if (rows.length === 0) return (
    <div className="flex flex-col items-center justify-center h-full text-gray-400 gap-2 p-8">
      <span className="text-3xl">📭</span>
      <p className="text-sm text-center">{error ? t('insights.loadError') : t('insights.empty')}</p>
      {error && <p className="text-xs text-red-400">{error}</p>}
    </div>
  )

  return (
    <div className="flex flex-col gap-2 p-4 overflow-y-auto">
      {rows.map(row => {
        const isHistorico = row.insight_type?.startsWith('insight.historico')
        const isConvo     = row.insight_type?.startsWith('insight.conversa')
        const borderColor = isHistorico ? 'border-violet-400' : isConvo ? 'border-teal-400' : 'border-blue-300'
        const badgeBg     = isHistorico ? 'bg-violet-100 text-violet-700' : isConvo ? 'bg-teal-100 text-teal-700' : 'bg-blue-100 text-blue-700'
        const dt = row.timestamp
          ? new Date(row.timestamp).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' })
          : '—'
        return (
          <div key={row.insight_id} className={`bg-white border-l-4 ${borderColor} rounded-lg shadow-sm px-4 py-3 space-y-1.5`}>
            <div className="flex items-center gap-2 flex-wrap">
              <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${badgeBg}`}>{row.insight_type}</span>
              {row.category && <span className="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-600">{row.category}</span>}
              <span className="ml-auto text-xs text-gray-400 tabular-nums">{dt}</span>
            </div>
            {row.value && <p className="text-sm text-gray-700 font-medium leading-snug">{row.value}</p>}
            {row.tags?.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {row.tags.map(tag => (
                  <span key={tag} className="text-xs px-2 py-0.5 rounded border border-gray-200 text-gray-500 bg-gray-50">#{tag}</span>
                ))}
              </div>
            )}
            {row.agent_id && (
              <p className="text-xs text-gray-400">{t('insights.registeredBy')} <code className="bg-gray-100 rounded px-1">{row.agent_id}</code></p>
            )}
          </div>
        )
      })}
    </div>
  )
}

// ── ContactDetail ─────────────────────────────────────────────────────────────

function ContactDetail({ tenantId, sessionId, onBack }: {
  tenantId: string; sessionId: string; onBack: () => void
}) {
  const { t } = useTranslation('contacts')
  const [detailSegment, setDetailSegment] = useState<ContactSegment | null>(null)

  if (detailSegment) {
    return (
      <div style={{ height: '100%', backgroundColor: '#0f172a' }}>
        <SessionTranscript
          tenantId={tenantId}
          sessionId={sessionId}
          segment={detailSegment}
          onBack={() => setDetailSegment(null)}
        />
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full bg-gray-50">
      <div className="flex items-center gap-0 border-b border-gray-200 bg-white px-4 flex-shrink-0">
        <button onClick={onBack} className="mr-4 text-sm text-gray-500 hover:text-primary py-3 transition-colors">
          {t('detail.back')}
        </button>
        <span className="text-sm font-medium text-gray-700 py-3">{t('detail.segments')}</span>
        <span className="ml-auto text-xs text-gray-400 font-mono py-3 truncate max-w-xs">{sessionId}</span>
      </div>
      <div className="flex-1 overflow-hidden">
        <SegmentList
          tenantId={tenantId}
          sessionId={sessionId}
          onSelect={seg => setDetailSegment(seg)}
          onBack={onBack}
        />
      </div>
    </div>
  )
}

// ── Filter bar ─────────────────────────────────────────────────────────────────

const inp = 'text-sm border border-gray-300 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary/30'

function FilterBar({ filters, setFilters }: {
  filters: SessionFilters
  setFilters: React.Dispatch<React.SetStateAction<SessionFilters>>
}) {
  const { t } = useTranslation('contacts')
  const [showExtra, setShowExtra] = useState(false)

  function set<K extends keyof SessionFilters>(key: K, value: SessionFilters[K]) {
    setFilters(prev => ({ ...prev, [key]: value }))
  }

  function clearAll() { setFilters(DEFAULT_SESSION_FILTERS) }

  const hasExtra = !!(filters.poolId || filters.agentId || filters.ani || filters.dnis
    || filters.insightCategory || filters.insightTags)

  const hasAny = !!(filters.sessionIdSearch || filters.channel || filters.outcome
    || filters.sessionType || filters.sessionStatus || hasExtra
    || filters.fromDt !== DEFAULT_FILTERS.fromDt || filters.toDt !== DEFAULT_FILTERS.toDt)

  return (
    <div className="bg-white border-b border-gray-200 px-4 py-2.5 flex-shrink-0">
      <div className="flex flex-wrap items-center gap-2">

        <div className="flex items-center gap-1.5 text-xs text-gray-500">
          <span>{t('filter.from')}</span>
          <input type="date" value={filters.fromDt} onChange={e => set('fromDt', e.target.value)} className={inp} />
          <span>{t('filter.to')}</span>
          <input type="date" value={filters.toDt}   onChange={e => set('toDt',   e.target.value)} className={inp} />
        </div>

        <input type="text" value={filters.sessionIdSearch}
          onChange={e => set('sessionIdSearch', e.target.value)}
          placeholder={t('filter.sessionId')}
          className={`${inp} w-44`} />

        <select value={filters.channel} onChange={e => set('channel', e.target.value)} className={`${inp} bg-white`}>
          <option value="">{t('filter.allChannels')}</option>
          {['webchat','whatsapp','voice','email','sms','instagram','telegram','webrtc'].map(c => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>

        <select value={filters.sessionType} onChange={e => set('sessionType', e.target.value)} className={`${inp} bg-white`}>
          <option value="">Todos os tipos</option>
          <option value="inbound">Inbound</option>
          <option value="outbound">Outbound</option>
        </select>

        <select value={filters.sessionStatus} onChange={e => set('sessionStatus', e.target.value)} className={`${inp} bg-white`}>
          <option value="">Todos os status</option>
          <option value="active">Ativo</option>
          <option value="closed">Encerrado</option>
          <option value="abandoned">Abandonado</option>
        </select>

        <button onClick={() => setShowExtra(v => !v)}
          className={`text-xs px-3 py-1.5 rounded-lg border transition-colors flex items-center gap-1 ${
            showExtra || hasExtra
              ? 'bg-primary/10 text-primary border-primary/30 font-semibold'
              : 'text-gray-500 border-gray-300 hover:border-primary hover:text-primary'
          }`}>
          {showExtra ? '▲' : '▼'} {t('filter.moreFilters')}
          {hasExtra && (
            <span className="ml-1 inline-flex items-center justify-center w-4 h-4 rounded-full bg-primary text-white text-[10px] font-bold">
              {[filters.poolId, filters.agentId, filters.ani, filters.dnis, filters.insightCategory, filters.insightTags].filter(Boolean).length}
            </span>
          )}
        </button>

        {hasAny && (
          <button onClick={clearAll}
            className="text-xs text-gray-400 hover:text-red-500 px-2 py-1.5 rounded-lg border border-gray-200 hover:border-red-300 transition-colors ml-auto">
            {t('filter.clearFilters')}
          </button>
        )}
      </div>

      {showExtra && (
        <div className="flex flex-wrap items-center gap-2 mt-2 pt-2 border-t border-gray-100">
          {([
            { key: 'poolId',          label: t('filter.pool'),          placeholder: 'ex: sac_ia',    width: 'w-36' },
            { key: 'agentId',         label: t('filter.agent'),         placeholder: 'participant…',  width: 'w-44' },
            { key: 'ani',             label: t('filter.ani'),           placeholder: '+5511…',        width: 'w-36' },
            { key: 'dnis',            label: t('filter.dnis'),          placeholder: '+5511…',        width: 'w-36' },
            { key: 'insightCategory', label: t('filter.eventCategory'), placeholder: 'categoria…',   width: 'w-40' },
            { key: 'insightTags',     label: t('filter.tags'),          placeholder: 'tag1,tag2',     width: 'w-36' },
          ] as { key: keyof SessionFilters; label: string; placeholder: string; width: string }[]).map(f => (
            <div key={f.key} className="flex items-center gap-1">
              <span className="text-xs text-gray-400 whitespace-nowrap">{f.label}:</span>
              <input type="text" value={filters[f.key] as string}
                onChange={e => set(f.key, e.target.value)}
                placeholder={f.placeholder}
                className={`${inp} ${f.width}`} />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── SessionsPage ──────────────────────────────────────────────────────────────

export default function SessionsPage() {
  const { t } = useTranslation('contacts')
  const { tenantId } = useAuth()

  const [filters,         setFilters]         = useState<SessionFilters>(DEFAULT_SESSION_FILTERS)
  const [detailSessionId, setDetailSessionId] = useState<string | null>(null)

  if (!tenantId) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400 text-sm">
        {t('noTenant')}
      </div>
    )
  }

  if (detailSessionId) {
    return (
      <ContactDetail
        tenantId={tenantId}
        sessionId={detailSessionId}
        onBack={() => setDetailSessionId(null)}
      />
    )
  }

  // Merge extended filters back into ContactFilters shape for ListaTab
  const contactFilters: ContactFilters = {
    fromDt:          filters.fromDt,
    toDt:            filters.toDt,
    sessionIdSearch: filters.sessionIdSearch,
    channel:         filters.channel,
    outcome:         filters.outcome,
    poolId:          filters.poolId,
    agentId:         filters.agentId,
    ani:             filters.ani,
    dnis:            filters.dnis,
    insightCategory: filters.insightCategory,
    insightTags:     filters.insightTags,
  }

  return (
    <div className="flex flex-col h-full overflow-hidden bg-gray-50">
      <div className="bg-white flex-shrink-0 border-b border-gray-200">
        <div className="px-4 pt-3 pb-2">
          <span className="font-bold text-gray-800 text-base">Sessões</span>
        </div>
      </div>

      <FilterBar filters={filters} setFilters={setFilters} />

      <div className="flex-1 overflow-hidden">
        <ListaTab
          tenantId={tenantId}
          filters={contactFilters}
          onOpenDetail={sid => setDetailSessionId(sid)}
        />
      </div>
    </div>
  )
}
