import React, { ReactNode, useEffect, useRef } from 'react'

type ModalSize = 'sm' | 'md' | 'lg' | 'xl'

interface ModalProps {
  isOpen: boolean
  onClose: () => void
  title: string
  children: ReactNode
  footer?: ReactNode
  /** Controls the maximum width of the dialog panel. Default: 'md' */
  size?: ModalSize
  /** When true, clicking the backdrop does not close the modal */
  disableBackdropClose?: boolean
  /** When true, renders a loading overlay over the modal body */
  loading?: boolean
}

const maxWidthClasses: Record<ModalSize, string> = {
  sm: 'max-w-sm',
  md: 'max-w-md',
  lg: 'max-w-2xl',
  xl: 'max-w-4xl',
}

const Modal: React.FC<ModalProps> = ({
  isOpen,
  onClose,
  title,
  children,
  footer,
  size = 'md',
  disableBackdropClose = false,
  loading = false,
}) => {
  const dialogRef = useRef<HTMLDivElement>(null)

  // Lock body scroll while open
  useEffect(() => {
    if (!isOpen) return
    const original = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = original }
  }, [isOpen])

  // Close on Escape key
  useEffect(() => {
    if (!isOpen) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [isOpen, onClose])

  // Move focus into dialog when it opens
  useEffect(() => {
    if (isOpen) {
      // Defer to let the element render first
      requestAnimationFrame(() => dialogRef.current?.focus())
    }
  }, [isOpen])

  if (!isOpen) return null

  return (
    <div
      className="fixed inset-0 z-modal flex items-center justify-center"
      role="dialog"
      aria-modal="true"
      aria-labelledby="modal-title"
    >
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/50"
        onClick={disableBackdropClose ? undefined : onClose}
        aria-hidden="true"
      />

      {/* Panel */}
      <div
        ref={dialogRef}
        tabIndex={-1}
        className={`
          relative bg-surface rounded-lg shadow-modal w-full mx-4
          outline-none focus-visible:ring-2 focus-visible:ring-primary
          ${maxWidthClasses[size]}
        `}
      >
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-border">
          <h2 id="modal-title" className="text-lg font-semibold text-dark">
            {title}
          </h2>
          <button
            onClick={onClose}
            aria-label="Fechar"
            className="p-1 text-muted hover:text-dark transition-colors rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Body */}
        <div className="relative p-6">
          {loading && (
            <div className="absolute inset-0 flex items-center justify-center bg-surface/80 rounded-b-lg z-10">
              <div className="w-8 h-8 border-4 border-primary border-t-transparent rounded-full animate-spin" aria-hidden="true" />
            </div>
          )}
          {children}
        </div>

        {/* Footer */}
        {footer && (
          <div className="border-t border-border p-6 flex justify-end gap-2">
            {footer}
          </div>
        )}
      </div>
    </div>
  )
}

export default Modal
