import React, { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import { apiFetch } from '@/api/apiFetch'
import Spinner from '@/components/ui/Spinner'
import Badge from '@/components/ui/Badge'
import EmptyState from '@/components/ui/EmptyState'
import type { CampaignSummary, CollectEvent } from '@/types'

// ── Helpers ────────────────────────────────────────────────────────────────────

function fmtDate(s: string | null) {
  if (!s) return '—'
  try { return new Date(s).toLocaleString('pt-BR') }
  catch { return s }
}

function fmtDuration(ms: number | null) {
  if (ms == null) return '—'
  if (ms < 1000) return `${ms}ms`
  const s = ms / 1000
  if (s < 60) return `${s.toFixed(1)}s`
  const m = Math.floor(s / 60)
  const rem = Math.round(s % 60)
  return `${m}m ${rem}s`
}

const CHANNEL_ICONS: Record<string, string> = {
  whatsapp: '💬',
  webchat:  '🌐',
  voice:    '📞',
  email:    '📧',
  sms:      '💬',
  telegram: '✈️',
  instagram:'📸',
}

const STATUS_COLORS: Record<string, string> = {
  responded:  'bg-green-light text-green-text',
  timed_out:  'bg-red-light text-red-text',
  sent:       'bg-primary-light text-primary',
  requested:  'bg-warning-light text-warning-text',
}

// ── API hook ───────────────────────────────────────────────────────────────────

interface CampaignApiResponse {
  data:    CollectEvent[]
  summary: CampaignSummary[]
  meta:    { page: number; page_size: number; total: number }
}

function useCampaignData(
  tenantId:   string,
  campaignId?: string,
  channel?:    string,
  status?:     string,
  intervalMs  = 30_000,
) {
  const { t } = useTranslation('campaigns')
  const [data,    setData]    = useState<CampaignApiResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState<string | null>(null)

  const refresh = useCallback(async () => {
    if (!tenantId) return
    setLoading(true); setError(null)
    try {
      const params = new URLSearchParams({ tenant_id: tenantId, page_size: '50' })
      if (campaignId) params.set('campaign_id', campaignId)
      if (channel)    params.set('channel', channel)
      if (status)     params.set('status', status)
      const res = await apiFetch(`/reports/campaigns?${params}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setData(await res.json())
    } catch (e) {
      setError(e instanceof Error ? e.message : t('unknownError', { defaultValue: 'Unknown error' }))
    } finally {
      setLoading(false)
    }
  }, [tenantId, campaignId, channel, status])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, intervalMs)
    return () => clearInterval(id)
  }, [refresh, intervalMs])

  return { data, loading, error, refresh }
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function RateBadge({ rate }: { rate: number }) {
  const color =
    rate >= 70 ? 'bg-green-light text-green-text' :
    rate >= 40 ? 'bg-warning-light text-warning-text' :
                 'bg-red-light text-red-text'
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold ${color}`}>
      {rate.toFixed(1)}%
    </span>
  )
}

function MiniBar({ responded, sent, timed_out, requested }: {
  responded: number; sent: number; timed_out: number; requested: number
}) {
  const { t } = useTranslation('campaigns')
  const total = responded + sent + timed_out + requested
  if (total === 0) return <div className="h-2 bg-surface-alt rounded-full w-full" />

  const pct = (n: number) => `${((n / total) * 100).toFixed(1)}%`

  return (
    <div className="flex h-2 rounded-full overflow-hidden w-full gap-0.5">
      {responded > 0 && (
        <div title={`${t('statusLabels.responded')}: ${responded}`}
          className="bg-green rounded-l-full" style={{ width: pct(responded) }} />
      )}
      {sent > 0 && (
        <div title={`${t('statusLabels.sent')}: ${sent}`}
          className="bg-secondary" style={{ width: pct(sent) }} />
      )}
      {timed_out > 0 && (
        <div title={`${t('statusLabels.timedOut')}: ${timed_out}`}
          className="bg-red" style={{ width: pct(timed_out) }} />
      )}
      {requested > 0 && (
        <div title={`${t('statusLabels.requested')}: ${requested}`}
          className="bg-warning rounded-r-full" style={{ width: pct(requested) }} />
      )}
    </div>
  )
}

function CampaignCard({
  summary,
  selected,
  onClick,
}: {
  summary: CampaignSummary
  selected: boolean
  onClick: () => void
}) {
  const { t } = useTranslation('campaigns')
  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-4 py-3 border-b border-border hover:bg-surface-muted transition-colors ${
        selected ? 'bg-primary-light border-l-2 border-l-secondary' : ''
      }`}
    >
      <div className="flex items-center justify-between mb-1">
        <p className="text-sm font-medium text-dark truncate max-w-[170px]">
          {summary.campaign_id}
        </p>
        <RateBadge rate={summary.response_rate_pct} />
      </div>
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-muted">{summary.total} {t('statusLabels.shots')}</span>
        <span className="text-xs text-muted-light">
          {summary.responded} {t('statusLabels.responded')}
        </span>
      </div>
      <MiniBar
        responded={summary.responded}
        sent={summary.sent}
        timed_out={summary.timed_out}
        requested={summary.requested}
      />
    </button>
  )
}

function KpiCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-white border border-border rounded-lg p-4">
      <p className="text-xs text-muted mb-1">{label}</p>
      <p className="text-2xl font-bold text-dark">{value}</p>
      {sub && <p className="text-xs text-muted-light mt-1">{sub}</p>}
    </div>
  )
}

function ChannelBreakdown({ events }: { events: CollectEvent[] }) {
  const { t } = useTranslation('campaigns')
  const counts: Record<string, { total: number; responded: number }> = {}
  for (const e of events) {
    if (!counts[e.channel]) counts[e.channel] = { total: 0, responded: 0 }
    counts[e.channel].total++
    if (e.status === 'responded') counts[e.channel].responded++
  }

  const channels = Object.entries(counts).sort((a, b) => b[1].total - a[1].total)
  if (channels.length === 0) return null

  return (
    <div className="bg-white border border-border rounded-lg p-4">
      <h3 className="text-sm font-semibold text-dark mb-3">{t('detail.channelBreak')}</h3>
      <div className="space-y-2">
        {channels.map(([ch, { total, responded }]) => (
          <div key={ch} className="flex items-center gap-2">
            <span className="text-base">{CHANNEL_ICONS[ch] ?? '📡'}</span>
            <span className="text-sm text-dark w-24 capitalize">{ch}</span>
            <div className="flex-1 h-2 bg-surface-alt rounded-full overflow-hidden">
              <div
                className="h-2 bg-green rounded-full"
                style={{ width: total > 0 ? `${(responded / total) * 100}%` : '0%' }}
              />
            </div>
            <span className="text-xs text-muted w-16 text-right">
              {responded}/{total}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

function CollectEventRow({ event }: { event: CollectEvent }) {
  const statusClass = STATUS_COLORS[event.status] ?? 'bg-surface-alt text-muted'

  return (
    <tr className="hover:bg-surface-muted">
      <td className="px-4 py-2 text-xs font-mono text-muted max-w-[120px] truncate">
        {event.collect_token.slice(0, 16)}…
      </td>
      <td className="px-4 py-2 text-xs text-dark capitalize">
        {CHANNEL_ICONS[event.channel] ?? '📡'} {event.channel}
      </td>
      <td className="px-4 py-2">
        <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${statusClass}`}>
          {event.status}
        </span>
      </td>
      <td className="px-4 py-2 text-xs text-muted">{fmtDate(event.send_at)}</td>
      <td className="px-4 py-2 text-xs text-muted">{fmtDuration(event.elapsed_ms)}</td>
    </tr>
  )
}

function CampaignDetail({
  summary,
  events,
}: {
  summary: CampaignSummary
  events: CollectEvent[]
}) {
  const { t } = useTranslation('campaigns')
  const campaignEvents = events.filter(
    e => e.campaign_id === summary.campaign_id
  )

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-dark">{summary.campaign_id}</h2>
          <p className="text-sm text-muted mt-0.5">
            {summary.total} {t('statusLabels.shots')} • {t('responseRate')}{' '}
            <strong className="text-dark">{summary.response_rate_pct.toFixed(1)}%</strong>
          </p>
        </div>
        <RateBadge rate={summary.response_rate_pct} />
      </div>

      {/* KPI grid */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <KpiCard label={t('detail.total')} value={String(summary.total)} />
        <KpiCard
          label={t('detail.responded')}
          value={String(summary.responded)}
          sub={`${summary.response_rate_pct.toFixed(1)}% ${t('responseRate')}`}
        />
        <KpiCard label={t('detail.expired')} value={String(summary.timed_out)} />
        <KpiCard
          label={t('detail.avgTime')}
          value={fmtDuration(summary.avg_elapsed_ms)}
          sub={t('untilResponse', { defaultValue: 'until response' })}
        />
      </div>

      {/* Status bar */}
      <div className="bg-white border border-border rounded-lg p-4">
        <h3 className="text-sm font-semibold text-dark mb-3">{t('detail.statusDist')}</h3>
        <MiniBar
          responded={summary.responded}
          sent={summary.sent}
          timed_out={summary.timed_out}
          requested={summary.requested}
        />
        <div className="flex gap-4 mt-2">
          {[
            { label: t('detail.responded'), color: 'bg-green',     count: summary.responded },
            { label: t('detail.sent'),    color: 'bg-secondary',  count: summary.sent },
            { label: t('detail.expired'),   color: 'bg-red',       count: summary.timed_out },
            { label: t('statusLabels.requested'),  color: 'bg-warning', count: summary.requested },
          ].map(({ label, color, count }) => (
            <div key={label} className="flex items-center gap-1.5 text-xs text-muted">
              <div className={`w-2.5 h-2.5 rounded-full ${color}`} />
              {label}: {count}
            </div>
          ))}
        </div>
      </div>

      {/* Channel breakdown */}
      {campaignEvents.length > 0 && <ChannelBreakdown events={campaignEvents} />}

      {/* Collect events table */}
      <div className="bg-white border border-border rounded-lg overflow-hidden">
        <div className="px-4 py-3 border-b border-border">
          <h3 className="text-sm font-semibold text-dark">
            {t('detail.recentEvents')} ({campaignEvents.length})
          </h3>
        </div>
        {campaignEvents.length === 0 ? (
          <div className="px-4 py-8 text-center text-sm text-muted">
            {t('noRecentEvents', { defaultValue: 'No events recorded yet' })}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-surface-muted text-xs text-muted uppercase tracking-wide">
                  <th className="px-4 py-2 text-left">{t('detail.token')}</th>
                  <th className="px-4 py-2 text-left">{t('detail.channel')}</th>
                  <th className="px-4 py-2 text-left">{t('detail.status')}</th>
                  <th className="px-4 py-2 text-left">{t('detail.sent')}</th>
                  <th className="px-4 py-2 text-left">{t('detail.elapsed')}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {campaignEvents.slice(0, 50).map(e => (
                  <CollectEventRow key={e.collect_token} event={e} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Filters bar ────────────────────────────────────────────────────────────────

const CHANNEL_OPTIONS = ['', 'whatsapp', 'webchat', 'voice', 'email', 'sms', 'telegram', 'instagram']
const STATUS_OPTIONS  = ['', 'responded', 'timed_out', 'sent', 'requested']

// ── Main page ──────────────────────────────────────────────────────────────────

export default function CampaignsPage() {
  const { t } = useTranslation('campaigns')
  const { tenantId } = useAuth()

  const [selectedCampaignId, setSelectedCampaignId] = useState<string | null>(null)
  const [filterChannel,      setFilterChannel]       = useState('')
  const [filterStatus,       setFilterStatus]        = useState('')

  const { data, loading, error, refresh } = useCampaignData(
    tenantId,
    undefined,        // no campaign_id filter — load all for the sidebar
    filterChannel || undefined,
    filterStatus  || undefined,
  )

  const summaries = data?.summary ?? []
  const events    = data?.data    ?? []

  const selectedSummary = summaries.find(s => s.campaign_id === selectedCampaignId) ?? null

  // Auto-select first campaign if nothing is selected
  useEffect(() => {
    if (!selectedCampaignId && summaries.length > 0) {
      setSelectedCampaignId(summaries[0].campaign_id)
    }
  }, [summaries, selectedCampaignId])

  return (
    <div className="flex h-full overflow-hidden">
      {/* Sidebar */}
      <aside className="w-80 border-r border-border bg-white flex flex-col shrink-0">
        {/* Header */}
        <div className="px-4 py-4 border-b border-border">
          <div className="flex items-center justify-between mb-3">
            <h1 className="text-base font-semibold text-dark">{t('title')}</h1>
            <button
              onClick={refresh}
              className="text-xs text-secondary hover:text-primary transition-colors"
              title={t('refresh', { defaultValue: 'Refresh' })}
            >
              ↻ {t('refresh', { defaultValue: 'Refresh' })}
            </button>
          </div>

          {/* Global KPIs */}
          {summaries.length > 0 && (
            <div className="grid grid-cols-3 gap-2 mb-3">
              {[
                {
                  label: t('kpi.campaigns'),
                  value: String(summaries.length),
                },
                {
                  label: t('kpi.total'),
                  value: String(summaries.reduce((acc, s) => acc + s.total, 0)),
                },
                {
                  label: t('kpi.responseRate'),
                  value: (() => {
                    const total     = summaries.reduce((a, s) => a + s.total, 0)
                    const responded = summaries.reduce((a, s) => a + s.responded, 0)
                    return total > 0 ? `${((responded / total) * 100).toFixed(1)}%` : '—'
                  })(),
                },
              ].map(({ label, value }) => (
                <div key={label} className="bg-surface-muted rounded-lg p-2 text-center">
                  <p className="text-xs text-muted">{label}</p>
                  <p className="text-sm font-bold text-dark">{value}</p>
                </div>
              ))}
            </div>
          )}

          {/* Filters */}
          <div className="flex gap-2">
            <select
              value={filterChannel}
              onChange={e => setFilterChannel(e.target.value)}
              className="flex-1 text-xs border border-border rounded px-2 py-1.5 text-dark bg-white"
            >
              <option value="">{t('filters.all')} {t('filters.channel')}</option>
              {CHANNEL_OPTIONS.filter(Boolean).map(ch => (
                <option key={ch} value={ch}>{ch}</option>
              ))}
            </select>
            <select
              value={filterStatus}
              onChange={e => setFilterStatus(e.target.value)}
              className="flex-1 text-xs border border-border rounded px-2 py-1.5 text-dark bg-white"
            >
              <option value="">{t('filters.all')} {t('filters.status')}</option>
              {STATUS_OPTIONS.filter(Boolean).map(st => (
                <option key={st} value={st}>{st}</option>
              ))}
            </select>
          </div>
        </div>

        {/* Campaign list */}
        <div className="flex-1 overflow-y-auto">
          {loading && summaries.length === 0 ? (
            <div className="flex justify-center items-center h-32">
              <Spinner />
            </div>
          ) : error ? (
            <div className="px-4 py-6 text-center">
              <p className="text-sm text-red mb-2">{t('errorLoading', { defaultValue: 'Error loading campaigns' })}</p>
              <p className="text-xs text-muted-light">{error}</p>
            </div>
          ) : summaries.length === 0 ? (
            <div className="px-4 py-8 text-center text-sm text-muted">
              {t('noData')}
            </div>
          ) : (
            summaries.map(s => (
              <CampaignCard
                key={s.campaign_id}
                summary={s}
                selected={s.campaign_id === selectedCampaignId}
                onClick={() => setSelectedCampaignId(s.campaign_id)}
              />
            ))
          )}
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-y-auto bg-surface-muted">
        {selectedSummary ? (
          <CampaignDetail
            summary={selectedSummary}
            events={events}
          />
        ) : loading ? (
          <div className="flex justify-center items-center h-full">
            <Spinner />
          </div>
        ) : (
          <div className="flex justify-center items-center h-full">
            <EmptyState
              title={t('emptyTitle', { defaultValue: 'Select a campaign' })}
              description={t('emptyDesc', { defaultValue: 'Choose a campaign from the list to see details' })}
            />
          </div>
        )}
      </main>
    </div>
  )
}
