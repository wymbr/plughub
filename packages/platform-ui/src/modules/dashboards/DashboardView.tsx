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
  loadRoleCatalog,
  useDefaultTemplateId,
  useTemplate,
  useTemplates,
  type RoleCatalog,
} from '@/api/dashboard-hooks'
import { ENDPOINT_CATALOG } from '@/dashboard/catalog'
import type { DashboardCard, GlobalFilter, TimeseriesCardConfig } from '@/types'

const COLS = 12
const CATALOG_BY_ENDPOINT = new Map(ENDPOINT_CATALOG.map(e => [e.endpoint, e.id]))

function cardTitle(card: DashboardCard): string {
  if ('title' in card && (card as { title?: string }).title) return (card as { title: string }).title
  const cfg = (card as { config?: TimeseriesCardConfig }).config
  return cfg?.title ?? (card as { type?: string }).type ?? ''
}

/** Drop cards whose catalog entry is not in the role's allowlist.
 *  Empty/absent allowlist = no restriction; unknown/legacy cards are kept. */
function reconcileCards(cards: DashboardCard[], allowed: string[] | null): DashboardCard[] {
  if (!allowed || allowed.length === 0) return cards
  return cards.filter(c => {
    const ep = (c as { query?: { endpoint?: string } }).query?.endpoint
    if (!ep) return true
    const id = CATALOG_BY_ENDPOINT.get(ep)
    if (!id) return true
    return allowed.includes(id)
  })
}

export default function DashboardView() {
  const { session } = useAuth()
  const { t } = useTranslation('dashboards')
  const tenantId   = session?.tenantId ?? ''
  const userId     = session?.userId ?? ''
  const adminToken = session?.accessToken ?? ''

  const role = session?.role ?? ''
  const { templates, loading: tmplLoading } = useTemplates(tenantId, adminToken)
  const defaultTemplateId = useDefaultTemplateId(session?.moduleConfig)

  // Per-role allowlist + starter (undefined = still loading)
  const [roleCatalog, setRoleCatalog] = useState<RoleCatalog | null | undefined>(undefined)
  useEffect(() => {
    if (!tenantId || !role) { setRoleCatalog(null); return }
    let cancelled = false
    loadRoleCatalog(tenantId, role)
      .then(rc => { if (!cancelled) setRoleCatalog(rc) })
      .catch(() => { if (!cancelled) setRoleCatalog(null) })
    return () => { cancelled = true }
  }, [tenantId, role])

  // Resolution: role starter → module default → first template (wait for role catalog).
  const [activeTemplateId, setActiveTemplateId] = useState<string | null>(null)
  useEffect(() => {
    if (activeTemplateId) return
    if (roleCatalog === undefined) return
    const starter = roleCatalog?.starter_template_id
    if (starter) { setActiveTemplateId(starter); return }
    if (defaultTemplateId) { setActiveTemplateId(defaultTemplateId); return }
    if (templates.length > 0) setActiveTemplateId(templates[0].template_id)
  }, [roleCatalog, defaultTemplateId, templates, activeTemplateId])

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

  const visibleCards = reconcileCards(cards, roleCatalog?.allowed ?? null)
  const layout: Layout[] = visibleCards.map(c => ({ i: c.id, x: c.x, y: c.y, w: c.w, h: c.h }))
  const loading = tmplLoading || tLoading || roleCatalog === undefined
  const empty   = !loading && (!template || visibleCards.length === 0)

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
          {visibleCards.map(card => (
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
