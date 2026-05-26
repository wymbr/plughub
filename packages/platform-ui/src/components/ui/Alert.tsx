import React, { ReactNode } from 'react'

type AlertVariant = 'info' | 'success' | 'warning' | 'error'

interface AlertProps {
  variant?: AlertVariant
  title?: string
  children: ReactNode
  className?: string
  /** When provided, renders a dismiss button calling this handler */
  onDismiss?: () => void
}

const config: Record<
  AlertVariant,
  { container: string; icon: ReactNode; dismissHover: string }
> = {
  info: {
    container: 'bg-info-light border border-info/30 text-info-text',
    dismissHover: 'hover:text-info-text',
    icon: (
      <svg className="w-4 h-4 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
          d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    ),
  },
  success: {
    container: 'bg-green-light border border-green/30 text-green-text',
    dismissHover: 'hover:text-green-text',
    icon: (
      <svg className="w-4 h-4 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
      </svg>
    ),
  },
  warning: {
    container: 'bg-warning-light border border-warning/30 text-warning-text',
    dismissHover: 'hover:text-warning-text',
    icon: (
      <svg className="w-4 h-4 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
          d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
      </svg>
    ),
  },
  error: {
    container: 'bg-red-light border border-red/30 text-red-text',
    dismissHover: 'hover:text-red-text',
    icon: (
      <svg className="w-4 h-4 flex-shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
          d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    ),
  },
}

const Alert: React.FC<AlertProps> = ({
  variant = 'info',
  title,
  children,
  className = '',
  onDismiss,
}) => {
  const { container, icon, dismissHover } = config[variant]

  return (
    <div
      role="alert"
      className={`flex gap-3 rounded-lg p-3 text-sm ${container} ${className}`}
    >
      {icon}

      <div className="flex-1 min-w-0">
        {title && <p className="font-semibold mb-0.5">{title}</p>}
        <div>{children}</div>
      </div>

      {onDismiss && (
        <button
          onClick={onDismiss}
          aria-label="Fechar alerta"
          className={`flex-shrink-0 opacity-60 transition-opacity hover:opacity-100 ${dismissHover} focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-current rounded`}
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      )}
    </div>
  )
}

export default Alert
