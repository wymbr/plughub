/**
 * ChannelEndpointList.tsx
 * CRUD list of ChannelEndpoint records for a given channel type.
 *
 * Each row shows: identifier, pool, display_name, active status.
 * Inline form for create / edit (no modal).
 */
import React, { useState, useEffect, useCallback } from 'react'
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
  CreateChannelEndpointInput,
  Pool,
} from '@/types'

// ── Helpers ────────────────────────────────────────────────────────────────────

const IDENTIFIER_HINT: Record<ChannelEndpointChannel, string> = {
  webchat:  'URL slug, e.g. "support" → {host}/webchat/support',
  whatsapp: 'E.164 number, e.g. +5511999999999',
  voice:    'DID / E.164, e.g. +5511000000',
  sms:      'Short code or long code, e.g. 55119',
  email:    'Address, e.g. support@company.com',
  webhook:  'URL slug, e.g. "salesforce" → {host}/channel/webhook/salesforce',
}

const IDENTIFIER_PLACEHOLDER: Record<ChannelEndpointChannel, string> = {
  webchat:  'support',
  whatsapp: '+5511999999999',
  voice:    '+5511000000',
  sms:      '55119',
  email:    'support@company.com',
  webhook:  'salesforce',
}

// ── Empty form state ───────────────────────────────────────────────────────────

const emptyForm = (): Omit<CreateChannelEndpointInput, 'channel'> => ({
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
  const { tenantId } = useAuth()

  const [endpoints, setEndpoints] = useState<ChannelEndpoint[]>([])
  const [pools,     setPools]     = useState<Pool[]>([])
  const [loading,   setLoading]   = useState(true)
  const [error,     setError]     = useState<string | null>(null)

  // Form state — null = closed, 'new' = creating, string id = editing
  const [formMode, setFormMode]   = useState<null | 'new' | string>(null)
  const [form,     setForm]       = useState(emptyForm())
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
    if (!form.identifier.trim()) { setFormErr('Identifier is required');   return }
    if (!form.pool_id)           { setFormErr('Pool is required');          return }
    if (!form.display_name.trim()) { setFormErr('Display name is required'); return }

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
    if (!confirm('Delete this endpoint?')) return
    try {
      await deleteChannelEndpoint(id, tenantId)
      await load()
    } catch (e) {
      setError(String(e))
    }
  }

  // ── Pool label helper ────────────────────────────────────────────────────────

  function poolLabel(poolId: string): string {
    const p = pools.find(x => x.pool_id === poolId)
    return p ? `${p.pool_id}` : poolId
  }

  // ── Render ───────────────────────────────────────────────────────────────────

  if (loading) return <p className="text-sm text-gray-400 py-4">Loading…</p>
  if (error)   return <p className="text-sm text-red-600 py-4">⚠ {error}</p>

  return (
    <div className="space-y-4">

      {/* Header row */}
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs text-gray-500">{IDENTIFIER_HINT[channel]}</p>
        </div>
        <button
          onClick={openNew}
          disabled={formMode !== null}
          className="px-3 py-1.5 rounded text-xs font-semibold bg-primary text-white hover:bg-blue-800 disabled:opacity-40 transition-colors"
        >
          + Add endpoint
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
        <p className="text-sm text-gray-400 py-2">No endpoints configured. Click "+ Add endpoint" to create one.</p>
      ) : (
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="text-left text-xs text-gray-500 border-b border-gray-200">
              <th className="py-2 pr-4 font-medium">Identifier</th>
              <th className="py-2 pr-4 font-medium">Pool</th>
              <th className="py-2 pr-4 font-medium">Display name</th>
              <th className="py-2 pr-4 font-medium">Status</th>
              <th className="py-2 font-medium"></th>
            </tr>
          </thead>
          <tbody>
            {endpoints.map(ep => (
              <React.Fragment key={ep.id}>
                <tr className="border-b border-gray-100 hover:bg-gray-50">
                  <td className="py-2.5 pr-4 font-mono text-xs text-gray-700">{ep.identifier}</td>
                  <td className="py-2.5 pr-4 text-xs text-gray-600">{poolLabel(ep.pool_id)}</td>
                  <td className="py-2.5 pr-4 text-xs">{ep.display_name}</td>
                  <td className="py-2.5 pr-4">
                    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                      ep.active
                        ? 'bg-green-100 text-green-700'
                        : 'bg-gray-100 text-gray-500'
                    }`}>
                      {ep.active ? 'active' : 'inactive'}
                    </span>
                  </td>
                  <td className="py-2.5 flex gap-2 justify-end">
                    <button
                      onClick={() => openEdit(ep)}
                      disabled={formMode !== null}
                      className="text-xs text-secondary hover:text-primary disabled:opacity-40"
                    >
                      Edit
                    </button>
                    <button
                      onClick={() => handleDelete(ep.id)}
                      disabled={formMode !== null}
                      className="text-xs text-red-500 hover:text-red-700 disabled:opacity-40"
                    >
                      Delete
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

interface FormState {
  identifier:   string
  pool_id:      string
  display_name: string
  active:       boolean
}

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
  const inp = 'text-xs border border-gray-300 rounded px-2 py-1.5 focus:outline-none focus:border-primary w-full'

  return (
    <div className="bg-gray-50 border border-gray-200 rounded-lg p-4 space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="text-xs font-medium text-gray-700 block mb-1">Identifier</label>
          <input
            className={inp + (identifierReadonly ? ' bg-gray-100 text-gray-500' : '')}
            value={form.identifier}
            placeholder={placeholder}
            readOnly={identifierReadonly}
            onChange={e => setForm(p => ({ ...p, identifier: e.target.value }))}
          />
        </div>
        <div>
          <label className="text-xs font-medium text-gray-700 block mb-1">Pool</label>
          <select
            className={inp}
            value={form.pool_id}
            onChange={e => setForm(p => ({ ...p, pool_id: e.target.value }))}
          >
            <option value="">— select pool —</option>
            {pools.map(p => (
              <option key={p.pool_id} value={p.pool_id}>{p.pool_id}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="text-xs font-medium text-gray-700 block mb-1">Display name</label>
          <input
            className={inp}
            value={form.display_name}
            placeholder="e.g. Technical Support"
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
            Active
          </label>
        </div>
      </div>

      {error && <p className="text-xs text-red-600">{error}</p>}

      <div className="flex gap-2">
        <button
          onClick={onSave}
          disabled={saving}
          className="px-3 py-1.5 rounded text-xs font-semibold bg-primary text-white disabled:opacity-40 hover:bg-blue-800 transition-colors"
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
        <button
          onClick={onCancel}
          className="px-3 py-1.5 rounded text-xs border border-gray-300 text-gray-600 hover:text-gray-900 transition-colors"
        >
          Cancel
        </button>
      </div>
    </div>
  )
}

export default ChannelEndpointList
