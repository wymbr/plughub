import React, { useId } from 'react'

interface TextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string
  error?: string
  /** Hint shown below the field (replaces error when no error) */
  hint?: string
  /** When provided, renders a character counter below the field */
  maxLength?: number
  /** Show character count even before maxLength is approached */
  showCount?: boolean
}

const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  (
    {
      label,
      error,
      hint,
      maxLength,
      showCount = false,
      className = '',
      value,
      id: idProp,
      ...props
    },
    ref,
  ) => {
    const autoId = useId()
    const id     = idProp ?? autoId

    const charCount = typeof value === 'string' ? value.length : 0
    const nearLimit = maxLength !== undefined && charCount >= maxLength * 0.9

    return (
      <div className="flex flex-col gap-1">
        {label && (
          <label htmlFor={id} className="text-sm font-semibold text-dark">
            {label}
          </label>
        )}

        <textarea
          ref={ref}
          id={id}
          value={value}
          maxLength={maxLength}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-error` : hint ? `${id}-hint` : undefined}
          className={`
            w-full px-3 py-2 border rounded resize-y
            focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent
            placeholder:text-muted-light text-dark text-sm
            ${error ? 'border-red bg-red-light/20' : 'border-border'}
            ${className}
          `}
          rows={props.rows ?? 3}
          {...props}
        />

        <div className="flex items-start justify-between gap-2">
          {/* Error / hint */}
          <div>
            {error ? (
              <p id={`${id}-error`} className="text-xs text-red" role="alert">
                {error}
              </p>
            ) : hint ? (
              <p id={`${id}-hint`} className="text-xs text-muted">
                {hint}
              </p>
            ) : null}
          </div>

          {/* Character counter */}
          {(showCount || maxLength !== undefined) && (
            <p
              className={`text-xs flex-shrink-0 tabular-nums ${
                nearLimit ? 'text-warning font-semibold' : 'text-muted'
              }`}
              aria-live="polite"
              aria-atomic="true"
            >
              {charCount}
              {maxLength !== undefined && `/${maxLength}`}
            </p>
          )}
        </div>
      </div>
    )
  },
)

Textarea.displayName = 'Textarea'

export default Textarea
