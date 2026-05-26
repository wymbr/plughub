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
import { useTranslation } from 'react-i18next'
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
  'w-full px-3 py-1.5 text-xs border border-border-strong rounded-md focus:outline-none focus:border-primary bg-white placeholder-muted-light'

function FieldLabel({ children }: { children: React.ReactNode }) {
  return <label className="block text-xs font-medium text-dark mb-1">{children}</label>
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

// ── ChannelAccountCard ─────────────────────────────────────────────────────────

interface Props {
  config:    GatewayConfig
  pools:     Pool[]
  onUpdated: () => void
  onDeleted: () => void
}

const ChannelAccountCard: React.FC<Props> = ({ config, pools, onUpdated, onDeleted }) => {
  const { t } = useTranslation('channels')
  const { tenantId } = useAuth()
  const meta = CHANNEL_META[config.channel]

  const [expanded,   setExpanded]   = useState(true)
  const [endpoints,  setEndpoints]  = useState<ChannelEndpoint[]>([])
  const [epLoading,  setEpLoading]  = useState(true)

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

  const [addingEp,   setAddingEp]   = useState(false)
  const [editingEp,  setEditingEp]  = useState<string | null>(null)
  const [epError,    setEpError]    = useState<string | null>(null)

  const loadEndpoints = useCallback(async () => {
    if (!tenantId) return
    setEpLoading(true)
    try {
      const result = await registryApi.listChannelEndpoints(tenantId, config.channel)
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
    if (!tenantId || !confirm(t('endpoint.deleteConfirm'))) return
    try {
      await registryApi.deleteChannelEndpoint(id, tenantId)
      loadEndpoints()
    } catch (e) {
      setEpError(String(e))
    }
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="border border-border rounded-lg overflow-hidden">

      {/* ── Card header ── */}
      <div className="flex items-center justify-between px-4 py-3 bg-surface-muted border-b border-border">
        <div className="flex items-center gap-3">
          <span className={`w-2.5 h-2.5 rounded-full ${config.active ? 'bg-green' : 'bg-border-strong'}`} />
          <div>
            <span className="text-sm font-semibold text-dark">{config.display_name}</span>
            <span className="ml-2 text-xs text-muted-light">
              {meta?.icon} {meta?.label ?? config.channel} · ID {config.id.slice(0, 8)}…
            </span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
            config.active ? 'bg-green-light text-green-text' : 'bg-surface-alt text-muted'
          }`}>
            {config.active ? t('status.active') : t('status.inactive')}
          </span>
          <button
            onClick={() => { setExpanded(v => !v); setEditingCreds(false) }}
            className="text-muted-light hover:text-dark text-xs px-2"
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
                <p className="text-xs font-medium text-muted mb-1">{t('integration.credentialsLabel')}</p>
                <div className="flex flex-wrap gap-2">
                  {meta && meta.fields.length > 0 ? meta.fields.map(f => (
                    <span key={f.key} className="text-xs text-muted bg-surface-alt px-2 py-0.5 rounded font-mono">
                      {f.label}: {config.credentials?.[f.key] ? '••••••' : '—'}
                    </span>
                  )) : (
                    <span className="text-xs text-muted-light">{t('integration.credentialsNoFields')}</span>
                  )}
                </div>
                {meta && meta.settingFields.length > 0 && (
                  <div className="flex flex-wrap gap-2 mt-1">
                    {meta.settingFields.map(f => {
                      const val = (config.settings as Record<string, unknown>)?.[f.key]
                      return val ? (
                        <span key={f.key} className="text-xs text-muted bg-primary-light px-2 py-0.5 rounded">
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
                  {t('integration.editCredentialsBtn')}
                </button>
                {!confirmDel ? (
                  <button
                    onClick={() => setConfirmDel(true)}
                    className="text-xs text-red hover:text-red-text"
                  >
                    {t('integration.deleteBtn')}
                  </button>
                ) : (
                  <>
                    <button
                      onClick={handleDeleteConfig}
                      disabled={deleting}
                      className="text-xs text-red-text font-semibold hover:text-red-text/80 disabled:opacity-50"
                    >
                      {deleting ? t('integration.deleting') : t('integration.deleteConfirm')}
                    </button>
                    <button
                      onClick={() => setConfirmDel(false)}
                      className="text-xs text-muted"
                    >
                      {t('actions.cancel')}
                    </button>
                  </>
                )}
              </div>
            </div>
          ) : (
            /* Credential edit form */
            <div className="bg-primary-light/40 border border-primary/20 rounded-lg p-4 space-y-3">
              <p className="text-xs font-semibold text-primary">{t('integration.editCredentialsTitle')}</p>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <FieldLabel>{t('form.displayName')}</FieldLabel>
                  <input
                    className={inputCls}
                    value={displayName}
                    onChange={e => setDisplayName(e.target.value)}
                  />
                </div>
                <div className="flex items-center gap-2 pt-4">
                  <FieldLabel>{t('form.active')}</FieldLabel>
                  <Toggle checked={active} onChange={setActive} />
                </div>
              </div>

              {meta && meta.fields.length > 0 && (
                <div>
                  <p className="text-xs font-medium text-muted mb-2">
                    {t('integration.credentialsSection')} <span className="text-muted-light">{t('integration.credentialsKeepCurrent')}</span>
                  </p>
                  <div className="grid grid-cols-2 gap-3">
                    {meta.fields.map(f => (
                      <div key={f.key}>
                        <FieldLabel>{f.label}</FieldLabel>
                        <input
                          type={f.sensitive ? 'password' : 'text'}
                          className={`${inputCls} font-mono`}
                          value={newCreds[f.key] ?? ''}
                          onChange={e => setNewCreds(p => ({ ...p, [f.key]: e.target.value }))}
                          placeholder={t('form.newValueOptional')}
                          autoComplete="off"
                        />
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {meta && meta.settingFields.length > 0 && (
                <div>
                  <p className="text-xs font-medium text-muted mb-2">{t('integration.settingsSection')}</p>
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

              {credError && <p className="text-xs text-red-text">{credError}</p>}

              <div className="flex gap-2">
                <button
                  onClick={handleSaveCreds}
                  disabled={saving}
                  className="px-3 py-1.5 rounded text-xs font-semibold bg-primary text-white disabled:opacity-40 hover:bg-primary-dark transition-colors"
                >
                  {saving ? t('actions.saving') : t('integration.saveCredentials')}
                </button>
                <button
                  onClick={() => { setEditingCreds(false); setCredError(null) }}
                  className="px-3 py-1.5 rounded text-xs border border-border-strong text-muted hover:text-dark transition-colors"
                >
                  {t('actions.cancel')}
                </button>
              </div>
            </div>
          )}

          {/* ── Endpoints section ── */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <p className="text-xs font-medium text-muted">
                {t('endpoint.title')}
                <span className="ml-1 text-muted-light">
                  — {t(`identifierHints.${config.channel}`, t('endpoint.identifierHintFallback'))}
                </span>
              </p>
              <button
                onClick={() => { setAddingEp(true); setEpError(null) }}
                disabled={addingEp}
                className="text-xs text-secondary hover:text-primary font-medium disabled:opacity-40"
              >
                {t('endpoint.add')}
              </button>
            </div>

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

            {epLoading ? (
              <p className="text-xs text-muted-light py-2">{t('loading')}</p>
            ) : endpoints.length === 0 && !addingEp ? (
              <p className="text-xs text-muted-light py-2 italic">
                {t('endpoint.noEndpoints', { channel: meta?.label ?? config.channel })}
              </p>
            ) : (
              <table className="w-full text-xs border-collapse">
                <thead>
                  <tr className="text-left text-muted-light border-b border-border">
                    <th className="py-1.5 pr-4 font-medium">{t('endpoint.colIdentifier')}</th>
                    <th className="py-1.5 pr-4 font-medium">{t('endpoint.colDisplayName')}</th>
                    <th className="py-1.5 pr-4 font-medium">{t('endpoint.colPool')}</th>
                    <th className="py-1.5 pr-4 font-medium">{t('endpoint.colStatus')}</th>
                    <th className="py-1.5 font-medium"></th>
                  </tr>
                </thead>
                <tbody>
                  {endpoints.map(ep => (
                    <React.Fragment key={ep.id}>
                      <tr className="border-b border-border hover:bg-surface-muted">
                        <td className="py-2 pr-4 font-mono text-dark">{ep.identifier}</td>
                        <td className="py-2 pr-4 text-muted">{ep.display_name}</td>
                        <td className="py-2 pr-4 text-muted">{ep.pool_id}</td>
                        <td className="py-2 pr-4">
                          <span className={`px-2 py-0.5 rounded-full font-medium ${
                            ep.active ? 'bg-green-light text-green-text' : 'bg-surface-alt text-muted'
                          }`}>
                            {ep.active ? t('status.active') : t('status.inactive')}
                          </span>
                        </td>
                        <td className="py-2 flex gap-2 justify-end">
                          <button
                            onClick={() => setEditingEp(ep.id === editingEp ? null : ep.id)}
                            className="text-secondary hover:text-primary disabled:opacity-40"
                          >
                            {ep.id === editingEp ? t('actions.close') : t('actions.edit')}
                          </button>
                          <button
                            onClick={() => handleDeleteEp(ep.id)}
                            className="text-red hover:text-red-text"
                          >
                            {t('actions.delete')}
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
            {epError && <p className="text-xs text-red-text mt-1">{epError}</p>}
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

const IDENTIFIER_PLACEHOLDER: Record<string, string> = {
  webchat:  'support',
  whatsapp: '+5511999999999',
  voice:    '+5511000000',
  sms:      '55119',
  email:    'support@company.com',
  webhook:  'salesforce',
}

const EndpointForm: React.FC<EpFormProps> = ({
  channel, gatewayConfigId, pools, tenantId, existing, onSaved, onCancel,
}) => {
  const { t } = useTranslation('channels')
  const isEdit = !!existing

  const [identifier,   setIdentifier]   = useState(existing?.identifier   ?? '')
  const [displayName,  setDisplayName]  = useState(existing?.display_name ?? '')
  const [poolId,       setPoolId]       = useState(existing?.pool_id       ?? '')
  const [active,       setActive]       = useState(existing?.active        ?? true)
  const [saving,       setSaving]       = useState(false)
  const [error,        setError]        = useState<string | null>(null)

  async function handleSave() {
    if (!identifier.trim())   { setError(t('errors.identifierRequired'));   return }
    if (!poolId)               { setError(t('errors.poolRequired'));         return }
    if (!displayName.trim())   { setError(t('errors.displayNameRequired')); return }

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
    <div className="bg-surface-muted border border-border rounded-lg p-3 space-y-2">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <FieldLabel>{t('form.identifier')}</FieldLabel>
          <input
            className={`${inputCls} font-mono${isEdit ? ' bg-surface-alt text-muted' : ''}`}
            value={identifier}
            placeholder={IDENTIFIER_PLACEHOLDER[channel] ?? ''}
            readOnly={isEdit}
            onChange={e => setIdentifier(e.target.value)}
          />
        </div>
        <div>
          <FieldLabel>{t('form.pool')}</FieldLabel>
          <select
            className={inputCls}
            value={poolId}
            onChange={e => setPoolId(e.target.value)}
          >
            <option value="">{t('form.selectPool')}</option>
            {pools.map(p => (
              <option key={p.pool_id} value={p.pool_id}>{p.pool_id}</option>
            ))}
          </select>
        </div>
        <div>
          <FieldLabel>{t('form.displayName')}</FieldLabel>
          <input
            className={inputCls}
            value={displayName}
            placeholder={t('form.displayNamePlaceholder')}
            onChange={e => setDisplayName(e.target.value)}
          />
        </div>
        <div className="flex items-end pb-1 gap-2">
          <FieldLabel>{t('form.active')}</FieldLabel>
          <Toggle checked={active} onChange={setActive} />
        </div>
      </div>

      {error && <p className="text-xs text-red-text">{error}</p>}

      <div className="flex gap-2">
        <button
          onClick={handleSave}
          disabled={saving}
          className="px-3 py-1.5 rounded text-xs font-semibold bg-primary text-white disabled:opacity-40 hover:bg-primary-dark transition-colors"
        >
          {saving ? t('actions.saving') : isEdit ? t('actions.saveChanges') : t('endpoint.addBtn')}
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

export default ChannelAccountCard
