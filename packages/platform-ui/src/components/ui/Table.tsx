import React, { KeyboardEvent } from 'react'

interface Column<T> {
  key: string
  label: string
  render?: (value: any, row: T) => React.ReactNode
}

interface TableProps<T> {
  columns: Column<T>[]
  data: T[]
  isLoading?: boolean
  keyField?: string
  onRowClick?: (row: T) => void
  /** Accessible description of what activating a row does — e.g. "Abrir detalhes" */
  rowActionLabel?: string
  caption?: string
  className?: string
}

function Table<T extends Record<string, any>>({
  columns,
  data,
  isLoading = false,
  keyField = 'id',
  onRowClick,
  rowActionLabel = 'Abrir',
  caption,
  className = ''
}: TableProps<T>) {
  const skeletonRows = 5
  const isClickable  = Boolean(onRowClick)

  const handleRowKeyDown = (e: KeyboardEvent<HTMLTableRowElement>, row: T) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      onRowClick?.(row)
    }
  }

  return (
    <div className={`overflow-x-auto rounded-lg border border-border ${className}`}>
      <table className="w-full" role="grid">
        {caption && (
          <caption className="sr-only">{caption}</caption>
        )}
        <thead>
          <tr className="bg-tableAlt border-b border-border">
            {columns.map(col => (
              <th
                key={col.key}
                scope="col"
                className="px-6 py-3 text-left text-xs font-semibold text-dark"
              >
                {col.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {isLoading ? (
            Array.from({ length: skeletonRows }).map((_, i) => (
              <tr key={`skeleton-${i}`} className="border-b border-border" aria-busy="true">
                {columns.map(col => (
                  <td key={col.key} className="px-6 py-3">
                    <div className="h-4 bg-border rounded motion-safe:animate-pulse" aria-hidden="true" />
                  </td>
                ))}
              </tr>
            ))
          ) : data.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="px-6 py-8 text-center text-muted">
                Nenhum dado disponível
              </td>
            </tr>
          ) : (
            data.map((row, idx) => (
              <tr
                key={row[keyField] || idx}
                onClick={isClickable ? () => onRowClick?.(row) : undefined}
                onKeyDown={isClickable ? (e) => handleRowKeyDown(e, row) : undefined}
                tabIndex={isClickable ? 0 : undefined}
                role={isClickable ? 'row' : undefined}
                aria-label={isClickable ? `${rowActionLabel}: ${row[keyField] || idx}` : undefined}
                className={`border-b border-border transition-colors
                  hover:bg-tableAlt
                  ${isClickable ? 'cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary' : ''}
                `}
              >
                {columns.map(col => (
                  <td key={col.key} className="px-6 py-3 text-sm text-dark">
                    {col.render ? col.render(row[col.key], row) : row[col.key]}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  )
}

export default Table
