/**
 * CampaignPanel.tsx
 * Aggregate campaign view: per-campaign response rates, channel breakdown,
 * and individual collect instance list.
 *
 * Data source: GET /reports/campaigns (analytics-api → ClickHouse collect_events table)
 * Polls every 30s — shows last 7 days by default.
 *
 * Layout:
 *   Left (60%)  — campaign summary cards + collect instance list
 *   Right (40%) — selected campaign detail: status breakdown, channel chart
 */
import React, { useState, useMemo } from 'react'
import { useCampaignData } from '../api/campaign-hooks'
import type { CollectEvent, CampaignSummary } from '../types'

interface Props {
  tenantId: string
  onBack:   () => void
}

const COLLECT_STATUS_COLORS: Record<string, string> = {
  responded:  '#22c55e',
  sent:       '#3b82f6',
  requested:  '#94a3b8',
  timed_out:  '#ef4444',
}

const CHANNEL_ICONS: Record<string, string> = {
  whatsapp:  '📱',
  email:     '📧',
  sms:       '💬',
  voice:     '📞',
  webchat:   '🌐',
  telegram:  '✈️',
  instagram: '📸',
}

function fmtMs(ms: number | null): string {
  if (ms === null || ms === undefined) return '—'
  if (ms < 60_000) return `${(ms / 1000).toFixed(0)}s`
  if (ms < 3_600_000) return `${(ms / 60_000).toFixed(1)}m`
  return `${(ms / 3_600_000).toFixed(1)}h`
}

function RateBadge({ pct }: { pct: number }) {
  const color = pct >= 70 ? '#22c55e' : pct >= 40 ? '#eab308' : '#ef4444'
  return (
    <span
      style={{
        display: 'inline-block',
        padding: '2px 8px',
        borderRadius: 12,
        background: color + '33',
        border: `1px solid ${color}`,
        color,
        fontSize: 12,
        fontWeight: 700,
      }}
    >
      {pct.toFixed(1)}%
    </span>
  )
}

