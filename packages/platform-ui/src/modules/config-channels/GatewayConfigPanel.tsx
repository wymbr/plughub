/**
 * GatewayConfigPanel.tsx
 * Channel-scoped GatewayConfig CRUD — credentials and integration settings.
 *
 * Used as the "Credentials" sub-tab inside Config → Channels for each channel.
 * Shows all GatewayConfig records for the given channel type; supports
 * create, edit, and delete inline (no modal, no sidebar).
 *
 * Props:
 *   channel — channel type string (e.g. 'whatsapp', 'webchat')
 */
import React, { useState, useEffect, useCallback } from 'react'
import { useAuth } from '@/auth/useAuth'
import { CHANNEL_META, type ChannelMeta } from './channel-meta'
import type { GatewayConfig, ChannelType } from '@/types'
import * as registryApi from '@/api/registry'

// ── Helpers ────────────────────────────────────────────────────────────────────

const inputCls =
  'w-full px-3 py-1.5 text-xs border border-gray-300 rounded-md focus:outline-none focus:border-primary bg-white text-dark placeholder-gray-400'

function Section({ title, subtitle, children }: {
  title:     string
  subtitle?: string
  children:  React.ReactNode
}) {
  return (
    <div className="mb-5 pb-5 border-b border-gray-100 last:border-0">
      <div className="text-xs font-bold text-gray-500 uppercase tracking-wider mb-3">
        {title}
        {subtitle && <span className="font-normal ml-1 normal-case text-gray-400"> — {subtitle}</span>}
      </div>
      {children}
    </div>
  )
}

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

// ── GatewayConfigPanel ─────────────────────────────────────────────────────────

interface Props {
  /** Channel type string — accepts any value; panels gracefully handle unknown channels */
  channel: string
}

