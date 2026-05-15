/**
 * MetricSelector.tsx
 * Shared component for selecting quality metrics + agent_event overlays.
 *
 * Renders pill-style toggles for the 4 base metrics and a "+ Evento" button
 * that fetches available categories from GET /reports/agent-events/categories
 * and lets the user pick any as an `agent_event:{category}` overlay metric.
 *
 * Used by: AnaliseComparacaoPage, AnaliseQualidadePage (TimeseriesView, ComparisonView)
 */
import React, { useState, useEffect, useRef } from 'react'

// ── Metric definitions ─────────────────────────────────────────────────────────

export interface MetricDef {
  key:              string
  label:            string
  /** Format the raw backend value (already resolved) for display */
  format:           (v: number | null) => string
  higherIsBetter:   boolean
  /** Recharts line color (timeseries only) */
  color?:           string
}

export const BASE_METRIC_DEFS: MetricDef[] = [
  {
    key:            'evaluation_score',
    label:          'Nota de Avaliação',
    format:         (v) => v === null ? '—' : `${(v * 100).toFixed(1)}%`,
    higherIsBetter: true,
    color:          '#1B4F8A',
  },
  {
    key:            'resolution_rate',
    label:          'Taxa de Resolução',
    format:         (v) => v === null ? '—' : `${(v * 100).toFixed(1)}%`,
    higherIsBetter: true,
    color:          '#059669',
  },
  {
    key:            'escalation_rate',
    label:          'Taxa de Escalação',
    format:         (v) => v === null ? '—' : `${(v * 100).toFixed(1)}%`,
    higherIsBetter: false,
    color:          '#DC2626',
  },
  {
    key:            'aht_ms',
    label:          'TMA (min)',
    format:         (v) => v === null ? '—' : `${(v / 60000).toFixed(1)}`,
    higherIsBetter: false,
    color:          '#D97706',
  },
]

export const BASE_METRIC_KEYS = BASE_METRIC_DEFS.map(d => d.key)

const AGENT_EVENT_COLORS = ['#7C3AED', '#0891B2', '#BE185D', '#15803D', '#B45309', '#6D28D9']

export function makeAgentEventDef(category: string, idx: number): MetricDef {
  const short = category.split('.').pop() ?? category
  return {
    key:            `agent_event:${category}`,
    label:          `KPI: ${short}`,
    format:         (v) => v === null ? '—' : v.toFixed(2),
    higherIsBetter: true,
    color:          AGENT_EVENT_COLORS[idx % AGENT_EVENT_COLORS.length],
  }
}

export function buildMetricDefs(selectedMetrics: string[]): MetricDef[] {
  const base   = BASE_METRIC_DEFS.filter(d => selectedMetrics.includes(d.key))
  const events = selectedMetrics
    .filter(k => k.startsWith('agent_event:'))
    .map((k, i) => makeAgentEventDef(k.replace('agent_event:', ''), i))
  return [...base, ...events]
}

// ── Component ──────────────────────────────────────────────────────────────────

interface MetricSelectorProps {
  selected:  string[]
  onChange:  (metrics: string[]) => void
  tenantId:  string
}

export function MetricSelector({ selected, onChange, tenantId }: MetricSelectorProps) {
  const [categories,  setCategories]  = useState<string[]>([])
  const [loadingCats, setLoadingCats] = useState(false)
  const [showPicker,  setShowPicker]  = useState(false)
  const pickerRef                     = useRef<HTMLDivElement>(null)

  // Close picker when clicking outside
  useEffect(() => {
    function handler(e: MouseEvent) {
      if (pickerRef.current && !pickerRef.current.contains(e.target as Node)) {
        setShowPicker(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  function fetchCategories() {
    if (categories.length > 0 || loadingCats) return
    setLoadingCats(true)
    fetch(`/reports/agent-events/categories?tenant_id=${encodeURIComponent(tenantId)}`)
      .then(r => r.ok ? r.json() : Promise.reject())
      .then((body: { data?: Array<{ category: string }> }) => {
        setCategories(body.data?.map(d => d.category) ?? [])
      })
      .catch(() => {})
      .finally(() => setLoadingCats(false))
  }

  function toggle(key: string) {
    onChange(selected.includes(key)
      ? selected.filter(k => k !== key)
      : [...selected, key])
  }

  function addCategory(cat: string) {
    const key = `agent_event:${cat}`
    if (!selected.includes(key)) onChange([...selected, key])
    setShowPicker(false)
  }

  const agentEventKeys = selected.filter(k => k.startsWith('agent_event:'))

  return (
    <div className="flex flex-col gap-2">
      <span className="text-xs font-medium text-gray-600">Métricas</span>
      <div className="flex flex-wrap gap-1.5 items-center">
        {/* Base metric toggles */}
        {BASE_METRIC_DEFS.map(m => (
          <button
            key={m.key}
            onClick={() => toggle(m.key)}
            className={`text-xs rounded-full px-2.5 py-0.5 border transition-colors ${
              selected.includes(m.key)
                ? 'bg-primary text-white border-primary'
                : 'border-gray-300 text-gray-500 hover:border-primary hover:text-primary'
            }`}
          >
            {m.label}
          </button>
        ))}

        {/* Active agent_event pills */}
        {agentEventKeys.map(key => {
          const cat   = key.replace('agent_event:', '')
          const short = cat.split('.').pop() ?? cat
          return (
            <span key={key}
              className="flex items-center gap-1 text-xs bg-violet-50 text-violet-700 border border-violet-200 rounded-full px-2.5 py-0.5">
              📈 {short}
              <button
                onClick={() => toggle(key)}
                className="ml-0.5 text-violet-400 hover:text-violet-700 leading-none"
                aria-label={`Remover ${cat}`}
              >×</button>
            </span>
          )
        })}

        {/* Category picker trigger */}
        <div className="relative" ref={pickerRef}>
          <button
            onClick={() => { setShowPicker(v => !v); if (!showPicker) fetchCategories() }}
            className="text-xs border border-dashed border-primary text-primary rounded-full px-2.5 py-0.5 hover:bg-primary hover:text-white transition-colors"
          >
            + Evento
          </button>

          {showPicker && (
            <div className="absolute top-7 left-0 z-20 bg-white border border-gray-200 rounded-lg shadow-lg min-w-[280px] max-h-52 overflow-y-auto">
              {loadingCats ? (
                <div className="px-3 py-2 text-xs text-gray-400">Carregando categorias…</div>
              ) : categories.length === 0 ? (
                <div className="px-3 py-2 text-xs text-gray-400">Nenhuma categoria disponível</div>
              ) : (
                categories.map(cat => {
                  const key   = `agent_event:${cat}`
                  const added = selected.includes(key)
                  return (
                    <button
                      key={cat}
                      onClick={() => !added && addCategory(cat)}
                      disabled={added}
                      className={`w-full text-left px-3 py-2 text-xs transition-colors hover:bg-gray-50 ${
                        added ? 'text-gray-300 cursor-not-allowed' : 'text-gray-700'
                      }`}
                    >
                      {added ? '✓ ' : ''}{cat}
                    </button>
                  )
                })
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
