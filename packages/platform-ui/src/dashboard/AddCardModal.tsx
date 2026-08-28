/**
 * AddCardModal.tsx
 * 3-step modal for adding a new-format card to a dashboard.
 *
 *  Step 1 — Choose metric: which data to display (endpoint catalog)
 *  Step 2 — Choose visualization: which Display Tool to use
 *  Step 3 — Configure: optional fixed params + card title
 *
 * On confirm, calls onAdd(card: NewDashboardCard).
 * tenant_id is always injected as a fixed param.
 */
import React, { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { titleForNewCard, ENDPOINT_CATALOG, type EndpointDescriptor } from './catalog'
import { listDisplayTools, getDisplayTool } from './tools/registry'
import type { NewDashboardCard, QueryParam } from './tools/types'

// ─── Helpers ──────────────────────────────────────────────────────────────────

function uuid(): string {
  return crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2)
}

// ─── Props ────────────────────────────────────────────────────────────────────

interface AddCardModalProps {
  tenantId: string
  onAdd:    (card: NewDashboardCard) => void
  onClose:  () => void
}

// ─── Step indicator ───────────────────────────────────────────────────────────

function StepDots({ current, total }: { current: number; total: number }) {
  return (
    <div className="flex items-center gap-1.5">
      {Array.from({ length: total }, (_, i) => (
        <span
          key={i}
          className={`inline-block rounded-full transition-all ${
            i === current
              ? 'w-4 h-1.5 bg-primary'
              : i < current
                ? 'w-1.5 h-1.5 bg-primary/40'
                : 'w-1.5 h-1.5 bg-border'
          }`}
        />
      ))}
    </div>
  )
}

// ─── Step 1 — Choose metric ───────────────────────────────────────────────────

