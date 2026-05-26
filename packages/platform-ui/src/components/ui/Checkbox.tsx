import React, { useId } from 'react'

interface CheckboxProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'type'> {
  label?: React.ReactNode
  error?: string
  /** Indeterminate state (visually shows a dash) */
  indeterminate?: boolean
}

const Checkbox = React.forwardRef<HTMLInputElement, CheckboxProps>(
  ({ label, error, indeterminate = false, className = '', id: idProp, ...props }, ref) => {
    const autoId = useId()
    const id     = idProp ?? autoId

    // Apply indeterminate imperatively via ref callback
    const setRef = (el: HTMLInputElement | null) => {
      if (el) el.indeterminate = indeterminate
      if (typeof ref === 'function') ref(el)
      else if (ref) (ref as React.MutableRefObject<HTMLInputElement | null>).current = el
    }

    return (
      <div className={`flex flex-col gap-1 ${className}`}>
        <label htmlFor={id} className="inline-flex items-start gap-2 cursor-pointer group">
          <input
            ref={setRef}
            id={id}
            type="checkbox"
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? `${id}-error` : undefined}
            className={`
              mt-0.5 w-4 h-4 flex-shrink-0 rounded border-2 cursor-pointer
              border-border text-primary bg-surface
              checked:bg-primary checked:border-primary
              indeterminate:bg-primary indeterminate:border-primary
              focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-1
              transition-colors
              disabled:opacity-50 disabled:cursor-not-allowed
            `}
            {...props}
          />
          {label && (
            <span className="text-sm text-dark group-has-[:disabled]:opacity-50 select-none">
              {label}
            </span>
          )}
        </label>

        {error && (
          <p id={`${id}-error`} className="text-xs text-red" role="alert">
            {error}
          </p>
        )}
      </div>
    )
  },
)

Checkbox.displayName = 'Checkbox'

export default Checkbox
