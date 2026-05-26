/**
 * config-channels/index.tsx
 * Configuration → Channels — /config/channels
 *
 * Hierarchical view: GatewayConfig (integration account) is the parent;
 * ChannelEndpoints (phone numbers / slugs / addresses) are children.
 *
 * Per channel tab:
 *   ┌─ Account: "WhatsApp Business — Produção" [active] ──────────┐
 *   │  Credentials: access_token=••••  waba_id=9876543210         │
 *   │  Endpoints:                                                   │
 *   │    +5511999999999  Suporte   retencao_humano  active         │
 *   │    +5511888888888  Vendas    vendas_humano     active        │
 *   │    [+ Add endpoint]                                           │
 *   └──────────────────────────────────────────────────────────────┘
 *   [+ Add integration]
 *
 * Special case — Webhook channel: no GatewayConfig needed, shows
 * ChannelEndpoints directly (standalone, no parent account).
 *
 * Runtime Settings (Config API): sub-tab only for webchat.
 */
import React, { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import ChannelAccountCard from './ChannelAccountCard'
import WebChatConfigPage  from './WebChatConfigPage'
import WebhookConfigPage  from './WebhookConfigPage'
import { ChannelEndpointList } from './ChannelEndpointList'
import { CHANNEL_META } from './channel-meta'
import type { GatewayConfig, Pool } from '@/types'
import type { ChannelEndpointChannel } from '@/types'
import { useAuth } from '@/auth/useAuth'
import * as registryApi from '@/api/registry'

// ── Channel tabs ───────────────────────────────────────────────────────────────

type ChannelTab = ChannelEndpointChannel

const CHANNEL_TABS: { id: ChannelTab; icon: string }[] = [
  { id: 'webchat',  icon: '💻' },
  { id: 'whatsapp', icon: '💬' },
  { id: 'voice',    icon: '📞' },
  { id: 'email',    icon: '✉️'  },
  { id: 'sms',      icon: '📱' },
  { id: 'webhook',  icon: '🔗' },
]

// Channels rendered without GatewayConfig parent (no API account needed)
const STANDALONE_CHANNELS = new Set<ChannelTab>(['webhook'])

// Channels with runtime Settings page (Config API)
const HAS_SETTINGS = new Set<ChannelTab>(['webchat'])

type SubTab = 'accounts' | 'settings'

// ── New integration form ───────────────────────────────────────────────────────

interface NewIntegrationFormProps {
  channel:   string
  tenantId:  string
  pools:     Pool[]
  onSaved:   () => void
  onCancel:  () => void
}

const NewIntegrationForm: React.FC<NewIntegrationFormProps> = ({ channel, tenantId, pools, onSaved, onCancel }) => {
  const { t } = useTranslation('channels')
  const meta = CHANNEL_META[channel]
  const inputCls = 'w-full px-3 py-1.5 text-xs border border-border-strong rounded-md focus:outline-none focus:border-primary bg-white placeholder-muted-light'

  // Integration fields
  const [displayName, setDisplayName] = useState(`${meta?.label ?? channel} — ${new Date().getFullYear()}`)
  const [creds,       setCreds]       = useState<Record<string, string>>({})
  const [settings,    setSettings]    = useState<Record<string, string>>({})
  const [active,      setActive]      = useState(true)

  // First endpoint fields
  const [epIdentifier,   setEpIdentifier]   = useState('')
  const [epDisplayName,  setEpDisplayName]  = useState('')
  const [epPoolId,       setEpPoolId]       = useState('')

  const [saving, setSaving] = useState(false)
  const [error,  setError]  = useState<string | null>(null)

  async function handleSave() {
    if (!displayName.trim()) { setError(t('errors.displayNameRequired')); return }
    const hasEpData = epIdentifier.trim() || epPoolId || epDisplayName.trim()
    if (hasEpData) {
      if (!epIdentifier.trim()) { setError(t('errors.endpointIdentifierRequired')); return }
      if (!epPoolId)             { setError(t('errors.endpointPoolRequired'));       return }
      if (!epDisplayName.trim()) { setError(t('errors.endpointDisplayNameRequired')); return }
    }

    setSaving(true); setError(null)
    try {
      const created = await registryApi.createChannel({
        channel:      channel as import('@/types').ChannelType,
        display_name: displayName.trim(),
        active,
        credentials:  Object.fromEntries(Object.entries(creds).filter(([, v]) => v !== '')),
        settings:     Object.fromEntries(Object.entries(settings).filter(([, v]) => v !== '')),
      }, tenantId)

      if (epIdentifier.trim() && epPoolId) {
        await registryApi.createChannelEndpoint({
          channel:           channel as import('@/types').ChannelEndpointChannel,
          identifier:        epIdentifier.trim(),
          pool_id:           epPoolId,
          display_name:      epDisplayName.trim() || epIdentifier.trim(),
          active:            true,
          gateway_config_id: created.id,
        }, tenantId)
      }

      onSaved()
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  const identifierHint = t(`identifierHints.${channel}`, t('form.identifierHintFallback'))

  return (
    <div className="border border-primary/30 rounded-lg p-4 bg-primary-light/30 space-y-4 mb-4">
      <p className="text-xs font-semibold text-primary">
        {meta?.icon} {t('integration.new', { channel: meta?.label ?? channel })}
      </p>

      {/* ── Integration name + active ── */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="text-xs font-medium text-dark">{t('form.displayName')}</label>
          <div className="flex items-center gap-2">
            <label className="text-xs font-medium text-dark">{t('form.active')}</label>
            <button
              type="button"
              onClick={() => setActive(v => !v)}
              style={{ cursor: 'pointer', padding: 0, border: 'none', background: 'none', lineHeight: 0 }}
            >
              <span style={{
                position: 'relative', display: 'inline-block',
                width: 36, height: 20, borderRadius: 9999,
                backgroundColor: active ? '#1B4F8A' : '#D1D5DB',
                transition: 'background-color 200ms', overflow: 'hidden',
              }}>
                <span style={{
                  position: 'absolute', top: 2,
                  left: active ? 18 : 2,
                  width: 16, height: 16, borderRadius: 9999,
                  backgroundColor: 'white',
                  transition: 'left 200ms',
                }} />
              </span>
            </button>
          </div>
        </div>
        <input
          className={inputCls}
          value={displayName}
          onChange={e => setDisplayName(e.target.value)}
          placeholder={`e.g. ${meta?.label ?? channel} — Production`}
        />
      </div>

      {/* ── API credentials ── */}
      {meta && meta.fields.length > 0 && (
        <div>
          <p className="text-xs font-medium text-muted mb-2">
            {t('integration.credentialsSection')} <span className="text-muted-light">{t('integration.credentialsEncrypted')}</span>
          </p>
          <div className="grid grid-cols-2 gap-3">
            {meta.fields.map(f => (
              <div key={f.key}>
                <label className="block text-xs font-medium text-dark mb-1">{f.label}</label>
                <input
                  type={f.sensitive ? 'password' : 'text'}
                  className={`${inputCls} font-mono`}
                  value={creds[f.key] ?? ''}
                  onChange={e => setCreds(p => ({ ...p, [f.key]: e.target.value }))}
                  placeholder={f.placeholder}
                  autoComplete="off"
                />
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Settings ── */}
      {meta && meta.settingFields.length > 0 && (
        <div>
          <p className="text-xs font-medium text-muted mb-2">{t('integration.settingsSection')}</p>
          <div className="grid grid-cols-2 gap-3">
            {meta.settingFields.map(f => (
              <div key={f.key}>
                <label className="block text-xs font-medium text-dark mb-1">{f.label}</label>
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

      {/* ── First endpoint ── */}
      {channel !== 'webhook' && (
        <div className="border-t border-primary/10 pt-4">
          <p className="text-xs font-medium text-dark mb-1">
            {t('form.firstEndpoint')}
            <span className="ml-1 text-muted-light font-normal">{t('form.firstEndpointOptional')}</span>
          </p>
          <p className="text-xs text-muted-light mb-3">{identifierHint}</p>
          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="block text-xs font-medium text-dark mb-1">{t('form.identifier')}</label>
              <input
                className={`${inputCls} font-mono`}
                value={epIdentifier}
                onChange={e => setEpIdentifier(e.target.value)}
                placeholder={meta?.label ?? channel}
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-dark mb-1">{t('form.pool')}</label>
              <select
                className={inputCls}
                value={epPoolId}
                onChange={e => setEpPoolId(e.target.value)}
              >
                <option value="">{t('form.selectPool')}</option>
                {pools.map(p => (
                  <option key={p.pool_id} value={p.pool_id}>{p.pool_id}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-dark mb-1">{t('form.displayName')}</label>
              <input
                className={inputCls}
                value={epDisplayName}
                onChange={e => setEpDisplayName(e.target.value)}
                placeholder={t('form.displayNamePlaceholder')}
              />
            </div>
          </div>
        </div>
      )}

      {error && <p className="text-xs text-red-text">{error}</p>}

      <div className="flex gap-2">
        <button
          onClick={handleSave}
          disabled={saving}
          className="px-3 py-1.5 rounded text-xs font-semibold bg-primary text-white disabled:opacity-40 hover:bg-primary-dark transition-colors"
        >
          {saving ? t('actions.saving') : t('integration.create')}
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

// ── ChannelPanel — main content for one channel tab ───────────────────────────

interface ChannelPanelProps {
  channel: ChannelTab
}

const ChannelPanel: React.FC<ChannelPanelProps> = ({ channel }) => {
  const { t } = useTranslation('channels')
  const { tenantId } = useAuth()
  const [configs,    setConfigs]    = useState<GatewayConfig[]>([])
  const [pools,      setPools]      = useState<Pool[]>([])
  const [loading,    setLoading]    = useState(true)
  const [error,      setError]      = useState<string | null>(null)
  const [addingNew,  setAddingNew]  = useState(false)

  const load = useCallback(async () => {
    if (!tenantId) return
    setLoading(true); setError(null)
    try {
      const [allConfigs, poolsResult] = await Promise.all([
        registryApi.listChannels(tenantId),
        registryApi.listPools(tenantId).then(r => r.items ?? []),
      ])
      setConfigs((allConfigs.items ?? []).filter((c: GatewayConfig) => c.channel === channel))
      setPools(poolsResult)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [tenantId, channel])

  useEffect(() => { load() }, [load])

  if (loading) return <p className="text-sm text-muted-light py-4">{t('loading')}</p>
  if (error)   return <p className="text-sm text-red-text py-4">⚠ {error}</p>

  // Webhook: standalone endpoints, no GatewayConfig parent
  if (STANDALONE_CHANNELS.has(channel)) {
    return (
      <div>
        <p className="text-xs text-muted mb-4">{t('endpoint.webhookNote')}</p>
        <ChannelEndpointList channel={channel} />
      </div>
    )
  }

  const meta = CHANNEL_META[channel]

  return (
    <div className="space-y-3">
      {addingNew && (
        <NewIntegrationForm
          channel={channel}
          tenantId={tenantId!}
          pools={pools}
          onSaved={() => { setAddingNew(false); load() }}
          onCancel={() => setAddingNew(false)}
        />
      )}

      {configs.length === 0 && !addingNew ? (
        <div className="text-center py-10 border border-dashed border-border rounded-lg">
          <p className="text-sm text-muted-light">
            {meta?.icon} {t('integration.noConfigurations', { channel: meta?.label ?? channel })}
          </p>
          <p className="text-xs text-muted-light mt-1">{t('integration.noConfigurationsHint')}</p>
          <button
            onClick={() => setAddingNew(true)}
            className="mt-3 px-4 py-2 rounded text-xs font-semibold bg-primary text-white hover:bg-primary-dark transition-colors"
          >
            {t('integration.addFirst', { channel: meta?.label ?? channel })}
          </button>
        </div>
      ) : (
        <>
          {configs.map(cfg => (
            <ChannelAccountCard
              key={cfg.id}
              config={cfg}
              pools={pools}
              onUpdated={load}
              onDeleted={load}
            />
          ))}
          {!addingNew && (
            <button
              onClick={() => setAddingNew(true)}
              className="w-full py-2 border border-dashed border-border rounded-lg text-xs text-muted-light hover:text-primary hover:border-primary transition-colors"
            >
              {t('integration.addAnother', { channel: meta?.label ?? channel })}
            </button>
          )}
        </>
      )}
    </div>
  )
}

// ── Root component ─────────────────────────────────────────────────────────────

const ConfigChannelsIndex: React.FC = () => {
  const { t } = useTranslation('channels')
  const [activeChannel, setActiveChannel] = useState<ChannelTab>('webchat')
  const [activeSubTab,  setActiveSubTab]  = useState<SubTab>('accounts')

  function handleChannelChange(ch: ChannelTab) {
    setActiveChannel(ch)
    setActiveSubTab('accounts')
  }

  return (
    <div>
      {/* ── Channel selector ── */}
      <div className="mb-0 border-b border-border flex gap-6">
        {CHANNEL_TABS.map(tab => (
          <button
            key={tab.id}
            onClick={() => handleChannelChange(tab.id)}
            className={`py-3 px-1 font-semibold text-sm transition-colors border-b-2 flex items-center gap-1.5 ${
              activeChannel === tab.id
                ? 'text-primary border-primary'
                : 'text-muted border-transparent hover:text-dark'
            }`}
          >
            <span>{tab.icon}</span>
            <span>{t(`tabs.${tab.id}`)}</span>
          </button>
        ))}
      </div>

      {/* ── Sub-tab (Settings only shown for webchat) ── */}
      {HAS_SETTINGS.has(activeChannel) && (
        <div className="mt-4 mb-6 flex gap-4 border-b border-border">
          {(['accounts', 'settings'] as SubTab[]).map(sub => (
            <button
              key={sub}
              onClick={() => setActiveSubTab(sub)}
              className={`pb-2 px-1 text-xs font-medium transition-colors border-b-2 ${
                activeSubTab === sub
                  ? 'text-primary border-primary'
                  : 'text-muted border-transparent hover:text-dark'
              }`}
            >
              {t(`subTabs.${sub}`)}
            </button>
          ))}
        </div>
      )}

      {/* ── Content ── */}
      <div className={HAS_SETTINGS.has(activeChannel) ? '' : 'mt-6'}>
        {activeSubTab === 'accounts' && (
          <ChannelPanel channel={activeChannel} />
        )}
        {activeSubTab === 'settings' && activeChannel === 'webchat' && (
          <WebChatConfigPage />
        )}
        {activeSubTab === 'settings' && activeChannel === 'webhook' && (
          <WebhookConfigPage />
        )}
      </div>
    </div>
  )
}

export default ConfigChannelsIndex
