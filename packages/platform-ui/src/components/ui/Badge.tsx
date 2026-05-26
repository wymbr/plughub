import React from 'react'

/**
 * Semantic badge variants:
 *
 * Generic lifecycle:  active | pending | processing | completed | suspended | failed | default
 * Semantic:           success | warning | error | info
 * Evaluation/review:  approved | contested | rejected | revised
 * Agent type:         ai | human
 */
export type BadgeVariant =
  // Agent lifecycle (original)
  | 'active'
  | 'suspended'
  | 'failed'
  | 'default'
  // Generic workflow lifecycle
  | 'pending'
  | 'processing'
  | 'completed'
  | 'cancelled'
  // Semantic (generic)
  | 'success'
  | 'warning'
  | 'error'
  | 'info'
  // Evaluation / review / contestation states
  | 'approved'
  | 'contested'
  | 'rejected'
  | 'revised'
  // Agent type
  | 'ai'
  | 'human'

interface BadgeProps {
  variant?: BadgeVariant
  children: React.ReactNode
  className?: string
  /** Optional dot indicator prepended before the label */
  dot?: boolean
}

const variantClasses: Record<BadgeVariant, string> = {
  // ── Agent lifecycle ───────────────────────────────────────────────────────
  active:      'bg-green-light text-green-text border border-green/30',
  suspended:   'bg-warning-light text-warning-text border border-warning/30',
  failed:      'bg-red-light text-red-text border border-red/30',
  default:     'bg-surface-alt text-muted border border-border',

  // ── Generic workflow ──────────────────────────────────────────────────────
  pending:     'bg-info-light text-info-text border border-info/30',
  processing:  'bg-primary-light text-primary border border-primary/30',
  completed:   'bg-green-light text-green-text border border-green/30',
  cancelled:   'bg-surface-alt text-muted border border-border',

  // ── Semantic generics ─────────────────────────────────────────────────────
  success:     'bg-green-light text-green-text border border-green/30',
  warning:     'bg-warning-light text-warning-text border border-warning/30',
  error:       'bg-red-light text-red-text border border-red/30',
  info:        'bg-info-light text-info-text border border-info/30',

  // ── Evaluation / contestation states ─────────────────────────────────────
  approved:    'bg-green-light text-green-text border border-green/30',
  contested:   'bg-contested-light text-contested-text border border-contested/30',
  rejected:    'bg-red-light text-red-text border border-red/30',
  revised:     'bg-revised-light text-revised-text border border-revised/30',

  // ── Agent type ────────────────────────────────────────────────────────────
  ai:          'bg-ai-light text-ai-text border border-ai/30',
  human:       'bg-primary-light text-primary border border-primary/30',
}

const dotColour: Record<BadgeVariant, string> = {
  active:     'bg-green',
  suspended:  'bg-warning',
  failed:     'bg-red',
  default:    'bg-muted',
  pending:    'bg-info',
  processing: 'bg-primary',
  completed:  'bg-green',
  cancelled:  'bg-muted',
  success:    'bg-green',
  warning:    'bg-warning',
  error:      'bg-red',
  info:       'bg-info',
  approved:   'bg-green',
  contested:  'bg-contested',
  rejected:   'bg-red',
  revised:    'bg-revised',
  ai:         'bg-ai',
  human:      'bg-primary',
}

const Badge: React.FC<BadgeProps> = ({ variant = 'default', children, className = '', dot = false }) => {
  return (
    <span
      className={`
        inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold
        ${variantClasses[variant]}
        ${className}
      `}
    >
      {dot && (
        <span
          className={`inline-block w-1.5 h-1.5 rounded-full flex-shrink-0 ${dotColour[variant]}`}
          aria-hidden="true"
        />
      )}
      {children}
    </span>
  )
}

export default Badge
