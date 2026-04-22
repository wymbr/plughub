import React from 'react'
import Spinner from './Spinner'

interface TableProps<T> {
  columns: {
    key: string
    label: string
    render?: (value: any, row: T) => React.ReactNode
  }[]
  data: T[]
  isLoading?: boolean
  keyField?: string
  onRowClick?: (row: T) => void
  className?: string
}

function Table<T extends Record<string, any>>({
  columns,
  data,
  isLoading = false,
  keyField = 'id',
  onRowClick,
  className = ''
}: TableProps<T>) {
  const skeletonRows = 5

  return (
    <div className={`overflow-x-auto rounded-lg border border-lightGray ${className}`}>
      <table className="w-full">
        <thead>
          <tr className="bg-tableAlt border-b border-lightGray">
            {columns.map(col => (
              <th key={col.key} className="px-6 py-3 text-left text-xs font-semibold text-dark">
                {col.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {isLoading ? (
            Array.from({ length: skeletonRows }).map((_, i) => (
              <tr key={`skeleton-${i}`} className="border-b border-lightGray hover:bg-tableAlt">
                {columns.map(col => (
                  <td key={col.key} className="px-6 py-3">
                    <div className="h-4 bg-lightGray rounded animate-pulse" />
                  </td>
                ))}
              </tr>
            ))
          ) : data.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="px-6 py-8 text-center text-gray">
                No data available
              </td>
            </tr>
          ) : (
            data.map((row, idx) => (
              <tr
                key={row[keyField] || idx}
                className="border-b border-lightGray hover:bg-tableAlt transition-colors cursor-pointer"
                onClick={() => onRowClick?.(row)}
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
