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
import { ENDPOINT_CATALOG, type EndpointDescriptor } from './catalog'
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
                : 'w-1.5 h-1.5 bg-gray-200'
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
  return (
    <div className="flex flex-col gap-3">
      <div>
        <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-0.5">
          O que você quer ver?
        </p>
        <p className="text-xs text-gray-400">Escolha a métrica que este card vai exibir.</p>
      </div>

      <div className="grid grid-cols-2 gap-2 max-h-72 overflow-y-auto pr-1">
        {ENDPOINT_CATALOG.map(ep => (
          <button
            key={ep.id}
            onClick={() => onSelect(ep)}
            className={`flex items-start gap-2.5 px-3 py-2.5 rounded-lg border text-left transition-colors ${
              selected?.id === ep.id
                ? 'border-primary bg-blue-50 text-primary'
                : 'border-gray-200 text-gray-700 hover:border-primary hover:bg-blue-50/40'
            }`}
          >
            <span className="text-lg flex-shrink-0 mt-0.5">{ep.icon}</span>
            <div className="min-w-0">
              <p className="text-xs font-medium leading-snug truncate">{ep.label}</p>
              <p className="text-xs text-gray-400 leading-snug mt-0.5 line-clamp-2">
                {ep.description}
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
  const tools = endpoint.compatible_tools
    .map(id => getDisplayTool(id))
    .filter(Boolean)

  return (
    <div className="flex flex-col gap-3">
      <div>
        <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-0.5">
          Como você quer ver?
        </p>
        <p className="text-xs text-gray-400">
          Visualizações disponíveis para{' '}
          <span className="font-medium text-gray-600">{endpoint.label}</span>.
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
                  ? 'border-primary bg-blue-50'
                  : 'border-gray-200 hover:border-primary hover:bg-blue-50/40'
              }`}
            >
              <span className={`text-2xl font-mono ${selected === tool.id ? 'text-primary' : 'text-gray-400'}`}>
                {tool.icon}
              </span>
              <div>
                <p className={`text-sm font-medium ${selected === tool.id ? 'text-primary' : 'text-gray-700'}`}>
                  {tool.label}
                </p>
                <p className="text-xs text-gray-400">{tool.description}</p>
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
        <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-0.5">
          Configurar card
        </p>
        <p className="text-xs text-gray-400">
          Defina o título e qualquer filtro fixo para este card.
        </p>
      </div>

      {/* Title */}
      <div>
        <label className="block text-xs font-medium text-gray-600 mb-1">
          Título do card
        </label>
        <input
          type="text"
          value={title}
          onChange={e => onTitleChange(e.target.value)}
          className="w-full border border-gray-200 rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
          placeholder={endpoint.label}
        />
      </div>

      {/* Configurable params */}
      {hasParams && (
        <div className="flex flex-col gap-3">
          <p className="text-xs font-medium text-gray-600">
            Filtros fixos{' '}
            <span className="font-normal text-gray-400">
              — deixe em branco para usar os filtros globais do dashboard
            </span>
          </p>
          {endpoint.configurable_params!.map(param => (
            <div key={param.key}>
              <label className="block text-xs text-gray-500 mb-1">
                {param.label}
                {!param.optional && <span className="text-red-500 ml-0.5">*</span>}
              </label>
              {param.options_from && paramOptions[param.key] ? (
                <select
                  value={fixedParams[param.key] ?? ''}
                  onChange={e => onParamChange(param.key, e.target.value)}
                  className="w-full border border-gray-200 rounded px-3 py-1.5 text-sm text-gray-700 focus:outline-none focus:ring-1 focus:ring-primary bg-white"
                >
                  <option value="">— selecione —</option>
                  {paramOptions[param.key].map(opt => (
                    <option key={opt} value={opt}>{opt}</option>
                  ))}
                </select>
              ) : (
                <input
                  type="text"
                  value={fixedParams[param.key] ?? ''}
                  onChange={e => onParamChange(param.key, e.target.value)}
                  placeholder={param.options_from ? 'Carregando opções…' : param.placeholder}
                  className="w-full border border-gray-200 rounded px-3 py-1.5 text-sm text-gray-700 focus:outline-none focus:ring-1 focus:ring-primary placeholder:text-gray-300"
                />
              )}
            </div>
          ))}
        </div>
      )}

      {/* Info: always-runtime params */}
      <div className="rounded-lg bg-gray-50 border border-gray-100 px-3 py-2.5 text-xs text-gray-400">
        <span className="font-medium text-gray-500">Período e tenant</span> são sempre controlados
        pelos filtros globais do dashboard — não precisam ser configurados aqui.
      </div>
    </div>
  )
}

// ─── Main modal ───────────────────────────────────────────────────────────────

export function AddCardModal({ tenantId, onAdd, onClose }: AddCardModalProps) {
  const [step, setStep] = useState<1 | 2 | 3>(1)

  const [selectedEndpoint, setSelectedEndpoint] = useState<EndpointDescriptor | null>(null)
  const [selectedToolId,   setSelectedToolId]   = useState<string>('')
  const [title,            setTitle]            = useState<string>('')
  const [fixedParams,      setFixedParams]      = useState<Record<string, string>>({})

  // ── Step 1 → 2 ────────────────────────────────────────────────────────────

  function confirmMetric(ep: EndpointDescriptor) {
    setSelectedEndpoint(ep)
    setSelectedToolId(ep.default_tool)
    setTitle(ep.label)
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
      title:       title.trim() || selectedEndpoint.label,
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
    1: 'Escolher métrica',
    2: 'Visualização',
    3: 'Configurar',
  }

  const canAdvance1 = !!selectedEndpoint
  const canAdvance2 = !!selectedToolId
  const canAdvance3 = !!title.trim() || !!selectedEndpoint

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg flex flex-col overflow-hidden max-h-[90vh]">

        {/* Header */}
        <div className="flex items-center justify-between px-6 pt-5 pb-4 border-b border-gray-100 flex-shrink-0">
          <div className="flex flex-col gap-1">
            <h2 className="text-base font-semibold text-gray-800">Adicionar card</h2>
            <div className="flex items-center gap-2">
              <StepDots current={step - 1} total={3} />
              <span className="text-xs text-gray-400">{STEP_LABELS[step]}</span>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 text-xl leading-none"
          >
            ×
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-6 py-5">
          {step === 1 && (
            <StepMetric
              selected={selectedEndpoint}
              onSelect={ep => { setSelectedEndpoint(ep); setSelectedToolId(ep.default_tool); setTitle(ep.label) }}
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
        <div className="flex gap-2 px-6 py-4 border-t border-gray-100 flex-shrink-0">
          {step > 1 && (
            <button
              onClick={goBack}
              className="px-4 py-2 border border-gray-200 text-gray-600 text-sm rounded hover:bg-gray-50 transition-colors"
            >
              ← Voltar
            </button>
          )}

          <div className="flex-1" />

          <button
            onClick={onClose}
            className="px-4 py-2 border border-gray-200 text-gray-600 text-sm rounded hover:bg-gray-50 transition-colors"
          >
            Cancelar
          </button>

          {step === 1 && (
            <button
              onClick={() => selectedEndpoint && confirmMetric(selectedEndpoint)}
              disabled={!canAdvance1}
              className="px-4 py-2 bg-primary text-white text-sm font-medium rounded hover:opacity-90 disabled:opacity-40 transition-opacity"
            >
              Próximo →
            </button>
          )}
          {step === 2 && (
            <button
              onClick={confirmTool}
              disabled={!canAdvance2}
              className="px-4 py-2 bg-primary text-white text-sm font-medium rounded hover:opacity-90 disabled:opacity-40 transition-opacity"
            >
              Próximo →
            </button>
          )}
          {step === 3 && (
            <button
              onClick={confirmCard}
              disabled={!canAdvance3}
              className="px-4 py-2 bg-primary text-white text-sm font-medium rounded hover:opacity-90 disabled:opacity-40 transition-opacity"
            >
              Adicionar card
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

export default AddCardModal