function MiniBar({ responded, timed_out, sent, requested, total }: {
  responded: number; timed_out: number; sent: number; requested: number; total: number
}) {
  if (total === 0) return null
  const pct = (n: number) => `${((n / total) * 100).toFixed(0)}%`
  const segments = [
    { label: 'responded', count: responded, color: '#22c55e' },
    { label: 'sent',      count: sent,      color: '#3b82f6' },
    { label: 'requested', count: requested, color: '#475569' },
    { label: 'timed_out', count: timed_out, color: '#ef4444' },
  ]
  return (
    <div
      style={{
        display: 'flex',
        height: 6,
        borderRadius: 3,
        overflow: 'hidden',
        marginTop: 8,
        background: '#1e293b',
      }}
    >
      {segments.map(s =>
        s.count > 0 ? (
          <div
            key={s.label}
            title={`${s.label}: ${s.count} (${pct(s.count)})`}
            style={{ flex: s.count, background: s.color }}
          />
        ) : null,
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
  return (
    <div
      onClick={onClick}
      style={{
        padding: '12px 16px',
        borderBottom: '1px solid #1e293b',
        cursor: 'pointer',
        background: selected ? '#1e293b' : 'transparent',
        borderLeft: selected ? '3px solid #3b82f6' : '3px solid transparent',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div
          style={{
            fontSize: 13,
            fontWeight: 600,
            color: '#e2e8f0',
            fontFamily: 'monospace',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            maxWidth: 200,
          }}
        >
          {summary.campaign_id}
        </div>
        <RateBadge pct={summary.response_rate_pct} />
      </div>
      <div
        style={{
          display: 'flex',
          gap: 16,
          marginTop: 6,
          fontSize: 11,
          color: '#64748b',
        }}
      >
        <span>{summary.total} total</span>
        <span style={{ color: '#22c55e' }}>{summary.responded} responded</span>
        {summary.timed_out > 0 && (
          <span style={{ color: '#ef4444' }}>{summary.timed_out} timed out</span>
        )}
        <span>avg {fmtMs(summary.avg_elapsed_ms)}</span>
      </div>
      <MiniBar
        responded={summary.responded}
        timed_out={summary.timed_out}
        sent={summary.sent}
        requested={summary.requested}
        total={summary.total}
      />
    </div>
  )
}

function CollectRow({ event }: { event: CollectEvent }) {
  const ts = event.timestamp
    ? new Date(event.timestamp).toLocaleTimeString()
    : '—'
  const channel = CHANNEL_ICONS[event.channel] ?? '📡'
  const color = COLLECT_STATUS_COLORS[event.status] ?? '#94a3b8'
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        padding: '8px 16px',
        borderBottom: '1px solid #0f172a',
        fontSize: 11,
      }}
    >
      <span title={event.channel}>{channel}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            color: '#94a3b8',
            fontFamily: 'monospace',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {event.collect_token.slice(0, 16)}…
        </div>
        <div style={{ color: '#475569', marginTop: 2 }}>{event.interaction}</div>
      </div>
      <div style={{ textAlign: 'right' }}>
        <div
          style={{
            display: 'inline-block',
            padding: '1px 6px',
            borderRadius: 3,
            background: color + '22',
            border: `1px solid ${color}`,
            color,
            fontSize: 10,
            fontWeight: 600,
          }}
        >
          {event.status}
        </div>
        <div style={{ color: '#475569', marginTop: 2 }}>{ts}</div>
        {event.elapsed_ms !== null && (
          <div style={{ color: '#64748b' }}>{fmtMs(event.elapsed_ms)}</div>
        )}
      </div>
    </div>
  )
}

function CampaignDetail({
  summary,
  events,
}: {
  summary: CampaignSummary
  events: CollectEvent[]
}) {
  // Channel breakdown from events
  const byChannel = useMemo(() => {
    const acc: Record<string, { responded: number; timed_out: number; total: number }> = {}
    for (const e of events) {
      const ch = e.channel || 'unknown'
      if (!acc[ch]) acc[ch] = { responded: 0, timed_out: 0, total: 0 }
      acc[ch].total++
      if (e.status === 'responded') acc[ch].responded++
      if (e.status === 'timed_out') acc[ch].timed_out++
    }
    return Object.entries(acc).sort((a, b) => b[1].total - a[1].total)
  }, [events])

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        overflow: 'hidden',
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: '16px',
          borderBottom: '1px solid #1e293b',
        }}
      >
        <div
          style={{
            fontSize: 13,
            fontWeight: 700,
            color: '#e2e8f0',
            fontFamily: 'monospace',
            marginBottom: 8,
          }}
        >
          {summary.campaign_id}
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <RateBadge pct={summary.response_rate_pct} />
          <span style={{ fontSize: 11, color: '#64748b', alignSelf: 'center' }}>
            response rate
          </span>
        </div>
      </div>

      {/* KPIs */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: 12,
          padding: 16,
          borderBottom: '1px solid #1e293b',
        }}
      >
        {[
          { label: 'Total',      value: summary.total,      color: '#e2e8f0' },
          { label: 'Responded',  value: summary.responded,  color: '#22c55e' },
          { label: 'Timed Out',  value: summary.timed_out,  color: '#ef4444' },
          { label: 'Avg Time',   value: fmtMs(summary.avg_elapsed_ms), color: '#94a3b8' },
        ].map(kpi => (
          <div
            key={kpi.label}
            style={{
              background: '#1e293b',
              borderRadius: 6,
              padding: '8px 12px',
            }}
          >
            <div style={{ fontSize: 11, color: '#64748b' }}>{kpi.label}</div>
            <div style={{ fontSize: 18, fontWeight: 700, color: kpi.color, marginTop: 2 }}>
              {kpi.value}
            </div>
          </div>
        ))}
      </div>

      {/* Channel breakdown */}
      {byChannel.length > 0 && (
        <div style={{ padding: '12px 16px', borderBottom: '1px solid #1e293b' }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: '#64748b', marginBottom: 8 }}>
            BY CHANNEL
          </div>
          {byChannel.map(([ch, stats]) => (
            <div
              key={ch}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                marginBottom: 6,
                fontSize: 11,
              }}
            >
              <span style={{ width: 20, textAlign: 'center' }}>
                {CHANNEL_ICONS[ch] ?? '📡'}
              </span>
              <span style={{ flex: 1, color: '#94a3b8', textTransform: 'capitalize' }}>{ch}</span>
              <span style={{ color: '#22c55e' }}>{stats.responded}</span>
              <span style={{ color: '#475569' }}>/</span>
              <span style={{ color: '#e2e8f0' }}>{stats.total}</span>
              <span style={{ color: '#64748b' }}>
                ({stats.total > 0 ? ((stats.responded / stats.total) * 100).toFixed(0) : 0}%)
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Recent events list */}
      <div style={{ flex: 1, overflowY: 'auto' }}>
        <div
          style={{
            padding: '8px 16px',
            fontSize: 11,
            fontWeight: 600,
            color: '#64748b',
            borderBottom: '1px solid #1e293b',
            position: 'sticky',
            top: 0,
            background: '#0f172a',
            zIndex: 1,
          }}
        >
          RECENT COLLECT EVENTS ({events.length})
        </div>
        {events.map(e => (
          <CollectRow key={e.collect_token} event={e} />
        ))}
        {events.length === 0 && (
          <div style={{ padding: 24, textAlign: 'center', color: '#475569', fontSize: 12 }}>
            No collect events found for this campaign.
          </div>
        )}
      </div>
    </div>
  )
}

