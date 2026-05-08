/**
 * ChannelAccountCard.tsx
 * Shows one GatewayConfig (integration account) as a card with its
 * child ChannelEndpoints listed below it.
 *
 * Layout:
 *   ┌─ Account: "WhatsApp Business — Produção" [active] ──────────┐
 *   │  [Edit credentials]                                          │
 *   │                                                              │
 *   │  Endpoints (phone numbers / slugs → pools):                 │
 *   │  ┌──────────────────┬──────────────────┬────────┬────────┐  │
 *   │  │ +5511999999999   │ Suporte          │ active │ pool_x │  │
 *   │  │ +5511888888888   │ Vendas           │ active │ pool_y │  │
 *   │  └──────────────────┴──────────────────┴────────┴────────┘  │
 *   │  [+ Add endpoint]                                            │
 *   └──────────────────────────────────────────────────────────────┘
 */
import React, { useState, useEffect, useCallback } from 'react'
import { useAuth } from '@/auth/useAuth'
import { CHANNEL_META } from './channel-meta'
import type {
  GatewayConfig,
  ChannelEndpoint,
  Pool,
  ChannelType,
} from '@/types'
import * as registryApi from '@/api/registry'

// ── Helpers ────────────────────────────────────────────────────────────────────

const inputCls =
  'w-full px-3 py-1.5 text-xs border border-gray-300 rounded-md focus:outline-none focus:border-primary bg-white placeholder-gray-400'

function FieldLabel({ children }: { children: React.ReactNode }) {
  return <label className="block text-xs font-medium text-gray-700 mb-1">{children}</label>
}

function Toggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      style={{ cursor: 'pointer', padding: 0, border: 'none', background: 'none', lineHeight: 0 }}
    >
      <span style={{
        position: 'relative', display: 'inline-block',
        width: 36, height: 20, borderRadius: 9999,
        backgroundColor: checked ? '#1B4F8A' : '#D1D5DB',
        transition: 'background-color 200ms', overflow: 'hidden',
      }}>
        <span style={{
          position: 'absolute', top: 2,
          left: checked ? 18 : 2,
          width: 16, height: 16, borderRadius: 9999,
          backgroundColor: 'white',
          transition: 'left 200ms',
        }} />
      </span>
    </button>
  )
}

// ── Identifier hints ───────────────────────────────────────────────────────────

const IDENTIFIER_HINT: Record<string, string> = {
  webchat:  'URL slug, e.g. "support" → {host}/webchat/support',
  whatsapp: 'E.164 number, e.g. +5511999999999',
  voice:    'DID / E.164, e.g. +5511000000',
  sms:      'Short code or long code, e.g. 55119',
  email:    'Address, e.g. support@company.com',
  webhook:  'URL slug, e.g. "salesforce" → {host}/channel/webhook/salesforce',
}

const IDENTIFIER_PLACEHOLDER: Record<string, string> = {
  webchat:  'support',
  whatsapp: '+5511999999999',
  voice:    '+5511000000',
  sms:      '55119',
  email:    'support@company.com',
  webhook:  'salesforce',
}

// ── ChannelAccountCard ─────────────────────────────────────────────────────────

interface Props {
  config:    GatewayConfig
  pools:     Pool[]
  onUpdated: () => void  // notify parent to reload
  onDeleted: () => void
}

