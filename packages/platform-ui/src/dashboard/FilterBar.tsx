/**
 * FilterBar.tsx
 * Runtime filter bar — renders controls from template.global_filters.
 *
 * Sits between the TopBar and the grid area. Visible in both view and edit mode.
 * Each filter maps to a key in the runtimeFilters record that CardRenderer reads.
 *
 * Filter types:
 *   date         → <input type="date">
 *   select       → <select> dropdown
 *   multi_select → multiple <select> (or tag-style list of checkboxes)
 */
import React from 'react'
import type { GlobalFilter } from './tools/types'

// ─── Props ────────────────────────────────────────────────────────────────────

interface FilterBarProps {
  filters:        GlobalFilter[]
  values:         Record<string, unknown>
  onChange:       (key: string, value: unknown) => void
  onReset:        () => void
}

// ─── Individual filter control ────────────────────────────────────────────────

function FilterControl({
  filter,
  value,
  onChange,
}: {
  filter:   GlobalFilter
  value:    unknown
  onChange: (v: unknown) => void
}) {
  const strVal = value !== null && value !== undefined ? String(value) : ''

  if (filter.type === 'date') {
    return (
      <div className="flex items-center gap-1.5">
        <label className="text-xs text-gray-500 whitespace-nowrap">{filter.label}</label>
        <input
          type="date"
          value={strVal}
          onChange={e => onChange(e.target.value || null)}
          className="text-xs border border-gray-200 rounded px-2 py-1 text-gray-700 focus:outline-none focus:ring-1 focus:ring-primary bg-white"
        />
      </div>
    )
  }

  if (filter.type === 'select') {
    return (
      <div className="flex items-center gap-1.5">
        <label className="text-xs text-gray-500 whitespace-nowrap">{filter.label}</label>
        <select
          value={strVal}
          onChange={e => onChange(e.target.value || null)}
          className="text-xs border border-gray-200 rounded px-2 py-1 text-gray-700 focus:outline-none focus:ring-1 focus:ring-primary bg-white"
        >
          <option value="">Todos</option>
          {filter.options?.map(opt => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
      </div>
    )
  }

  if (filter.type === 'multi_select') {
    const selected = Array.isArray(value) ? (value as string[]) : []
    return (
      <div className="flex items-center gap-1.5">
        <label className="text-xs text-gray-500 whitespace-nowrap">{filter.label}</label>
        <select
          multiple
          value={selected}
          onChange={e => {
            const vals = Array.from(e.target.selectedOptions, o => o.value)
            onChange(vals.length > 0 ? vals : null)
          }}
          className="text-xs border border-gray-200 rounded px-2 py-1 text-gray-700 focus:outline-none focus:ring-1 focus:ring-primary bg-white max-h-20"
          size={Math.min(filter.options?.length ?? 3, 3)}
        >
          {filter.options?.map(opt => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
      </div>
    )
  }

  return null
}

// ─── FilterBar ────────────────────────────────────────────────────────────────

export function FilterBar({ filters, values, onChange, onReset }: FilterBarProps) {
  if (filters.length === 0) return null

  const hasActiveFilter = filters.some(f => {
    const v = values[f.filter_key]
    return v !== null && v !== undefined && v !== '' && !(Array.isArray(v) && v.length === 0)
  })

  return (
    <div className="flex items-center gap-4 px-6 py-2 border-b border-gray-200 bg-white flex-shrink-0 flex-wrap">
      {filters.map(filter => (
        <FilterControl
          key={filter.filter_key}
          filter={filter}
          value={values[filter.filter_key] ?? filter.default ?? ''}
          onChange={v => onChange(filter.filter_key, v)}
        />
      ))}

      {hasActiveFilter && (
        <button
          onClick={onReset}
          className="text-xs text-gray-400 hover:text-gray-600 transition-colors ml-auto flex-shrink-0"
          title="Limpar filtros"
        >
          ↺ Limpar
        </button>
      )}
    </div>
  )
}

// ─── Filter preset definitions (used by admin filter editor) ──────────────────

export interface FilterPreset {
  id:      string
  label:   string
  icon:    string
  filters: GlobalFilter[]
}

export const FILTER_PRESETS: FilterPreset[] = [
  {
    id:    'date_range',
    label: 'Período (De / Até)',
    icon:  '📅',
    filters: [
      {
        filter_key: 'date_from',
        label:      'De',
        type:       'date',
        default:    '',
      },
      {
        filter_key: 'date_to',
        label:      'Até',
        type:       'date',
        default:    '',
      },
    ],
  },
  {
    id:    'pool_selector',
    label: 'Pool',
    icon:  '🏊',
    filters: [
      {
        filter_key: 'pool_id',
        label:      'Pool',
        type:       'select',
        options:    [],   // populated by admin
        default:    '',
      },
    ],
  },
]

export default FilterBar
