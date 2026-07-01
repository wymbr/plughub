/**
 * LlmAccountsPage.tsx — CRUD for LLM Accounts (ai-gateway multi-account catalog)
 *
 * Stored in config-api namespace: llm_accounts
 * Each entry:
 *   key   — account id  (e.g. "realtime_primary")
 *   value — { provider, display_name, rpm_limit, tpm_limit, active }
 *
 * The API key itself is NEVER stored here — only in the env var
 * PLUGHUB_LLM_ACCOUNT_<ID_UPPER_SNAKE>_API_KEY on the ai-gateway service
 * (see llm_accounts_catalog.py). This page only manages the catalog metadata;
 * association to a Pool happens on the Pool edit drawer (Resources > Pools).
 *
 * Write operations require Bearer + ABAC config.plataforma (same pattern as
 * SkillsPage / competency_skills — no admin-token box).
 */
import React, { useCallback, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import {
  useNamespace,
  putConfig,
  deleteConfig,
} from '@/modules/config-plataforma/api/config-hooks'
import Spinner from '@/components/ui/Spinner'

const NS = 'llm_accounts'

// ── helpers ────────────────────────────────────────────────────────────────────

interface LlmAccountValue {
  provider:     'anthropic' | 'openai'
  display_name: string
  rpm_limit:    number
  tpm_limit:    number
  active:       boolean
}

function envVarName(id: string): string {
  const normalized = id.replace(/[^A-Za-z0-9]+/g, '_').replace(/^_+|_+$/g, '').toUpperCase()
  return `PLUGHUB_LLM_ACCOUNT_${normalized}_API_KEY`
}

function getAccount(value: unknown): LlmAccountValue {
  const v = (typeof value === 'object' && value !== null) ? (value as Record<string, unknown>) : {}
  return {
    provider:     v.provider === 'openai' ? 'openai' : 'anthropic',
    display_name: typeof v.display_name === 'string' ? v.display_name : '',
    rpm_limit:    typeof v.rpm_limit === 'number' ? v.rpm_limit : 60,
    tpm_limit:    typeof v.tpm_limit === 'number' ? v.tpm_limit : 100000,
    active:       v.active !== false,
  }
}

const EMPTY: LlmAccountValue = { provider: 'anthropic', display_name: '', rpm_limit: 60, tpm_limit: 100000, active: true }

// ── AccountForm — shared add/edit fields ────────────────────────────────────────

function AccountForm({
  value, onChange, t,
}: {
  value: LlmAccountValue
  onChange: (v: LlmAccountValue) => void
  t: (k: string) => string
}) {
  return (
    <div className="flex flex-col gap-2 flex-1">
      <div className="flex gap-2 items-center">
        <select
          value={value.provider}
          onChange={e => onChange({ ...value, provider: e.target.value as 'anthropic' | 'openai' })}
          className="text-xs px-2 py-1.5 border border-border-strong rounded focus:outline-none focus:border-primary bg-white"
        >
          <option value="anthropic">Anthropic</option>
          <option value="openai">OpenAI</option>
        </select>
        <input
          value={value.display_name}
          onChange={e => onChange({ ...value, display_name: e.target.value })}
          placeholder={t('llmAccounts.displayNamePlaceholder')}
          className="flex-1 text-xs px-2 py-1.5 border border-border-strong rounded focus:outline-none focus:border-primary bg-white"
        />
      </div>
      <div className="flex gap-2 items-center">
        <label className="flex items-center gap-1 text-2xs text-muted-light">
          RPM
          <input
            type="number" min={1}
            value={value.rpm_limit}
            onChange={e => onChange({ ...value, rpm_limit: Number(e.target.value) })}
            className="w-20 text-xs px-2 py-1 border border-border-strong rounded focus:outline-none focus:border-primary bg-white"
          />
        </label>
        <label className="flex items-center gap-1 text-2xs text-muted-light">
          TPM
          <input
            type="number" min={1}
            value={value.tpm_limit}
            onChange={e => onChange({ ...value, tpm_limit: Number(e.target.value) })}
            className="w-24 text-xs px-2 py-1 border border-border-strong rounded focus:outline-none focus:border-primary bg-white"
          />
        </label>
        <label className="flex items-center gap-1.5 text-xs text-muted whitespace-nowrap ml-2">
          <input type="checkbox" checked={value.active} onChange={e => onChange({ ...value, active: e.target.checked })} className="rounded" />
          {t('llmAccounts.active')}
        </label>
      </div>
    </div>
  )
}

// ── LlmAccountsPage ──────────────────────────────────────────────────────────────

const LlmAccountsPage: React.FC = () => {
  const { session } = useAuth()
  const { t } = useTranslation('configRecursos')
  const tenantId = session?.tenantId ?? ''

  const { entries, loading, error: loadError, reload } = useNamespace(tenantId, NS)
  const sortedKeys = Object.keys(entries).sort()

  const adminToken = session?.accessToken ?? ''
  const [editingKey, setEditingKey] = useState<string | null>(null)
  const [editValue,  setEditValue]  = useState<LlmAccountValue>(EMPTY)

  const [isAdding, setIsAdding] = useState(false)
  const [newKey,   setNewKey]   = useState('')
  const [newValue, setNewValue] = useState<LlmAccountValue>(EMPTY)

  const [saving, setSaving] = useState(false)
  const [deletingKey, setDeletingKey] = useState<string | null>(null)
  const [opError, setOpError] = useState<string | null>(null)

  const handleAddStart = () => {
    setIsAdding(true)
    setNewKey(''); setNewValue(EMPTY)
    setEditingKey(null)
    setOpError(null)
  }

  const handleAddSave = useCallback(async () => {
    const key = newKey.trim()
    if (!key) { setOpError(t('llmAccounts.keyRequired')); return }
    if (!/^[a-z0-9_]+$/.test(key)) { setOpError(t('llmAccounts.keyInvalid')); return }
    if (entries[key]) { setOpError(t('llmAccounts.keyExists', { key })); return }
    if (!adminToken) { setOpError(t('competencySkills.adminRequired')); return }
    setSaving(true); setOpError(null)
    try {
      await putConfig(NS, key, newValue, null, '', adminToken)
      reload()
      setIsAdding(false)
    } catch (e) {
      setOpError(String(e))
    } finally {
      setSaving(false)
    }
  }, [newKey, newValue, adminToken, entries, reload, t])

  const handleEditStart = (key: string) => {
    setEditValue(getAccount(entries[key]?.value))
    setEditingKey(key)
    setIsAdding(false)
    setOpError(null)
  }

  const handleEditSave = useCallback(async (key: string) => {
    if (!adminToken) { setOpError(t('competencySkills.adminRequired')); return }
    setSaving(true); setOpError(null)
    try {
      await putConfig(NS, key, editValue, null, '', adminToken)
      reload()
      setEditingKey(null)
    } catch (e) {
      setOpError(String(e))
    } finally {
      setSaving(false)
    }
  }, [editValue, adminToken, reload, t])

  const handleDelete = useCallback(async (key: string) => {
    if (!adminToken) { setOpError(t('competencySkills.adminRequiredDelete')); return }
    if (!window.confirm(t('llmAccounts.confirmDelete', { key }))) return
    setDeletingKey(key); setOpError(null)
    try {
      await deleteConfig(NS, key, null, '', adminToken)
      reload()
    } catch (e) {
      setOpError(String(e))
    } finally {
      setDeletingKey(null)
    }
  }, [adminToken, reload, t])

  return (
    <div className="flex flex-col gap-4">
      {/* Info banner */}
      <div className="bg-info-light border border-info/30 rounded px-4 py-2.5 text-sm text-info-text">
        {t('llmAccounts.infoBanner', {
          defaultValue:
            'LLM Accounts are referenced by id from Pools (Resources > Pools) to steer AI reasoning to a specific quota/account, with graceful fallback. The API key itself is never stored here — set it as an environment variable on ai-gateway.',
        })}
      </div>

      {/* Controls row */}
      <div className="flex items-center gap-3 flex-wrap">
        <button
          onClick={handleAddStart}
          disabled={isAdding}
          className="ml-auto px-3 py-1.5 text-xs font-semibold bg-primary text-white rounded hover:bg-primary-dark disabled:opacity-40 transition-colors"
        >
          {t('llmAccounts.newAccount')}
        </button>
      </div>

      {(opError || loadError) && (
        <div className="bg-red-light border border-red/30 text-red-text px-3 py-2 rounded text-xs flex justify-between items-center">
          <span>{opError ?? loadError}</span>
          <button onClick={() => setOpError(null)} className="ml-3 font-bold leading-none">✕</button>
        </div>
      )}

      <div className="border border-border rounded overflow-hidden">
        <div className="flex gap-4 px-4 py-2 bg-surface-muted border-b border-border text-2xs font-semibold text-muted-light uppercase tracking-wide shrink-0">
          <span className="w-40 shrink-0">{t('llmAccounts.columns.id')}</span>
          <span className="flex-1">{t('llmAccounts.columns.config')}</span>
          <span className="w-28 shrink-0 text-right">{t('llmAccounts.columns.actions')}</span>
        </div>

        {loading && (
          <div className="flex justify-center py-8"><Spinner /></div>
        )}

        {isAdding && (
          <div className="flex items-start gap-4 px-4 py-3 border-b border-primary/20 bg-primary-light/40">
            <div className="w-40 shrink-0">
              <input
                value={newKey}
                onChange={e => setNewKey(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, ''))}
                placeholder={t('llmAccounts.keyPlaceholder')}
                autoFocus
                className="w-full text-xs font-mono px-2 py-1.5 border border-border-strong rounded focus:outline-none focus:border-primary bg-white"
              />
              {newKey && (
                <p className="text-2xs text-muted-light mt-1 font-mono break-all">{envVarName(newKey)}</p>
              )}
            </div>
            <AccountForm value={newValue} onChange={setNewValue} t={t} />
            <div className="flex gap-1.5 shrink-0">
              <button
                onClick={handleAddSave}
                disabled={saving || !adminToken}
                className="px-2.5 py-1 text-xs font-semibold bg-primary text-white rounded disabled:opacity-40 hover:bg-primary-dark transition-colors"
              >
                {saving ? '…' : t('competencySkills.save')}
              </button>
              <button
                onClick={() => { setIsAdding(false); setOpError(null) }}
                className="px-2.5 py-1 text-xs border border-border-strong rounded text-muted hover:text-dark transition-colors"
              >
                {t('competencySkills.cancel')}
              </button>
            </div>
          </div>
        )}

        {!loading && sortedKeys.length === 0 && !isAdding && (
          <div className="px-4 py-8 text-center text-sm text-muted-light">
            {t('llmAccounts.empty')}{' '}
            <button onClick={handleAddStart} className="text-primary hover:underline font-medium">
              {t('llmAccounts.newAccount')}
            </button>
          </div>
        )}

        {sortedKeys.map(key => {
          const account = getAccount(entries[key]?.value)
          const isEditing = editingKey === key
          const isDeleting = deletingKey === key

          return (
            <div key={key} className="flex items-start gap-4 px-4 py-3 border-b border-border last:border-0">
              <div className="w-40 shrink-0">
                <span className="text-xs font-mono font-semibold text-dark break-all">{key}</span>
                <p className="text-2xs text-muted-light mt-1 font-mono break-all">{envVarName(key)}</p>
              </div>

              {isEditing ? (
                <>
                  <AccountForm value={editValue} onChange={setEditValue} t={t} />
                  <div className="flex gap-1.5 shrink-0">
                    <button
                      onClick={() => handleEditSave(key)}
                      disabled={saving || !adminToken}
                      className="px-2.5 py-1 text-xs font-semibold bg-primary text-white rounded disabled:opacity-40 hover:bg-primary-dark transition-colors"
                    >
                      {saving ? '…' : t('competencySkills.save')}
                    </button>
                    <button
                      onClick={() => { setEditingKey(null); setOpError(null) }}
                      className="px-2.5 py-1 text-xs border border-border-strong rounded text-muted hover:text-dark transition-colors"
                    >
                      {t('competencySkills.cancel')}
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <div className="flex-1 flex items-center gap-3 flex-wrap">
                    <span className="text-2xs font-semibold uppercase px-1.5 py-0.5 rounded bg-surface-muted text-muted">
                      {account.provider}
                    </span>
                    <span className="text-xs text-dark">{account.display_name || '—'}</span>
                    <span className="text-2xs text-muted-light">RPM {account.rpm_limit} · TPM {account.tpm_limit}</span>
                    {!account.active && (
                      <span className="text-2xs bg-warning-light text-warning-text px-1.5 py-0.5 rounded">{t('llmAccounts.inactive')}</span>
                    )}
                  </div>
                  <div className="flex gap-1.5 shrink-0">
                    <button
                      onClick={() => handleEditStart(key)}
                      className="px-2 py-1 text-xs border border-border-strong rounded text-muted hover:text-dark hover:border-border transition-colors"
                    >
                      {t('competencySkills.edit')}
                    </button>
                    {adminToken && (
                      <button
                        onClick={() => handleDelete(key)}
                        disabled={isDeleting}
                        className="px-2 py-1 text-xs border border-red/30 rounded text-red hover:bg-red-light disabled:opacity-40 transition-colors"
                      >
                        {isDeleting ? '…' : '🗑'}
                      </button>
                    )}
                  </div>
                </>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default LlmAccountsPage
