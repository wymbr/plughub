/**
 * SessionsPage — /analise/sessions
 *
 * Three-level drill-down (matches Journeys / Processes pattern):
 *   Level 1: filtered session list
 *   Level 2: segment list for a specific session  (breadcrumb bar)
 *   Level 3: SessionTranscript for a specific segment
 */
import React, { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronRight } from 'lucide-react'
import { useAuth } from '@/auth/useAuth'
import { PoolDomainSelect } from '@/components/ui/PoolDomainSelect'
import { SessionTranscript }   from '@/modules/service/components/SessionTranscript'
import { SegmentList }         from '@/modules/service/components/SegmentList'
import { WorkflowTraceList }   from '@/modules/service/components/WorkflowTraceList'
import { WebhookSegmentDetail } from '@/modules/service/components/WebhookSegmentDetail'
import type { ContactSegment } from '@/modules/service/types'
import type { TraceNode }      from '@/modules/service/components/WorkflowTraceList'
import type { ContactFilters } from './types'
import { DEFAULT_FILTERS }    from './types'
import { ListaTab }           from './tabs/ListaTab'

// ── Extended filters ──────────────────────────────────────────────────────────

interface SessionFilters extends ContactFilters {
  sessionType:   string
  sessionStatus: string
}

const DEFAULT_SESSION_FILTERS: SessionFilters = {
  ...DEFAULT_FILTERS,
  sessionType:   '',
  sessionStatus: '',
}

// ── Filter bar ─────────────────────────────────────────────────────────────────

const inp = 'text-sm border border-border-strong rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary/30'

