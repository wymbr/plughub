/**
 * CardRenderer.tsx
 * Unified card content renderer.
 *
 * Routes to the correct display:
 *   new-format card (tool_id present) → fetches from query.endpoint, renders via DisplayTool
 *   legacy card (type present)        → legacy path via TimeseriesChart (unchanged behaviour)
 *
 * This component owns the data-fetch cycle for new-format cards.
 * Polling interval is taken from card.refresh_ms (default 30s).
 */
import React, { useEffect, useRef, useState } from 'react'
import { TimeseriesChart } from '@/components/TimeseriesChart'
import type { DashboardCard, TimeseriesCardConfig, PoolStatusCardConfig } from '@/types'
import { getDisplayTool } from './tools/registry'
import type { NewDashboardCard } from './tools/types'
import { buildQueryUrl } from './tools/types'
import type { DisplayToolDataShape } from './tools/types'

// ─── Props ────────────────────────────────────────────────────────────────────

interface CardRendererProps {
  /** The raw card — may be old-format (DashboardCard) or new-format (NewDashboardCard). */
  card:            DashboardCard | NewDashboardCard
  tenantId:        string
  /** Active runtime filters from the FilterBar (Part 3). Empty object for now. */
  runtimeFilters?: Record<string, unknown>
}

// ─── New-format card fetcher ──────────────────────────────────────────────────

function useFetchCardData(
  card:           NewDashboardCard,
  runtimeFilters: Record<string, unknown>,
): { data: DisplayToolDataShape | null; loading: boolean; error: string | null } {
  const [data,    setData]    = useState<DisplayToolDataShape | null>(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState<string | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    let cancelled = false

    async function fetchData() {
      try {
        setLoading(true)
        const url = buildQueryUrl(card, runtimeFilters)
        const res = await fetch(url)
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const json = await res.json()
        if (!cancelled) { setData(json); setError(null) }
      } catch (e) {
        if (!cancelled) setError(String(e))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    fetchData()

    const interval = card.refresh_ms ?? 30_000
    if (interval > 0) {
      timerRef.current = setInterval(fetchData, interval)
    }

    return () => {
      cancelled = true
      if (timerRef.current) clearInterval(timerRef.current)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [card.id, card.query.endpoint, JSON.stringify(card.query.params), JSON.stringify(runtimeFilters)])

  return { data, loading, error }
}

// ─── New-format card renderer ─────────────────────────────────────────────────

function NewCardContent({
  card,
  runtimeFilters,
}: {
  card:           NewDashboardCard
  runtimeFilters: Record<string, unknown>
}) {
  const tool = getDisplayTool(card.tool_id)
  const { data, loading, error } = useFetchCardData(card, runtimeFilters)

  if (!tool) {
    return (
      <div className="h-full flex items-center justify-center text-xs text-gray-400">
        Tool &quot;{card.tool_id}&quot; não registrado
      </div>
    )
  }

  const ToolComponent = tool.component

  return (
    <ToolComponent
      data={data as DisplayToolDataShape}
      config={card.tool_config}
      loading={loading}
      error={error}
    />
  )
}

// ─── Legacy card renderer (unchanged behaviour) ───────────────────────────────

function LegacyCardContent({ card, tenantId }: { card: DashboardCard; tenantId: string }) {
  if (card.type.startsWith('timeseries_')) {
    const cfg = card.config as TimeseriesCardConfig
    const formatType = card.type === 'timeseries_handle_time'
      ? 'duration_ms'
      : card.type === 'timeseries_score'
        ? 'score'
        : 'count'
    return (
      <TimeseriesChart
        baseUrl={cfg.url}
        tenantId={tenantId}
        title={cfg.title}
        valueLabel={cfg.valueLabel}
        displayType={cfg.displayType ?? 'bar'}
        formatType={formatType}
        defaultInterval={cfg.interval ?? 60}
        defaultBreakdownBy={cfg.breakdownBy}
        poolId={cfg.poolId}
        compact
        pollMs={30_000}
      />
    )
  }

  if (card.type === 'pool_status') {
    const cfg = card.config as PoolStatusCardConfig
    return (
      <div className="h-full flex flex-col">
        <p className="text-xs text-gray-500 px-1 pb-1">{cfg.title}</p>
        <div className="flex-1 flex items-center justify-center text-xs text-gray-400">
          {/* Operational pool status — pending backend (Part 4) */}
        </div>
      </div>
    )
  }

  return (
    <div className="h-full flex items-center justify-center text-xs text-gray-400">
      {card.type}
    </div>
  )
}

// ─── Main export ──────────────────────────────────────────────────────────────

export function CardRenderer({ card, tenantId, runtimeFilters = {} }: CardRendererProps) {
  // New-format cards have tool_id; legacy cards have type
  if ('tool_id' in card && typeof card.tool_id === 'string') {
    return (
      <NewCardContent
        card={card as NewDashboardCard}
        runtimeFilters={runtimeFilters}
      />
    )
  }

  return <LegacyCardContent card={card as DashboardCard} tenantId={tenantId} />
}

export default CardRenderer
