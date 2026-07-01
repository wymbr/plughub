/**
 * DashboardView.tsx
 *
 * Personalisable dashboard renderer for consumption surfaces (e.g. Home).
 * Reuses the Display Tool registry (CardRenderer) + runtime FilterBar. In "customize"
 * mode the user can drag/resize, remove cards, and add components from their role's
 * allowlist; the result is saved as the user's personal layout.
 *
 * Resolution: personal layout (if any) → role starter → module default → first template.
 * The allowlist (role_catalog) constrains what a user may add and reconciles cards.
 * Data is always scoped per principal at the /reports/display/* endpoints.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react'
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
  savePersonalLayout,
  useDefaultTemplateId,
  useTemplate,
  useTemplates,
  type RoleCatalog,
} from '@/api/dashboard-hooks'
import { ENDPOINT_CATALOG, type EndpointDescriptor } from '@/dashboard/catalog'
import type { DashboardCard, GlobalFilter, TimeseriesCardConfig } from '@/types'

const COLS = 12
const CATALOG_BY_ENDPOINT = new Map(ENDPOINT_CATALOG.map(e => [e.endpoint, e.id]))

function uuid(): string {
  return crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2)
}

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

/** Build a card from a catalog descriptor, with runtime-wired params (like AddCardModal). */
function buildCard(ep: EndpointDescriptor, label: string): DashboardCard {
  const params: Record<string, unknown> = {
    from: { type: 'runtime', filter_key: 'date_from', default: '-7d' },
    to:   { type: 'runtime', filter_key: 'date_to',   default: '' },
  }
  for (const cp of ep.configurable_params ?? []) {
    params[cp.key] = { type: 'runtime', filter_key: cp.key, default: '' }
  }
  return {
    id: uuid(),
    x: 0, y: Infinity, w: ep.defaultW, h: ep.defaultH,
    tool_id: ep.default_tool,
    title: label,
    query: { endpoint: ep.endpoint, params },
    tool_config: { compact: true },
    refresh_ms: 30_000,
  } as unknown as DashboardCard
}

export default function DashboardView() {
  const { session } = useAuth()
  const { t } = useTranslation('dashboards')
  const tenantId   = session?.tenantId ?? ''
  const userId     = session?.userId ?? ''
  const adminToken = session?.accessToken ?? ''
  const role       = session?.role ?? ''

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
  const [editing,        setEditing]        = useState(false)
  const [saving,         setSaving]         = useState(false)

  const allowed = roleCatalog?.allowed ?? null

  // Load working cards: personal layout wins; else the starter template's cards. Reconciled.
  useEffect(() => {
    if (!template || roleCatalog === undefined) return
    const tf = template.global_filters ?? []
    setGlobalFilters(tf)
    const defaults: Record<string, unknown> = {}
    for (const f of tf) if (f.default !== null && f.default !== undefined) defaults[f.filter_key] = f.default
    setRuntimeFilters(defaults)
    loadPersonalLayout(tenantId, userId, adminToken).then(personal => {
      const base = (personal && personal.length > 0 ? personal : template.cards) as DashboardCard[]
      setCards(reconcileCards(base, allowed))
    })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [template, roleCatalog, tenantId, userId])

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

  // ── Edit actions ──────────────────────────────────────────────────────────
  const onLayoutChange = (next: Layout[]) => {
    if (!editing) return
    setCards(prev => prev.map(c => {
      const pos = next.find(l => l.i === c.id)
      return pos ? { ...c, x: pos.x, y: pos.y, w: pos.w, h: pos.h } : c
    }))
  }
  const removeCard = (id: string) => setCards(prev => prev.filter(c => c.id !== id))
  const addComponent = (id: string) => {
    const ep = ENDPOINT_CATALOG.find(e => e.id === id)
    if (!ep) return
    setCards(prev => [...prev, buildCard(ep, t(`catalog.${ep.id}.label`, { defaultValue: ep.label }))])
  }
  const persist = async (next: DashboardCard[]) => {
    setSaving(true)
    try { await savePersonalLayout(tenantId, userId, next, adminToken) } finally { setSaving(false) }
  }
  const finishEditing = async () => { await persist(cards); setEditing(false) }
  const resetToDefault = async () => {
    if (!template) return
    const base = reconcileCards(template.cards as DashboardCard[], allowed)
    setCards(base)
    await persist(base)
  }

  const addable = useMemo<EndpointDescriptor[]>(
    () => ENDPOINT_CATALOG.filter(e => !allowed || allowed.length === 0 || allowed.includes(e.id)),
    [allowed],
  )

  const layout: Layout[] = cards.map(c => ({ i: c.id, x: c.x, y: c.y, w: c.w, h: c.h, minW: 2, minH: 2 }))
  const loading = tmplLoading || tLoading || roleCatalog === undefined
  const empty   = !loading && (!template || cards.length === 0)

  return (
    <div ref={gridRef}>
      {/* Toolbar */}
      {!loading && !empty && (
        <div className="flex items-center justify-end gap-2 mb-2">
          {editing && addable.length > 0 && (
            <select
              value=""
              onChange={e => { if (e.target.value) { addComponent(e.target.value); e.target.value = '' } }}
              className="text-xs border border-border-strong rounded px-2 py-1.5 bg-white focus:outline-none focus:ring-1 focus:ring-primary/40"
            >
              <option value="">{t('home.addComponent')}</option>
              {addable.map(e => (
                <option key={e.id} value={e.id}>{t(`catalog.${e.id}.label`, { defaultValue: e.label })}</option>
              ))}
            </select>
          )}
          {editing && (
            <button onClick={resetToDefault} className="text-xs px-3 py-1.5 rounded border border-border text-muted hover:bg-surface-muted transition-colors">
              {t('home.reset')}
            </button>
          )}
          <button
            onClick={() => (editing ? finishEditing() : setEditing(true))}
            disabled={saving}
            className={`text-xs px-3 py-1.5 rounded border transition-colors disabled:opacity-50 ${
              editing ? 'bg-primary text-white border-primary' : 'border-border text-muted hover:bg-surface-muted'
            }`}
          >
            {editing ? (saving ? t('home.saving') : t('home.done')) : t('home.customize')}
          </button>
        </div>
      )}

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
          isDraggable={editing}
          isResizable={editing}
          onLayoutChange={onLayoutChange}
          compactType="vertical"
          margin={[12, 12]}
        >
          {cards.map(card => (
            <div
              key={card.id}
              className="bg-white rounded-lg border border-border shadow-sm overflow-hidden flex flex-col"
            >
              <div className="flex items-center justify-between px-3 py-1.5 border-b border-border bg-surface-muted flex-shrink-0">
                <span className="text-xs font-medium text-muted truncate">{cardTitle(card)}</span>
                {editing && (
                  <button
                    onMouseDown={e => e.stopPropagation()}
                    onClick={() => removeCard(card.id)}
                    className="text-muted-light hover:text-red text-sm leading-none ml-2 flex-shrink-0"
                    title={t('home.removeCard')}
                  >
                    &times;
                  </button>
                )}
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