function FilterBar({ filters, setFilters }: {
  filters: SessionFilters
  setFilters: React.Dispatch<React.SetStateAction<SessionFilters>>
}) {
  const { t } = useTranslation('contacts')
  const { tenantId, currentUser } = useAuth()
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
    <div className="bg-white border-b border-border px-4 py-2.5 flex-shrink-0">
      <div className="flex flex-wrap items-center gap-2">

        <div className="flex items-center gap-1.5 text-xs text-muted">
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
          {['webchat','whatsapp','voice','email','sms','instagram','telegram','webrtc','webhook'].map(c => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>

        <select value={filters.sessionType} onChange={e => set('sessionType', e.target.value)} className={`${inp} bg-white`}>
          <option value="">{t('sessions.allTypes')}</option>
          <option value="inbound">Inbound</option>
          <option value="outbound">Outbound</option>
        </select>

        <select value={filters.sessionStatus} onChange={e => set('sessionStatus', e.target.value)} className={`${inp} bg-white`}>
          <option value="">{t('sessions.allStatuses')}</option>
          <option value="active">{t('sessions.status.active')}</option>
          <option value="suspended">{t('sessions.status.suspended')}</option>
          <option value="closed">{t('sessions.status.closed')}</option>
          <option value="abandoned">{t('sessions.status.abandoned')}</option>
        </select>

        <button onClick={() => setShowExtra(v => !v)}
          className={`text-xs px-3 py-1.5 rounded-lg border transition-colors flex items-center gap-1 ${
            showExtra || hasExtra
              ? 'bg-primary/10 text-primary border-primary/30 font-semibold'
              : 'text-muted border-border-strong hover:border-primary hover:text-primary'
          }`}>
          {showExtra ? '▲' : '▼'} {t('filter.moreFilters')}
          {hasExtra && (
            <span className="ml-1 inline-flex items-center justify-center w-4 h-4 rounded-full bg-primary text-white text-2xs font-bold">
              {[filters.poolId, filters.agentId, filters.ani, filters.dnis, filters.insightCategory, filters.insightTags].filter(Boolean).length}
            </span>
          )}
        </button>

        {hasAny && (
          <button onClick={clearAll}
            className="text-xs text-muted-light hover:text-red px-2 py-1.5 rounded-lg border border-border hover:border-red/30 transition-colors ml-auto">
            {t('filter.clearFilters')}
          </button>
        )}
      </div>

      {showExtra && (
        <div className="flex flex-wrap items-center gap-2 mt-2 pt-2 border-t border-border">
          {([
            { key: 'poolId',          label: t('filter.pool'),          placeholder: 'ex: sac_ia',    width: 'w-36' },
            { key: 'agentId',         label: t('filter.agent'),         placeholder: 'participant…',  width: 'w-44' },
            { key: 'ani',             label: t('filter.ani'),           placeholder: '+5511…',        width: 'w-36' },
            { key: 'dnis',            label: t('filter.dnis'),          placeholder: '+5511…',        width: 'w-36' },
            { key: 'insightCategory', label: t('filter.eventCategory'), placeholder: 'categoria…',   width: 'w-40' },
            { key: 'insightTags',     label: t('filter.tags'),          placeholder: 'tag1,tag2',     width: 'w-36' },
          ] as { key: keyof SessionFilters; label: string; placeholder: string; width: string }[]).map(f => (
            <div key={f.key} className="flex items-center gap-1">
              <span className="text-xs text-muted-light whitespace-nowrap">{f.label}:</span>
              {f.key === 'poolId' ? (
                // Segurança Fase E — pool = combo do DOMÍNIO (listPools ∩ accessiblePools),
                // não texto livre. Vazio no combo = todo o domínio; o backend reintersecta.
                <PoolDomainSelect
                  tenantId={tenantId ?? ''}
                  accessiblePools={currentUser?.accessiblePools ?? []}
                  value={filters.poolId}
                  onChange={v => set('poolId', v)}
                  allLabel={t('filter.allPools', { defaultValue: 'Todos os pools do domínio' })}
                  className={`${inp} ${f.width}`} />
              ) : (
                <input type="text" value={filters[f.key] as string}
                  onChange={e => set(f.key, e.target.value)}
                  placeholder={f.placeholder}
                  className={`${inp} ${f.width}`} />
              )}
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

  const [filters,            setFilters]            = useState<SessionFilters>(DEFAULT_SESSION_FILTERS)
  const [detailSessionId,    setDetailSessionId]    = useState<string | null>(null)
  const [detailSessionCh,    setDetailSessionCh]    = useState<string | null>(null)
  const [detailSegment,      setDetailSegment]      = useState<ContactSegment | null>(null)
  const [detailWebhookNode,  setDetailWebhookNode]  = useState<TraceNode | null>(null)

  // Clear drill-down state when session changes
  useEffect(() => {
    setDetailSegment(null)
    setDetailWebhookNode(null)
    if (!detailSessionId) setDetailSessionCh(null)
  }, [detailSessionId])

  const isWebhookSession = detailSessionCh === 'webhook'

  if (!tenantId) {
    return (
      <div className="flex items-center justify-center h-full text-muted-light text-sm">
        {t('noTenant')}
      </div>
    )
  }

  // ── Level 3a: webhook exec detail ──────────────────────────────────────────

  if (detailSessionId && detailWebhookNode) {
    return (
      <div className="flex flex-col h-full overflow-hidden">
        {/* Breadcrumb */}
        <div className="bg-white border-b border-border px-5 py-2.5 flex items-center gap-2 text-xs flex-shrink-0 sticky top-0 z-10">
          <button
            onClick={() => setDetailSessionId(null)}
            className="text-muted-light hover:text-dark transition-colors font-medium"
          >
            {t('sessions.breadcrumbs.sessions')}
          </button>
          <ChevronRight className="w-3.5 h-3.5 text-border-strong" aria-hidden="true" />
          <button
            onClick={() => setDetailWebhookNode(null)}
            className="text-muted-light hover:text-dark transition-colors font-medium font-mono"
            title={detailSessionId}
          >
            {'…' + detailSessionId.slice(-14)}
          </button>
          <ChevronRight className="w-3.5 h-3.5 text-border-strong" aria-hidden="true" />
          <span className="text-dark font-medium">{t('trace.execWindow')}</span>
        </div>
        <div className="flex-1 overflow-hidden p-3">
          <WebhookSegmentDetail
            tenantId={tenantId}
            node={detailWebhookNode}
            onBack={() => setDetailWebhookNode(null)}
          />
        </div>
      </div>
    )
  }

  // ── Level 3b: agent segment transcript ──────────────────────────────────────

  if (detailSessionId && detailSegment) {
    // Cross-session navigation (e.g. input_origin from WorkflowTraceList belongs
    // to a different session than detailSessionId). When segment.session_id differs
    // from the current context session, omit the segment filter so SessionTranscript
    // shows all messages instead of filtering by segment time-window.
    const isCrossSession = detailSegment.session_id !== detailSessionId
    return (
      <div className="h-full overflow-hidden">
        <SessionTranscript
          tenantId={tenantId}
          sessionId={detailSegment.session_id}
          segment={isCrossSession ? undefined : detailSegment}
          canJoin={!isCrossSession && detailSegment.ended_at === null}
          onBack={() => setDetailSegment(null)}
        />
      </div>
    )
  }

  // ── Level 2: trace list (webhook) or segment list (regular) ─────────────────

  if (detailSessionId) {
    const shortId = detailSessionId.length > 16
      ? '…' + detailSessionId.slice(-14)
      : detailSessionId

    return (
      <div className="flex flex-col h-full overflow-hidden">
        {/* Breadcrumb */}
        <div className="bg-white border-b border-border px-5 py-2.5 flex items-center gap-2 text-xs flex-shrink-0 sticky top-0 z-10">
          <button
            onClick={() => setDetailSessionId(null)}
            className="text-muted-light hover:text-dark transition-colors font-medium"
          >
            {t('sessions.breadcrumbs.sessions')}
          </button>
          <ChevronRight className="w-3.5 h-3.5 text-border-strong" aria-hidden="true" />
          <span className="text-dark font-medium font-mono" title={detailSessionId}>{shortId}</span>
        </div>

        <div className="flex-1 overflow-hidden">
          {isWebhookSession ? (
            /* Arc 19: webhook sessions use WorkflowTraceList (cross-session trace) */
            <WorkflowTraceList
              tenantId={tenantId}
              sessionId={detailSessionId}
              onSelectAgent={seg => setDetailSegment(seg)}
              onSelectWebhook={node => setDetailWebhookNode(node)}
            />
          ) : (
            /* Regular sessions use standard SegmentList */
            <SegmentList
              tenantId={tenantId}
              sessionId={detailSessionId}
              onSelect={seg => setDetailSegment(seg)}
              onBack={() => setDetailSessionId(null)}
              showBack={false}
            />
          )}
        </div>
      </div>
    )
  }

  // ── Level 1: session list ───────────────────────────────────────────────────

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
    status:          filters.sessionStatus || undefined,  // Arc 19: pass status filter
  }

  return (
    <div className="flex flex-col h-full overflow-hidden bg-surface-muted">
      <FilterBar filters={filters} setFilters={setFilters} />

      <div className="flex-1 overflow-hidden">
        <ListaTab
          tenantId={tenantId}
          filters={contactFilters}
          onOpenDetail={(sid, ch) => { setDetailSessionId(sid); setDetailSessionCh(ch) }}
        />
      </div>
    </div>
  )
}
