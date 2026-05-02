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
import { useAuth } from '@/auth/useAuth'
import { TimeseriesChart, type DisplayType } from '@/components/TimeseriesChart'
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
  DashboardCardType,
  DashboardTemplate,
  KpiCardConfig,
  PoolStatusCardConfig,
  TimeseriesCardConfig,
} from '@/types'

// ─── Helpers ──────────────────────────────────────────────────────────────────

function uuid(): string {
  return crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2)
}

const TENANT_ID = import.meta.env.VITE_TENANT_ID ?? 'tenant_demo'

// ─── Default card catalogue (used in "Add card" modal) ────────────────────────

interface CardPreset {
  type:   DashboardCardType
  label:  string
  icon:   string
  defaultConfig: TimeseriesCardConfig | KpiCardConfig | PoolStatusCardConfig
  defaultW: number
  defaultH: number
}

const CARD_PRESETS: CardPreset[] = [
  {
    type: 'timeseries_volume',
    label: 'Volume de Sessões',
    icon: '📊',
    defaultConfig: {
      url:         '/reports/timeseries/volume',
      title:       'Volume de Sessões',
      valueLabel:  'Sessões',
      displayType: 'bar',
      interval:    60,
    } as TimeseriesCardConfig,
    defaultW: 6, defaultH: 4,
  },
  {
    type: 'timeseries_handle_time',
    label: 'Tempo Médio de Atendimento',
    icon: '⏱️',
    defaultConfig: {
      url:         '/reports/timeseries/handle_time',
      title:       'Tempo Médio',
      valueLabel:  'Duração média',
      displayType: 'line',
      interval:    60,
    } as TimeseriesCardConfig,
    defaultW: 6, defaultH: 4,
  },
  {
    type: 'timeseries_score',
    label: 'Nota Média de Avaliação',
    icon: '⭐',
    defaultConfig: {
      url:         '/reports/timeseries/score',
      title:       'Nota Média',
      valueLabel:  'Nota',
      displayType: 'line',
      interval:    60,
    } as TimeseriesCardConfig,
    defaultW: 6, defaultH: 4,
  },
  {
    type: 'pool_status',
    label: 'Status dos Pools',
    icon: '🟢',
    defaultConfig: {
      title:  'Status dos Pools',
    } as PoolStatusCardConfig,
    defaultW: 4, defaultH: 3,
  },
]

// ─── Card renderer ────────────────────────────────────────────────────────────

