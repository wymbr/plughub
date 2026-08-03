/**
 * NamespaceEditor.tsx
 * Platform config editor (config-api, port 3600).
 *
 * Left sidebar: namespace buttons with colour dot.
 * Right table: key / value / actions for selected namespace.
 *   - "tenant override" badge when entry.tenant_id ≠ '__global__'
 *   - Edit inline: scope selector (🌐 Global default vs 🏢 Tenant)
 *   - Reset button removes the tenant override (falls back to global default)
 *
 * Namespaces removed from here (have dedicated UIs or deferred):
 *   sentiment    → SentimentBandsEditor (tab Sentimento)
 *   dashboard    → Dashboards module (template-level config)
 *   webchat      → Configuração/Canais/WebChat
 *   audit_policy → Configuração/Mascaramento (MaskingPage)
 *   masking      → removed (legacy, replaced by audit_policy)
 *   quota        → deferred (Pricing/Metering module, not yet implemented)
 */
import React, { useState, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { useNamespace, useMultiNamespace, putConfig, deleteConfig, type ConfigEntry } from '../api/config-hooks'
import Spinner from '@/components/ui/Spinner'

// ── Namespace catalogue ────────────────────────────────────────────────────────
// An entry with `namespaceIds` is a "group": multiple API namespaces merged in one tab.
// An entry with just `id` maps 1:1 to a single API namespace.

interface NsEntry {
  id:            string          // virtual ID for selection state
  namespaceIds?: string[]        // if present: group of multiple API namespaces
  label:         string
  icon:          string
  color:         string
  desc:          string
  sections?:     { ns: string; label: string }[]  // labels for each sub-section in a group
}

const NAMESPACES: NsEntry[] = [
  {
    id:           'routing_timeouts',
    namespaceIds: ['routing', 'session'],
    sections: [
      { ns: 'routing', label: 'Routing (SLA & TTL)' },
      { ns: 'session', label: 'Component Timeouts' },
    ],
    label: 'Routing & Timeouts',
    icon: '🔀', color: 'bg-secondary',
    // `snapshot_ttl_s` saiu da descrição em 2026-08-03 junto com a chave: ela era
    // semeada, aparecia aqui e nenhum código a lia (o routing grava TTL 3600, o
    // seed prometia 120). Esta linha era o ÚNICO hit da chave fora do config-api
    // — a tela era a última a afirmar o número que o sistema ignorava.
    desc: 'SLA & queue TTLs | TTLs: ai_gateway, channel_gateway, transcript, replayer. Weights/factors stay in pool settings.',
  },
  {
    id: 'consumer',
    label: 'Consumer Analytics',
    icon: '📥', color: 'bg-warning',
    desc: 'analytics-api Kafka consumer: batch_size, timeout_ms, restart_delay_s, max_restart_delay_s',
  },
  {
    id: 'expurgo',
    label: 'Data Retention',
    icon: '🗑️', color: 'bg-slate-400',
    desc: 'Data retention periods: voice_recording_days (recordings), attachment_days (message attachments) — applies to DB and file storage',
  },
]

// ── Helpers ────────────────────────────────────────────────────────────────────

function isGlobal(e: ConfigEntry) {
  return e.tenant_id === '__global__' || e.tenant_id === null
}

function prettyJson(v: unknown): string {
  return JSON.stringify(v, null, 2)
}

// ── EditRow — inline editor for a single key ──────────────────────────────────

function EditRow({
  entryKey,
  entry,
  tenantId,
  accessToken,
  onCancel,
  onSaved,
}: {
  entryKey:   string
  entry:      ConfigEntry
  tenantId:   string
  accessToken: string
  onCancel:   () => void
  onSaved:    () => void
}) {
  const { t } = useTranslation('configPlataforma')
  const [raw,      setRaw]      = useState(prettyJson(entry.value))
  const [scope,    setScope]    = useState<'global' | 'tenant'>('global')
  const [saving,   setSaving]   = useState(false)
  const [jsonError, setJsonError] = useState<string | null>(null)

  const handleChange = useCallback((text: string) => {
    setRaw(text)
    try { JSON.parse(text); setJsonError(null) }
    catch { setJsonError(t('namespace.jsonInvalid')) }
  }, [t])

  const handleSave = useCallback(async () => {
    let parsed: unknown
    try { parsed = JSON.parse(raw) }
    catch { setJsonError(t('namespace.jsonInvalidSave')); return }

    if (!accessToken) { setJsonError(t('namespace.adminRequired')); return }
    setSaving(true); setJsonError(null)
    try {
      await putConfig(
        entry.namespace ?? entryKey.split('.')[0],
        entryKey,
        parsed,
        scope === 'global' ? null : tenantId,
        '',           // admin-token slot (não usado — UI migrada para Bearer)
        accessToken,  // Bearer do operador
      )
      onSaved()
    } catch (e) {
      setJsonError(String(e))
    } finally {
      setSaving(false)
    }
  }, [raw, scope, tenantId, accessToken, entry, entryKey, onSaved])

  const rows = Math.min(10, raw.split('\n').length + 1)

  return (
    <div className="flex flex-col gap-2 flex-1">
      {/* Scope selector */}
      <div className="flex gap-2">
        {(['global', 'tenant'] as const).map(s => (
          <button
            key={s}
            onClick={() => setScope(s)}
            className={`px-3 py-1 rounded text-xs font-medium border transition-colors ${
              scope === s
                ? 'border-primary bg-primary-light text-primary'
                : 'border-border text-muted hover:text-dark'
            }`}
          >
            {s === 'global' ? '🌐 Global default' : `🏢 Tenant: ${tenantId}`}
          </button>
        ))}
      </div>

      {/* JSON textarea */}
      <textarea
        value={raw}
        onChange={e => handleChange(e.target.value)}
        rows={rows}
        spellCheck={false}
        className={`w-full font-mono text-xs p-2 rounded border bg-surface-muted text-dark resize-y outline-none ${
          jsonError ? 'border-red/40' : 'border-border-strong focus:border-primary'
        }`}
      />
      {jsonError && <p className="text-xs text-red-text">{jsonError}</p>}

      {/* Action buttons */}
      <div className="flex gap-2">
        <button
          onClick={handleSave}
          disabled={saving || !!jsonError || !accessToken}
          className="px-3 py-1.5 rounded text-xs font-semibold bg-primary text-white disabled:opacity-40 hover:bg-primary-dark transition-colors"
        >
          {saving ? t('namespace.saving') : t('namespace.save')}
        </button>
        <button
          onClick={onCancel}
          className="px-3 py-1.5 rounded text-xs border border-border-strong text-muted hover:text-dark transition-colors"
        >
          {t('namespace.cancel')}
        </button>
        {!accessToken && (
          <span className="text-xs text-warning self-center">
            {t('namespace.adminRequiredHint')}
          </span>
        )}
      </div>
    </div>
  )
}

// ── ConfigRow ─────────────────────────────────────────────────────────────────

function ConfigRow({
  entryKey,
  entry,
  tenantId,
  accessToken,
  isEditing,
  onEdit,
  onCancelEdit,
  onSaved,
  onDeleted,
}: {
  entryKey:     string
  entry:        ConfigEntry
  tenantId:     string
  accessToken:   string
  isEditing:    boolean
  onEdit:       () => void
  onCancelEdit: () => void
  onSaved:      () => void
  onDeleted:    () => void
}) {
  const { t } = useTranslation('configPlataforma')
  const [deleting, setDeleting] = useState(false)
  const override = !isGlobal(entry)

  const handleDelete = useCallback(async () => {
    if (!window.confirm(t('namespace.resetConfirm', { key: entryKey }))) return
    setDeleting(true)
    try {
      await deleteConfig(entry.namespace ?? '', entryKey, tenantId, '', accessToken)
      onDeleted()
    } catch { setDeleting(false) }
  }, [entry, entryKey, tenantId, accessToken, onDeleted])

  return (
    <div className={`flex items-start gap-4 px-5 py-3 border-b border-border ${
      override ? 'bg-primary-light/40' : ''
    }`}>
      {/* Key + badge */}
      <div className="w-52 shrink-0 pt-0.5">
        <p className="text-xs font-semibold font-mono text-dark">{entryKey}</p>
        {override && (
          <span className="text-2xs font-medium text-primary mt-0.5 block">
            tenant override
          </span>
        )}
        {entry.description && (
          <p className="text-2xs text-muted-light mt-0.5 leading-tight">{entry.description}</p>
        )}
      </div>

      {/* Value / editor */}
      {isEditing ? (
        <EditRow
          entryKey={entryKey}
          entry={entry}
          tenantId={tenantId}
          accessToken={accessToken}
          onCancel={onCancelEdit}
          onSaved={() => { onCancelEdit(); onSaved() }}
        />
      ) : (
        <>
          <pre className="flex-1 text-xs font-mono text-muted whitespace-pre-wrap break-all max-h-20 overflow-hidden">
            {prettyJson(entry.value)}
          </pre>

          <div className="flex gap-1.5 shrink-0">
            <button
              onClick={onEdit}
              className="px-2 py-1 text-xs border border-border-strong rounded text-muted hover:text-dark hover:border-border-strong transition-colors"
            >
              {t('namespace.edit')}
            </button>
            {override && accessToken && (
              <button
                onClick={handleDelete}
                disabled={deleting}
                className="px-2 py-1 text-xs border border-red/30 rounded text-red hover:bg-red-light disabled:opacity-40 transition-colors"
              >
                {deleting ? '…' : t('namespace.reset')}
              </button>
            )}
          </div>
        </>
      )}
    </div>
  )
}

// ── NamespaceEditor ───────────────────────────────────────────────────────────

interface Props {
  tenantId:   string
  accessToken: string
}

// ── NamespacePanel — single namespace view ─────────────────────────────────────

export function NamespacePanel({
  nsId, sectionLabel, tenantId, accessToken, editingKey, setEditingKey,
}: {
  nsId:         string
  sectionLabel?: string
  tenantId:     string
  accessToken:   string
  editingKey:   string | null
  setEditingKey: (k: string | null) => void
}) {
  const { t } = useTranslation('configPlataforma')
  const { entries, loading, error, reload } = useNamespace(tenantId, nsId)
  const sortedKeys = Object.keys(entries).sort()

  return (
    <>
      {sectionLabel && (
        <div className="px-5 py-2 bg-surface-muted border-b border-border text-2xs font-bold text-muted uppercase tracking-widest">
          {sectionLabel} <code className="normal-case font-normal text-muted-light">({nsId})</code>
          {loading && <span className="ml-2 text-muted-light">…</span>}
          {error   && <span className="ml-2 text-red">⚠ {error}</span>}
        </div>
      )}
      {!sectionLabel && loading && (
        <div className="flex justify-center py-6"><Spinner /></div>
      )}
      {!sectionLabel && error && (
        <div className="px-5 py-2 text-xs text-red-text">⚠ {error}</div>
      )}
      {!loading && sortedKeys.length === 0 && !error && (
        <div className="px-5 py-6 text-center text-sm text-muted-light">
          {t('namespace.noEntriesNs', { ns: nsId })}
        </div>
      )}
      {sortedKeys.map(key => (
        <ConfigRow
          key={`${nsId}:${key}`}
          entryKey={key}
          entry={entries[key]}
          tenantId={tenantId}
          accessToken={accessToken}
          isEditing={editingKey === `${nsId}:${key}`}
          onEdit={() => setEditingKey(`${nsId}:${key}`)}
          onCancelEdit={() => setEditingKey(null)}
          onSaved={reload}
          onDeleted={() => { setEditingKey(null); reload() }}
        />
      ))}
    </>
  )
}

// ── NamespaceEditor ───────────────────────────────────────────────────────────

export function NamespaceEditor({ tenantId, accessToken }: Props) {
  const { t } = useTranslation('configPlataforma')
  const [selectedId, setSelectedId] = useState(NAMESPACES[0].id)
  const [editingKey, setEditingKey] = useState<string | null>(null)

  const ns = NAMESPACES.find(n => n.id === selectedId)!
  const isGroup = !!(ns.namespaceIds && ns.namespaceIds.length > 0)

  return (
    <div className="flex h-full overflow-hidden">
      {/* Namespace sidebar */}
      <aside className="w-48 shrink-0 border-r border-border bg-surface-muted flex flex-col overflow-y-auto">
        {NAMESPACES.map(n => {
          const active = n.id === selectedId
          return (
            <button
              key={n.id}
              onClick={() => { setSelectedId(n.id); setEditingKey(null) }}
              className={`flex items-center gap-2.5 px-3 py-2.5 text-left text-sm transition-colors border-l-2 ${
                active
                  ? 'border-l-primary bg-white text-dark font-semibold'
                  : 'border-l-transparent text-muted hover:text-dark hover:bg-white/60'
              }`}
            >
              <span className={`w-2 h-2 rounded-full shrink-0 ${n.color}`} />
              {n.label}
            </button>
          )
        })}
      </aside>

      {/* Right panel */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Namespace header */}
        <div className="flex items-center gap-3 px-5 py-3 border-b border-border shrink-0">
          <span className="text-base">{ns.icon}</span>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold text-dark">{ns.label}</p>
            <p className="text-xs text-muted truncate">{ns.desc}</p>
          </div>
        </div>

        {/* Column headers */}
        <div className="flex gap-4 px-5 py-2 bg-surface-muted border-b border-border text-2xs font-semibold text-muted-light uppercase tracking-wide shrink-0">
          <span className="w-52 shrink-0">{t('namespace.key')}</span>
          <span className="flex-1">{t('namespace.value')}</span>
          <span className="w-20 shrink-0 text-right">{t('namespace.actions')}</span>
        </div>

        {/* Rows — group or single */}
        <div className="flex-1 overflow-y-auto">
          {isGroup
            ? ns.namespaceIds!.map(nsId => (
                <NamespacePanel
                  key={nsId}
                  nsId={nsId}
                  sectionLabel={ns.sections?.find(s => s.ns === nsId)?.label ?? nsId}
                  tenantId={tenantId}
                  accessToken={accessToken}
                  editingKey={editingKey}
                  setEditingKey={setEditingKey}
                />
              ))
            : (
              <NamespacePanel
                nsId={ns.id}
                tenantId={tenantId}
                accessToken={accessToken}
                editingKey={editingKey}
                setEditingKey={setEditingKey}
              />
            )
          }
        </div>
      </div>
    </div>
  )
}
