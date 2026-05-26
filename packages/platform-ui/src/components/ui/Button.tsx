import React from 'react'

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  size?: 'sm' | 'md' | 'lg'
  /** Shows a spinner and blocks interaction while true */
  loading?: boolean
  /** Prepend an icon before the button label */
  leftIcon?: React.ReactNode
  /** Append an icon after the button label */
  rightIcon?: React.ReactNode
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      variant = 'primary',
      size = 'md',
      className = '',
      disabled,
      loading = false,
      leftIcon,
      rightIcon,
      children,
      ...props
    },
    ref,
  ) => {
    const variantClasses = {
      primary:   'bg-primary hover:bg-primary-dark active:bg-primary-dark text-white disabled:bg-muted-light',
      secondary: 'bg-secondary hover:bg-secondary-dark active:bg-secondary-dark text-white disabled:bg-muted-light',
      ghost:     'bg-transparent hover:bg-surface-alt active:bg-border text-dark disabled:text-muted-light',
      danger:    'bg-red hover:bg-red-text active:bg-red-text text-white disabled:bg-muted-light',
    }

    const sizeClasses = {
      sm: 'px-3 py-1 text-sm gap-1.5',
      md: 'px-4 py-2 text-base gap-2',
      lg: 'px-6 py-3 text-lg gap-2.5',
    }

    const spinnerSize = { sm: 'w-3 h-3', md: 'w-4 h-4', lg: 'w-5 h-5' }

    const isDisabled = disabled || loading

    return (
      <button
        ref={ref}
        disabled={isDisabled}
        aria-busy={loading || undefined}
        className={`
          inline-flex items-center justify-center font-semibold rounded transition-colors
          ${variantClasses[variant]}
          ${sizeClasses[size]}
          ${isDisabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'}
          ${className}
        `}
        {...props}
      >
        {loading ? (
          <span
            className={`inline-block border-2 border-current border-t-transparent rounded-full animate-spin ${spinnerSize[size]}`}
            aria-hidden="true"
          />
        ) : leftIcon ? (
          <span aria-hidden="true">{leftIcon}</span>
        ) : null}

        {children}

        {!loading && rightIcon ? (
          <span aria-hidden="true">{rightIcon}</span>
        ) : null}
      </button>
    )
  },
)

Button.displayName = 'Button'

export default Button
