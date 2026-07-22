/**
 * outbound/_ui.tsx
 * Shared presentational helpers for the Outbound tabs (same style as SchedulesPage).
 */
import React from 'react'
import { useTranslation } from 'react-i18next'

export const inputCls =
  'w-full text-sm border border-border-strong rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-primary/40 bg-white'

export function Field({ label, hint, children }: {
  label: string; hint?: string; children: React.ReactNode
}) {
  return (
    <div>
      <label className="block text-xs font-medium text-dark mb-1">{label}</label>
      {children}
      {hint && <p className="text-xs text-muted-light mt-1">{hint}</p>}
    </div>
  )
}

export function Modal({ title, onClose, children, wide }: {
  title: string; onClose: () => void; children: React.ReactNode; wide?: boolean
}) {
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className={`bg-white rounded-xl shadow-xl w-full ${wide ? 'max-w-2xl' : 'max-w-xl'} max-h-[90vh] flex flex-col`}>
        <div className="flex items-center justify-between px-6 py-4 border-b border-border">
          <h2 className="text-base font-semibold text-dark">{title}</h2>
          <button onClick={onClose} className="text-muted-light hover:text-muted text-xl leading-none">×</button>
        </div>
        <div className="flex-1 overflow-y-auto px-6 py-4">{children}</div>
      </div>
    </div>
  )
}

export function ConfirmModal({ message, confirmLabel, onCancel, onConfirm }: {
  message: string; confirmLabel: string; onCancel: () => void; onConfirm: () => void
}) {
  const { t } = useTranslation('outbound')
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-sm">
        <p className="text-sm text-dark mb-4">{message}</p>
        <div className="flex gap-2 justify-end">
          <button onClick={onCancel} className="px-4 py-2 text-sm text-muted hover:text-dark">{t('actions.cancel')}</button>
          <button onClick={onConfirm} className="px-4 py-2 text-sm bg-red text-white rounded-lg hover:bg-red-text">{confirmLabel}</button>
        </div>
      </div>
    </div>
  )
}

const DELIVERY_STYLES: Record<string, string> = {
  claimed:            'bg-surface-alt text-muted',
  pending:            'bg-surface-alt text-muted',
  contacted:          'bg-primary-light text-primary',
  responded:          'bg-green/10 text-green',
  failed:             'bg-red-light text-red-text',
  skipped_ineligible: 'bg-warning-light text-warning-text',
  suppressed:         'bg-warning-light text-warning-text',
}

export function ResultPill({ result }: { result: string }) {
  const { t } = useTranslation('outbound')
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${DELIVERY_STYLES[result] ?? 'bg-surface-alt text-muted'}`}>
      {t(`delivery.result.${result}`, result)}
    </span>
  )
}
