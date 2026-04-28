/**
 * ChannelsPage.tsx
 * Manage GatewayConfig credentials per channel (WhatsApp, Webchat, Voice, etc.)
 * Migrated from operator-console/ChannelPanel.tsx — uses platform-ui design system.
 */
import React, { useState, useEffect, useCallback } from 'react'
import { useAuth } from '@/auth/useAuth'
import { GatewayConfig, ChannelType } from '@/types'
import * as registryApi from '@/api/registry'
import Badge from '@/components/ui/Badge'
import Button from '@/components/ui/Button'
import Spinner from '@/components/ui/Spinner'
import EmptyState from '@/components/ui/EmptyState'

// ── Channel metadata ──────────────────────────────────────────────────────────

interface ChannelFieldDef {
  key: string
  label: string
  placeholder: string
  sensitive: boolean
}
interface ChannelSettingDef {
  key: string
  label: string
  placeholder: string
  type?: string
}
interface ChannelMeta {
  label: string
  icon: string
  color: string
  fields: ChannelFieldDef[]
  settingFields: ChannelSettingDef[]
}

const CHANNEL_META: Record<ChannelType, ChannelMeta> = {
  whatsapp: {
    label: 'WhatsApp', icon: '💬', color: '#25d366',
    fields: [
      { key: 'access_token',         label: 'Access Token',          placeholder: 'EAAxxxxx…',         sensitive: true  },
      { key: 'phone_number_id',      label: 'Phone Number ID',       placeholder: '1234567890',        sensitive: false },
      { key: 'waba_id',              label: 'WhatsApp Business ID',  placeholder: '9876543210',        sensitive: false },
      { key: 'webhook_verify_token', label: 'Webhook Verify Token',  placeholder: 'my-verify-token',  sensitive: true  },
    ],
    settingFields: [
      { key: 'api_version',  label: 'API Version',  placeholder: 'v19.0' },
      { key: 'webhook_path', label: 'Webhook Path', placeholder: '/webhooks/whatsapp' },
    ],
  },
  webchat: {
    label: 'Webchat', icon: '🌐', color: '#3b82f6',
    fields: [
      { key: 'jwt_secret', label: 'JWT Secret', placeholder: 'changeme-secret-32+chars', sensitive: true },
    ],
    settingFields: [
      { key: 'ws_auth_timeout_s',      label: 'Auth Timeout (s)',         placeholder: '30',                    type: 'number' },
      { key: 'attachment_expiry_days', label: 'Attachment Expiry (days)', placeholder: '30',                    type: 'number' },
      { key: 'serving_base_url',       label: 'Serving Base URL',         placeholder: 'https://my-domain.com' },
      { key: 'cors_origins',           label: 'CORS Origins (comma-sep)', placeholder: 'https://app.company.com' },
    ],
  },
  voice: {
    label: 'Voice', icon: '📞', color: '#8b5cf6',
    fields: [
      { key: 'api_key',     label: 'API Key',    placeholder: 'sk-…',    sensitive: true  },
      { key: 'api_secret',  label: 'API Secret', placeholder: 'secret',  sensitive: true  },
      { key: 'account_sid', label: 'Account SID',placeholder: 'ACxxx',   sensitive: false },
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
      { key: 'smtp_password', label: 'SMTP Password', placeholder: '••••••••',  sensitive: true },
      { key: 'api_key',       label: 'API Key',        placeholder: 'SG.xxxxx', sensitive: true },
    ],
    settingFields: [
      { key: 'smtp_host',    label: 'SMTP Host',    placeholder: 'smtp.sendgrid.net' },
      { key: 'smtp_port',    label: 'SMTP Port',    placeholder: '587', type: 'number' },
      { key: 'from_address', label: 'From Address', placeholder: 'support@company.com' },
      { key: 'from_name',    label: 'From Name',    placeholder: 'Support Team' },
      { key: 'provider',     label: 'Provider',     placeholder: 'sendgrid | ses | smtp' },
    ],
  },
  sms: {
    label: 'SMS', icon: '📱', color: '#ec4899',
    fields: [
      { key: 'api_key',    label: 'API Key',    placeholder: 'key-…',  sensitive: true },
      { key: 'api_secret', label: 'API Secret', placeholder: 'secret', sensitive: true },
    ],
    settingFields: [
      { key: 'sender_id', label: 'Sender ID', placeholder: '+15551234567' },
      { key: 'provider',  label: 'Provider',  placeholder: 'twilio | vonage | aws-sns' },
    ],
  },
  instagram: {
    label: 'Instagram', icon: '📸', color: '#e1306c',
    fields: [
      { key: 'access_token',         label: 'Page Access Token',    placeholder: 'EAAxxxxx…',        sensitive: true },
      { key: 'app_secret',           label: 'App Secret',           placeholder: 'app_secret',       sensitive: true },
      { key: 'webhook_verify_token', label: 'Webhook Verify Token', placeholder: 'my-verify-token', sensitive: true },
    ],
    settingFields: [
      { key: 'page_id',     label: 'Instagram Page ID', placeholder: '1234567890' },
      { key: 'api_version', label: 'API Version',        placeholder: 'v19.0' },
    ],
  },
  telegram: {
    label: 'Telegram', icon: '✈️', color: '#2aabee',
    fields: [
      { key: 'bot_token', label: 'Bot Token', placeholder: '1234567890:ABC…', sensitive: true },
    ],
    settingFields: [
      { key: 'webhook_path', label: 'Webhook Path', placeholder: '/webhooks/telegram' },
      { key: 'bot_username', label: 'Bot Username', placeholder: '@mybot' },
    ],
  },
  webrtc: {
    label: 'WebRTC', icon: '🎥', color: '#06b6d4',
    fields: [
      { key: 'turn_secret', label: 'TURN Secret', placeholder: 'secret', sensitive: true },
    ],
    settingFields: [
      { key: 'stun_url',      label: 'STUN URL',      placeholder: 'stun:stun.l.google.com:19302' },
      { key: 'turn_url',      label: 'TURN URL',      placeholder: 'turn:turn.company.com:3478' },
      { key: 'turn_username', label: 'TURN Username', placeholder: 'plughub' },
    ],
  },
}