function StepMetric({
  selected,
  onSelect,
}: {
  selected: EndpointDescriptor | null
  onSelect: (e: EndpointDescriptor) => void
}) {
  const { t } = useTranslation('dashboards')

  return (
    <div className="flex flex-col gap-3">
      <div>
        <p className="text-xs font-semibold text-muted uppercase tracking-wide mb-0.5">
          {t('modal.metric.heading')}
        </p>
        <p className="text-xs text-muted-light">{t('modal.metric.subtitle')}</p>
      </div>

      <div className="grid grid-cols-2 gap-2 max-h-72 overflow-y-auto pr-1">
        {ENDPOINT_CATALOG.map(ep => (
          <button
            key={ep.id}
            onClick={() => onSelect(ep)}
            className={`flex items-start gap-2.5 px-3 py-2.5 rounded-lg border text-left transition-colors ${
              selected?.id === ep.id
                ? 'border-primary bg-primary-light text-primary'
                : 'border-border text-dark hover:border-primary hover:bg-primary-light/40'
            }`}
          >
            <span className="text-lg flex-shrink-0 mt-0.5">{ep.icon}</span>
            <div className="min-w-0">
              <p className="text-xs font-medium leading-snug truncate">
                {t(`catalog.${ep.id}.label`, { defaultValue: ep.label })}
              </p>
              <p className="text-xs text-muted-light leading-snug mt-0.5 line-clamp-2">
                {t(`catalog.${ep.id}.description`, { defaultValue: ep.description })}
              </p>
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}

// ─── Step 2 — Choose visualization ───────────────────────────────────────────

function StepVisualization({
  endpoint,
  selected,
  onSelect,
}: {
  endpoint:  EndpointDescriptor
  selected:  string
  onSelect:  (toolId: string) => void
}) {
  const { t } = useTranslation('dashboards')

  const tools = endpoint.compatible_tools
    .map(id => getDisplayTool(id))
    .filter(Boolean)

  return (
    <div className="flex flex-col gap-3">
      <div>
        <p className="text-xs font-semibold text-muted uppercase tracking-wide mb-0.5">
          {t('modal.visualization.heading')}
        </p>
        <p className="text-xs text-muted-light">
          {t('modal.visualization.subtitle')}{' '}
          <span className="font-medium text-muted">
            {t(`catalog.${endpoint.id}.label`, { defaultValue: endpoint.label })}
          </span>.
        </p>
      </div>

      <div className="flex flex-col gap-2">
        {tools.map(tool => {
          if (!tool) return null
          return (
            <button
              key={tool.id}
              onClick={() => onSelect(tool.id)}
              className={`flex items-center gap-3 px-4 py-3 rounded-lg border text-left transition-colors ${
                selected === tool.id
                  ? 'border-primary bg-primary-light'
                  : 'border-border hover:border-primary hover:bg-primary-light/40'
              }`}
            >
              <span className={`text-2xl font-mono ${selected === tool.id ? 'text-primary' : 'text-muted-light'}`}>
                {tool.icon}
              </span>
              <div>
                <p className={`text-sm font-medium ${selected === tool.id ? 'text-primary' : 'text-dark'}`}>
                  {tool.label}
                </p>
                <p className="text-xs text-muted-light">{tool.description}</p>
              </div>
              {selected === tool.id && (
                <span className="ml-auto text-primary text-sm">✓</span>
              )}
            </button>
          )
        })}
      </div>
    </div>
  )
}

// ─── Step 3 — Configure ───────────────────────────────────────────────────────

function StepConfigure({
  endpoint,
  tenantId,
  title,
  onTitleChange,
  fixedParams,
  onParamChange,
}: {
  endpoint:      EndpointDescriptor
  tenantId:      string
  title:         string
  onTitleChange: (v: string) => void
  fixedParams:   Record<string, string>
  onParamChange: (key: string, value: string) => void
}) {
  const { t } = useTranslation('dashboards')
  const hasParams = (endpoint.configurable_params?.length ?? 0) > 0
  const [paramOptions, setParamOptions] = useState<Record<string, string[]>>({})

  // Fetch options for params with options_from
  useEffect(() => {
    const params = endpoint.configurable_params ?? []
    params.forEach(param => {
      if (!param.options_from) return
      const url = `${param.options_from}?tenant_id=${encodeURIComponent(tenantId)}`
      fetch(url)
        .then(r => r.ok ? r.json() : Promise.reject())
        .then((body: { data?: Array<{ category: string }> }) => {
          const opts = body.data?.map(d => d.category) ?? []
          setParamOptions(prev => ({ ...prev, [param.key]: opts }))
        })
        .catch(() => {})
    })
  }, [endpoint, tenantId])

  return (
    <div className="flex flex-col gap-4">
      <div>
        <p className="text-xs font-semibold text-muted uppercase tracking-wide mb-0.5">
          {t('modal.configure.heading')}
        </p>
        <p className="text-xs text-muted-light">
          {t('modal.configure.subtitle')}
        </p>
      </div>

      {/* Title */}
      <div>
        <label className="block text-xs font-medium text-muted mb-1">
          {t('modal.configure.cardTitle')}
        </label>
        <input
          type="text"
          value={title}
          onChange={e => onTitleChange(e.target.value)}
          className="w-full border border-border rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
          placeholder={t(`catalog.${endpoint.id}.label`, { defaultValue: endpoint.label })}
        />
      </div>

      {/* Configurable params */}
      {hasParams && (
        <div className="flex flex-col gap-3">
          <p className="text-xs font-medium text-muted">
            {t('modal.configure.fixedFilters')}{' '}
            <span className="font-normal text-muted-light">
              {t('modal.configure.fixedFiltersHint')}
            </span>
          </p>
          {endpoint.configurable_params!.map(param => (
            <div key={param.key}>
              <label className="block text-xs text-muted mb-1">
                {t(`catalog.params.${param.key}.label`, { defaultValue: param.label })}
                {!param.optional && <span className="text-red ml-0.5">*</span>}
              </label>
              {param.options_from && paramOptions[param.key] ? (
                <select
                  value={fixedParams[param.key] ?? ''}
                  onChange={e => onParamChange(param.key, e.target.value)}
                  className="w-full border border-border rounded px-3 py-1.5 text-sm text-dark focus:outline-none focus:ring-1 focus:ring-primary bg-white"
                >
                  <option value="">{t('modal.configure.selectPlaceholder')}</option>
                  {paramOptions[param.key].map(opt => (
                    <option key={opt} value={opt}>{opt}</option>
                  ))}
                </select>
              ) : (
                <input
                  type="text"
                  value={fixedParams[param.key] ?? ''}
                  onChange={e => onParamChange(param.key, e.target.value)}
                  placeholder={
                    param.options_from
                      ? t('modal.configure.loadingOptions')
                      : t(`catalog.params.${param.key}.placeholder`, { defaultValue: param.placeholder })
                  }
                  className="w-full border border-border rounded px-3 py-1.5 text-sm text-dark focus:outline-none focus:ring-1 focus:ring-primary placeholder:text-border-strong"
                />
              )}
            </div>
          ))}
        </div>
      )}

      {/* Info: always-runtime params */}
      <div className="rounded-lg bg-surface-muted border border-border px-3 py-2.5 text-xs text-muted-light">
        <span className="font-medium text-muted">{t('modal.configure.periodNoteLabel')}</span>
        {' '}{t('modal.configure.periodNoteBody')}
      </div>
    </div>
  )
}

// ─── Main modal ───────────────────────────────────────────────────────────────

export function AddCardModal({ tenantId, onAdd, onClose }: AddCardModalProps) {
  const { t } = useTranslation('dashboards')
  const [step, setStep] = useState<1 | 2 | 3>(1)

  const [selectedEndpoint, setSelectedEndpoint] = useState<EndpointDescriptor | null>(null)
  const [selectedToolId,   setSelectedToolId]   = useState<string>('')
  const [title,            setTitle]            = useState<string>('')
  const [fixedParams,      setFixedParams]      = useState<Record<string, string>>({})

  // ── Step 1 → 2 ────────────────────────────────────────────────────────────

  function confirmMetric(ep: EndpointDescriptor) {
    setSelectedEndpoint(ep)
    setSelectedToolId(ep.default_tool)
    setTitle(t(`catalog.${ep.id}.label`, { defaultValue: ep.label }))
    setFixedParams({})
    setStep(2)
  }

  // ── Step 2 → 3 ────────────────────────────────────────────────────────────

  function confirmTool() {
    setStep(3)
  }

  // ── Back navigation ────────────────────────────────────────────────────────

  function goBack() {
    if (step === 2) setStep(1)
    else if (step === 3) setStep(2)
  }

  // ── Param helper ──────────────────────────────────────────────────────────

  function setParam(key: string, value: string) {
    setFixedParams(prev => ({ ...prev, [key]: value }))
  }

  // ── Confirm card ──────────────────────────────────────────────────────────

  function confirmCard() {
    if (!selectedEndpoint || !selectedToolId) return

    const tool = getDisplayTool(selectedToolId)
    if (!tool) return

    // Build params: tenant_id always fixed; configurable params fixed if filled
    const params: Record<string, QueryParam> = {
      tenant_id: { type: 'fixed', value: tenantId },
    }

    // Date range: always runtime — wired to filter bar in Part 3
    params.from = { type: 'runtime', filter_key: 'date_from', default: '-7d' }
    params.to   = { type: 'runtime', filter_key: 'date_to',   default: '' }

    // Configurable params: fixed if user filled, runtime otherwise
    for (const cp of selectedEndpoint.configurable_params ?? []) {
      const val = fixedParams[cp.key]?.trim()
      if (val) {
        params[cp.key] = { type: 'fixed', value: val }
      } else {
        // wire to global filter with matching key (e.g. "pool_id")
        params[cp.key] = { type: 'runtime', filter_key: cp.key, default: '' }
      }
    }

    const card: NewDashboardCard = {
      id:          uuid(),
      x:           0,
      y:           Infinity,
      w:           selectedEndpoint.defaultW,
      h:           selectedEndpoint.defaultH,
      tool_id:     selectedToolId,
      // Vazio = derivar do catalogo no render (`resolveCardTitle`). Assar aqui
      // congelaria a lingua da criacao — e a chave crua, quando o namespace
      // ainda nao carregou. Ver `titleForNewCard`.
      title:       titleForNewCard(title),
      query: {
        endpoint: selectedEndpoint.endpoint,
        params,
      },
      tool_config: { compact: true },
      refresh_ms:  30_000,
    }

    onAdd(card)
    onClose()
  }

  // ── Step labels ───────────────────────────────────────────────────────────

  const STEP_LABELS: Record<number, string> = {
    1: t('modal.steps.1'),
    2: t('modal.steps.2'),
    3: t('modal.steps.3'),
  }

  const canAdvance1 = !!selectedEndpoint
  const canAdvance2 = !!selectedToolId
  const canAdvance3 = !!title.trim() || !!selectedEndpoint

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg flex flex-col overflow-hidden max-h-[90vh]">

        {/* Header */}
        <div className="flex items-center justify-between px-6 pt-5 pb-4 border-b border-border flex-shrink-0">
          <div className="flex flex-col gap-1">
            <h2 className="text-base font-semibold text-dark">{t('modal.title')}</h2>
            <div className="flex items-center gap-2">
              <StepDots current={step - 1} total={3} />
              <span className="text-xs text-muted-light">{STEP_LABELS[step]}</span>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-muted-light hover:text-muted text-xl leading-none"
          >
            ×
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-6 py-5">
          {step === 1 && (
            <StepMetric
              selected={selectedEndpoint}
              onSelect={ep => { setSelectedEndpoint(ep); setSelectedToolId(ep.default_tool); setTitle(t(`catalog.${ep.id}.label`, { defaultValue: ep.label })) }}
            />
          )}
          {step === 2 && selectedEndpoint && (
            <StepVisualization
              endpoint={selectedEndpoint}
              selected={selectedToolId}
              onSelect={setSelectedToolId}
            />
          )}
          {step === 3 && selectedEndpoint && (
            <StepConfigure
              endpoint={selectedEndpoint}
              tenantId={tenantId}
              title={title}
              onTitleChange={setTitle}
              fixedParams={fixedParams}
              onParamChange={setParam}
            />
          )}
        </div>

        {/* Footer */}
        <div className="flex gap-2 px-6 py-4 border-t border-border flex-shrink-0">
          {step > 1 && (
            <button
              onClick={goBack}
              className="px-4 py-2 border border-border text-muted text-sm rounded hover:bg-surface-muted transition-colors"
            >
              {t('modal.back')}
            </button>
          )}

          <div className="flex-1" />

          <button
            onClick={onClose}
            className="px-4 py-2 border border-border text-muted text-sm rounded hover:bg-surface-muted transition-colors"
          >
            {t('modal.cancel')}
          </button>

          {step === 1 && (
            <button
              onClick={() => selectedEndpoint && confirmMetric(selectedEndpoint)}
              disabled={!canAdvance1}
              className="px-4 py-2 bg-primary text-white text-sm font-medium rounded hover:opacity-90 disabled:opacity-40 transition-opacity"
            >
              {t('modal.next')}
            </button>
          )}
          {step === 2 && (
            <button
              onClick={confirmTool}
              disabled={!canAdvance2}
              className="px-4 py-2 bg-primary text-white text-sm font-medium rounded hover:opacity-90 disabled:opacity-40 transition-opacity"
            >
              {t('modal.next')}
            </button>
          )}
          {step === 3 && (
            <button
              onClick={confirmCard}
              disabled={!canAdvance3}
              className="px-4 py-2 bg-primary text-white text-sm font-medium rounded hover:opacity-90 disabled:opacity-40 transition-opacity"
            >
              {t('modal.addCard')}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

export default AddCardModal
