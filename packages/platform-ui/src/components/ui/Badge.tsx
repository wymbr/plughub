import React from 'react'

interface BadgeProps {
  variant?: 'active' | 'suspended' | 'failed' | 'default'
  children: React.ReactNode
  className?: string
}

const Badge: React.FC<BadgeProps> = ({ variant = 'default', children, className = '' }) => {
  const variantClasses = {
    active: 'bg-green/20 text-green border border-green/30',
    suspended: 'bg-warning/20 text-warning border border-warning/30',
    failed: 'bg-red/20 text-red border border-red/30',
    default: 'bg-gray/20 text-gray border border-gray/30'
  }

  return (
    <span className={`
      inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold
      ${variantClasses[variant]}
      ${className}
    `}>
      {children}
    </span>
  )
}

export default Badge
