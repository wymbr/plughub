/**
 * ChannelEndpointList.tsx
 * CRUD list of ChannelEndpoint records for a given channel type.
 *
 * Each row shows: identifier, pool, display_name, active status.
 * Inline form for create / edit (no modal).
 */
import React, { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import {
  listChannelEndpoints,
  createChannelEndpoint,
  updateChannelEndpoint,
  deleteChannelEndpoint,
  listPools,
} from '@/api/registry'
import type {
  ChannelEndpoint,
  ChannelEndpointChannel,
  Pool,
} from '@/types'

// ── Identifier placeholders (technical format, not translated) ─────────────────

const IDENTIFIER_PLACEHOLDER: Record<ChannelEndpointChannel, string> = {
  webchat:  'support',
  whatsapp: '+5511999999999',
  voice:    '+5511000000',
  sms:      '55119',
  email:    'support@company.com',
  webhook:  'salesforce',
}

// ── Form state type ────────────────────────────────────────────────────────────

interface FormState {
  identifier:   string
  pool_id:      string
  display_name: string
  active:       boolean
}

const emptyForm = (): FormState => ({
  identifier:   '',
  pool_id:      '',
  display_name: '',
  active:       true,
})

// ── Component ─────────────────────────────────────────────────────────────────

interface Props {
  channel: ChannelEndpointChannel
}

export const ChannelEndpointList: React.FC<Props> = ({ channel }) => {
  const { t } = useTranslation('channels')
  const { tenantId } = useAuth()

  const [endpoints, setEndpoints] = useState<ChannelEndpoint[]>([])
  const [pools,     setPools]     = useState<Pool[]>([])
  const [loading,   setLoading]   = useState(true)
  const [error,     setError]     = useState<string | null>(null)

  const [formMode, setFormMode]   = useState<null | 'new' | string>(null)
  const [form,     setForm]       = useState<FormState>(emptyForm())
  const [saving,   setSaving]     = useState(false)
  const [formErr,  setFormErr]    = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!tenantId) return
    setLoading(true); setError(null)
    try {
      const [eps, ps] = await Promise.all([
        listChannelEndpoints(tenantId, channel),
        listPools(tenantId).then(r => r.items ?? []),
      ])
      setEndpoints(eps)
      setPools(ps)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [tenantId, channel])

  useEffect(() => { load() }, [load])

  // ── Form handlers ────────────────────────────────────────────────────────────

  function openNew() {
    setForm(emptyForm())
    setFormErr(null)
    setFormMode('new')
  }

  function openEdit(ep: ChannelEndpoint) {
    setForm({
      identifier:   ep.identifier,
      pool_id:      ep.pool_id,
      display_name: ep.display_name,
      active:       ep.active,
    })
    setFormErr(null)
    setFormMode(ep.id)
  }

  function closeForm() {
    setFormMode(null)
    setFormErr(null)
  }

  async function handleSave() {
    if (!tenantId) return
    if (!form.identifier.trim())   { setFormErr(t('errors.identifierRequired'));   return }
    if (!form.pool_id)             { setFormErr(t('errors.poolRequired'));          return }
    if (!form.display_name.trim()) { setFormErr(t('errors.displayNameRequired'));   return }

    setSaving(true); setFormErr(null)
    try {
      if (formMode === 'new') {
        await createChannelEndpoint({ ...form, channel }, tenantId)
      } else if (formMode) {
        await updateChannelEndpoint(formMode, {
          pool_id:      form.pool_id,
          display_name: form.display_name,
          active:       form.active,
        }, tenantId)
      }
      closeForm()
      await load()
    } catch (e) {
      setFormErr(String(e))
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete(id: string) {
    if (!tenantId) return
    if (!confirm(t('endpoint.deleteConfirm'))) return
    try {
      await deleteChannelEndpoint(id, tenantId)
      await load()
    } catch (e) {
      setError(String(e))
    }
  }

  function poolLabel(poolId: string): string {
    const p = pools.find(x => x.pool_id === poolId)
    return p ? p.pool_id : poolId
  }

  // ── Render ───────────────────────────────────────────────────────────────────

  if (loading) return <p className="text-sm text-muted-light py-4">{t('loading')}</p>
  if (error)   return <p className="text-sm text-red-text py-4">⚠ {error}</p>

  const identifierHint = t(`identifierHints.${channel}`, t('form.identifierHintFallback'))

  return (
    <div className="space-y-4">

      {/* Header row */}
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs text-muted">{identifierHint}</p>
        </div>
        <button
          onClick={openNew}
          disabled={formMode !== null}
          className="px-3 py-1.5 rounded text-xs font-semibold bg-primary text-white hover:bg-primary-dark disabled:opacity-40 transition-colors"
        >
          {t('endpoint.add')}
        </button>
      </div>

      {/* Create form */}
      {formMode === 'new' && (
        <EndpointForm
          form={form}
          setForm={setForm}
          pools={pools}
          channel={channel}
          placeholder={IDENTIFIER_PLACEHOLDER[channel]}
          identifierReadonly={false}
          saving={saving}
          error={formErr}
          onSave={handleSave}
          onCancel={closeForm}
        />
      )}

      {/* List */}
      {endpoints.length === 0 && formMode !== 'new' ? (
        <p className="text-sm text-muted-light py-2">{t('endpoint.noEndpointsStandalone')}</p>
      ) : (
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="text-left text-xs text-muted border-b border-border">
              <th className="py-2 pr-4 font-medium">{t('endpoint.colIdentifier')}</th>
              <th className="py-2 pr-4 font-medium">{t('endpoint.colPool')}</th>
              <th className="py-2 pr-4 font-medium">{t('endpoint.colDisplayName')}</th>
              <th className="py-2 pr-4 font-medium">{t('endpoint.colStatus')}</th>
              <th className="py-2 font-medium"></th>
            </tr>
          </thead>
          <tbody>
            {endpoints.map(ep => (
              <React.Fragment key={ep.id}>
                <tr className="border-b border-border hover:bg-surface-muted">
                  <td className="py-2.5 pr-4 font-mono text-xs text-dark">{ep.identifier}</td>
                  <td className="py-2.5 pr-4 text-xs text-muted">{poolLabel(ep.pool_id)}</td>
                  <td className="py-2.5 pr-4 text-xs">{ep.display_name}</td>
                  <td className="py-2.5 pr-4">
                    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                      ep.active
                        ? 'bg-green-light text-green-text'
                        : 'bg-surface-alt text-muted'
                    }`}>
                      {ep.active ? t('status.active') : t('status.inactive')}
                    </span>
                  </td>
                  <td className="py-2.5 flex gap-2 justify-end">
                    <button
                      onClick={() => openEdit(ep)}
                      disabled={formMode !== null}
                      className="text-xs text-secondary hover:text-primary disabled:opacity-40"
                    >
                      {t('actions.edit')}
                    </button>
                    <button
                      onClick={() => handleDelete(ep.id)}
                      disabled={formMode !== null}
                      className="text-xs text-red hover:text-red-text disabled:opacity-40"
                    >
                      {t('actions.delete')}
                    </button>
                  </td>
                </tr>

                {/* Inline edit form */}
                {formMode === ep.id && (
                  <tr>
                    <td colSpan={5} className="pb-3 pt-1">
                      <EndpointForm
                        form={form}
                        setForm={setForm}
                        pools={pools}
                        channel={channel}
                        placeholder={IDENTIFIER_PLACEHOLDER[channel]}
                        identifierReadonly={true}
                        saving={saving}
                        error={formErr}
                        onSave={handleSave}
                        onCancel={closeForm}
                      />
                    </td>
                  </tr>
                )}
              </React.Fragment>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

// ── EndpointForm ───────────────────────────────────────────────────────────────

interface FormProps {
  form:               FormState
  setForm:            React.Dispatch<React.SetStateAction<FormState>>
  pools:              Pool[]
  channel:            ChannelEndpointChannel
  placeholder:        string
  identifierReadonly: boolean
  saving:             boolean
  error:              string | null
  onSave:             () => void
  onCancel:           () => void
}

function EndpointForm({
  form, setForm, pools, placeholder, identifierReadonly,
  saving, error, onSave, onCancel,
}: FormProps) {
  const { t } = useTranslation('channels')
  const inp = 'text-xs border border-border-strong rounded px-2 py-1.5 focus:outline-none focus:border-primary w-full'

  return (
    <div className="bg-surface-muted border border-border rounded-lg p-4 space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="text-xs font-medium text-dark block mb-1">{t('form.identifier')}</label>
          <input
            className={inp + (identifierReadonly ? ' bg-surface-alt text-muted' : '')}
            value={form.identifier}
            placeholder={placeholder}
            readOnly={identifierReadonly}
            onChange={e => setForm(p => ({ ...p, identifier: e.target.value }))}
          />
        </div>
        <div>
          <label className="text-xs font-medium text-dark block mb-1">{t('form.pool')}</label>
          <select
            className={inp}
            value={form.pool_id}
            onChange={e => setForm(p => ({ ...p, pool_id: e.target.value }))}
          >
            <option value="">{t('form.selectPool')}</option>
            {pools.map(p => (
              <option key={p.pool_id} value={p.pool_id}>{p.pool_id}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="text-xs font-medium text-dark block mb-1">{t('form.displayName')}</label>
          <input
            className={inp}
            value={form.display_name}
            placeholder={t('form.displayNamePlaceholder')}
            onChange={e => setForm(p => ({ ...p, display_name: e.target.value }))}
          />
        </div>
        <div className="flex items-end pb-1">
          <label className="flex items-center gap-2 text-xs cursor-pointer">
            <input
              type="checkbox"
              checked={form.active}
              onChange={e => setForm(p => ({ ...p, active: e.target.checked }))}
            />
            {t('form.active')}
          </label>
        </div>
      </div>

      {error && <p className="text-xs text-red-text">{error}</p>}

      <div className="flex gap-2">
        <button
          onClick={onSave}
          disabled={saving}
          className="px-3 py-1.5 rounded text-xs font-semibold bg-primary text-white disabled:opacity-40 hover:bg-primary-dark transition-colors"
        >
          {saving ? t('actions.saving') : t('actions.save')}
        </button>
        <button
          onClick={onCancel}
          className="px-3 py-1.5 rounded text-xs border border-border-strong text-muted hover:text-dark transition-colors"
        >
          {t('actions.cancel')}
        </button>
      </div>
    </div>
  )
}

export default ChannelEndpointList
