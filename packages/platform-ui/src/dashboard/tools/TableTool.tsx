/**
 * TableTool.tsx
 * Sortable data table with fixed header and internal scroll.
 *
 * tool_id: "table"
 * data shape: TableData
 */
import React, { useState } from 'react'
import type { DisplayToolProps, TableData } from './types'

function Skeleton() {
  return (
    <div className="h-full flex flex-col gap-2 p-2 animate-pulse">
      <div className="h-4 rounded bg-gray-200 w-full" />
      {[1, 2, 3, 4].map(i => (
        <div key={i} className="h-3 rounded bg-gray-100 w-full" />
      ))}
    </div>
  )
}

function formatCell(value: string | number | null): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'number') return value.toLocaleString('pt-BR')
  return String(value)
}

export const TableTool: React.FC<DisplayToolProps<TableData>> = ({
  data,
  loading,
  error,
}) => {
  const [sortKey, setSortKey]   = useState<string | null>(null)
  const [sortAsc, setSortAsc]   = useState(true)

  if (loading) return <Skeleton />

  if (error) {
    return (
      <div className="h-full flex items-center justify-center text-xs text-red-400">
        Indisponível
      </div>
    )
  }

  function handleSort(key: string) {
    if (sortKey === key) {
      setSortAsc(a => !a)
    } else {
      setSortKey(key)
      setSortAsc(true)
    }
  }

  const rows = sortKey
    ? [...data.rows].sort((a, b) => {
        const av = a[sortKey] ?? ''
        const bv = b[sortKey] ?? ''
        const cmp = String(av).localeCompare(String(bv), 'pt-BR', { numeric: true })
        return sortAsc ? cmp : -cmp
      })
    : data.rows

  return (
    <div className="h-full flex flex-col overflow-hidden text-xs">
      {/* Fixed header */}
      <div className="flex-shrink-0 bg-gray-50 border-b border-gray-100">
        <table className="w-full table-fixed border-collapse">
          <thead>
            <tr>
              {data.columns.map(col => (
                <th
                  key={col.key}
                  onClick={col.sortable ? () => handleSort(col.key) : undefined}
                  className={`px-3 py-1.5 font-semibold text-gray-500 ${
                    col.align === 'right' ? 'text-right' : col.align === 'center' ? 'text-center' : 'text-left'
                  } ${col.sortable ? 'cursor-pointer select-none hover:text-gray-700' : ''}`}
                >
                  {col.label}
                  {col.sortable && sortKey === col.key && (
                    <span className="ml-1 text-primary">{sortAsc ? '↑' : '↓'}</span>
                  )}
                </th>
              ))}
            </tr>
          </thead>
        </table>
      </div>

      {/* Scrollable body */}
      <div className="flex-1 overflow-y-auto">
        <table className="w-full table-fixed border-collapse">
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td
                  colSpan={data.columns.length}
                  className="px-3 py-4 text-center text-gray-400"
                >
                  Sem dados
                </td>
              </tr>
            )}
            {rows.map((row, i) => (
              <tr
                key={i}
                className={i % 2 === 0 ? 'bg-white' : 'bg-gray-50/50'}
              >
                {data.columns.map(col => (
                  <td
                    key={col.key}
                    className={`px-3 py-1.5 text-gray-700 truncate ${
                      col.align === 'right' ? 'text-right' : col.align === 'center' ? 'text-center' : 'text-left'
                    }`}
                  >
                    {formatCell(row[col.key] as string | number | null)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {data.total !== undefined && data.total > data.rows.length && (
        <div className="flex-shrink-0 px-3 py-1 border-t border-gray-100 text-gray-400 text-right">
          {data.rows.length} de {data.total.toLocaleString('pt-BR')}
        </div>
      )}
    </div>
  )
}

export default TableTool