const ChannelAccountCard: React.FC<Props> = ({ config, pools, onUpdated, onDeleted }) => {
  const { tenantId } = useAuth()
  const meta = CHANNEL_META[config.channel]

  const [expanded,   setExpanded]   = useState(true)
  const [endpoints,  setEndpoints]  = useState<ChannelEndpoint[]>([])
  const [epLoading,  setEpLoading]  = useState(true)

  // Credentials edit state
  const [editingCreds,   setEditingCreds]   = useState(false)
  const [displayName,    setDisplayName]    = useState(config.display_name)
  const [active,         setActive]         = useState(config.active)
  const [newCreds,       setNewCreds]       = useState<Record<string, string>>({})
  const [settings,       setSettings]       = useState<Record<string, string>>(
    Object.fromEntries(Object.entries(config.settings ?? {}).map(([k, v]) => [k, String(v)]))
  )
  const [saving,         setSaving]         = useState(false)
  const [confirmDel,     setConfirmDel]     = useState(false)
  const [deleting,       setDeleting]       = useState(false)
  const [credError,      setCredError]      = useState<string | null>(null)

  // Endpoint form state
  const [addingEp,   setAddingEp]   = useState(false)
  const [editingEp,  setEditingEp]  = useState<string | null>(null)  // endpoint id being edited
  const [epError,    setEpError]    = useState<string | null>(null)

  const loadEndpoints = useCallback(async () => {
    if (!tenantId) return
    setEpLoading(true)
    try {
      const result = await registryApi.listChannelEndpoints(tenantId, config.channel)
      // Filter to only show endpoints linked to this GatewayConfig
      setEndpoints(result.filter(ep => ep.gateway_config_id === config.id))
    } catch {
      setEndpoints([])
    } finally {
      setEpLoading(false)
    }
  }, [tenantId, config.id, config.channel])

  useEffect(() => { loadEndpoints() }, [loadEndpoints])

  // ── Credential save ────────────────────────────────────────────────────────

  async function handleSaveCreds() {
    if (!tenantId) return
    setSaving(true); setCredError(null)
    try {
      const updates: Record<string, unknown> = {
        display_name: displayName.trim(),
        active,
        settings: Object.fromEntries(Object.entries(settings).filter(([, v]) => v !== '')),
      }
      const filteredCreds = Object.fromEntries(Object.entries(newCreds).filter(([, v]) => v !== ''))
      if (Object.keys(filteredCreds).length > 0) updates['credentials'] = filteredCreds
      await registryApi.updateChannel(config.id, updates, tenantId)
      setEditingCreds(false); setNewCreds({})
      onUpdated()
    } catch (e) {
      setCredError(String(e))
    } finally {
      setSaving(false)
    }
  }

  async function handleDeleteConfig() {
    if (!tenantId) return
    setDeleting(true)
    try {
      await registryApi.deleteChannel(config.id, tenantId)
      onDeleted()
    } catch (e) {
      setCredError(String(e))
      setDeleting(false); setConfirmDel(false)
    }
  }

  // ── Endpoint ops ───────────────────────────────────────────────────────────

  async function handleDeleteEp(id: string) {
    if (!tenantId || !confirm('Delete this endpoint?')) return
    try {
      await registryApi.deleteChannelEndpoint(id, tenantId)
      loadEndpoints()
    } catch (e) {
      setEpError(String(e))
    }
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="border border-gray-200 rounded-lg overflow-hidden">

      {/* ── Card header ── */}
      <div className="flex items-center justify-between px-4 py-3 bg-gray-50 border-b border-gray-200">
        <div className="flex items-center gap-3">
          <span className={`w-2.5 h-2.5 rounded-full ${config.active ? 'bg-green-500' : 'bg-gray-300'}`} />
          <div>
            <span className="text-sm font-semibold text-gray-800">{config.display_name}</span>
            <span className="ml-2 text-xs text-gray-400">
              {meta?.icon} {meta?.label ?? config.channel} · ID {config.id.slice(0, 8)}…
            </span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
            config.active ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'
          }`}>
            {config.active ? 'active' : 'inactive'}
          </span>
          <button
            onClick={() => { setExpanded(v => !v); setEditingCreds(false) }}
            className="text-gray-400 hover:text-gray-700 text-xs px-2"
          >
            {expanded ? '▲' : '▼'}
          </button>
        </div>
      </div>

      {/* ── Card body ── */}
      {expanded && (
        <div className="p-4 space-y-4">

          {/* Credentials section */}
          {!editingCreds ? (
            <div className="flex items-start justify-between">
              <div>
                <p className="text-xs font-medium text-gray-600 mb-1">Credentials</p>
                <div className="flex flex-wrap gap-2">
                  {meta && meta.fields.length > 0 ? meta.fields.map(f => (
                    <span key={f.key} className="text-xs text-gray-500 bg-gray-100 px-2 py-0.5 rounded font-mono">
                      {f.label}: {config.credentials?.[f.key] ? '••••••' : '—'}
                    </span>
                  )) : (
                    <span className="text-xs text-gray-400">No credential fields for this channel.</span>
                  )}
                </div>
                {meta && meta.settingFields.length > 0 && (
                  <div className="flex flex-wrap gap-2 mt-1">
                    {meta.settingFields.map(f => {
                      const val = (config.settings as Record<string, unknown>)?.[f.key]
                      return val ? (
                        <span key={f.key} className="text-xs text-gray-500 bg-blue-50 px-2 py-0.5 rounded">
                          {f.label}: {String(val)}
                        </span>
                      ) : null
                    })}
                  </div>
                )}
              </div>
              <div className="flex items-center gap-2 shrink-0 ml-4">
                <button
                  onClick={() => setEditingCreds(true)}
                  className="text-xs text-secondary hover:text-primary font-medium"
                >
                  Edit credentials
                </button>
                {!confirmDel ? (
                  <button
                    onClick={() => setConfirmDel(true)}
                    className="text-xs text-red-400 hover:text-red-600"
                  >
                    Delete
                  </button>
                ) : (
                  <>
                    <button
                      onClick={handleDeleteConfig}
                      disabled={deleting}
                      className="text-xs text-red-600 font-semibold hover:text-red-800 disabled:opacity-50"
                    >
                      {deleting ? 'Deleting…' : 'Confirm delete'}
                    </button>
                    <button
                      onClick={() => setConfirmDel(false)}
                      className="text-xs text-gray-500"
                    >
                      Cancel
                    </button>
                  </>
                )}
              </div>
            </div>
          ) : (
            /* Credential edit form */
            <div className="bg-blue-50/40 border border-primary/20 rounded-lg p-4 space-y-3">
              <p className="text-xs font-semibold text-primary">Edit credentials</p>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <FieldLabel>Display name</FieldLabel>
                  <input
                    className={inputCls}
                    value={displayName}
                    onChange={e => setDisplayName(e.target.value)}
                  />
                </div>
                <div className="flex items-center gap-2 pt-4">
                  <FieldLabel>Active</FieldLabel>
                  <Toggle checked={active} onChange={setActive} />
                </div>
              </div>

              {meta && meta.fields.length > 0 && (
                <div>
                  <p className="text-xs font-medium text-gray-600 mb-2">API credentials <span className="text-gray-400">(leave blank to keep current)</span></p>
                  <div className="grid grid-cols-2 gap-3">
                    {meta.fields.map(f => (
                      <div key={f.key}>
                        <FieldLabel>{f.label}</FieldLabel>
                        <input
                          type={f.sensitive ? 'password' : 'text'}
                          className={`${inputCls} font-mono`}
                          value={newCreds[f.key] ?? ''}
                          onChange={e => setNewCreds(p => ({ ...p, [f.key]: e.target.value }))}
                          placeholder="New value (optional)"
                          autoComplete="off"
                        />
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {meta && meta.settingFields.length > 0 && (
                <div>
                  <p className="text-xs font-medium text-gray-600 mb-2">Settings</p>
                  <div className="grid grid-cols-2 gap-3">
                    {meta.settingFields.map(f => (
                      <div key={f.key}>
                        <FieldLabel>{f.label}</FieldLabel>
                        <input
                          type={f.type ?? 'text'}
                          className={inputCls}
                          value={settings[f.key] ?? ''}
                          onChange={e => setSettings(p => ({ ...p, [f.key]: e.target.value }))}
                          placeholder={f.placeholder}
                        />
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {credError && <p className="text-xs text-red-600">{credError}</p>}

              <div className="flex gap-2">
                <button
                  onClick={handleSaveCreds}
                  disabled={saving}
                  className="px-3 py-1.5 rounded text-xs font-semibold bg-primary text-white disabled:opacity-40 hover:bg-blue-800 transition-colors"
                >
                  {saving ? 'Saving…' : 'Save credentials'}
                </button>
                <button
                  onClick={() => { setEditingCreds(false); setCredError(null) }}
                  className="px-3 py-1.5 rounded text-xs border border-gray-300 text-gray-600 hover:text-gray-900 transition-colors"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}

          {/* ── Endpoints section ── */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <p className="text-xs font-medium text-gray-600">
                Endpoints
                <span className="ml-1 text-gray-400">— {IDENTIFIER_HINT[config.channel] ?? 'identifiers routing to pools'}</span>
              </p>
              <button
                onClick={() => { setAddingEp(true); setEpError(null) }}
                disabled={addingEp}
                className="text-xs text-secondary hover:text-primary font-medium disabled:opacity-40"
              >
                + Add endpoint
              </button>
            </div>

            {/* Add endpoint form */}
            {addingEp && (
              <EndpointForm
                channel={config.channel}
                gatewayConfigId={config.id}
                pools={pools}
                tenantId={tenantId!}
                onSaved={() => { setAddingEp(false); loadEndpoints() }}
                onCancel={() => { setAddingEp(false); setEpError(null) }}
              />
            )}

            {/* Endpoint list */}
            {epLoading ? (
              <p className="text-xs text-gray-400 py-2">Loading…</p>
            ) : endpoints.length === 0 && !addingEp ? (
              <p className="text-xs text-gray-400 py-2 italic">
                No endpoints yet. Click "+ Add endpoint" to add a {meta?.label ?? config.channel} number or address.
              </p>
            ) : (
              <table className="w-full text-xs border-collapse">
                <thead>
                  <tr className="text-left text-gray-400 border-b border-gray-100">
                    <th className="py-1.5 pr-4 font-medium">Identifier</th>
                    <th className="py-1.5 pr-4 font-medium">Display name</th>
                    <th className="py-1.5 pr-4 font-medium">Pool</th>
                    <th className="py-1.5 pr-4 font-medium">Status</th>
                    <th className="py-1.5 font-medium"></th>
                  </tr>
                </thead>
                <tbody>
                  {endpoints.map(ep => (
                    <React.Fragment key={ep.id}>
                      <tr className="border-b border-gray-50 hover:bg-gray-50">
                        <td className="py-2 pr-4 font-mono text-gray-700">{ep.identifier}</td>
                        <td className="py-2 pr-4 text-gray-600">{ep.display_name}</td>
                        <td className="py-2 pr-4 text-gray-500">{ep.pool_id}</td>
                        <td className="py-2 pr-4">
                          <span className={`px-2 py-0.5 rounded-full font-medium ${
                            ep.active ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'
                          }`}>
                            {ep.active ? 'active' : 'inactive'}
                          </span>
                        </td>
                        <td className="py-2 flex gap-2 justify-end">
                          <button
                            onClick={() => setEditingEp(ep.id === editingEp ? null : ep.id)}
                            className="text-secondary hover:text-primary disabled:opacity-40"
                          >
                            {ep.id === editingEp ? 'Close' : 'Edit'}
                          </button>
                          <button
                            onClick={() => handleDeleteEp(ep.id)}
                            className="text-red-400 hover:text-red-600"
                          >
                            Delete
                          </button>
                        </td>
                      </tr>
                      {editingEp === ep.id && (
                        <tr>
                          <td colSpan={5} className="pb-3 pt-1">
                            <EndpointForm
                              channel={config.channel}
                              gatewayConfigId={config.id}
                              pools={pools}
                              tenantId={tenantId!}
                              existing={ep}
                              onSaved={() => { setEditingEp(null); loadEndpoints() }}
                              onCancel={() => { setEditingEp(null); setEpError(null) }}
                            />
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  ))}
                </tbody>
              </table>
            )}
            {epError && <p className="text-xs text-red-600 mt-1">{epError}</p>}
          </div>
        </div>
      )}
    </div>
  )
}

// ── EndpointForm — inline create / edit ───────────────────────────────────────

interface EpFormProps {
  channel:         string
  gatewayConfigId: string
  pools:           Pool[]
  tenantId:        string
  existing?:       ChannelEndpoint
  onSaved:         () => void
  onCancel:        () => void
}

const EndpointForm: React.FC<EpFormProps> = ({
  channel, gatewayConfigId, pools, tenantId, existing, onSaved, onCancel,
}) => {
  const isEdit = !!existing

  const [identifier,   setIdentifier]   = useState(existing?.identifier   ?? '')
  const [displayName,  setDisplayName]  = useState(existing?.display_name ?? '')
  const [poolId,       setPoolId]       = useState(existing?.pool_id       ?? '')
  const [active,       setActive]       = useState(existing?.active        ?? true)
  const [saving,       setSaving]       = useState(false)
  const [error,        setError]        = useState<string | null>(null)

  async function handleSave() {
    if (!identifier.trim())   { setError('Identifier is required');   return }
    if (!poolId)               { setError('Pool is required');         return }
    if (!displayName.trim())   { setError('Display name is required'); return }

    setSaving(true); setError(null)
    try {
      if (isEdit && existing) {
        await registryApi.updateChannelEndpoint(existing.id, {
          pool_id:            poolId,
          display_name:       displayName.trim(),
          active,
          gateway_config_id:  gatewayConfigId,
        }, tenantId)
      } else {
        await registryApi.createChannelEndpoint({
          channel:            channel as import('@/types').ChannelEndpointChannel,
          identifier:         identifier.trim(),
          pool_id:            poolId,
          display_name:       displayName.trim(),
          active,
          gateway_config_id:  gatewayConfigId,
        }, tenantId)
      }
      onSaved()
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="bg-gray-50 border border-gray-200 rounded-lg p-3 space-y-2">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <FieldLabel>Identifier</FieldLabel>
          <input
            className={`${inputCls} font-mono${isEdit ? ' bg-gray-100 text-gray-500' : ''}`}
            value={identifier}
            placeholder={IDENTIFIER_PLACEHOLDER[channel] ?? ''}
            readOnly={isEdit}
            onChange={e => setIdentifier(e.target.value)}
          />
        </div>
        <div>
          <FieldLabel>Pool</FieldLabel>
          <select
            className={inputCls}
            value={poolId}
            onChange={e => setPoolId(e.target.value)}
          >
            <option value="">— select pool —</option>
            {pools.map(p => (
              <option key={p.pool_id} value={p.pool_id}>{p.pool_id}</option>
            ))}
          </select>
        </div>
        <div>
          <FieldLabel>Display name</FieldLabel>
          <input
            className={inputCls}
            value={displayName}
            placeholder="e.g. Technical Support"
            onChange={e => setDisplayName(e.target.value)}
          />
        </div>
        <div className="flex items-end pb-1 gap-2">
          <FieldLabel>Active</FieldLabel>
          <Toggle checked={active} onChange={setActive} />
        </div>
      </div>

      {error && <p className="text-xs text-red-600">{error}</p>}

      <div className="flex gap-2">
        <button
          onClick={handleSave}
          disabled={saving}
          className="px-3 py-1.5 rounded text-xs font-semibold bg-primary text-white disabled:opacity-40 hover:bg-blue-800 transition-colors"
        >
          {saving ? 'Saving…' : isEdit ? 'Save changes' : 'Add endpoint'}
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

export default ChannelAccountCard
