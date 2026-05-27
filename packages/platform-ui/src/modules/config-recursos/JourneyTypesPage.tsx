/**
 * JourneyTypesPage.tsx — CRUD for JourneyType entities (Arc 17)
 *
 * JourneyTypes are tenant-scoped definitions of business process types.
 * Each type has a snake_case slug (journey_type_id), optional description,
 * and optional sla_ms.  Pools reference them via authorized_journey_types[].
 */
import React, { useState, useCallback, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import {
  listJourneyTypes,
  createJourneyType,
  updateJourneyType,
  deleteJourneyType,
} from '@/api/registry'
import type { JourneyType } from '@/types'
import Spinner from '@/components/ui/Spinner'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtSla(ms: number | undefined | null): string {
  if (!ms) return '—'
  if (ms < 60_000) return `${ms} ms`
  if (ms < 3_600_000) return `${(ms / 60_000).toFixed(1)} min`
  return `${(ms / 3_600_000).toFixed(1)} h`
}

// ── Inline form for create / edit ─────────────────────────────────────────────

interface FormState {
  journey_type_id: string
  description:     string
  sla_ms:          string   // stored as string for input, parsed on submit
}

const BLANK: FormState = { journey_type_id: '', description: '', sla_ms: '' }

interface JourneyTypeFormProps {
  initial?: FormState
  locked?: boolean              // journey_type_id cannot be changed when editing
  onSave:   (f: FormState) => Promise<void>
  onCancel: () => void
  saving:   boolean
}

function JourneyTypeForm({ initial = BLANK, locked = false, onSave, onCancel, saving }: JourneyTypeFormProps) {
  const { t } = useTranslation('configRecursos')
  const [form, setForm] = useState<FormState>(initial)
  const [error, setError] = useState('')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!form.journey_type_id.trim()) {
      setError(t('journeyTypes.validation.idRequired'))
      return
    }
    if (!/^[a-z0-9_]+$/.test(form.journey_type_id.trim())) {
      setError(t('journeyTypes.validation.idInvalid'))
      return
    }
    if (form.sla_ms && isNaN(Number(form.sla_ms))) {
      setError(t('journeyTypes.validation.slaInvalid'))
      return
    }
    setError('')
    await onSave(form)
  }

  return (
    <form onSubmit={handleSubmit} className="bg-surface border border-border rounded-lg p-4 space-y-3">
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div>
          <label className="block text-xs font-semibold text-muted mb-1">
            {t('journeyTypes.fields.id')} *
          </label>
          <input
            type="text"
            value={form.journey_type_id}
            onChange={e => setForm(f => ({ ...f, journey_type_id: e.target.value }))}
            disabled={locked || saving}
            placeholder={t('journeyTypes.fields.idPlaceholder')}
            className="w-full border border-border rounded px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary disabled:opacity-50 font-mono"
          />
        </div>
        <div>
          <label className="block text-xs font-semibold text-muted mb-1">
            {t('journeyTypes.fields.description')}
          </label>
          <input
            type="text"
            value={form.description}
            onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
            disabled={saving}
            placeholder={t('journeyTypes.fields.descPlaceholder')}
            className="w-full border border-border rounded px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary disabled:opacity-50"
          />
        </div>
        <div>
          <label className="block text-xs font-semibold text-muted mb-1">
            {t('journeyTypes.fields.slaMs')}
          </label>
          <input
            type="number"
            min={0}
            value={form.sla_ms}
            onChange={e => setForm(f => ({ ...f, sla_ms: e.target.value }))}
            disabled={saving}
            placeholder={t('journeyTypes.fields.slaMsPlaceholder')}
            className="w-full border border-border rounded px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary disabled:opacity-50"
          />
        </div>
      </div>

      {error && (
        <p className="text-xs text-error">{error}</p>
      )}

      <div className="flex gap-2 justify-end">
        <button
          type="button"
          onClick={onCancel}
          disabled={saving}
          className="px-3 py-1.5 text-sm rounded border border-border hover:bg-hover transition-colors disabled:opacity-50"
        >
          {t('journeyTypes.buttons.cancel')}
        </button>
        <button
          type="submit"
          disabled={saving}
          className="px-3 py-1.5 text-sm rounded bg-primary text-white hover:bg-primary-dark transition-colors disabled:opacity-50"
        >
          {saving ? t('journeyTypes.buttons.saving') : t('journeyTypes.buttons.save')}
        </button>
      </div>
    </form>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

const JourneyTypesPage: React.FC = () => {
  const { t } = useTranslation('configRecursos')
  const { session } = useAuth()

  const [items, setItems]       = useState<JourneyType[]>([])
  const [loading, setLoading]   = useState(true)
  const [loadErr, setLoadErr]   = useState('')
  const [creating, setCreating] = useState(false)
  const [saving, setSaving]     = useState(false)
  const [editId, setEditId]     = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)

  const tenantId = session?.tenantId ?? ''

  // ── Load ──────────────────────────────────────────────────────────────────

  const load = useCallback(async () => {
    if (!tenantId) return
    setLoading(true)
    setLoadErr('')
    try {
      const data = await listJourneyTypes(tenantId)
      setItems(data.sort((a, b) => a.journey_type_id.localeCompare(b.journey_type_id)))
    } catch {
      setLoadErr(t('journeyTypes.loadFailed'))
    } finally {
      setLoading(false)
    }
  }, [tenantId, t])

  useEffect(() => { void load() }, [load])

  // ── Create ────────────────────────────────────────────────────────────────

  const handleCreate = async (form: FormState) => {
    setSaving(true)
    try {
      const created = await createJourneyType(
        {
          journey_type_id: form.journey_type_id.trim(),
          description:     form.description.trim() || undefined,
          sla_ms:          form.sla_ms ? Number(form.sla_ms) : undefined,
        },
        tenantId,
      )
      setItems(prev =>
        [...prev, created].sort((a, b) => a.journey_type_id.localeCompare(b.journey_type_id)),
      )
      setCreating(false)
    } finally {
      setSaving(false)
    }
  }

  // ── Edit ──────────────────────────────────────────────────────────────────

  const handleEdit = async (form: FormState) => {
    if (!editId) return
    setSaving(true)
    try {
      const updated = await updateJourneyType(
        editId,
        {
          description: form.description.trim() || undefined,
          sla_ms:      form.sla_ms ? Number(form.sla_ms) : null,
        },
        tenantId,
      )
      setItems(prev => prev.map(jt => jt.journey_type_id === editId ? updated : jt))
      setEditId(null)
    } finally {
      setSaving(false)
    }
  }

  // ── Delete ────────────────────────────────────────────────────────────────

  const handleDelete = async (id: string) => {
    setDeleting(true)
    try {
      await deleteJourneyType(id, tenantId)
      setItems(prev => prev.filter(jt => jt.journey_type_id !== id))
      setConfirmDelete(null)
    } finally {
      setDeleting(false)
    }
  }

  // ── Render ────────────────────────────────────────────────────────────────

  if (loading) {
    return <div className="flex justify-center py-12"><Spinner /></div>
  }

  if (loadErr) {
    return <p className="text-sm text-error py-4">{loadErr}</p>
  }

  const editItem = editId ? items.find(jt => jt.journey_type_id === editId) : undefined

  return (
    <div className="space-y-4">
      {/* Header row */}
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted">{t('journeyTypes.hint')}</p>
        {!creating && (
          <button
            onClick={() => { setCreating(true); setEditId(null) }}
            className="px-3 py-1.5 text-sm rounded bg-primary text-white hover:bg-primary-dark transition-colors"
          >
            {t('journeyTypes.newButton')}
          </button>
        )}
      </div>

      {/* Create form */}
      {creating && (
        <JourneyTypeForm
          onSave={handleCreate}
          onCancel={() => setCreating(false)}
          saving={saving}
        />
      )}

      {/* Table */}
      {items.length === 0 && !creating ? (
        <p className="text-sm text-muted py-6 text-center">{t('journeyTypes.empty')}</p>
      ) : (
        <div className="border border-border rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-surface border-b border-border text-xs text-muted uppercase tracking-wide">
                <th className="px-4 py-2 text-left">{t('journeyTypes.columns.id')}</th>
                <th className="px-4 py-2 text-left">{t('journeyTypes.columns.description')}</th>
                <th className="px-4 py-2 text-right">{t('journeyTypes.columns.slaMs')}</th>
                <th className="px-4 py-2 text-right">{t('journeyTypes.columns.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {items.map(jt => (
                <React.Fragment key={jt.journey_type_id}>
                  <tr className="border-b border-border last:border-0 hover:bg-hover transition-colors">
                    <td className="px-4 py-2.5 font-mono font-semibold text-dark">
                      {jt.journey_type_id}
                    </td>
                    <td className="px-4 py-2.5 text-muted">
                      {jt.description || '—'}
                    </td>
                    <td className="px-4 py-2.5 text-right font-mono text-muted">
                      {fmtSla(jt.sla_ms)}
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      <div className="flex gap-2 justify-end">
                        <button
                          onClick={() => { setEditId(jt.journey_type_id); setCreating(false) }}
                          className="text-xs text-primary hover:underline"
                        >
                          {t('journeyTypes.buttons.edit')}
                        </button>
                        <button
                          onClick={() => setConfirmDelete(jt.journey_type_id)}
                          className="text-xs text-error hover:underline"
                        >
                          {t('journeyTypes.buttons.delete')}
                        </button>
                      </div>
                    </td>
                  </tr>

                  {/* Inline edit row */}
                  {editId === jt.journey_type_id && editItem && (
                    <tr>
                      <td colSpan={4} className="p-0">
                        <div className="px-4 py-3 bg-surface-alt border-b border-border">
                          <JourneyTypeForm
                            initial={{
                              journey_type_id: editItem.journey_type_id,
                              description:     editItem.description ?? '',
                              sla_ms:          editItem.sla_ms != null ? String(editItem.sla_ms) : '',
                            }}
                            locked
                            onSave={handleEdit}
                            onCancel={() => setEditId(null)}
                            saving={saving}
                          />
                        </div>
                      </td>
                    </tr>
                  )}

                  {/* Confirm delete row */}
                  {confirmDelete === jt.journey_type_id && (
                    <tr>
                      <td colSpan={4} className="p-0">
                        <div className="px-4 py-3 bg-warning-light border-b border-border flex items-center gap-3">
                          <span className="text-sm text-warning-text">
                            {t('journeyTypes.deleteConfirm', { id: jt.journey_type_id })}
                          </span>
                          <button
                            onClick={() => handleDelete(jt.journey_type_id)}
                            disabled={deleting}
                            className="px-3 py-1 text-sm rounded bg-red text-white hover:bg-red-text disabled:opacity-50"
                          >
                            {deleting ? t('journeyTypes.buttons.deleting') : t('journeyTypes.buttons.confirmDelete')}
                          </button>
                          <button
                            onClick={() => setConfirmDelete(null)}
                            disabled={deleting}
                            className="px-3 py-1 text-sm rounded border border-border hover:bg-hover disabled:opacity-50"
                          >
                            {t('journeyTypes.buttons.cancel')}
                          </button>
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

export default JourneyTypesPage
