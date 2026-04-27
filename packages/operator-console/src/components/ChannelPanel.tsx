/**
 * ChannelPanel.tsx
 * Channel Configuration panel — manage GatewayConfig credentials per channel.
 *
 * Accent: teal (#14b8a6 / #042f2e)
 * Channels: WhatsApp, Webchat, Voice, Email, SMS, Instagram, Telegram, WebRTC
 *
 * Layout:
 *   ┌─────────────────┬──────────────────────────────────────────────────────┐
 *   │  Channel list   │  Channel detail / create form                        │
 *   │  (left 280px)   │  (right flex)                                        │
 *   └─────────────────┴──────────────────────────────────────────────────────┘
 */
import { useState, useEffect } from 'react'
import { useChannels, createChannel, updateChannel, deleteChannel } from '../api/channel-hooks'
import type { GatewayConfig, ChannelType } from '../types'

// ── Colour palette ────────────────────────────────────────────────────────────
const C = {
  bg:          '#0f1923',
  surface:     '#1a2332',
  surfaceHi:   '#1e2a3a',
  border:      '#1e3a4a',
  borderFaint: '#162030',
  accent:      '#14b8a6',
  accentDark:  '#042f2e',
  accentMid:   '#0d3d38',
  text:        '#e2e8f0',
  textMid:     '#94a3b8',
  textFaint:   '#475569',
  danger:      '#ef4444',
  success:     '#22c55e',
  warn:        '#f59e0b',
}

// ── Channel metadata ──────────────────────────────────────────────────────────
const CHANNEL_META: Record<ChannelType, {
  label:       string
  icon:        string
  color:       string
  fields:      Array<{ key: string; label: string; placeholder: string; sensitive: boolean }>
  settingFields: Array<{ key: string; label: string; placeholder: string; type?: string }>
}> = {
  whatsapp: {
    label: 'WhatsApp', icon: '💬', color: '#25d366',
    fields: [
      { key: 'access_token',        label: 'Access Token',          placeholder: 'EAAxxxxx...', sensitive: true },
      { key: 'phone_number_id',     label: 'Phone Number ID',       placeholder: '1234567890',  sensitive: false },
      { key: 'waba_id',             label: 'WhatsApp Business ID',  placeholder: '9876543210',  sensitive: false },
      { key: 'webhook_verify_token',label: 'Webhook Verify Token',  placeholder: 'my-verify-token', sensitive: true },
    ],
    settingFields: [
      { key: 'api_version',   label: 'API Version',    placeholder: 'v19.0' },
      { key: 'webhook_path',  label: 'Webhook Path',   placeholder: '/webhooks/whatsapp' },
    ],
  },
  webchat: {
    label: 'Webchat', icon: '🌐', color: '#3b82f6',
    fields: [
      { key: 'jwt_secret',    label: 'JWT Secret',     placeholder: 'changeme-secret-32+chars', sensitive: true },
    ],
    settingFields: [
      { key: 'ws_auth_timeout_s',       label: 'Auth Timeout (s)',         placeholder: '30', type: 'number' },
      { key: 'attachment_expiry_days',  label: 'Attachment Expiry (days)', placeholder: '30', type: 'number' },
      { key: 'serving_base_url',        label: 'Serving Base URL',         placeholder: 'https://my-domain.com' },
      { key: 'cors_origins',            label: 'CORS Origins (comma-sep)', placeholder: 'https://app.company.com' },
    ],
  },
  voice: {
    label: 'Voice', icon: '📞', color: '#8b5cf6',
    fields: [
      { key: 'api_key',       label: 'API Key',        placeholder: 'sk-...', sensitive: true },
      { key: 'api_secret',    label: 'API Secret',     placeholder: 'secret', sensitive: true },
      { key: 'account_sid',   label: 'Account SID',    placeholder: 'ACxxx', sensitive: false },
    ],
    settingFields: [
      { key: 'inbound_number', label: 'Inbound Number', placeholder: '+15551234567' },
      { key: 'provider',       label: 'Provider',        placeholder: 'twilio | vonage | sinch' },
      { key: 'region',         label: 'Region',          placeholder: 'us1' },
    ],
  },
  email: {
    label: 'Email', icon: '✉️', color: '#f59e0b',
    fields: [
      { key: 'smtp_password',  label: 'SMTP Password',   placeholder: '••••••••', sensitive: true },
      { key: 'api_key',        label: 'API Key',          placeholder: 'SG.xxxxx', sensitive: true },
    ],
    settingFields: [
      { key: 'smtp_host',      label: 'SMTP Host',        placeholder: 'smtp.sendgrid.net' },
      { key: 'smtp_port',      label: 'SMTP Port',        placeholder: '587', type: 'number' },
      { key: 'from_address',   label: 'From Address',     placeholder: 'support@company.com' },
      { key: 'from_name',      label: 'From Name',        placeholder: 'Support Team' },
      { key: 'provider',       label: 'Provider',         placeholder: 'sendgrid | ses | smtp' },
    ],
  },
  sms: {
    label: 'SMS', icon: '📱', color: '#ec4899',
    fields: [
      { key: 'api_key',        label: 'API Key',          placeholder: 'key-...', sensitive: true },
      { key: 'api_secret',     label: 'API Secret',       placeholder: 'secret', sensitive: true },
    ],
    settingFields: [
      { key: 'sender_id',      label: 'Sender ID',        placeholder: '+15551234567' },
      { key: 'provider',       label: 'Provider',         placeholder: 'twilio | vonage | aws-sns' },
    ],
  },
  instagram: {
    label: 'Instagram', icon: '📸', color: '#e1306c',
    fields: [
      { key: 'access_token',        label: 'Page Access Token',     placeholder: 'EAAxxxxx...', sensitive: true },
      { key: 'app_secret',          label: 'App Secret',            placeholder: 'app_secret', sensitive: true },
      { key: 'webhook_verify_token',label: 'Webhook Verify Token',  placeholder: 'my-verify-token', sensitive: true },
    ],
    settingFields: [
      { key: 'page_id',        label: 'Instagram Page ID', placeholder: '1234567890' },
      { key: 'api_version',    label: 'API Version',       placeholder: 'v19.0' },
    ],
  },
  telegram: {
    label: 'Telegram', icon: '✈️', color: '#2aabee',
    fields: [
      { key: 'bot_token',      label: 'Bot Token',        placeholder: '1234567890:ABC...', sensitive: true },
    ],
    settingFields: [
      { key: 'webhook_path',   label: 'Webhook Path',     placeholder: '/webhooks/telegram' },
      { key: 'bot_username',   label: 'Bot Username',     placeholder: '@mybot' },
    ],
  },
  webrtc: {
    label: 'WebRTC', icon: '🎥', color: '#06b6d4',
    fields: [
      { key: 'turn_secret',    label: 'TURN Secret',      placeholder: 'secret', sensitive: true },
    ],
    settingFields: [
      { key: 'stun_url',       label: 'STUN URL',         placeholder: 'stun:stun.l.google.com:19302' },
      { key: 'turn_url',       label: 'TURN URL',         placeholder: 'turn:turn.company.com:3478' },
      { key: 'turn_username',  label: 'TURN Username',    placeholder: 'plughub' },
    ],
  },
}

