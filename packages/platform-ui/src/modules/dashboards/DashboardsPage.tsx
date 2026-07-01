/**
 * DashboardsPage.tsx
 *
 * Unified dashboard with drag-and-drop cards and template management.
 *
 * Layout:
 *   ┌──────────────────────────────────────────────────────────┐
 *   │ TopBar: template name + mode toggle (view/edit) + save  │
 *   ├────────────┬─────────────────────────────────────────────┤
 *   │  Sidebar   │  react-grid-layout card grid               │
 *   │ (admin)    │                                            │
 *   │  Templates │                                            │
 *   │  + New     │                                            │
 *   └────────────┴─────────────────────────────────────────────┘
 *
 * Roles:
 *   admin/developer → full edit: create/delete templates, add/remove/configure cards
 *   operator/supervisor/business → view only: can drag to rearrange (personal layout)
 */
import React, { useCallback, useEffect, useState } from 'react'
import GridLayout, { Layout } from 'react-grid-layout'
import 'react-grid-layout/css/styles.css'
import 'react-resizable/css/styles.css'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import { AddCardModal } from '@/dashboard/AddCardModal'
import { RoleDefaultsModal } from '@/modules/dashboards/RoleDefaultsModal'
import { CardRenderer } from '@/dashboard/CardRenderer'
import { resolveCardTitle } from '@/dashboard/catalog'
import { FilterBar } from '@/dashboard/FilterBar'
import { FilterConfigPanel } from '@/dashboard/FilterConfigPanel'
import {
  deleteTemplate,
  loadPersonalLayout,
  savePersonalLayout,
  saveTemplate,
  useDefaultTemplateId,
  useTemplate,
  useTemplates,
} from '@/api/dashboard-hooks'
import type {
  DashboardCard,
  DashboardTemplate,
  GlobalFilter,
  NewDashboardCard,
} from '@/types'

// ─── Helpers ──────────────────────────────────────────────────────────────────

function uuid(): string {
  return crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2)
}

// AddCardModal is imported from @/dashboard/AddCardModal (new 3-step flow)

// ─── New Template Modal ───────────────────────────────────────────────────────