const ALL_CHANNELS = Object.keys(CHANNEL_META) as ChannelType[]

// ── Main page ─────────────────────────────────────────────────────────────────

const ChannelsPage: React.FC = () => {
  const { session } = useAuth()
  const [channels,  setChannels]  = useState<GatewayConfig[]>([])
  const [loading,   setLoading]   = useState(true)
  const [selected,  setSelected]  = useState<GatewayConfig | null>(null)
  const [creating,  setCreating]  = useState<ChannelType | null>(null)
  const [error,     setError]     = useState('')

  const loadChannels = useCallback(async () => {
    if (!session) return
    setLoading(true)
    try {
      const result = await registryApi.listChannels(session.tenantId)
      setChannels(result.items ?? [])
    } catch {
      setError('Falha ao carregar configurações de canal')
    } finally {
      setLoading(false)
    }
  }, [session])

  useEffect(() => { loadChannels() }, [loadChannels])

  const byChannel: Record<string, GatewayConfig[]> = {}
  for (const cfg of channels) {
    (byChannel[cfg.channel] ??= []).push(cfg)
  }

  function handleSelect(cfg: GatewayConfig) {
    setSelected(cfg); setCreating(null); setError('')
  }
  function handleCreateClick(ch: ChannelType) {
    setCreating(ch); setSelected(null); setError('')
  }
  function handleSaved() {
    setCreating(null); setSelected(null); loadChannels()
  }
  function handleDeleted() {
    setSelected(null); loadChannels()
  }
  function handleSavedUpdate(updated: GatewayConfig) {
    setSelected(updated); loadChannels()
  }

  if (loading) {
    return (
      <div className="flex justify-center py-12">
        <Spinner />
      </div>
    )
  }

  return (
    <div className="flex h-full border border-lightGray rounded-lg overflow-hidden bg-white" style={{ minHeight: 520 }}>

      {/* ── Left sidebar ─────────────────────────────────────────────────────── */}
      <div className="w-72 flex-shrink-0 border-r border-lightGray flex flex-col bg-gray-50 overflow-y-auto">
        <div className="px-4 py-3 border-b border-lightGray">
          <div className="text-sm font-bold text-dark">Canais</div>
          <div className="text-xs text-gray mt-0.5">{channels.length} configurado(s)</div>
        </div>

        <div className="flex-1 py-2">
          {ALL_CHANNELS.map(ch => {
            const meta    = CHANNEL_META[ch]
            const configs = byChannel[ch] ?? []
            const isCreating = creating === ch

            return (
              <div key={ch} className="mb-1">
                {/* Channel section header */}
                <div className="flex items-center justify-between px-4 py-1.5">
                  <div className="flex items-center gap-1.5">
                    <span className="text-sm">{meta.icon}</span>
                    <span className="text-xs font-semibold text-gray">{meta.label}</span>
                    {configs.length > 0 && (
                      <span className="text-xs px-1.5 py-0.5 rounded-full bg-primary/10 text-primary font-medium">
                        {configs.length}
                      </span>
                    )}
                  </div>
                  <button
                    onClick={() => handleCreateClick(ch)}
                    className={`text-xs px-2 py-0.5 rounded border transition-colors ${
                      isCreating
                        ? 'bg-primary/10 border-primary text-primary'
                        : 'bg-white border-lightGray text-gray hover:border-primary hover:text-primary'
                    }`}
                  >
                    + Add
                  </button>
                </div>

                {/* Existing configs */}
                {configs.map(cfg => {
                  const isSelected = selected?.id === cfg.id
                  return (
                    <button
                      key={cfg.id}
                      onClick={() => handleSelect(cfg)}
                      className={`w-full text-left px-4 py-2 pl-10 flex items-center justify-between transition-colors border-l-2 ${
                        isSelected
                          ? 'bg-primary/5 border-l-primary'
                          : 'bg-transparent border-l-transparent hover:bg-gray-50'
                      }`}
                    >
                      <span className={`text-xs ${isSelected ? 'text-primary font-semibold' : 'text-dark'}`}>
                        {cfg.display_name}
                      </span>
                      <span className={`w-2 h-2 rounded-full flex-shrink-0 ${cfg.active ? 'bg-green' : 'bg-lightGray'}`} />
                    </button>
                  )
                })}
              </div>
            )
          })}
        </div>
      </div>

      {/* ── Right panel ──────────────────────────────────────────────────────── */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {error && (
          <div className="px-4 py-2 bg-red/5 border-b border-red/30 text-red text-xs">
            {error}
          </div>
        )}

        {creating && (
          <CreateForm
            tenantId={session!.tenantId}
            channel={creating}
            onSaved={handleSaved}
            onCancel={() => setCreating(null)}
            onError={setError}
          />
        )}

        {selected && !creating && (
          <ConfigDetail
            tenantId={session!.tenantId}
            config={selected}
            onSaved={handleSavedUpdate}
            onDeleted={handleDeleted}
            onError={setError}
          />
        )}

        {!creating && !selected && (
          <EmptyState
            title="Selecione um canal ou adicione uma configuração"
            description={channels.length === 0 ? 'Nenhum canal configurado ainda.' : `${channels.length} canal(is) configurado(s)`}
          />
        )}
      </div>
    </div>
  )
}

// ── CreateForm ────────────────────────────────────────────────────────────────

function CreateForm({ tenantId, channel, onSaved, onCancel, onError }: {
  tenantId: string
  channel:  ChannelType
  onSaved:  () => void
  onCancel: () => void
  onError:  (msg: string) => void
}) {
  const meta = CHANNEL_META[channel]
  const [displayName, setDisplayName] = useState(`${meta.label} — ${new Date().getFullYear()}`)
  const [creds,       setCreds]       = useState<Record<string, string>>({})
  const [settings,    setSettings]    = useState<Record<string, string>>({})
  const [active,      setActive]      = useState(true)
  const [saving,      setSaving]      = useState(false)

  async function handleSave() {
    if (!displayName.trim()) { onError('Display name é obrigatório'); return }
    setSaving(true)
    try {
      await registryApi.createChannel({
        channel,
        display_name: displayName.trim(),
        active,
        credentials: creds,
        settings: Object.fromEntries(Object.entries(settings).filter(([, v]) => v !== '')),
      }, tenantId)
      onSaved()
      onError('')
    } catch (e) {
      onError((e as Error).message)
    } finally { setSaving(false) }
  }

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="flex items-center gap-3 mb-6">
        <span className="text-2xl">{meta.icon}</span>
        <div>
          <div className="text-base font-bold text-dark">Nova configuração — {meta.label}</div>
          <div className="text-xs text-gray mt-0.5">Credenciais são mascaradas após salvar</div>
        </div>
      </div>

      {/* General */}
      <Section title="Geral">
        <div className="flex gap-4 items-end">
          <div className="flex-1">
            <FieldLabel>Display Name</FieldLabel>
            <input
              className={inputCls}
              value={displayName}
              onChange={e => setDisplayName(e.target.value)}
              placeholder="ex: WhatsApp Business — Produção"
            />
          </div>
          <div className="mb-0.5">
            <FieldLabel>Ativo</FieldLabel>
            <Toggle checked={active} onChange={setActive} />
          </div>
        </div>
      </Section>

      {/* Credentials */}
      {meta.fields.length > 0 && (
        <Section title="Credenciais" subtitle="valores são mascarados após salvar">
          {meta.fields.map(f => (
            <div key={f.key} className="mb-3">
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
        </Section>
      )}

      {/* Settings */}
      {meta.settingFields.length > 0 && (
        <Section title="Configurações">
          <div className="grid grid-cols-2 gap-x-4 gap-y-3">
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

      <div className="flex gap-3 mt-2">
        <Button variant="primary" onClick={handleSave} disabled={saving}>
          {saving ? 'Salvando…' : 'Salvar configuração'}
        </Button>
        <Button variant="ghost" onClick={onCancel}>Cancelar</Button>
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
  const meta = CHANNEL_META[config.channel] ?? CHANNEL_META['webchat']

  const [displayName, setDisplayName] = useState(config.display_name)
  const [active,      setActive]      = useState(config.active)
  const [newCreds,    setNewCreds]    = useState<Record<string, string>>({})
  const [settings,    setSettings]    = useState<Record<string, string>>(
    Object.fromEntries(Object.entries(config.settings ?? {}).map(([k, v]) => [k, String(v)]))
  )
  const [saving,    setSaving]    = useState(false)
  const [deleting,  setDeleting]  = useState(false)
  const [confirmDel,setConfirmDel]= useState(false)
  const [modified,  setModified]  = useState(false)

  useEffect(() => {
    setDisplayName(config.display_name)
    setActive(config.active)
    setNewCreds({})
    setSettings(Object.fromEntries(Object.entries(config.settings ?? {}).map(([k, v]) => [k, String(v)])))
    setModified(false)
    setConfirmDel(false)
  }, [config.id])

  function mark() { setModified(true) }

  async function handleSave() {
    setSaving(true)
    try {
      const updates: Record<string, unknown> = {
        display_name: displayName.trim(),
        active,
        settings: Object.fromEntries(Object.entries(settings).filter(([, v]) => v !== '')),
      }
      const filteredCreds = Object.fromEntries(Object.entries(newCreds).filter(([, v]) => v !== ''))
      if (Object.keys(filteredCreds).length > 0) updates['credentials'] = filteredCreds
      const updated = await registryApi.updateChannel(config.id, updates, tenantId)
      setModified(false); setNewCreds({})
      onSaved(updated); onError('')
    } catch (e) {
      onError((e as Error).message)
    } finally { setSaving(false) }
  }

  async function handleDelete() {
    setDeleting(true)
    try {
      await registryApi.deleteChannel(config.id, tenantId)
      onDeleted(); onError('')
    } catch (e) {
      onError((e as Error).message)
      setDeleting(false); setConfirmDel(false)
    }
  }

  return (
    <div className="flex-1 overflow-y-auto p-6">
      {/* Header */}
      <div className="flex items-start justify-between mb-6">
        <div className="flex items-center gap-3">
          <span className="text-2xl">{meta.icon}</span>
          <div>
            <div className="text-base font-bold text-dark flex items-center gap-2">
              {config.display_name}
              {modified && <span className="text-secondary text-sm">●</span>}
            </div>
            <div className="text-xs text-gray mt-0.5">
              {config.channel} · ID: {config.id.slice(0, 8)}…
            </div>
          </div>
        </div>
        <Badge variant={config.active ? 'active' : 'default'}>
          {config.active ? 'Ativo' : 'Inativo'}
        </Badge>
      </div>

      {/* General */}
      <Section title="Geral">
        <div className="flex gap-4 items-end">
          <div className="flex-1">
            <FieldLabel>Display Name</FieldLabel>
            <input
              className={inputCls}
              value={displayName}
              onChange={e => { setDisplayName(e.target.value); mark() }}
            />
          </div>
          <div className="mb-0.5">
            <FieldLabel>Ativo</FieldLabel>
            <Toggle checked={active} onChange={v => { setActive(v); mark() }} />
          </div>
        </div>
      </Section>

      {/* Credentials */}
      {meta.fields.length > 0 && (
        <Section title="Credenciais" subtitle="deixe em branco para manter o valor atual">
          {meta.fields.map(f => (
            <div key={f.key} className="mb-3">
              <FieldLabel>{f.label}</FieldLabel>
              <div className="flex gap-2 items-center">
                <div className={`${inputCls} text-gray flex-1 font-mono`}>
                  {config.credentials[f.key] ?? '—'}
                </div>
                <span className="text-gray text-xs">→</span>
                <input
                  type={f.sensitive ? 'password' : 'text'}
                  className={`${inputCls} flex-1 font-mono`}
                  value={newCreds[f.key] ?? ''}
                  onChange={e => { setNewCreds(prev => ({ ...prev, [f.key]: e.target.value })); mark() }}
                  placeholder="Novo valor (opcional)"
                  autoComplete="off"
                />
              </div>
            </div>
          ))}
        </Section>
      )}

      {/* Settings */}
      {meta.settingFields.length > 0 && (
        <Section title="Configurações">
          <div className="grid grid-cols-2 gap-x-4 gap-y-3">
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

      {/* Metadata */}
      <div className="flex gap-6 text-xs text-gray mb-6">
        <span>Criado: {new Date(config.created_at).toLocaleDateString('pt-BR')}</span>
        <span>Atualizado: {new Date(config.updated_at).toLocaleDateString('pt-BR')}</span>
        <span>Por: {config.created_by}</span>
      </div>

      {/* Actions */}
      <div className="flex gap-3 items-center">
        {modified && (
          <Button variant="primary" onClick={handleSave} disabled={saving}>
            {saving ? 'Salvando…' : 'Salvar alterações'}
          </Button>
        )}

        {!confirmDel ? (
          <Button
            variant="ghost"
            onClick={() => setConfirmDel(true)}
            className="text-red border-red/30 hover:bg-red/5"
          >
            Excluir
          </Button>
        ) : (
          <>
            <Button
              variant="primary"
              onClick={handleDelete}
              disabled={deleting}
              className="bg-red border-red hover:bg-red/90"
            >
              {deleting ? 'Excluindo…' : 'Confirmar exclusão'}
            </Button>
            <Button variant="ghost" onClick={() => setConfirmDel(false)}>Cancelar</Button>
          </>
        )}
      </div>
    </div>
  )
}

// ── Shared micro-components ───────────────────────────────────────────────────

function Section({ title, subtitle, children }: {
  title: string; subtitle?: string; children: React.ReactNode
}) {
  return (
    <div className="mb-5 pb-5 border-b border-lightGray last:border-0">
      <div className="text-xs font-bold text-gray uppercase tracking-wider mb-3">
        {title}
        {subtitle && <span className="font-normal ml-1 normal-case text-gray/60"> — {subtitle}</span>}
      </div>
      {children}
    </div>
  )
}

function FieldLabel({ children }: { children: React.ReactNode }) {
  return (
    <label className="block text-xs font-medium text-gray mb-1">{children}</label>
  )
}

function Toggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      className={`relative w-9 h-5 rounded-full transition-colors ${
        checked ? 'bg-primary' : 'bg-lightGray'
      }`}
    >
      <span
        className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${
          checked ? 'translate-x-4' : 'translate-x-0.5'
        }`}
      />
    </button>
  )
}

const inputCls =
  'w-full px-3 py-1.5 text-xs border border-lightGray rounded-md focus:outline-none focus:border-secondary bg-white text-dark placeholder-gray/50'

export default ChannelsPage
