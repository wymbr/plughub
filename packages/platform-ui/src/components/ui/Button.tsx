import React from 'react'

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  size?: 'sm' | 'md' | 'lg'
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = 'primary', size = 'md', className = '', disabled, ...props }, ref) => {
    const variantClasses = {
      primary: 'bg-primary hover:bg-blue-900 text-white disabled:bg-gray-400',
      secondary: 'bg-secondary hover:bg-blue-500 text-white disabled:bg-gray-400',
      ghost: 'bg-transparent hover:bg-gray-200 text-dark disabled:text-gray-400',
      danger: 'bg-red hover:bg-red-700 text-white disabled:bg-gray-400'
    }

    const sizeClasses = {
      sm: 'px-3 py-1 text-sm',
      md: 'px-4 py-2 text-base',
      lg: 'px-6 py-3 text-lg'
    }

    return (
      <button
        ref={ref}
        disabled={disabled}
        className={`
          font-semibold rounded transition-colors
          ${variantClasses[variant]}
          ${sizeClasses[size]}
          ${disabled ? 'cursor-not-allowed' : 'cursor-pointer'}
          ${className}
        `}
        {...props}
      />
    )
  }
)

Button.displayName = 'Button'

export default Button
