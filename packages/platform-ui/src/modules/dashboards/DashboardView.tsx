/**
 * DashboardView.tsx
 *
 * View-only dashboard renderer for consumption surfaces (e.g. Home).
 * Reuses the Display Tool registry (CardRenderer) + runtime FilterBar, but without
 * any builder chrome (no template sidebar, no add/remove/edit). Personalisation
 * (drag layout, add/remove within an allowlist) is a later phase (F3/F4).
 *
 * Template resolution: module_config.dashboard.default_template_id → else first template.
 * Data is always scoped per principal at the /reports/display/* endpoints.
 */
import React, { useCallback, useEffect, useState } from 'react'
import GridLayout, { Layout } from 'react-grid-layout'
import 'react-grid-layout/css/styles.css'
import 'react-resizable/css/styles.css'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import { CardRenderer } from '@/dashboard/CardRenderer'
import { FilterBar } from '@/dashboard/FilterBar'
import {
  loadPersonalLayout,
  useDefaultTemplateId,
  useTemplate,
  useTemplates,
} from '@/api/dashboard-hooks'
import type { DashboardCard, GlobalFilter, TimeseriesCardConfig } from '@/types'

const COLS = 12

function cardTitle(card: DashboardCard): string {
  if ('title' in card && (card as { title?: string }).title) return (card as { title: string }).title
  const cfg = (card as { config?: TimeseriesCardConfig }).config
  return cfg?.title ?? (card as { type?: string }).type ?? ''
}

export default function DashboardView() {
  const { session } = useAuth()
  const { t } = useTranslation('dashboards')
  const tenantId   = session?.tenantId ?? ''
  const userId     = session?.userId ?? ''
  const adminToken = session?.accessToken ?? ''

  const { templates, loading: tmplLoading } = useTemplates(tenantId, adminToken)
  const defaultTemplateId = useDefaultTemplateId(session?.moduleConfig)

  const [activeTemplateId, setActiveTemplateId] = useState<string | null>(null)
  useEffect(() => {
    if (activeTemplateId) return
    if (defaultTemplateId) setActiveTemplateId(defaultTemplateId)
    else if (templates.length > 0) setActiveTemplateId(templates[0].template_id)
  }, [defaultTemplateId, templates, activeTemplateId])

  const { template, loading: tLoading } = useTemplate(activeTemplateId, adminToken, tenantId)

  const [cards,          setCards]          = useState<DashboardCard[]>([])
  const [globalFilters,  setGlobalFilters]  = useState<GlobalFilter[]>([])
  const [runtimeFilters, setRuntimeFilters] = useState<Record<string, unknown>>({})

  useEffect(() => {
    if (!template) { setCards([]); setGlobalFilters([]); setRuntimeFilters({}); return }
    const tf = template.global_filters ?? []
    setGlobalFilters(tf)
    const defaults: Record<string, unknown> = {}
    for (const f of tf) if (f.default !== null && f.default !== undefined) defaults[f.filter_key] = f.default
    setRuntimeFilters(defaults)
    loadPersonalLayout(tenantId, userId).then(personal => {
      // Apply personal layout only if it covers exactly the template's card set.
      if (personal && personal.length === template.cards.length) {
        const ids = new Set(personal.map(c => c.id))
        if (template.cards.every(c => ids.has(c.id))) { setCards(personal as DashboardCard[]); return }
      }
      setCards(template.cards as DashboardCard[])
    })
  }, [template, tenantId, userId])

  const [gridWidth, setGridWidth] = useState(900)
  const gridRef = useCallback((node: HTMLDivElement | null) => {
    if (!node) return
    const obs = new ResizeObserver(([entry]) => setGridWidth(entry.contentRect.width))
    obs.observe(node)
    return () => obs.disconnect()
  }, [])

  const resetRuntimeFilters = () => {
    const d: Record<string, unknown> = {}
    for (const f of globalFilters) if (f.default !== null && f.default !== undefined) d[f.filter_key] = f.default
    setRuntimeFilters(d)
  }

  const layout: Layout[] = cards.map(c => ({ i: c.id, x: c.x, y: c.y, w: c.w, h: c.h }))
  const loading = tmplLoading || tLoading
  const empty   = !loading && (!template || cards.length === 0)

  return (
    <div ref={gridRef}>
      {globalFilters.length > 0 && (
        <div className="mb-3">
          <FilterBar
            filters={globalFilters}
            values={runtimeFilters}
            onChange={(k, v) => setRuntimeFilters(prev => ({ ...prev, [k]: v }))}
            onReset={resetRuntimeFilters}
          />
        </div>
      )}

      {loading ? (
        <div className="text-sm text-muted-light py-10 text-center">{t('home.loading')}</div>
      ) : empty ? (
        <div className="text-sm text-muted-light py-10 text-center border border-dashed border-border rounded-lg">
          {t('home.empty')}
        </div>
      ) : (
        <GridLayout
          layout={layout}
          cols={COLS}
          rowHeight={60}
          width={gridWidth}
          isDraggable={false}
          isResizable={false}
          compactType="vertical"
          margin={[12, 12]}
        >
          {cards.map(card => (
            <div
              key={card.id}
              className="bg-white rounded-lg border border-border shadow-sm overflow-hidden flex flex-col"
            >
              <div className="flex items-center px-3 py-1.5 border-b border-border bg-surface-muted flex-shrink-0">
                <span className="text-xs font-medium text-muted truncate">{cardTitle(card)}</span>
              </div>
              <div className="flex-1 p-2 min-h-0">
                <CardRenderer card={card} tenantId={tenantId} runtimeFilters={runtimeFilters} />
              </div>
            </div>
          ))}
        </GridLayout>
      )}
    </div>
  )
}
