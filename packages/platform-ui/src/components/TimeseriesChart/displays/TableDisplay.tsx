/**
 * TableDisplay.tsx
 *
 * Tabular view of timeseries buckets.
 *
 * Columns:
 *   Período | Total | <breakdown-series>…
 *
 * Features:
 *   – Sortable columns (click header to toggle asc/desc)
 *   – Compact mode shows first 5 rows + "Ver mais" expander
 *   – Sticky header when scrolling
 */
import React, { useState } from 'react'
import type { TimeseriesBucket } from '@/types'
import { formatBucketLabel, getFormatter, type FormatType } from '../formatters'

export interface TableDisplayProps {
  buckets:         TimeseriesBucket[]
  intervalMinutes: number
  formatType:      FormatType
  valueLabel:      string
  height:          number
  compact:         boolean
}

type SortDir = 'asc' | 'desc'

interface ColDef {
  key:    string
  label:  string
  isText: boolean
}

export function TableDisplay({
  buckets,
  intervalMinutes,
  formatType,
  valueLabel,
  height,
  compact,
}: TableDisplayProps) {
  const fmt = getFormatter(formatType)
  const [sortCol, setSortCol] = useState<string>('_bucket')
  const [sortDir, setSortDir] = useState<SortDir>('asc')
  const [showAll, setShowAll] = useState(false)

  // Collect all breakdown series labels
  const seriesKeys: string[] = Array.from(
    new Set(buckets.flatMap(b => b.breakdown.map(bd => bd.label)))
  )
  const hasBreakdown = seriesKeys.length > 0

  // Build flat row objects
  type RowData = Record<string, string | number>
  const rows: RowData[] = buckets.map(b => {
    const row: RowData = {
      _bucket:    b.bucket,
      _label:     formatBucketLabel(b.bucket, intervalMinutes),
      [valueLabel]: b.value,
    }
    if (hasBreakdown) {
      for (const key of seriesKeys) {
        const bd = b.breakdown.find(x => x.label === key)
        row[key] = bd?.value ?? 0
      }
    }
    return row
  })

  // Sort
  const sorted = [...rows].sort((a, b) => {
    const av = a[sortCol]
    const bv = b[sortCol]
    let cmp: number
    if (typeof av === 'number' && typeof bv === 'number') {
      cmp = av - bv
    } else {
      cmp = String(av).localeCompare(String(bv))
    }
    return sortDir === 'asc' ? cmp : -cmp
  })

  const displayed = compact && !showAll ? sorted.slice(0, 5) : sorted

  function toggleSort(col: string) {
    if (sortCol === col) {
      setSortDir(d => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortCol(col)
      setSortDir('asc')
    }
  }

  // Column definitions — breakdown columns between period and total
  const columns: ColDef[] = [
    { key: '_label', label: 'Período', isText: true },
    ...(hasBreakdown ? seriesKeys : []).map(k => ({ key: k, label: k, isText: false })),
    { key: valueLabel, label: hasBreakdown ? 'Total' : valueLabel, isText: false },
  ]

  const sortIndicator = (col: string) =>
    sortCol === col
      ? <span className="ml-0.5 text-primary">{sortDir === 'asc' ? '↑' : '↓'}</span>
      : null

  return (
    <div className="overflow-auto" style={{ maxHeight: compact ? height : undefined }}>
      <table className="w-full text-xs border-collapse">
        <thead className="sticky top-0 bg-gray-50 z-10">
          <tr className="border-b border-gray-200">
            {columns.map(col => (
              <th
                key={col.key}
                onClick={() => toggleSort(col.key === '_label' ? '_bucket' : col.key)}
                className={`px-2 py-2 text-xs font-semibold text-gray-500 whitespace-nowrap cursor-pointer
                  select-none hover:text-gray-800 transition-colors
                  ${col.isText ? 'text-left' : 'text-right'}`}
              >
                {col.label}
                {sortIndicator(col.key === '_label' ? '_bucket' : col.key)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {displayed.map((row, i) => (
            <tr
              key={i}
              className="border-b border-gray-100 hover:bg-blue-50/40 transition-colors"
            >
              {columns.map(col => {
                const rawKey = col.key
                const val = row[rawKey]
                return (
                  <td
                    key={rawKey}
                    className={`px-2 py-1.5 ${
                      col.isText
                        ? 'text-gray-600'
                        : 'text-right font-mono text-gray-800 tabular-nums'
                    }`}
                  >
                    {col.isText ? String(val) : fmt(Number(val ?? 0))}
                  </td>
                )
              })}
            </tr>
          ))}
          {displayed.length === 0 && (
            <tr>
              <td
                colSpan={columns.length}
                className="py-6 text-center text-xs text-gray-400"
              >
                Nenhum dado
              </td>
            </tr>
          )}
        </tbody>
      </table>

      {compact && !showAll && rows.length > 5 && (
        <button
          onClick={() => setShowAll(true)}
          className="w-full py-1.5 text-center text-xs text-primary hover:underline transition-all"
        >
          Ver mais {rows.length - 5} {rows.length - 5 === 1 ? 'período' : 'períodos'}
        </button>
      )}
    </div>
  )
}