function CardContent({ card, tenantId }: { card: DashboardCard; tenantId: string }) {
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
          Conectar ao SSE operacional
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

// ─── Display type metadata ────────────────────────────────────────────────────

const DISPLAY_OPTIONS: { value: DisplayType; label: string; icon: string; desc: string }[] = [
  { value: 'bar',   label: 'Barras',      icon: '▐',  desc: 'Comparação por período' },
  { value: 'line',  label: 'Linha',       icon: '╱',  desc: 'Tendência ao longo do tempo' },
  { value: 'area',  label: 'Área',        icon: '◣',  desc: 'Linha com área preenchida' },
  { value: 'pie',   label: 'Pizza',       icon: '◔',  desc: 'Proporção por categoria' },
  { value: 'table', label: 'Tabela',      icon: '☰',  desc: 'Dados tabulares ordenáveis' },
  { value: 'tile',  label: 'KPI / Tile',  icon: '◈',  desc: 'Número grande + tendência' },
]

// ─── Add Card Modal ───────────────────────────────────────────────────────────

function AddCardModal({
  onAdd,
  onClose,
}: {
  onAdd: (preset: CardPreset, displayType: DisplayType) => void
  onClose: () => void
}) {
  const [selectedPreset, setSelectedPreset] = useState<CardPreset | null>(null)
  const [displayType, setDisplayType]       = useState<DisplayType>('bar')

  function handleAdd() {
    if (!selectedPreset) return
    onAdd(selectedPreset, displayType)
    onClose()
  }

  // When preset changes, set a sensible default display type
  function selectPreset(p: CardPreset) {
    setSelectedPreset(p)
    const cfg = p.defaultConfig as TimeseriesCardConfig
    setDisplayType(cfg.displayType ?? 'bar')
  }

  const isTimeseries = selectedPreset?.type.startsWith('timeseries_') ?? false

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg p-6 flex flex-col gap-5">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold text-gray-800">Adicionar card</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">×</button>
        </div>

        {/* Step 1 — pick data source */}
        <div>
          <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
            1. Tipo de dado
          </p>
          <div className="grid grid-cols-2 gap-2">
            {CARD_PRESETS.map(preset => (
              <button
                key={preset.type}
                onClick={() => selectPreset(preset)}
                className={`flex items-center gap-3 px-3 py-2.5 rounded-lg border text-left transition-colors ${
                  selectedPreset?.type === preset.type
                    ? 'border-primary bg-blue-50 text-primary'
                    : 'border-gray-200 text-gray-700 hover:border-primary hover:bg-blue-50/50'
                }`}
              >
                <span className="text-xl">{preset.icon}</span>
                <span className="text-xs font-medium leading-snug">{preset.label}</span>
              </button>
            ))}
          </div>
        </div>

        {/* Step 2 — pick display type (only for timeseries cards) */}
        {isTimeseries && (
          <div>
            <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
              2. Visualização
            </p>
            <div className="grid grid-cols-3 gap-2">
              {DISPLAY_OPTIONS.map(opt => (
                <button
                  key={opt.value}
                  onClick={() => setDisplayType(opt.value)}
                  title={opt.desc}
                  className={`flex flex-col items-center gap-1 px-2 py-2 rounded-lg border text-center
                    transition-colors ${
                    displayType === opt.value
                      ? 'border-primary bg-blue-50 text-primary'
                      : 'border-gray-200 text-gray-600 hover:border-primary hover:bg-blue-50/50'
                  }`}
                >
                  <span className="text-base font-mono">{opt.icon}</span>
                  <span className="text-xs font-medium">{opt.label}</span>
                </button>
              ))}
            </div>
            <p className="text-xs text-gray-400 mt-1.5">
              {DISPLAY_OPTIONS.find(o => o.value === displayType)?.desc}
            </p>
          </div>
        )}

        {/* Action buttons */}
        <div className="flex gap-2 pt-1">
          <button
            onClick={handleAdd}
            disabled={!selectedPreset}
            className="flex-1 bg-primary text-white text-sm font-medium py-2 rounded hover:opacity-90
              disabled:opacity-40 transition-opacity"
          >
            Adicionar
          </button>
          <button
            onClick={onClose}
            className="flex-1 border border-gray-200 text-gray-600 text-sm py-2 rounded
              hover:bg-gray-50 transition-colors"
          >
            Cancelar
          </button>
        </div>
      </div>
    </div>
  )
}

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
      setError(String(e))
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-sm p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-semibold text-gray-800">Novo template</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">×</button>
        </div>
        <div className="flex flex-col gap-3">
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Nome *</label>
            <input
              value={name}
              onChange={e => setName(e.target.value)}
              className="w-full border border-gray-200 rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
              placeholder="Ex: Dashboard Operacional"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Descrição</label>
            <input
              value={description}
              onChange={e => setDesc(e.target.value)}
              className="w-full border border-gray-200 rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
              placeholder="Opcional"
            />
          </div>
          {error && (
            <p className="text-xs text-red-600 bg-red-50 border border-red-200 rounded px-2 py-1">{error}</p>
          )}
          <div className="flex gap-2 pt-1">
            <button
              onClick={handleCreate}
              disabled={!name.trim() || saving}
              className="flex-1 bg-primary text-white text-sm font-medium py-2 rounded hover:opacity-90 disabled:opacity-40 transition-opacity"
            >
              {saving ? 'Salvando…' : 'Criar'}
            </button>
            <button
              onClick={onClose}
              className="flex-1 border border-gray-200 text-gray-600 text-sm py-2 rounded hover:bg-gray-50 transition-colors"
            >
              Cancelar
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ─── Main page ────────────────────────────────────────────────────────────────

