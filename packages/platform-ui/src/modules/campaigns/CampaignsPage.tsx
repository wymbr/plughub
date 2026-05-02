import React, { useCallback, useEffect, useState } from 'react'
import { useAuth } from '@/auth/useAuth'
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
  responded:  'bg-green-100 text-green-700',
  timed_out:  'bg-red-100 text-red-700',
  sent:       'bg-blue-100 text-blue-700',
  requested:  'bg-yellow-100 text-yellow-700',
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
      const res = await fetch(`/reports/campaigns?${params}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setData(await res.json())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Erro desconhecido')
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
    rate >= 70 ? 'bg-green-100 text-green-700' :
    rate >= 40 ? 'bg-yellow-100 text-yellow-700' :
                 'bg-red-100 text-red-700'
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold ${color}`}>
      {rate.toFixed(1)}%
    </span>
  )
}

function MiniBar({ responded, sent, timed_out, requested }: {
  responded: number; sent: number; timed_out: number; requested: number
}) {
  const total = responded + sent + timed_out + requested
  if (total === 0) return <div className="h-2 bg-gray-100 rounded-full w-full" />

  const pct = (n: number) => `${((n / total) * 100).toFixed(1)}%`

  return (
    <div className="flex h-2 rounded-full overflow-hidden w-full gap-0.5">
      {responded > 0 && (
        <div title={`Respondidos: ${responded}`}
          className="bg-green-400 rounded-l-full" style={{ width: pct(responded) }} />
      )}
      {sent > 0 && (
        <div title={`Enviados: ${sent}`}
          className="bg-blue-400" style={{ width: pct(sent) }} />
      )}
      {timed_out > 0 && (
        <div title={`Expirados: ${timed_out}`}
          className="bg-red-400" style={{ width: pct(timed_out) }} />
      )}
      {requested > 0 && (
        <div title={`Aguardando: ${requested}`}
          className="bg-yellow-300 rounded-r-full" style={{ width: pct(requested) }} />
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
    <button
      onClick={onClick}
      className={`w-full text-left px-4 py-3 border-b border-gray-100 hover:bg-gray-50 transition-colors ${
        selected ? 'bg-blue-50 border-l-2 border-l-secondary' : ''
      }`}
    >
      <div className="flex items-center justify-between mb-1">
        <p className="text-sm font-medium text-gray-900 truncate max-w-[170px]">
          {summary.campaign_id}
        </p>
        <RateBadge rate={summary.response_rate_pct} />
      </div>
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-gray-500">{summary.total} disparos</span>
        <span className="text-xs text-gray-400">
          {summary.responded} respondidos
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
    <div className="bg-white border border-gray-200 rounded-lg p-4">
      <p className="text-xs text-gray-500 mb-1">{label}</p>
      <p className="text-2xl font-bold text-gray-900">{value}</p>
      {sub && <p className="text-xs text-gray-400 mt-1">{sub}</p>}
    </div>
  )
}

function ChannelBreakdown({ events }: { events: CollectEvent[] }) {
  const counts: Record<string, { total: number; responded: number }> = {}
  for (const e of events) {
    if (!counts[e.channel]) counts[e.channel] = { total: 0, responded: 0 }
    counts[e.channel].total++
    if (e.status === 'responded') counts[e.channel].responded++
  }

  const channels = Object.entries(counts).sort((a, b) => b[1].total - a[1].total)
  if (channels.length === 0) return null

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-4">
      <h3 className="text-sm font-semibold text-gray-700 mb-3">Por canal</h3>
      <div className="space-y-2">
        {channels.map(([ch, { total, responded }]) => (
          <div key={ch} className="flex items-center gap-2">
            <span className="text-base">{CHANNEL_ICONS[ch] ?? '📡'}</span>
            <span className="text-sm text-gray-700 w-24 capitalize">{ch}</span>
            <div className="flex-1 h-2 bg-gray-100 rounded-full overflow-hidden">
              <div
                className="h-2 bg-green-400 rounded-full"
                style={{ width: total > 0 ? `${(responded / total) * 100}%` : '0%' }}
              />
            </div>
            <span className="text-xs text-gray-500 w-16 text-right">
              {responded}/{total}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

function CollectEventRow({ event }: { event: CollectEvent }) {
  const statusClass = STATUS_COLORS[event.status] ?? 'bg-gray-100 text-gray-600'

  return (
    <tr className="hover:bg-gray-50">
      <td className="px-4 py-2 text-xs font-mono text-gray-500 max-w-[120px] truncate">
        {event.collect_token.slice(0, 16)}…
      </td>
      <td className="px-4 py-2 text-xs text-gray-700 capitalize">
        {CHANNEL_ICONS[event.channel] ?? '📡'} {event.channel}
      </td>
      <td className="px-4 py-2">
        <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${statusClass}`}>
          {event.status}
        </span>
      </td>
      <td className="px-4 py-2 text-xs text-gray-500">{fmtDate(event.send_at)}</td>
      <td className="px-4 py-2 text-xs text-gray-500">{fmtDuration(event.elapsed_ms)}</td>
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
  const campaignEvents = events.filter(
    e => e.campaign_id === summary.campaign_id
  )

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">{summary.campaign_id}</h2>
          <p className="text-sm text-gray-500 mt-0.5">
            {summary.total} disparos • taxa de resposta{' '}
            <strong className="text-gray-700">{summary.response_rate_pct.toFixed(1)}%</strong>
          </p>
        </div>
        <RateBadge rate={summary.response_rate_pct} />
      </div>

      {/* KPI grid */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <KpiCard label="Total" value={String(summary.total)} />
        <KpiCard
          label="Respondidos"
          value={String(summary.responded)}
          sub={`${summary.response_rate_pct.toFixed(1)}% de resposta`}
        />
        <KpiCard label="Expirados" value={String(summary.timed_out)} />
        <KpiCard
          label="Tempo médio"
          value={fmtDuration(summary.avg_elapsed_ms)}
          sub="até resposta"
        />
      </div>

      {/* Status bar */}
      <div className="bg-white border border-gray-200 rounded-lg p-4">
        <h3 className="text-sm font-semibold text-gray-700 mb-3">Distribuição de status</h3>
        <MiniBar
          responded={summary.responded}
          sent={summary.sent}
          timed_out={summary.timed_out}
          requested={summary.requested}
        />
        <div className="flex gap-4 mt-2">
          {[
            { label: 'Respondidos', color: 'bg-green-400', count: summary.responded },
            { label: 'Enviados',    color: 'bg-blue-400',  count: summary.sent },
            { label: 'Expirados',   color: 'bg-red-400',   count: summary.timed_out },
            { label: 'Aguardando',  color: 'bg-yellow-300', count: summary.requested },
          ].map(({ label, color, count }) => (
            <div key={label} className="flex items-center gap-1.5 text-xs text-gray-600">
              <div className={`w-2.5 h-2.5 rounded-full ${color}`} />
              {label}: {count}
            </div>
          ))}
        </div>
      </div>

      {/* Channel breakdown */}
      {campaignEvents.length > 0 && <ChannelBreakdown events={campaignEvents} />}

      {/* Collect events table */}
      <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
        <div className="px-4 py-3 border-b border-gray-100">
          <h3 className="text-sm font-semibold text-gray-700">
            Eventos recentes ({campaignEvents.length})
          </h3>
        </div>
        {campaignEvents.length === 0 ? (
          <div className="px-4 py-8 text-center text-sm text-gray-500">
            Nenhum evento registrado ainda
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-50 text-xs text-gray-500 uppercase tracking-wide">
                  <th className="px-4 py-2 text-left">Token</th>
                  <th className="px-4 py-2 text-left">Canal</th>
                  <th className="px-4 py-2 text-left">Status</th>
                  <th className="px-4 py-2 text-left">Enviado em</th>
                  <th className="px-4 py-2 text-left">Tempo</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
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
      <aside className="w-80 border-r border-gray-200 bg-white flex flex-col shrink-0">
        {/* Header */}
        <div className="px-4 py-4 border-b border-gray-100">
          <div className="flex items-center justify-between mb-3">
            <h1 className="text-base font-semibold text-gray-900">Campanhas</h1>
            <button
              onClick={refresh}
              className="text-xs text-secondary hover:text-primary transition-colors"
              title="Atualizar"
            >
              ↻ Atualizar
            </button>
          </div>

          {/* Global KPIs */}
          {summaries.length > 0 && (
            <div className="grid grid-cols-3 gap-2 mb-3">
              {[
                {
                  label: 'Campanhas',
                  value: String(summaries.length),
                },
                {
                  label: 'Total',
                  value: String(summaries.reduce((acc, s) => acc + s.total, 0)),
                },
                {
                  label: 'Taxa',
                  value: (() => {
                    const total     = summaries.reduce((a, s) => a + s.total, 0)
                    const responded = summaries.reduce((a, s) => a + s.responded, 0)
                    return total > 0 ? `${((responded / total) * 100).toFixed(1)}%` : '—'
                  })(),
                },
              ].map(({ label, value }) => (
                <div key={label} className="bg-gray-50 rounded-lg p-2 text-center">
                  <p className="text-xs text-gray-500">{label}</p>
                  <p className="text-sm font-bold text-gray-900">{value}</p>
                </div>
              ))}
            </div>
          )}

          {/* Filters */}
          <div className="flex gap-2">
            <select
              value={filterChannel}
              onChange={e => setFilterChannel(e.target.value)}
              className="flex-1 text-xs border border-gray-200 rounded px-2 py-1.5 text-gray-700 bg-white"
            >
              <option value="">Todos os canais</option>
              {CHANNEL_OPTIONS.filter(Boolean).map(ch => (
                <option key={ch} value={ch}>{ch}</option>
              ))}
            </select>
            <select
              value={filterStatus}
              onChange={e => setFilterStatus(e.target.value)}
              className="flex-1 text-xs border border-gray-200 rounded px-2 py-1.5 text-gray-700 bg-white"
            >
              <option value="">Todos os status</option>
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
              <p className="text-sm text-red-600 mb-2">Erro ao carregar campanhas</p>
              <p className="text-xs text-gray-400">{error}</p>
            </div>
          ) : summaries.length === 0 ? (
            <div className="px-4 py-8 text-center text-sm text-gray-500">
              Nenhuma campanha encontrada
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
      <main className="flex-1 overflow-y-auto bg-gray-50">
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
              title="Selecione uma campanha"
              description="Escolha uma campanha na lista para ver os detalhes"
            />
          </div>
        )}
      </main>
    </div>
  )
}