export function CampaignPanel({ tenantId, onBack }: Props) {
  const [filterChannel, setFilterChannel] = useState<string>('')
  const [filterStatus, setFilterStatus]   = useState<string>('')
  const [selectedCampaignId, setSelected] = useState<string | null>(null)

  const { campaign, loading } = useCampaignData(
    tenantId,
    undefined,
    filterChannel || undefined,
    filterStatus  || undefined,
  )

  const summaries: CampaignSummary[] = campaign?.summary ?? []
  const allEvents: CollectEvent[]    = campaign?.data    ?? []

  const selectedSummary = selectedCampaignId
    ? summaries.find(s => s.campaign_id === selectedCampaignId) ?? null
    : null

  const selectedEvents = selectedCampaignId
    ? allEvents.filter(e => e.campaign_id === selectedCampaignId)
    : []

  const totalCollects  = summaries.reduce((n, s) => n + s.total, 0)
  const totalResponded = summaries.reduce((n, s) => n + s.responded, 0)
  const globalRate     = totalCollects > 0
    ? (totalResponded / totalCollects) * 100
    : 0

  return (
    <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
      {/* Left: campaign list */}
      <div
        style={{
          flex: 1,
          borderRight: '1px solid #1e293b',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}
      >
        {/* Toolbar */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '10px 16px',
            borderBottom: '1px solid #1e293b',
            flexWrap: 'wrap',
          }}
        >
          <button
            onClick={onBack}
            style={{
              padding: '4px 12px',
              borderRadius: 4,
              border: '1px solid #334155',
              background: '#0f172a',
              color: '#94a3b8',
              cursor: 'pointer',
              fontSize: 12,
            }}
          >
            ← Back
          </button>

          <select
            value={filterChannel}
            onChange={e => setFilterChannel(e.target.value)}
            style={{
              padding: '3px 8px',
              borderRadius: 4,
              border: '1px solid #334155',
              background: '#0f172a',
              color: '#94a3b8',
              fontSize: 11,
            }}
          >
            <option value="">All channels</option>
            {['whatsapp', 'email', 'sms', 'voice', 'webchat'].map(c => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>

          <select
            value={filterStatus}
            onChange={e => setFilterStatus(e.target.value)}
            style={{
              padding: '3px 8px',
              borderRadius: 4,
              border: '1px solid #334155',
              background: '#0f172a',
              color: '#94a3b8',
              fontSize: 11,
            }}
          >
            <option value="">All statuses</option>
            {['requested', 'sent', 'responded', 'timed_out'].map(s => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>

          <div style={{ marginLeft: 'auto', fontSize: 11, color: '#64748b' }}>
            {loading ? 'Loading…' : `${summaries.length} campaigns`}
          </div>
        </div>

        {/* Global KPIs */}
        {summaries.length > 0 && (
          <div
            style={{
              display: 'flex',
              gap: 16,
              padding: '10px 16px',
              borderBottom: '1px solid #1e293b',
              fontSize: 11,
              color: '#64748b',
              background: '#0a1628',
            }}
          >
            <span>{totalCollects} total collects</span>
            <span style={{ color: '#22c55e' }}>{totalResponded} responded</span>
            <span>global rate: <RateBadge pct={globalRate} /></span>
          </div>
        )}

        {/* Campaign cards */}
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {summaries.map(s => (
            <CampaignCard
              key={s.campaign_id}
              summary={s}
              selected={s.campaign_id === selectedCampaignId}
              onClick={() => setSelected(
                s.campaign_id === selectedCampaignId ? null : s.campaign_id,
              )}
            />
          ))}

          {summaries.length === 0 && !loading && (
            <div style={{ padding: 40, textAlign: 'center', color: '#475569', fontSize: 13 }}>
              No campaigns found for the selected filters.
            </div>
          )}
        </div>
      </div>

      {/* Right: detail */}
      {selectedSummary && (
        <div
          style={{
            width: 420,
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
            borderLeft: '1px solid #1e293b',
          }}
        >
          <CampaignDetail summary={selectedSummary} events={selectedEvents} />
        </div>
      )}
    </div>
  )
}
