import React from 'react'

interface EmptyStateProps {
  title: string
  description?: string
  action?: React.ReactNode
  icon?: React.ReactNode
}

const EmptyState: React.FC<EmptyStateProps> = ({ title, description, action, icon }) => {
  return (
    <div className="flex flex-col items-center justify-center py-12">
      {icon ? (
        <div className="mb-4 text-gray">{icon}</div>
      ) : (
        <svg
          className="w-12 h-12 text-gray mb-4"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
          />
        </svg>
      )}

      <h3 className="text-lg font-semibold text-dark mb-1">{title}</h3>

      {description && (
        <p className="text-gray text-sm mb-4 text-center max-w-md">
          {description}
        </p>
      )}

      {action && <div>{action}</div>}
    </div>
  )
}

export default EmptyState