function NewTemplateModal({
  tenantId,
  adminToken,
  onCreated,
  onClose,
}: {
  tenantId:   string
  adminToken: string
  onCreated:  (t: DashboardTemplate) => void
  onClose:    () => void
}) {
  const { t } = useTranslation('dashboards')
  const [name, setName]         = useState('')
  const [description, setDesc]  = useState('')
  const [saving, setSaving]     = useState(false)
  const [error, setError]       = useState<string | null>(null)

  async function handleCreate() {
    if (!name.trim()) return
    if (!adminToken) { setError('Admin token obrigatório. Preencha o campo no topo da página.'); return }
    setSaving(true)
    setError(null)
    const template: DashboardTemplate = {
      template_id:  uuid(),
      tenant_id:    tenantId,
      name:         name.trim(),
      description:  description.trim() || undefined,
      cards:        [],
      created_by:   'admin',
      created_at:   new Date().toISOString(),
    }
    try {
      await saveTemplate(template, adminToken)
      onCreated(template)
    } catch (e) {
      const msg = String(e)
      setError(msg)
      // On 401, clear the stored token so the user can re-enter a valid one
      if (msg.includes('401')) {
        localStorage.removeItem('plughub_admin_token')
      }
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-sm p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-semibold text-dark">{t('newTemplate')}</h2>
          <button onClick={onClose} className="text-muted-light hover:text-muted text-xl leading-none">×</button>
        </div>
        <div className="flex flex-col gap-3">
          <div>
            <label className="block text-xs font-medium text-muted mb-1">{t('templateName')} *</label>
            <input
              value={name}
              onChange={e => setName(e.target.value)}
              className="w-full border border-border rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
              placeholder="Ex: Dashboard Operacional"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-muted mb-1">{t('description')}</label>
            <input
              value={description}
              onChange={e => setDesc(e.target.value)}
              className="w-full border border-border rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
              placeholder="Opcional"
            />
          </div>
          {error && (
            <p className="text-xs text-red-text bg-red-light border border-red/30 rounded px-2 py-1">{error}</p>
          )}
          <div className="flex gap-2 pt-1">
            <button
              onClick={handleCreate}
              disabled={!name.trim() || saving}
              className="flex-1 bg-primary text-white text-sm font-medium py-2 rounded hover:opacity-90 disabled:opacity-40 transition-opacity"
            >
              {saving ? t('saving') : t('create')}
            </button>
            <button
              onClick={onClose}
              className="flex-1 border border-border text-muted text-sm py-2 rounded hover:bg-surface-muted transition-colors"
            >
              {t('card.cancel')}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ─── Main page ────────────────────────────────────────────────────────────────

export default function DashboardsPage() {
  const { t } = useTranslation('dashboards')
  const { session, tenantId, currentUser } = useAuth()
  const isAdmin  = session?.role === 'admin' || session?.role === 'developer'
  const userId   = currentUser?.userId ?? 'anonymous'

  // G-PROBE platform-wide: as escritas de template usam o Bearer do operador + ABAC
  // `config.plataforma` (via token-store) — sem caixa de admin-token. `adminToken`
  // (= access token) é mantido só p/ os guards/props existentes (hooks já usam o Bearer).
  const adminToken = session?.accessToken ?? ''

  // Template list (admin sidebar)
  const { templates, loading: tmplLoading, reload: reloadTemplates } = useTemplates(tenantId, adminToken)

  // Resolve default template from module_config
  const defaultTemplateId = useDefaultTemplateId(session?.moduleConfig)

  // Active template ID
  const [activeTemplateId, setActiveTemplateId] = useState<string | null>(null)
  useEffect(() => {
    if (!activeTemplateId && defaultTemplateId) setActiveTemplateId(defaultTemplateId)
    else if (!activeTemplateId && templates.length > 0) setActiveTemplateId(templates[0].template_id)
  }, [defaultTemplateId, templates, activeTemplateId])

  const { template } = useTemplate(activeTemplateId, adminToken, tenantId)

  // Cards state (current working copy)
  const [cards, setCards] = useState<(DashboardCard | NewDashboardCard)[]>([])
  const [dirty,  setDirty]  = useState(false)
  const [editMode, setEditMode] = useState(false)

  // Global filters declared on the template (admin-configurable)
  const [globalFilters, setGlobalFilters] = useState<GlobalFilter[]>([])

  // Runtime filter values — user-controlled via FilterBar
  const [runtimeFilters, setRuntimeFilters] = useState<Record<string, unknown>>({})

  // Load personal layout override (or template cards on first load)
  useEffect(() => {
    if (!template) {
      // Template was deleted or deselected — clear the grid
      setCards([])
      setGlobalFilters([])
      setRuntimeFilters({})
      setDirty(false)
      setEditMode(false)
      return
    }
    // Initialise global filters from template
    const templateFilters = template.global_filters ?? []
    setGlobalFilters(templateFilters)
    // Seed runtime filters with each filter's default value
    const defaults: Record<string, unknown> = {}
    for (const f of templateFilters) {
      if (f.default !== null && f.default !== undefined) {
        defaults[f.filter_key] = f.default
      }
    }
    setRuntimeFilters(defaults)

    loadPersonalLayout(tenantId, userId).then(personal => {
      // Only apply personal layout if it matches the same set of card IDs
      if (personal && personal.length === template.cards.length) {
        const personalIds = new Set(personal.map(c => c.id))
        const allMatch = template.cards.every(c => personalIds.has(c.id))
        if (allMatch) { setCards(personal); return }
      }
      setCards(template.cards)
    })
    setDirty(false)
    setEditMode(false)
  }, [template, tenantId, userId])

  // ── Filter helpers ──────────────────────────────────────────────────────────

  function setRuntimeFilter(key: string, value: unknown) {
    setRuntimeFilters(prev => ({ ...prev, [key]: value }))
  }

  function resetRuntimeFilters() {
    const defaults: Record<string, unknown> = {}
    for (const f of globalFilters) {
      if (f.default !== null && f.default !== undefined) {
        defaults[f.filter_key] = f.default
      }
    }
    setRuntimeFilters(defaults)
  }

  // Admin: update global_filters on the working template copy
  function updateGlobalFilters(filters: GlobalFilter[]) {
    setGlobalFilters(filters)
    setDirty(true)
  }

  // Modals
  const [showAddCard,     setShowAddCard]     = useState(false)
  const [showNewTemplate, setShowNewTemplate] = useState(false)
  const [showRoleDefaults, setShowRoleDefaults] = useState(false)
  const [saving, setSaving] = useState(false)

  // ── Grid layout sync ────────────────────────────────────────────────────────

  const layout: Layout[] = cards.map(c => ({
    i: c.id,
    x: c.x, y: c.y,
    w: c.w, h: c.h,
    minW: 2, minH: 2,
  }))

  function onLayoutChange(newLayout: Layout[]) {
    setCards(prev => prev.map(c => {
      const pos = newLayout.find(l => l.i === c.id)
      return pos ? { ...c, x: pos.x, y: pos.y, w: pos.w, h: pos.h } : c
    }))
    setDirty(true)
  }

  // ── Card actions ────────────────────────────────────────────────────────────

  function addCard(card: NewDashboardCard) {
    setCards(prev => [...prev, card])
    setDirty(true)
  }

  function removeCard(id: string) {
    setCards(prev => prev.filter(c => c.id !== id))
    setDirty(true)
  }

  // ── Save ────────────────────────────────────────────────────────────────────

  async function handleSave() {
    if (!activeTemplateId || !template) return
    setSaving(true)
    if (isAdmin && adminToken) {
      // Admin: save cards + global_filters to shared template
      const updated: DashboardTemplate = {
        ...template,
        cards,
        global_filters: globalFilters,
        updated_at: new Date().toISOString(),
      }
      await saveTemplate(updated, adminToken)
      reloadTemplates()
    } else {
      // Regular user: save personal layout only
      await savePersonalLayout(tenantId, userId, cards)
    }
    setSaving(false)
    setDirty(false)
  }

  // ── Delete template ─────────────────────────────────────────────────────────

  async function handleDeleteTemplate(id: string) {
    if (!window.confirm(t('deleteTemplateConfirm'))) return
    await deleteTemplate(id, adminToken, tenantId)
    reloadTemplates()
    if (activeTemplateId === id) setActiveTemplateId(null)
  }

  // ── Grid width ──────────────────────────────────────────────────────────────

  const COLS = 12
  const [gridWidth, setGridWidth] = useState(900)
  const gridRef = useCallback((node: HTMLDivElement | null) => {
    if (!node) return
    const obs = new ResizeObserver(([entry]) => setGridWidth(entry.contentRect.width))
    obs.observe(node)
    return () => obs.disconnect()
  }, [])

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <div className="h-full flex flex-col">

      {/* Top bar */}
      <div className="flex items-center justify-between px-6 py-3 border-b border-border bg-white">
        <div className="flex items-center gap-3">
          {template?.name && (
            <span className="text-sm font-semibold text-dark">{template.name}</span>
          )}
          {dirty && <span className="text-xs text-warning font-medium">●</span>}
        </div>

        <div className="flex items-center gap-2">
          {isAdmin && (
            <button
              onClick={() => setEditMode(e => !e)}
              disabled={!activeTemplateId}
              title={!activeTemplateId ? t('selectFirstTooltip') : ''}
              className={`text-xs px-3 py-1.5 rounded border transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
                editMode
                  ? 'bg-primary text-white border-primary'
                  : 'border-border text-muted hover:bg-surface-muted'
              }`}
            >
              {editMode ? t('exitEdit') : t('editMode')}
            </button>
          )}
          {isAdmin && (
            <button
              onClick={() => setShowRoleDefaults(true)}
              className="text-xs px-3 py-1.5 rounded border border-border text-muted hover:bg-surface-muted transition-colors"
            >
              {t('roleDefaults.button')}
            </button>
          )}
          {editMode && (
            <button
              onClick={() => setShowAddCard(true)}
              className="text-xs px-3 py-1.5 rounded border border-border text-muted hover:bg-surface-muted transition-colors"
            >
              {t('addCard')}
            </button>
          )}
          {dirty && (
            <button
              onClick={handleSave}
              disabled={saving}
              className="text-xs px-3 py-1.5 rounded bg-primary text-white hover:opacity-90 disabled:opacity-50 transition-opacity"
            >
              {saving ? t('saving') : t('saveTemplate')}
            </button>
          )}
        </div>
      </div>

      {/* Filter bar — shown when template has global_filters */}
      <FilterBar
        filters={globalFilters}
        values={runtimeFilters}
        onChange={setRuntimeFilter}
        onReset={resetRuntimeFilters}
      />

      <div className="flex flex-1 overflow-hidden">

        {/* Sidebar — admin only */}
        {isAdmin && (
          <aside className="w-52 border-r border-border bg-surface-muted flex flex-col overflow-y-auto flex-shrink-0">
            <div className="px-4 py-3 border-b border-border">
              <p className="text-xs font-semibold text-muted uppercase tracking-wide mb-2">{t('templates')}</p>
              <button
                onClick={() => setShowNewTemplate(true)}
                disabled={!adminToken}
                className="w-full text-xs py-1.5 rounded border border-dashed border-border-strong text-muted hover:border-primary hover:text-primary disabled:opacity-40 transition-colors"
                title={!adminToken ? t('adminTokenRequired') : ''}
              >
                {t('newTemplate')}
              </button>
            </div>

            {tmplLoading && (
              <div className="flex items-center justify-center p-4">
                <div className="w-4 h-4 border-2 border-primary border-t-transparent rounded-full animate-spin" />
              </div>
            )}

            <nav className="flex-1 py-2">
              {templates.map(tmpl => (
                <div
                  key={tmpl.template_id}
                  className={`group flex items-center justify-between px-4 py-2 cursor-pointer transition-colors ${
                    activeTemplateId === tmpl.template_id
                      ? 'bg-primary/10 text-primary font-medium'
                      : 'text-dark hover:bg-surface-alt'
                  }`}
                  onClick={() => setActiveTemplateId(tmpl.template_id)}
                >
                  <span className="text-xs truncate">{tmpl.name}</span>
                  <button
                    onClick={e => { e.stopPropagation(); handleDeleteTemplate(tmpl.template_id) }}
                    className="opacity-0 group-hover:opacity-100 text-muted-light hover:text-red transition-all text-sm leading-none ml-1"
                    title={t('deleteTemplate')}
                  >
                    ×
                  </button>
                </div>
              ))}
              {!tmplLoading && templates.length === 0 && (
                <p className="text-xs text-muted-light px-4 py-3">{t('noTemplates')}</p>
              )}
            </nav>

            {/* Filter config — admin, edit mode only */}
            {editMode && activeTemplateId && (
              <FilterConfigPanel
                filters={globalFilters}
                onChange={updateGlobalFilters}
              />
            )}
          </aside>
        )}

        {/* Grid area */}
        <main className="flex-1 overflow-auto p-4 bg-surface-muted" ref={gridRef}>
          {!activeTemplateId && !tmplLoading && (
            <div className="flex flex-col items-center justify-center h-64 text-muted-light">
              <span className="text-4xl mb-3">📊</span>
              <p className="text-sm">{t('noTemplate')}</p>
              {isAdmin && (
                <button
                  onClick={() => setShowNewTemplate(true)}
                  className="mt-3 text-xs text-primary underline"
                >
                  {t('createFirst')}
                </button>
              )}
            </div>
          )}

          {activeTemplateId && cards.length === 0 && (
            <div className="flex flex-col items-center justify-center h-64 text-muted-light">
              <span className="text-4xl mb-3">🗂️</span>
              <p className="text-sm">{t('emptyDashboard')}</p>
              {isAdmin && editMode && (
                <button
                  onClick={() => setShowAddCard(true)}
                  className="mt-3 text-xs text-primary underline"
                >
                  {t('addCards')}
                </button>
              )}
            </div>
          )}

          {cards.length > 0 && (
            <GridLayout
              layout={layout}
              cols={COLS}
              rowHeight={60}
              width={gridWidth}
              isDraggable={editMode || !isAdmin}
              isResizable={editMode}
              onLayoutChange={onLayoutChange}
              compactType="vertical"
              margin={[12, 12]}
            >
              {cards.map(card => (
                <div
                  key={card.id}
                  className="bg-white rounded-lg border border-border shadow-sm overflow-hidden flex flex-col"
                >
                  {/* Card header — always show title; delete button only in edit mode */}
                  <div className="flex items-center justify-between px-3 py-1.5 border-b border-border bg-surface-muted flex-shrink-0">
                    <span className="text-xs font-medium text-muted truncate">
                      {resolveCardTitle(card, t)}
                    </span>
                    {editMode && (
                      <button
                        onMouseDown={e => e.stopPropagation()}
                        onClick={() => removeCard(card.id)}
                        className="text-muted-light hover:text-red text-sm leading-none ml-2 flex-shrink-0"
                        title={t('deleteCardTitle')}
                      >
                        ×
                      </button>
                    )}
                  </div>
                  {/* Card content */}
                  <div className="flex-1 p-2 min-h-0">
                    <CardRenderer
                      card={card}
                      tenantId={tenantId}
                      runtimeFilters={runtimeFilters}
                    />
                  </div>
                </div>
              ))}
            </GridLayout>
          )}
        </main>
      </div>

      {/* Modals */}
      {showAddCard && (
        <AddCardModal
          tenantId={tenantId}
          onAdd={addCard}
          onClose={() => setShowAddCard(false)}
        />
      )}
      {showNewTemplate && (
        <NewTemplateModal
          tenantId={tenantId}
          adminToken={adminToken}
          onCreated={t => {
            reloadTemplates()
            setActiveTemplateId(t.template_id)
            setShowNewTemplate(false)
          }}
          onClose={() => setShowNewTemplate(false)}
        />
      )}
      {showRoleDefaults && (
        <RoleDefaultsModal
          tenantId={tenantId}
          adminToken={adminToken}
          templates={templates}
          onClose={() => setShowRoleDefaults(false)}
        />
      )}
    </div>
  )
}