const ALL_CHANNELS = Object.keys(CHANNEL_META) as ChannelType[]

// ── Props ─────────────────────────────────────────────────────────────────────
interface Props {
  tenantId: string
  onBack:   () => void
}

// ── Main component ────────────────────────────────────────────────────────────
export function ChannelPanel({ tenantId, onBack }: Props) {
  const { channels, loading, refresh } = useChannels(tenantId)
  const [selected,   setSelected]      = useState<GatewayConfig | null>(null)
  const [creating,   setCreating]      = useState<ChannelType | null>(null)
  const [error,      setError]         = useState<string | null>(null)

  // Clear selection when tenant changes
  useEffect(() => { setSelected(null); setCreating(null) }, [tenantId])

  // Group configs by channel type
  const byChannel: Record<string, GatewayConfig[]> = {}
  for (const cfg of channels) {
    ;(byChannel[cfg.channel] ??= []).push(cfg)
  }

  function handleSelect(cfg: GatewayConfig) {
    setSelected(cfg)
    setCreating(null)
    setError(null)
  }
  function handleCreateClick(channel: ChannelType) {
    setCreating(channel)
    setSelected(null)
    setError(null)
  }

  return (
    <div style={{ display: 'flex', flex: 1, overflow: 'hidden', background: C.bg }}>

      {/* ── Left sidebar ────────────────────────────────────────────────────── */}
      <div style={{
        width: 280, flexShrink: 0,
        borderRight: `1px solid ${C.border}`,
        display: 'flex', flexDirection: 'column', overflow: 'hidden',
      }}>

        {/* Header */}
        <div style={{
          padding: '14px 16px 10px',
          borderBottom: `1px solid ${C.border}`,
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        }}>
          <div>
            <div style={{ fontSize: 13, fontWeight: 700, color: C.text }}>Channels</div>
            <div style={{ fontSize: 11, color: C.textFaint }}>
              {channels.length} configured
            </div>
          </div>
          <button onClick={onBack} style={backBtnStyle}>← Back</button>
        </div>

        {/* Channel type list */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '8px 0' }}>
          {ALL_CHANNELS.map(ch => {
            const meta    = CHANNEL_META[ch]
            const configs = byChannel[ch] ?? []
            const isActive = creating === ch || configs.some(c => c.id === selected?.id)

            return (
              <div key={ch}>
                {/* Channel section header */}
                <div style={{
                  padding: '6px 16px 4px',
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ fontSize: 14 }}>{meta.icon}</span>
                    <span style={{ fontSize: 12, fontWeight: 600, color: C.textMid }}>
                      {meta.label}
                    </span>
                    {configs.length > 0 && (
                      <span style={{
                        fontSize: 10, padding: '1px 5px',
                        borderRadius: 8, background: C.accentMid, color: C.accent,
                      }}>
                        {configs.length}
                      </span>
                    )}
                  </div>
                  <button
                    onClick={() => handleCreateClick(ch)}
                    style={{
                      background: isActive && creating === ch ? C.accentMid : 'transparent',
                      border: `1px solid ${isActive && creating === ch ? C.accent : C.border}`,
                      borderRadius: 4, color: C.accent, cursor: 'pointer',
                      fontSize: 11, padding: '1px 7px', lineHeight: '18px',
                    }}
                    title={`Add ${meta.label} config`}
                  >
                    + Add
                  </button>
                </div>

                {/* Existing configs for this channel */}
                {configs.map(cfg => (
                  <div
                    key={cfg.id}
                    onClick={() => handleSelect(cfg)}
                    style={{
                      padding: '6px 16px 6px 36px', cursor: 'pointer',
                      background: selected?.id === cfg.id ? C.accentMid : 'transparent',
                      borderLeft: selected?.id === cfg.id ? `2px solid ${C.accent}` : '2px solid transparent',
                      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    }}
                  >
                    <div>
                      <div style={{ fontSize: 12, color: C.text, fontWeight: selected?.id === cfg.id ? 600 : 400 }}>
                        {cfg.display_name}
                      </div>
                    </div>
                    <StatusDot active={cfg.active} />
                  </div>
                ))}
              </div>
            )
          })}
        </div>
      </div>

      {/* ── Right panel ──────────────────────────────────────────────────────── */}
      <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
        {error && (
          <div style={{
            padding: '8px 16px', background: '#2d0a0a',
            borderBottom: `1px solid ${C.danger}`,
            color: C.danger, fontSize: 12,
          }}>
            {error}
          </div>
        )}

        {creating && (
          <CreateForm
            tenantId={tenantId}
            channel={creating}
            onSaved={() => { setCreating(null); refresh() }}
            onCancel={() => setCreating(null)}
            onError={setError}
          />
        )}

        {selected && !creating && (
          <ConfigDetail
            tenantId={tenantId}
            config={selected}
            onSaved={(updated) => { setSelected(updated); refresh() }}
            onDeleted={() => { setSelected(null); refresh() }}
            onError={setError}
          />
        )}

        {!creating && !selected && !loading && (
          <div style={{
            flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
            flexDirection: 'column', gap: 8,
          }}>
            <div style={{ fontSize: 32 }}>⚙️</div>
            <div style={{ fontSize: 14, color: C.textMid }}>Select a channel config or add a new one</div>
            <div style={{ fontSize: 12, color: C.textFaint }}>
              {channels.length === 0 ? 'No channels configured yet' : `${channels.length} channel(s) configured`}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── CreateForm ────────────────────────────────────────────────────────────────
function CreateForm({ tenantId, channel, onSaved, onCancel, onError }: {
  tenantId:  string
  channel:   ChannelType
  onSaved:   () => void
  onCancel:  () => void
  onError:   (msg: string) => void
}) {
  const meta = CHANNEL_META[channel]
  const [displayName, setDisplayName] = useState(`${meta.label} — ${new Date().getFullYear()}`)
  const [creds,       setCreds]       = useState<Record<string, string>>({})
  const [settings,    setSettings]    = useState<Record<string, string>>({})
  const [saving,      setSaving]      = useState(false)
  const [active,      setActive]      = useState(true)

  async function handleSave() {
    if (!displayName.trim()) { onError('Display name is required'); return }
    setSaving(true)
    try {
      await createChannel(tenantId, {
        channel,
        display_name: displayName.trim(),
        active,
        credentials: creds,
        settings:    Object.fromEntries(
          Object.entries(settings).filter(([, v]) => v !== '')
        ),
      })
      onSaved()
      onError('')
    } catch (e) {
      onError((e as Error).message)
    } finally { setSaving(false) }
  }

  return (
    <div style={{ flex: 1, overflowY: 'auto', padding: 24 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 20 }}>
        <span style={{ fontSize: 22 }}>{meta.icon}</span>
        <div>
          <div style={{ fontSize: 15, fontWeight: 700, color: C.text }}>
            New {meta.label} Configuration
          </div>
          <div style={{ fontSize: 11, color: C.textFaint }}>
            Credentials are stored encrypted and masked on read
          </div>
        </div>
      </div>

      {/* Display name + active toggle */}
      <div style={sectionStyle}>
        <div style={sectionLabelStyle}>General</div>
        <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end' }}>
          <div style={{ flex: 1 }}>
            <label style={fieldLabelStyle}>Display Name</label>
            <input
              value={displayName}
              onChange={e => setDisplayName(e.target.value)}
              style={inputStyle}
              placeholder="e.g. WhatsApp Business — Production"
            />
          </div>
          <div style={{ marginBottom: 2 }}>
            <label style={fieldLabelStyle}>Active</label>
            <ToggleSwitch checked={active} onChange={setActive} />
          </div>
        </div>
      </div>

      {/* Credentials */}
      {meta.fields.length > 0 && (
        <div style={sectionStyle}>
          <div style={sectionLabelStyle}>
            Credentials
            <span style={{ fontSize: 10, color: C.textFaint, marginLeft: 6 }}>
              — values are masked after save
            </span>
          </div>
          {meta.fields.map(f => (
            <div key={f.key} style={{ marginBottom: 10 }}>
              <label style={fieldLabelStyle}>{f.label}</label>
              <input
                type={f.sensitive ? 'password' : 'text'}
                value={creds[f.key] ?? ''}
                onChange={e => setCreds(prev => ({ ...prev, [f.key]: e.target.value }))}
                style={inputStyle}
                placeholder={f.placeholder}
                autoComplete="off"
              />
            </div>
          ))}
        </div>
      )}

      {/* Settings */}
      {meta.settingFields.length > 0 && (
        <div style={sectionStyle}>
          <div style={sectionLabelStyle}>Settings</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px 16px' }}>
            {meta.settingFields.map(f => (
              <div key={f.key}>
                <label style={fieldLabelStyle}>{f.label}</label>
                <input
                  type={f.type ?? 'text'}
                  value={settings[f.key] ?? ''}
                  onChange={e => setSettings(prev => ({ ...prev, [f.key]: e.target.value }))}
                  style={inputStyle}
                  placeholder={f.placeholder}
                />
              </div>
            ))}
          </div>
        </div>
      )}

      <div style={{ display: 'flex', gap: 10, marginTop: 8 }}>
        <button
          onClick={handleSave}
          disabled={saving}
          style={{ ...btnStyle, background: C.accent, color: '#000', opacity: saving ? 0.6 : 1 }}
        >
          {saving ? 'Saving…' : 'Save Configuration'}
        </button>
        <button onClick={onCancel} style={{ ...btnStyle, background: 'transparent', border: `1px solid ${C.border}` }}>
          Cancel
        </button>
      </div>
    </div>
  )
}

// ── ConfigDetail ──────────────────────────────────────────────────────────────
function ConfigDetail({ tenantId, config, onSaved, onDeleted, onError }: {
  tenantId:  string
  config:    GatewayConfig
  onSaved:   (updated: GatewayConfig) => void
  onDeleted: () => void
  onError:   (msg: string) => void
}) {
  const meta = CHANNEL_META[config.channel as ChannelType] ?? CHANNEL_META['webchat']

  const [displayName, setDisplayName] = useState(config.display_name)
  const [active,      setActive]      = useState(config.active)
  const [newCreds,    setNewCreds]    = useState<Record<string, string>>({})
  const [settings,    setSettings]    = useState<Record<string, string>>(
    Object.fromEntries(Object.entries(config.settings ?? {}).map(([k, v]) => [k, String(v)]))
  )
  const [saving,    setSaving]    = useState(false)
  const [deleting,  setDeleting]  = useState(false)
  const [confirmDel,setConfirm]   = useState(false)
  const [modified,  setModified]  = useState(false)

  // Reset when config changes
  useEffect(() => {
    setDisplayName(config.display_name)
    setActive(config.active)
    setNewCreds({})
    setSettings(Object.fromEntries(Object.entries(config.settings ?? {}).map(([k, v]) => [k, String(v)])))
    setModified(false)
    setConfirm(false)
  }, [config.id])

  function markModified() { setModified(true) }

  async function handleSave() {
    setSaving(true)
    try {
      const updates: Record<string, unknown> = {
        display_name: displayName.trim(),
        active,
        settings: Object.fromEntries(Object.entries(settings).filter(([, v]) => v !== '')),
      }
      // Only include credentials if user typed new values
      const filteredCreds = Object.fromEntries(
        Object.entries(newCreds).filter(([, v]) => v !== '')
      )
      if (Object.keys(filteredCreds).length > 0) updates['credentials'] = filteredCreds

      const updated = await updateChannel(tenantId, config.id, updates as Parameters<typeof updateChannel>[2])
      setModified(false)
      setNewCreds({})
      onSaved(updated)
      onError('')
    } catch (e) {
      onError((e as Error).message)
    } finally { setSaving(false) }
  }

  async function handleDelete() {
    setDeleting(true)
    try {
      await deleteChannel(tenantId, config.id)
      onDeleted()
      onError('')
    } catch (e) {
      onError((e as Error).message)
      setDeleting(false)
      setConfirm(false)
    }
  }

  return (
    <div style={{ flex: 1, overflowY: 'auto', padding: 24 }}>
      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 22 }}>{meta.icon}</span>
          <div>
            <div style={{ fontSize: 15, fontWeight: 700, color: C.text }}>
              {config.display_name}
              {modified && <span style={{ color: C.accent, marginLeft: 6 }}>●</span>}
            </div>
            <div style={{ fontSize: 11, color: C.textFaint }}>
              {config.channel} · ID: {config.id.slice(0, 8)}…
            </div>
          </div>
        </div>
        <StatusBadge active={config.active} />
      </div>

      {/* General */}
      <div style={sectionStyle}>
        <div style={sectionLabelStyle}>General</div>
        <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end' }}>
          <div style={{ flex: 1 }}>
            <label style={fieldLabelStyle}>Display Name</label>
            <input
              value={displayName}
              onChange={e => { setDisplayName(e.target.value); markModified() }}
              style={inputStyle}
            />
          </div>
          <div style={{ marginBottom: 2 }}>
            <label style={fieldLabelStyle}>Active</label>
            <ToggleSwitch checked={active} onChange={v => { setActive(v); markModified() }} />
          </div>
        </div>
      </div>

      {/* Credentials — show masked values + allow overwrite */}
      {meta.fields.length > 0 && (
        <div style={sectionStyle}>
          <div style={sectionLabelStyle}>
            Credentials
            <span style={{ fontSize: 10, color: C.textFaint, marginLeft: 6 }}>
              — leave blank to keep existing value
            </span>
          </div>
          {meta.fields.map(f => (
            <div key={f.key} style={{ marginBottom: 10 }}>
              <label style={fieldLabelStyle}>{f.label}</label>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <div style={{ ...inputStyle, color: C.textFaint, flex: 1, display: 'flex', alignItems: 'center' }}>
                  {config.credentials[f.key] ?? '—'}
                </div>
                <span style={{ color: C.textFaint, fontSize: 11 }}>→</span>
                <input
                  type={f.sensitive ? 'password' : 'text'}
                  value={newCreds[f.key] ?? ''}
                  onChange={e => { setNewCreds(prev => ({ ...prev, [f.key]: e.target.value })); markModified() }}
                  style={{ ...inputStyle, flex: 1 }}
                  placeholder="New value (optional)"
                  autoComplete="off"
                />
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Settings */}
      {meta.settingFields.length > 0 && (
        <div style={sectionStyle}>
          <div style={sectionLabelStyle}>Settings</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px 16px' }}>
            {meta.settingFields.map(f => (
              <div key={f.key}>
                <label style={fieldLabelStyle}>{f.label}</label>
                <input
                  type={f.type ?? 'text'}
                  value={settings[f.key] ?? ''}
                  onChange={e => { setSettings(prev => ({ ...prev, [f.key]: e.target.value })); markModified() }}
                  style={inputStyle}
                  placeholder={f.placeholder}
                />
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Metadata */}
      <div style={{ ...sectionStyle, borderColor: 'transparent', paddingTop: 0 }}>
        <div style={{ display: 'flex', gap: 24, fontSize: 11, color: C.textFaint }}>
          <span>Created: {new Date(config.created_at).toLocaleDateString()}</span>
          <span>Updated: {new Date(config.updated_at).toLocaleDateString()}</span>
          <span>By: {config.created_by}</span>
        </div>
      </div>

      {/* Actions */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 8 }}>
        {modified && (
          <button
            onClick={handleSave}
            disabled={saving}
            style={{ ...btnStyle, background: C.accent, color: '#000', opacity: saving ? 0.6 : 1 }}
          >
            {saving ? 'Saving…' : 'Save Changes'}
          </button>
        )}

        {!confirmDel ? (
          <button
            onClick={() => setConfirm(true)}
            style={{ ...btnStyle, background: 'transparent', border: `1px solid ${C.danger}`, color: C.danger }}
          >
            Delete
          </button>
        ) : (
          <>
            <button
              onClick={handleDelete}
              disabled={deleting}
              style={{ ...btnStyle, background: C.danger, color: '#fff', opacity: deleting ? 0.6 : 1 }}
            >
              {deleting ? 'Deleting…' : 'Confirm Delete'}
            </button>
            <button
              onClick={() => setConfirm(false)}
              style={{ ...btnStyle, background: 'transparent', border: `1px solid ${C.border}` }}
            >
              Cancel
            </button>
          </>
        )}
      </div>
    </div>
  )
}

// ── Small helpers ─────────────────────────────────────────────────────────────
function StatusDot({ active }: { active: boolean }) {
  return (
    <span style={{
      width: 7, height: 7, borderRadius: '50%',
      background: active ? C.success : C.textFaint,
      display: 'inline-block',
      boxShadow: active ? `0 0 4px ${C.success}` : 'none',
    }} />
  )
}

function StatusBadge({ active }: { active: boolean }) {
  return (
    <span style={{
      fontSize: 11, padding: '3px 10px', borderRadius: 4,
      background: active ? '#052e16' : '#1e293b',
      color:      active ? C.success  : C.textFaint,
      border: `1px solid ${active ? '#166534' : C.border}`,
    }}>
      {active ? 'Active' : 'Inactive'}
    </span>
  )
}

function ToggleSwitch({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <div
      onClick={() => onChange(!checked)}
      style={{
        width: 36, height: 20, borderRadius: 10, cursor: 'pointer',
        background: checked ? C.accent : C.border,
        position: 'relative', transition: 'background 0.2s',
        display: 'flex', alignItems: 'center',
      }}
    >
      <div style={{
        width: 14, height: 14, borderRadius: '50%', background: '#fff',
        position: 'absolute', left: checked ? 18 : 3,
        transition: 'left 0.2s',
      }} />
    </div>
  )
}

// ── Shared styles ─────────────────────────────────────────────────────────────
const sectionStyle: React.CSSProperties = {
  borderBottom: `1px solid ${C.borderFaint}`,
  paddingBottom: 16, marginBottom: 16,
}
const sectionLabelStyle: React.CSSProperties = {
  fontSize: 10, fontWeight: 700, color: C.textFaint,
  letterSpacing: '0.8px', textTransform: 'uppercase',
  marginBottom: 10,
}
const fieldLabelStyle: React.CSSProperties = {
  display: 'block', fontSize: 11, color: C.textMid, marginBottom: 4,
}
const inputStyle: React.CSSProperties = {
  background:   '#0d1117',
  border:       `1px solid ${C.border}`,
  borderRadius: 6,
  color:        C.text,
  fontSize:     12,
  padding:      '5px 10px',
  outline:      'none',
  width:        '100%',
  boxSizing:    'border-box',
  fontFamily:   'monospace',
}
const btnStyle: React.CSSProperties = {
  padding:      '6px 14px',
  borderRadius: 5,
  border:       'none',
  color:        C.text,
  cursor:       'pointer',
  fontSize:     12,
  fontWeight:   600,
}
const backBtnStyle: React.CSSProperties = {
  background:   'transparent',
  border:       `1px solid ${C.border}`,
  borderRadius: 4,
  color:        C.textMid,
  cursor:       'pointer',
  fontSize:     11,
  padding:      '3px 8px',
}

import type React from 'react'