export default function DashboardsPage() {
  const { session, tenantId, currentUser } = useAuth()
  const isAdmin  = session?.role === 'admin' || session?.role === 'developer'
  const userId   = currentUser?.userId ?? 'anonymous'

  // Admin token — stored locally (never in JWT)
  const [adminToken, setAdminToken] = useState(() =>
    localStorage.getItem('plughub_admin_token') ?? ''
  )
  const saveAdminToken = (t: string) => {
    setAdminToken(t)
    localStorage.setItem('plughub_admin_token', t)
  }

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
  const [cards, setCards] = useState<DashboardCard[]>([])
  const [dirty,  setDirty]  = useState(false)
  const [editMode, setEditMode] = useState(false)

  // Load personal layout override (or template cards on first load)
  useEffect(() => {
    if (!template) {
      // Template was deleted or deselected — clear the grid
      setCards([])
      setDirty(false)
      setEditMode(false)
      return
    }
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

  // Modals
  const [showAddCard,     setShowAddCard]     = useState(false)
  const [showNewTemplate, setShowNewTemplate] = useState(false)
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

  function addCard(preset: CardPreset, displayType: DisplayType) {
    // Merge the chosen displayType into the config (only meaningful for timeseries cards)
    const config = preset.type.startsWith('timeseries_')
      ? { ...(preset.defaultConfig as TimeseriesCardConfig), displayType }
      : preset.defaultConfig

    const newCard: DashboardCard = {
      id:     uuid(),
      x:      0, y: Infinity,
      w:      preset.defaultW,
      h:      preset.defaultH,
      type:   preset.type,
      config,
    }
    setCards(prev => [...prev, newCard])
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
      // Admin: save to shared template
      const updated: DashboardTemplate = {
        ...template,
        cards,
        updated_at: new Date().toISOString(),
      }
      await saveTemplate(updated, adminToken)
      reloadTemplates()
    } else {
      // Regular user: save personal layout
      await savePersonalLayout(tenantId, userId, cards)
    }
    setSaving(false)
    setDirty(false)
  }

  // ── Delete template ─────────────────────────────────────────────────────────

  async function handleDeleteTemplate(id: string) {
    if (!window.confirm('Remover este template permanentemente?')) return
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
      <div className="flex items-center justify-between px-6 py-3 border-b border-gray-200 bg-white">
        <div className="flex items-center gap-3">
          <span className="text-sm font-semibold text-gray-800">
            {template?.name ?? 'Dashboard'}
          </span>
          {dirty && <span className="text-xs text-amber-500 font-medium">● não salvo</span>}
        </div>

        <div className="flex items-center gap-2">
          {isAdmin && (
            <button
              onClick={() => setEditMode(e => !e)}
              disabled={!activeTemplateId}
              title={!activeTemplateId ? 'Selecione um template primeiro' : ''}
              className={`text-xs px-3 py-1.5 rounded border transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
                editMode
                  ? 'bg-primary text-white border-primary'
                  : 'border-gray-200 text-gray-600 hover:bg-gray-50'
              }`}
            >
              {editMode ? '✓ Modo edição' : '✎ Editar'}
            </button>
          )}
          {editMode && (
            <button
              onClick={() => setShowAddCard(true)}
              className="text-xs px-3 py-1.5 rounded border border-gray-200 text-gray-600 hover:bg-gray-50 transition-colors"
            >
              + Card
            </button>
          )}
          {dirty && (
            <button
              onClick={handleSave}
              disabled={saving}
              className="text-xs px-3 py-1.5 rounded bg-primary text-white hover:opacity-90 disabled:opacity-50 transition-opacity"
            >
              {saving ? 'Salvando…' : 'Salvar'}
            </button>
          )}
          {/* Admin token input */}
          {isAdmin && (
            <input
              type="password"
              value={adminToken}
              onChange={e => saveAdminToken(e.target.value)}
              placeholder="Admin token"
              className="text-xs border border-gray-200 rounded px-2 py-1.5 w-32 focus:outline-none focus:ring-1 focus:ring-primary"
            />
          )}
        </div>
      </div>

      <div className="flex flex-1 overflow-hidden">

        {/* Sidebar — admin only */}
        {isAdmin && (
          <aside className="w-52 border-r border-gray-200 bg-gray-50 flex flex-col overflow-y-auto flex-shrink-0">
            <div className="px-4 py-3 border-b border-gray-200">
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Templates</p>
              <button
                onClick={() => setShowNewTemplate(true)}
                disabled={!adminToken}
                className="w-full text-xs py-1.5 rounded border border-dashed border-gray-300 text-gray-500 hover:border-primary hover:text-primary disabled:opacity-40 transition-colors"
                title={!adminToken ? 'Admin token necessário' : ''}
              >
                + Novo template
              </button>
            </div>

            {tmplLoading && (
              <div className="flex items-center justify-center p-4">
                <div className="w-4 h-4 border-2 border-primary border-t-transparent rounded-full animate-spin" />
              </div>
            )}

            <nav className="flex-1 py-2">
              {templates.map(t => (
                <div
                  key={t.template_id}
                  className={`group flex items-center justify-between px-4 py-2 cursor-pointer transition-colors ${
                    activeTemplateId === t.template_id
                      ? 'bg-primary/10 text-primary font-medium'
                      : 'text-gray-700 hover:bg-gray-100'
                  }`}
                  onClick={() => setActiveTemplateId(t.template_id)}
                >
                  <span className="text-xs truncate">{t.name}</span>
                  <button
                    onClick={e => { e.stopPropagation(); handleDeleteTemplate(t.template_id) }}
                    className="opacity-0 group-hover:opacity-100 text-gray-400 hover:text-red-500 transition-all text-sm leading-none ml-1"
                    title="Remover template"
                  >
                    ×
                  </button>
                </div>
              ))}
              {!tmplLoading && templates.length === 0 && (
                <p className="text-xs text-gray-400 px-4 py-3">Nenhum template ainda</p>
              )}
            </nav>
          </aside>
        )}

        {/* Grid area */}
        <main className="flex-1 overflow-auto p-4 bg-gray-50" ref={gridRef}>
          {!activeTemplateId && !tmplLoading && (
            <div className="flex flex-col items-center justify-center h-64 text-gray-400">
              <span className="text-4xl mb-3">📊</span>
              <p className="text-sm">Nenhum template selecionado</p>
              {isAdmin && (
                <button
                  onClick={() => setShowNewTemplate(true)}
                  className="mt-3 text-xs text-primary underline"
                >
                  Criar o primeiro template
                </button>
              )}
            </div>
          )}

          {activeTemplateId && cards.length === 0 && (
            <div className="flex flex-col items-center justify-center h-64 text-gray-400">
              <span className="text-4xl mb-3">🗂️</span>
              <p className="text-sm">Dashboard vazio</p>
              {isAdmin && editMode && (
                <button
                  onClick={() => setShowAddCard(true)}
                  className="mt-3 text-xs text-primary underline"
                >
                  Adicionar cards
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
                  className="bg-white rounded-lg border border-gray-200 shadow-sm overflow-hidden flex flex-col"
                >
                  {/* Card header (edit mode) */}
                  {editMode && (
                    <div className="flex items-center justify-between px-3 py-1.5 border-b border-gray-100 bg-gray-50 flex-shrink-0">
                      <span className="text-xs text-gray-500 truncate">
                        {(card.config as TimeseriesCardConfig).title ?? card.type}
                      </span>
                      <button
                        onClick={() => removeCard(card.id)}
                        className="text-gray-400 hover:text-red-500 text-sm leading-none ml-2 flex-shrink-0"
                        title="Remover card"
                      >
                        ×
                      </button>
                    </div>
                  )}
                  {/* Card content */}
                  <div className="flex-1 p-2 min-h-0">
                    <CardContent card={card} tenantId={tenantId} />
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
          onAdd={(preset, dt) => addCard(preset, dt)}
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
    </div>
  )
}
