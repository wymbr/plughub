import React, { ReactNode, useEffect, useRef } from 'react'

type DrawerSize = 'sm' | 'md' | 'lg' | 'xl'

interface DrawerProps {
  isOpen: boolean
  onClose: () => void
  /** Drawer heading shown in the header bar */
  title: string
  children: ReactNode
  /** Optional footer content — rendered in a sticky bottom bar */
  footer?: ReactNode
  /** Max-width of the drawer panel. Default: 'md' */
  size?: DrawerSize
  /** When true, clicking the backdrop does not close the drawer */
  disableBackdropClose?: boolean
  /** Accessible description for the dialog region */
  description?: string
}

const sizeClasses: Record<DrawerSize, string> = {
  sm: 'w-80',    // 320px
  md: 'w-96',    // 384px
  lg: 'w-[480px]',
  xl: 'w-[640px]',
}

const Drawer: React.FC<DrawerProps> = ({
  isOpen,
  onClose,
  title,
  children,
  footer,
  size = 'md',
  disableBackdropClose = false,
  description,
}) => {
  const panelRef = useRef<HTMLDivElement>(null)
  const titleId  = 'drawer-title'
  const descId   = description ? 'drawer-desc' : undefined

  // Body scroll lock
  useEffect(() => {
    if (!isOpen) return
    const original = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = original }
  }, [isOpen])

  // Escape key
  useEffect(() => {
    if (!isOpen) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [isOpen, onClose])

  // Focus trap — keep focus inside panel
  useEffect(() => {
    if (!isOpen || !panelRef.current) return
    const panel = panelRef.current

    // Move focus in on open
    requestAnimationFrame(() => panel.focus())

    const getFocusable = () =>
      Array.from(
        panel.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      ).filter(el => !el.closest('[aria-hidden="true"]'))

    const trapFocus = (e: KeyboardEvent) => {
      if (e.key !== 'Tab') return
      const focusable = getFocusable()
      if (focusable.length === 0) return

      const first = focusable[0]
      const last  = focusable[focusable.length - 1]

      if (e.shiftKey) {
        if (document.activeElement === first) {
          e.preventDefault()
          last.focus()
        }
      } else {
        if (document.activeElement === last) {
          e.preventDefault()
          first.focus()
        }
      }
    }

    panel.addEventListener('keydown', trapFocus)
    return () => panel.removeEventListener('keydown', trapFocus)
  }, [isOpen])

  if (!isOpen) return null

  return (
    <div className="fixed inset-0 z-overlay flex justify-end">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/40 transition-opacity"
        onClick={disableBackdropClose ? undefined : onClose}
        aria-hidden="true"
      />

      {/* Panel */}
      <div
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descId}
        className={`
          relative flex flex-col bg-surface h-full
          shadow-modal outline-none
          ${sizeClasses[size]}
        `}
      >
        {/* Header */}
        <div className="flex-shrink-0 flex items-center justify-between px-6 py-4 border-b border-border">
          <h2 id={titleId} className="text-base font-semibold text-dark truncate">
            {title}
          </h2>
          <button
            onClick={onClose}
            aria-label="Fechar painel"
            className="p-1 text-muted hover:text-dark transition-colors rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Optional accessible description */}
        {description && (
          <p id={descId} className="sr-only">
            {description}
          </p>
        )}

        {/* Scrollable body */}
        <div className="flex-1 overflow-y-auto px-6 py-5">
          {children}
        </div>

        {/* Optional sticky footer */}
        {footer && (
          <div className="flex-shrink-0 border-t border-border px-6 py-4 flex justify-end gap-2">
            {footer}
          </div>
        )}
      </div>
    </div>
  )
}

export default Drawer
