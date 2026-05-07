/**
 * ContactsPage — /contacts
 *
 * Unified tab shell: Relatório | Monitor | Análise
 *
 * - Filter bar is shared above all tabs (ContactFilters state lives here)
 * - ?tab= URL param syncs the active tab
 * - Drill-down into a session (ContactDetail) overlays the full page
 */
import React, { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuth }          from '@/auth/useAuth'
import { SessionTranscript } from '@/modules/service/components/SessionTranscript'
import { SegmentList }       from '@/modules/service/components/SegmentList'
import type { ContactSegment } from '@/modules/service/types'
import type { ContactFilters } from './types'
import { DEFAULT_FILTERS, iso7dAgo, isoToday } from './types'
import { ListaTab }   from './tabs/ListaTab'
import { MonitorTab } from './tabs/MonitorTab'
import { AnaliseTab } from './tabs/AnaliseTab'
import { AgentsTab }  from './tabs/AgentsTab'

// ─── Insight types (detail panel) ─────────────────────────────────────────────

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
        <span className="animate-spin text-lg">⟳</span> {t('insights.loading')}
      </div>
    )
  }

  if (rows.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-gray-400 gap-2 p-8">
        <span className="text-3xl">📭</span>
        <p className="text-sm text-center">
          {error ? t('insights.loadError') : t('insights.empty')}
        </p>
        {error && <p className="text-xs text-red-400">{error}</p>}
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-2 p-4 overflow-y-auto">
      {rows.map(row => <InsightCard key={row.insight_id} row={row} />)}
    </div>
  )
}

