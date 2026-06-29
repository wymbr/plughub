/**
 * RoleDefaultsModal.tsx
 *
 * Admin panel (Config → Dashboards) to define, per role:
 *   - allowed: which catalog components a role may use (constrains the user picker, F4)
 *   - starter_template_id: the dashboard a role lands on (before personal layout)
 *
 * Stored as role_catalog:{role} in the Config API `dashboards` namespace.
 */
import React, { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ENDPOINT_CATALOG } from '@/dashboard/catalog'
import { loadRoleCatalog, saveRoleCatalog } from '@/api/dashboard-hooks'
import type { DashboardTemplate } from '@/types'

const ROLES = ['operator', 'supervisor', 'admin', 'developer', 'business'] as const

export function RoleDefaultsModal({
  tenantId,
  adminToken,
  templates,
  onClose,
}: {
  tenantId:   string
  adminToken: string
  templates:  DashboardTemplate[]
  onClose:    () => void
}) {
  const { t } = useTranslation('dashboards')
  const [role,      setRole]      = useState<string>('operator')
  const [starterId, setStarterId] = useState<string>('')
  const [allowed,   setAllowed]   = useState<Set<string>>(new Set())
  const [loading,   setLoading]   = useState(false)
  const [saving,    setSaving]    = useState(false)
  const [saved,     setSaved]     = useState(false)
  const [error,     setError]     = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true); setSaved(false); setError(null)
    loadRoleCatalog(tenantId, role)
      .then(rc => {
        if (cancelled) return
        setStarterId(rc?.starter_template_id ?? '')
        setAllowed(new Set(rc?.allowed ?? []))
      })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [role, tenantId])

  const toggle = (id: string) =>
    setAllowed(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n })

  const save = async () => {
    setSaving(true); setError(null)
    try {
      await saveRoleCatalog(tenantId, role, { allowed: [...allowed], starter_template_id: starterId || null }, adminToken)
      setSaved(true); setTimeout(() => setSaved(false), 2000)
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div
      className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4"
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-xl max-h-[90vh] flex flex-col">
        <div className="flex items-center justify-between px-6 py-4 border-b border-border flex-shrink-0">
          <h2 className="text-lg font-semibold text-dark">{t('roleDefaults.title')}</h2>
          <button onClick={onClose} className="text-muted-light hover:text-muted text-xl leading-none">&times;</button>
        </div>

        <div className="px-6 py-4 space-y-4 overflow-y-auto flex-1">
          <p className="text-xs text-muted">{t('roleDefaults.hint')}</p>

          <div>
            <label className="block text-xs font-medium text-dark mb-1">{t('roleDefaults.role')}</label>
            <select
              value={role}
              onChange={e => setRole(e.target.value)}
              className="w-full border border-border-strong rounded-lg px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-primary/40"
            >
              {ROLES.map(r => <option key={r} value={r}>{r}</option>)}
            </select>
          </div>

          <div>
            <label className="block text-xs font-medium text-dark mb-1">{t('roleDefaults.starter')}</label>
            <select
              value={starterId}
              onChange={e => setStarterId(e.target.value)}
              disabled={loading}
              className="w-full border border-border-strong rounded-lg px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-primary/40"
            >
              <option value="">{t('roleDefaults.starterNone')}</option>
              {templates.map(tm => <option key={tm.template_id} value={tm.template_id}>{tm.name}</option>)}
            </select>
            <p className="text-2xs text-muted-light mt-1">{t('roleDefaults.starterHint')}</p>
          </div>

          <div>
            <label className="block text-xs font-medium text-dark mb-1">{t('roleDefaults.allowed')}</label>
            <div className="border border-border rounded-lg divide-y divide-border max-h-64 overflow-y-auto">
              {ENDPOINT_CATALOG.map(e => (
                <label key={e.id} className="flex items-center gap-3 px-3 py-2 cursor-pointer hover:bg-surface-muted">
                  <input
                    type="checkbox"
                    checked={allowed.has(e.id)}
                    onChange={() => toggle(e.id)}
                    disabled={loading}
                    className="rounded border-border-strong text-primary focus:ring-primary/40"
                  />
                  <span className="text-sm text-dark">{e.icon} {t(`catalog.${e.id}.label`, { defaultValue: e.label })}</span>
                </label>
              ))}
            </div>
            <p className="text-2xs text-muted-light mt-1">{t('roleDefaults.allowedHint')}</p>
          </div>

          {error && <p className="text-xs text-red-text">{error}</p>}
        </div>

        <div className="flex items-center gap-3 justify-end px-6 py-4 border-t border-border flex-shrink-0">
          {saved && <span className="text-xs text-green-text">{t('roleDefaults.saved')}</span>}
          <button onClick={onClose} className="px-4 py-2 text-sm text-muted hover:text-dark">{t('roleDefaults.cancel')}</button>
          <button
            onClick={save}
            disabled={saving || loading}
            className="px-4 py-2 text-sm bg-primary text-white rounded-lg hover:bg-primary-dark disabled:opacity-50"
          >
            {saving ? t('roleDefaults.saving') : t('roleDefaults.save')}
          </button>
        </div>
      </div>
    </div>
  )
}