const GatewayConfigPanel: React.FC<Props> = ({ channel }) => {
  const { tenantId } = useAuth()
  const meta = CHANNEL_META[channel]

  const [configs,  setConfigs]  = useState<GatewayConfig[]>([])
  const [loading,  setLoading]  = useState(true)
  const [error,    setError]    = useState<string | null>(null)
  const [selected, setSelected] = useState<GatewayConfig | null>(null)
  const [creating, setCreating] = useState(false)

  const load = useCallback(async () => {
    if (!tenantId) return
    setLoading(true); setError(null)
    try {
      const result = await registryApi.listChannels(tenantId)
      setConfigs((result.items ?? []).filter((c: GatewayConfig) => c.channel === channel))
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [tenantId, channel])

  useEffect(() => { load() }, [load])

  // Reset selection when channel changes
  useEffect(() => {
    setSelected(null)
    setCreating(false)
  }, [channel])

  if (!meta) return <p className="text-sm text-gray-400 py-4">Channel not configured.</p>

  // Channels with no credentials (webhook just uses URL path config)
  if (meta.fields.length === 0 && meta.settingFields.length === 0) {
    return (
      <p className="text-sm text-gray-400 py-4">
        This channel type does not require API credentials.
      </p>
    )
  }

  if (loading) return <p className="text-sm text-gray-400 py-4">Loading…</p>
  if (error)   return <p className="text-sm text-red-600 py-4">⚠ {error}</p>

  return (
    <div className="space-y-4">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm font-semibold text-gray-900">
            {meta.icon} {meta.label} — API Credentials
          </p>
          <p className="text-xs text-gray-500 mt-0.5">
            Manage API keys and integration settings for {meta.label}.
            Credentials are stored encrypted and masked after saving.
          </p>
        </div>
        <button
          onClick={() => { setCreating(true); setSelected(null) }}
          disabled={creating}
          className="px-3 py-1.5 rounded text-xs font-semibold bg-primary text-white hover:bg-blue-800 disabled:opacity-40 transition-colors"
        >
          + Add integration
        </button>
      </div>

      {/* Create form */}
      {creating && (
        <ConfigForm
          channel={channel}
          meta={meta}
          onSaved={() => { setCreating(false); load() }}
          onCancel={() => setCreating(false)}
        />
      )}

      {/* Existing configs */}
      {configs.length === 0 && !creating ? (
        <div className="text-center py-8 border border-dashed border-gray-200 rounded-lg">
          <p className="text-sm text-gray-400">No integrations configured.</p>
          <p className="text-xs text-gray-400 mt-1">Click "+ Add integration" to set up your first {meta.label} integration.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {configs.map(cfg => (
            <div key={cfg.id} className="border border-gray-200 rounded-lg overflow-hidden">
              {/* Config row */}
              <div
                className="flex items-center justify-between px-4 py-3 cursor-pointer hover:bg-gray-50 transition-colors"
                onClick={() => setSelected(selected?.id === cfg.id ? null : cfg)}
              >
                <div className="flex items-center gap-3">
                  <span className={`w-2 h-2 rounded-full ${cfg.active ? 'bg-green-500' : 'bg-gray-300'}`} />
                  <div>
                    <span className="text-sm font-medium text-gray-800">{cfg.display_name}</span>
                    <span className="ml-2 text-xs text-gray-400">ID: {cfg.id.slice(0, 8)}…</span>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                    cfg.active ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'
                  }`}>
                    {cfg.active ? 'active' : 'inactive'}
                  </span>
                  <span className="text-gray-400 text-sm">{selected?.id === cfg.id ? '▲' : '▼'}</span>
                </div>
              </div>

              {/* Expanded detail */}
              {selected?.id === cfg.id && (
                <div className="border-t border-gray-200 px-4 py-4 bg-gray-50">
                  <ConfigDetail
                    config={cfg}
                    meta={meta}
                    tenantId={tenantId!}
                    onSaved={() => load()}
                    onDeleted={() => { setSelected(null); load() }}
                  />
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── ConfigForm — create new integration ───────────────────────────────────────

interface ConfigFormProps {
  channel:   string
  meta:      ChannelMeta
  onSaved:   () => void
  onCancel:  () => void
}

const ConfigForm: React.FC<ConfigFormProps> = ({ channel, meta, onSaved, onCancel }) => {
  const { tenantId } = useAuth()
  const [displayName, setDisplayName] = useState(`${meta.label} — ${new Date().getFullYear()}`)
  const [creds,       setCreds]       = useState<Record<string, string>>({})
  const [settings,    setSettings]    = useState<Record<string, string>>({})
  const [active,      setActive]      = useState(true)
  const [saving,      setSaving]      = useState(false)
  const [error,       setError]       = useState<string | null>(null)

  async function handleSave() {
    if (!tenantId) return
    if (!displayName.trim()) { setError('Display name is required'); return }
    setSaving(true); setError(null)
    try {
      await registryApi.createChannel({
        channel: channel as ChannelType,
        display_name: displayName.trim(),
        active,
        credentials: Object.fromEntries(Object.entries(creds).filter(([, v]) => v !== '')),
        settings:    Object.fromEntries(Object.entries(settings).filter(([, v]) => v !== '')),
      }, tenantId)
      onSaved()
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="border border-primary/30 rounded-lg p-4 bg-blue-50/30 space-y-4">
      <p className="text-xs font-semibold text-primary">New {meta.label} Integration</p>

      {/* General */}
      <Section title="General">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <FieldLabel>Display name</FieldLabel>
            <input
              className={inputCls}
              value={displayName}
              onChange={e => setDisplayName(e.target.value)}
              placeholder={`e.g. ${meta.label} — Production`}
            />
          </div>
          <div className="flex items-end pb-1 gap-2">
            <FieldLabel>Active</FieldLabel>
            <Toggle checked={active} onChange={setActive} />
          </div>
        </div>
      </Section>

      {/* Credentials */}
      {meta.fields.length > 0 && (
        <Section title="Credentials" subtitle="stored encrypted">
          <div className="grid grid-cols-2 gap-4">
            {meta.fields.map(f => (
              <div key={f.key}>
                <FieldLabel>{f.label}</FieldLabel>
                <input
                  type={f.sensitive ? 'password' : 'text'}
                  className={`${inputCls} font-mono`}
                  value={creds[f.key] ?? ''}
                  onChange={e => setCreds(prev => ({ ...prev, [f.key]: e.target.value }))}
                  placeholder={f.placeholder}
                  autoComplete="off"
                />
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Settings */}
      {meta.settingFields.length > 0 && (
        <Section title="Settings">
          <div className="grid grid-cols-2 gap-4">
            {meta.settingFields.map(f => (
              <div key={f.key}>
                <FieldLabel>{f.label}</FieldLabel>
                <input
                  type={f.type ?? 'text'}
                  className={inputCls}
                  value={settings[f.key] ?? ''}
                  onChange={e => setSettings(prev => ({ ...prev, [f.key]: e.target.value }))}
                  placeholder={f.placeholder}
                />
              </div>
            ))}
          </div>
        </Section>
      )}

      {error && <p className="text-xs text-red-600">{error}</p>}

      <div className="flex gap-2">
        <button
          onClick={handleSave}
          disabled={saving}
          className="px-3 py-1.5 rounded text-xs font-semibold bg-primary text-white disabled:opacity-40 hover:bg-blue-800 transition-colors"
        >
          {saving ? 'Saving…' : 'Save integration'}
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

// ── ConfigDetail — view/edit existing integration ─────────────────────────────

interface DetailProps {
  config:    GatewayConfig
  meta:      ChannelMeta
  tenantId:  string
  onSaved:   () => void
  onDeleted: () => void
}

const ConfigDetail: React.FC<DetailProps> = ({ config, meta, tenantId, onSaved, onDeleted }) => {
  const [displayName, setDisplayName] = useState(config.display_name)
  const [active,      setActive]      = useState(config.active)
  const [newCreds,    setNewCreds]    = useState<Record<string, string>>({})
  const [settings,    setSettings]    = useState<Record<string, string>>(
    Object.fromEntries(Object.entries(config.settings ?? {}).map(([k, v]) => [k, String(v)]))
  )
  const [modified,   setModified]   = useState(false)
  const [saving,     setSaving]     = useState(false)
  const [deleting,   setDeleting]   = useState(false)
  const [confirmDel, setConfirmDel] = useState(false)
  const [error,      setError]      = useState<string | null>(null)

  useEffect(() => {
    setDisplayName(config.display_name)
    setActive(config.active)
    setNewCreds({})
    setSettings(Object.fromEntries(Object.entries(config.settings ?? {}).map(([k, v]) => [k, String(v)])))
    setModified(false); setConfirmDel(false)
  }, [config.id])

  function mark() { setModified(true); setError(null) }

  async function handleSave() {
    setSaving(true); setError(null)
    try {
      const updates: Record<string, unknown> = {
        display_name: displayName.trim(),
        active,
        settings: Object.fromEntries(Object.entries(settings).filter(([, v]) => v !== '')),
      }
      const filteredCreds = Object.fromEntries(Object.entries(newCreds).filter(([, v]) => v !== ''))
      if (Object.keys(filteredCreds).length > 0) updates['credentials'] = filteredCreds
      await registryApi.updateChannel(config.id, updates, tenantId)
      setModified(false); setNewCreds({})
      onSaved()
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete() {
    setDeleting(true); setError(null)
    try {
      await registryApi.deleteChannel(config.id, tenantId)
      onDeleted()
    } catch (e) {
      setError(String(e))
      setDeleting(false); setConfirmDel(false)
    }
  }

  return (
    <div className="space-y-4">

      {/* General */}
      <Section title="General">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <FieldLabel>Display name</FieldLabel>
            <input
              className={inputCls}
              value={displayName}
              onChange={e => { setDisplayName(e.target.value); mark() }}
            />
          </div>
          <div className="flex items-center gap-2 pt-4">
            <FieldLabel>Active</FieldLabel>
            <Toggle checked={active} onChange={v => { setActive(v); mark() }} />
          </div>
        </div>
        <p className="text-xs text-gray-400 mt-2">
          Created {new Date(config.created_at).toLocaleDateString('pt-BR')} ·
          Updated {new Date(config.updated_at).toLocaleDateString('pt-BR')}
        </p>
      </Section>

      {/* Credentials */}
      {meta.fields.length > 0 && (
        <Section title="Credentials" subtitle="leave blank to keep current value">
          <div className="grid grid-cols-2 gap-4">
            {meta.fields.map(f => (
              <div key={f.key}>
                <FieldLabel>{f.label}</FieldLabel>
                <div className="space-y-1">
                  <div className={`${inputCls} font-mono text-gray-400 bg-gray-100`}>
                    {config.credentials?.[f.key] ? '••••••••' : '(not set)'}
                  </div>
                  <input
                    type={f.sensitive ? 'password' : 'text'}
                    className={`${inputCls} font-mono`}
                    value={newCreds[f.key] ?? ''}
                    onChange={e => { setNewCreds(prev => ({ ...prev, [f.key]: e.target.value })); mark() }}
                    placeholder="New value (optional)"
                    autoComplete="off"
                  />
                </div>
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Settings */}
      {meta.settingFields.length > 0 && (
        <Section title="Settings">
          <div className="grid grid-cols-2 gap-4">
            {meta.settingFields.map(f => (
              <div key={f.key}>
                <FieldLabel>{f.label}</FieldLabel>
                <input
                  type={f.type ?? 'text'}
                  className={inputCls}
                  value={settings[f.key] ?? ''}
                  onChange={e => { setSettings(prev => ({ ...prev, [f.key]: e.target.value })); mark() }}
                  placeholder={f.placeholder}
                />
              </div>
            ))}
          </div>
        </Section>
      )}

      {error && <p className="text-xs text-red-600">{error}</p>}

      {/* Actions */}
      <div className="flex gap-2 items-center pt-1">
        {modified && (
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-3 py-1.5 rounded text-xs font-semibold bg-primary text-white disabled:opacity-40 hover:bg-blue-800 transition-colors"
          >
            {saving ? 'Saving…' : 'Save changes'}
          </button>
        )}

        {!confirmDel ? (
          <button
            onClick={() => setConfirmDel(true)}
            className="px-3 py-1.5 rounded text-xs border border-red-300 text-red-500 hover:bg-red-50 transition-colors"
          >
            Delete
          </button>
        ) : (
          <>
            <button
              onClick={handleDelete}
              disabled={deleting}
              className="px-3 py-1.5 rounded text-xs font-semibold bg-red-600 text-white disabled:opacity-40 hover:bg-red-700 transition-colors"
            >
              {deleting ? 'Deleting…' : 'Confirm delete'}
            </button>
            <button
              onClick={() => setConfirmDel(false)}
              className="px-3 py-1.5 rounded text-xs border border-gray-300 text-gray-600 hover:text-gray-900 transition-colors"
            >
              Cancel
            </button>
          </>
        )}
      </div>
    </div>
  )
}

export default GatewayConfigPanel