function InsightCard({ row }: { row: InsightRow }) {
  const { t } = useTranslation('contacts')
  const isHistorico = row.insight_type?.startsWith('insight.historico')
  const isConvo     = row.insight_type?.startsWith('insight.conversa')
  const borderColor = isHistorico ? 'border-violet-400' : isConvo ? 'border-teal-400' : 'border-blue-300'
  const badgeBg     = isHistorico ? 'bg-violet-100 text-violet-700' : isConvo ? 'bg-teal-100 text-teal-700' : 'bg-blue-100 text-blue-700'
  const dt = row.timestamp
    ? new Date(row.timestamp).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' })
    : '—'

  return (
    <div className={`bg-white border-l-4 ${borderColor} rounded-lg shadow-sm px-4 py-3 space-y-1.5`}>
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
}

// ─── ContactDetail ────────────────────────────────────────────────────────────

function ContactDetail({ tenantId, sessionId, onBack }: {
  tenantId: string; sessionId: string; onBack: () => void
}) {
  const { t } = useTranslation('contacts')
  const [detailSegment, setDetailSegment] = useState<ContactSegment | null>(null)

  // When a segment is selected, show the transcript narrowed to that segment.
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

// ─── Tab definition ───────────────────────────────────────────────────────────

type ContactTab = 'report' | 'monitor' | 'analysis' | 'agents'

// Note: Tab labels are i18n keys and will be resolved in the component
const ALL_TABS: { id: ContactTab; label: string; icon: string; abac?: { module: string; field: string } }[] = [
  { id: 'report',   label: 'tabs.list',      icon: '📋' },
  { id: 'monitor',  label: 'tabs.monitor',   icon: '📡', abac: { module: 'contacts', field: 'operacao'   } },
  { id: 'analysis', label: 'tabs.analysis',  icon: '📊', abac: { module: 'contacts', field: 'visualizar' } },
  { id: 'agents',   label: 'tabs.agents',    icon: '👤', abac: { module: 'contacts', field: 'visualizar' } },
]

// ─── Filter bar component ─────────────────────────────────────────────────────

const inp = 'text-sm border border-gray-300 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary/30'

interface FilterBarProps {
  filters:    ContactFilters
  setFilters: React.Dispatch<React.SetStateAction<ContactFilters>>
  loading?:   boolean
  totalLabel?: string
}

function FilterBar({ filters, setFilters, loading, totalLabel }: FilterBarProps) {
  const { t } = useTranslation('contacts')
  const [showExtra, setShowExtra] = useState(false)

  function set<K extends keyof ContactFilters>(key: K, value: ContactFilters[K]) {
    setFilters(prev => ({ ...prev, [key]: value }))
  }

  function clearAll() {
    setFilters(DEFAULT_FILTERS)
  }

  const hasExtra = filters.poolId || filters.agentId || filters.ani || filters.dnis
    || filters.insightCategory || filters.insightTags

  const hasAny = filters.sessionIdSearch || filters.channel || filters.outcome || hasExtra
    || filters.fromDt !== DEFAULT_FILTERS.fromDt || filters.toDt !== DEFAULT_FILTERS.toDt

  return (
    <div className="bg-white border-b border-gray-200 px-4 py-2.5 flex-shrink-0">
      {/* Primary row */}
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

        <select value={filters.channel} onChange={e => set('channel', e.target.value)}
          className={`${inp} bg-white`}>
          <option value="">{t('filter.allChannels')}</option>
          {['webchat','whatsapp','voice','email','sms','instagram','telegram','webrtc'].map(c => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>

        <select value={filters.outcome} onChange={e => set('outcome', e.target.value)}
          className={`${inp} bg-white`}>
          <option value="">{t('filter.allOutcomes')}</option>
          {['resolved','escalated','transferred','abandoned','timeout'].map(o => (
            <option key={o} value={o}>{o}</option>
          ))}
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

        {totalLabel && !loading && (
          <span className="text-xs text-gray-400 ml-auto">{totalLabel}</span>
        )}
        {loading && <span className="text-gray-400 text-sm animate-spin ml-auto">⟳</span>}
      </div>

      {/* Secondary row */}
      {showExtra && (
        <div className="flex flex-wrap items-center gap-2 mt-2 pt-2 border-t border-gray-100">
          {([
            { key: 'poolId',          label: t('filter.pool'),          placeholder: 'ex: sac_ia',       width: 'w-36' },
            { key: 'agentId',         label: t('filter.agent'),        placeholder: 'participant_id…',   width: 'w-44' },
            { key: 'ani',             label: t('filter.ani'),   placeholder: '+5511…',            width: 'w-36' },
            { key: 'dnis',            label: t('filter.dnis'), placeholder: '+5511…',            width: 'w-36' },
            { key: 'insightCategory', label: t('filter.eventCategory'),        placeholder: 'categoria…',        width: 'w-40' },
            { key: 'insightTags',     label: t('filter.tags'),          placeholder: 'tag1,tag2',         width: 'w-36' },
          ] as { key: keyof ContactFilters; label: string; placeholder: string; width: string }[]).map(f => (
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

// ─── ContactsPage ─────────────────────────────────────────────────────────────

export default function ContactsPage() {
  const { t } = useTranslation('contacts')
  const { session, tenantId, perms } = useAuth()

  // admin / developer / supervisor always see all tabs; other roles are gated by ABAC
  const isFullAccess = ['admin', 'developer', 'supervisor'].includes(session?.role ?? '')
  const TABS = ALL_TABS.filter(tab =>
    !tab.abac || isFullAccess || perms.can(tab.abac.module, tab.abac.field)
  )
  const validTabIds = TABS.map(t => t.id)

  const [searchParams, setSearchParams] = useSearchParams()
  const rawTab = searchParams.get('tab') as ContactTab | null
  // If requested tab is not accessible, fall back to first visible tab
  const activeTab: ContactTab = rawTab && validTabIds.includes(rawTab)
    ? rawTab : (validTabIds[0] ?? 'report')

  // Normalise stale / legacy URL params (e.g. ?tab=lista from old code)
  useEffect(() => {
    if (!rawTab || !validTabIds.includes(rawTab)) {
      setSearchParams(p => { p.set('tab', validTabIds[0] ?? 'report'); return p }, { replace: true })
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rawTab])

  function setTab(tab: ContactTab) {
    setSearchParams(p => { p.set('tab', tab); return p }, { replace: true })
  }

  // Shared filter state — lifted above all tabs
  const [filters, setFilters] = useState<ContactFilters>(DEFAULT_FILTERS)

  // Detail drill-down state
  const [detailSessionId, setDetailSessionId] = useState<string | null>(null)

  function openDetail(sid: string) { setDetailSessionId(sid) }
  function closeDetail()           { setDetailSessionId(null) }

  if (!tenantId) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400 text-sm">
        {t('noTenant')}
      </div>
    )
  }

  // Full-screen detail overlay
  if (detailSessionId) {
    return (
      <ContactDetail
        tenantId={tenantId}
        sessionId={detailSessionId}
        onBack={closeDetail}
      />
    )
  }

  return (
    <div className="flex flex-col h-full overflow-hidden bg-gray-50">

      {/* ── Page header with tab bar ─────────────────────────────────────── */}
      <div className="bg-white flex-shrink-0">
        <div className="px-4 pt-3 pb-1">
          <span className="font-bold text-gray-800 text-base">{t('title')}</span>
        </div>
        <div className="flex border-b border-gray-200 px-4">
          {TABS.map(tab => (
            <button key={tab.id} onClick={() => setTab(tab.id)}
              className={`flex items-center gap-1.5 px-5 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors ${
                activeTab === tab.id
                  ? 'border-primary text-primary'
                  : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
              }`}>
              <span>{tab.icon}</span>
              <span>{t(tab.label)}</span>
            </button>
          ))}
        </div>
      </div>

      {/* ── Shared filter bar ── */}
      <FilterBar filters={filters} setFilters={setFilters} />

      {/* ── Tab content ─────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-hidden">
        {activeTab === 'report' && (
          <ListaTab
            tenantId={tenantId}
            filters={filters}
            onOpenDetail={openDetail}
          />
        )}
        {activeTab === 'monitor' && (
          <MonitorTab
            tenantId={tenantId}
            filters={filters}
          />
        )}
        {activeTab === 'analysis' && (
          <AnaliseTab
            tenantId={tenantId}
            filters={filters}
          />
        )}
        {activeTab === 'agents' && (
          <AgentsTab
            tenantId={tenantId}
            filters={filters}
          />
        )}
      </div>
    </div>
  )
}
