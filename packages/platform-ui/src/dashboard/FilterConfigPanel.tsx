/**
 * FilterConfigPanel.tsx
 * Admin-only panel (edit mode) for managing the template's global_filters.
 *
 * Displayed in the sidebar below the template list when editMode=true.
 * Allows adding filter presets and removing individual filters.
 * For the 'select' type, allows adding comma-separated option values.
 */
import React, { useState } from 'react'
import { FILTER_PRESETS, type FilterPreset } from './FilterBar'
import type { GlobalFilter } from './tools/types'

// ─── Props ────────────────────────────────────────────────────────────────────

interface FilterConfigPanelProps {
  filters:  GlobalFilter[]
  onChange: (filters: GlobalFilter[]) => void
}

// ─── Option editor for select filters ────────────────────────────────────────

function OptionEditor({
  filter,
  onUpdate,
}: {
  filter:   GlobalFilter
  onUpdate: (f: GlobalFilter) => void
}) {
  const [raw, setRaw] = useState(
    (filter.options ?? []).map(o => o.value).join(', ')
  )

  function commit(val: string) {
    const opts = val
      .split(',')
      .map(s => s.trim())
      .filter(Boolean)
      .map(v => ({ value: v, label: v }))
    onUpdate({ ...filter, options: opts })
  }

  return (
    <div className="mt-1 pl-2 border-l-2 border-gray-100">
      <label className="block text-[10px] text-gray-400 mb-0.5">
        Opções (separadas por vírgula)
      </label>
      <input
        type="text"
        value={raw}
        onChange={e => setRaw(e.target.value)}
        onBlur={e => commit(e.target.value)}
        placeholder="Ex: pool_a, pool_b"
        className="w-full text-xs border border-gray-200 rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-primary"
      />
    </div>
  )
}

// ─── Main panel ───────────────────────────────────────────────────────────────

export function FilterConfigPanel({ filters, onChange }: FilterConfigPanelProps) {
  const [showPresets, setShowPresets] = useState(false)

  // Active preset IDs — for each preset, check if all its filter_keys are present
  function isPresetActive(preset: FilterPreset): boolean {
    return preset.filters.every(pf =>
      filters.some(f => f.filter_key === pf.filter_key)
    )
  }

  function addPreset(preset: FilterPreset) {
    const newFilters = [...filters]
    for (const pf of preset.filters) {
      if (!newFilters.some(f => f.filter_key === pf.filter_key)) {
        newFilters.push(pf)
      }
    }
    onChange(newFilters)
    setShowPresets(false)
  }

  function removePreset(preset: FilterPreset) {
    const keys = new Set(preset.filters.map(f => f.filter_key))
    onChange(filters.filter(f => !keys.has(f.filter_key)))
  }

  function updateFilter(updated: GlobalFilter) {
    onChange(filters.map(f => f.filter_key === updated.filter_key ? updated : f))
  }

  const activePresets  = FILTER_PRESETS.filter(isPresetActive)
  const availablePresets = FILTER_PRESETS.filter(p => !isPresetActive(p))

  return (
    <div className="border-t border-gray-200 px-4 py-3">
      <div className="flex items-center justify-between mb-2">
        <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
          Filtros
        </p>
        {availablePresets.length > 0 && (
          <button
            onClick={() => setShowPresets(s => !s)}
            className="text-[10px] text-primary hover:underline"
          >
            + Adicionar
          </button>
        )}
      </div>

      {/* Preset picker */}
      {showPresets && (
        <div className="flex flex-col gap-1 mb-2">
          {availablePresets.map(preset => (
            <button
              key={preset.id}
              onClick={() => addPreset(preset)}
              className="flex items-center gap-2 px-2 py-1.5 rounded border border-gray-200 hover:border-primary hover:bg-blue-50/40 text-left transition-colors"
            >
              <span className="text-sm">{preset.icon}</span>
              <span className="text-xs text-gray-700">{preset.label}</span>
            </button>
          ))}
        </div>
      )}

      {/* Active filters */}
      {activePresets.length === 0 && !showPresets && (
        <p className="text-xs text-gray-400 italic">Nenhum filtro configurado</p>
      )}

      {activePresets.map(preset => (
        <div key={preset.id} className="mb-2">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-gray-600 flex items-center gap-1">
              <span>{preset.icon}</span>
              {preset.label}
            </span>
            <button
              onClick={() => removePreset(preset)}
              className="text-gray-400 hover:text-red-500 text-sm leading-none transition-colors"
              title="Remover filtro"
            >
              ×
            </button>
          </div>

          {/* Option editor for select-type filters in this preset */}
          {preset.filters
            .filter(pf => pf.type === 'select' || pf.type === 'multi_select')
            .map(pf => {
              const current = filters.find(f => f.filter_key === pf.filter_key)
              if (!current) return null
              return (
                <OptionEditor
                  key={pf.filter_key}
                  filter={current}
                  onUpdate={updateFilter}
                />
              )
            })}
        </div>
      ))}
    </div>
  )
}

export default FilterConfigPanel
