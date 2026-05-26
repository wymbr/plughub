import React, { useId } from 'react'

interface SwitchProps extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'onChange'> {
  checked: boolean
  onChange: (checked: boolean) => void
  label?: React.ReactNode
  /** Shown below the label in smaller text */
  description?: string
  disabled?: boolean
}

const Switch = React.forwardRef<HTMLButtonElement, SwitchProps>(
  ({ checked, onChange, label, description, disabled = false, className = '', id: idProp, ...props }, ref) => {
    const autoId   = useId()
    const id       = idProp ?? autoId
    const descId   = description ? `${id}-desc` : undefined

    return (
      <div className={`flex items-start gap-3 ${className}`}>
        <button
          ref={ref}
          id={id}
          type="button"
          role="switch"
          aria-checked={checked}
          aria-describedby={descId}
          disabled={disabled}
          onClick={() => onChange(!checked)}
          className={`
            relative inline-flex flex-shrink-0 h-6 w-11 rounded-full
            border-2 border-transparent cursor-pointer transition-colors duration-200
            focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2
            disabled:opacity-50 disabled:cursor-not-allowed
            ${checked ? 'bg-primary' : 'bg-border'}
          `}
          {...props}
        >
          <span className="sr-only">{checked ? 'Ativado' : 'Desativado'}</span>
          <span
            aria-hidden="true"
            className={`
              pointer-events-none inline-block h-5 w-5 rounded-full bg-surface shadow
              ring-0 transition-transform duration-200
              ${checked ? 'translate-x-5' : 'translate-x-0'}
            `}
          />
        </button>

        {(label || description) && (
          <div className="flex flex-col">
            {label && (
              <label
                htmlFor={id}
                className={`text-sm font-semibold text-dark cursor-pointer ${disabled ? 'opacity-50' : ''}`}
              >
                {label}
              </label>
            )}
            {description && (
              <p id={descId} className={`text-xs text-muted ${disabled ? 'opacity-50' : ''}`}>
                {description}
              </p>
            )}
          </div>
        )}
      </div>
    )
  },
)

Switch.displayName = 'Switch'

export default Switch
