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
 * Namespaces removed from here (have dedicated UIs):
 *   sentiment    → SentimentBandsEditor (tab Sentimento)
 *   dashboard    → Dashboards module (template-level config)
 *   webchat      → Configuração/Canais/WebChat
 *   audit_policy → Configuração/Mascaramento (MaskingPage)
 *   masking      → removed (legacy, replaced by audit_policy)
 */
import React, { useState, useCallback } from 'react'
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
      { ns: 'routing', label: 'Roteamento (SLA & TTL)' },
      { ns: 'session', label: 'Timeouts de Componentes' },
    ],
    label: 'Roteamento & Timeouts',
    icon: '🔀', color: 'bg-blue-400',
    desc: 'SLA, snapshot_ttl_s | TTLs: ai_gateway, channel_gateway, transcript, replayer. Pesos/fatores ficam nas configurações de pool.',
  },
  {
    id: 'consumer',
    label: 'Consumer Analytics',
    icon: '📥', color: 'bg-yellow-400',
    desc: 'Kafka consumer da analytics-api: batch_size, timeout_ms, restart_delay_s, max_restart_delay_s',
  },
  {
    id: 'quota',
    label: 'Quotas',
    icon: '📏', color: 'bg-orange-400',
    desc: 'max_concurrent_sessions — limite operacional de sessões simultâneas por tenant',
  },
  {
    id: 'expurgo',
    label: 'Expurgo',
    icon: '🗑️', color: 'bg-slate-400',
    desc: 'Retenção de dados: voice_recording_days (gravações), attachment_days (anexos de mensagem) — aplica a DB e file storage',
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
  adminToken,
  onCancel,
  onSaved,
}: {
  entryKey:   string
  entry:      ConfigEntry
  tenantId:   string
  adminToken: string
  onCancel:   () => void
  onSaved:    () => void
}) {
  const [raw,      setRaw]      = useState(prettyJson(entry.value))
  const [scope,    setScope]    = useState<'global' | 'tenant'>('global')
  const [saving,   setSaving]   = useState(false)
  const [jsonError, setJsonError] = useState<string | null>(null)

  const handleChange = useCallback((text: string) => {
    setRaw(text)
    try { JSON.parse(text); setJsonError(null) }
    catch { setJsonError('JSON inválido') }
  }, [])

  const handleSave = useCallback(async () => {
    let parsed: unknown
    try { parsed = JSON.parse(raw) }
    catch { setJsonError('Não é possível salvar — JSON inválido'); return }

    if (!adminToken) { setJsonError('Admin token obrigatório para salvar'); return }
    setSaving(true); setJsonError(null)
    try {
      await putConfig(
        entry.namespace ?? entryKey.split('.')[0],
        entryKey,
        parsed,
        scope === 'global' ? null : tenantId,
        adminToken,
      )
      onSaved()
    } catch (e) {
      setJsonError(String(e))
    } finally {
      setSaving(false)
    }
  }, [raw, scope, tenantId, adminToken, entry, entryKey, onSaved])

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
                ? 'border-blue-400 bg-blue-50 text-blue-700'
                : 'border-gray-200 text-gray-500 hover:text-gray-700'
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
        className={`w-full font-mono text-xs p-2 rounded border bg-gray-50 text-gray-800 resize-y outline-none ${
          jsonError ? 'border-red-400' : 'border-gray-300 focus:border-blue-400'
        }`}
      />
      {jsonError && <p className="text-xs text-red-600">{jsonError}</p>}

      {/* Action buttons */}
      <div className="flex gap-2">
        <button
          onClick={handleSave}
          disabled={saving || !!jsonError || !adminToken}
          className="px-3 py-1.5 rounded text-xs font-semibold bg-primary text-white disabled:opacity-40 hover:bg-blue-800 transition-colors"
        >
          {saving ? 'Salvando…' : 'Salvar'}
        </button>
        <button
          onClick={onCancel}
          className="px-3 py-1.5 rounded text-xs border border-gray-300 text-gray-600 hover:text-gray-900 transition-colors"
        >
          Cancelar
        </button>
        {!adminToken && (
          <span className="text-xs text-amber-600 self-center">
            ⚠ Defina o admin token para salvar
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
  adminToken,
  isEditing,
  onEdit,
  onCancelEdit,
  onSaved,
  onDeleted,
}: {
  entryKey:     string
  entry:        ConfigEntry
  tenantId:     string
  adminToken:   string
  isEditing:    boolean
  onEdit:       () => void
  onCancelEdit: () => void
  onSaved:      () => void
  onDeleted:    () => void
}) {
  const [deleting, setDeleting] = useState(false)
  const override = !isGlobal(entry)

  const handleDelete = useCallback(async () => {
    if (!window.confirm(`Remover override de ${entryKey}? O valor global padrão será restaurado.`)) return
    setDeleting(true)
    try {
      await deleteConfig(entry.namespace ?? '', entryKey, tenantId, adminToken)
      onDeleted()
    } catch { setDeleting(false) }
  }, [entry, entryKey, tenantId, adminToken, onDeleted])

  return (
    <div className={`flex items-start gap-4 px-5 py-3 border-b border-gray-100 ${
      override ? 'bg-blue-50/40' : ''
    }`}>
      {/* Key + badge */}
      <div className="w-52 shrink-0 pt-0.5">
        <p className="text-xs font-semibold font-mono text-gray-700">{entryKey}</p>
        {override && (
          <span className="text-[10px] font-medium text-blue-600 mt-0.5 block">
            tenant override
          </span>
        )}
        {entry.description && (
          <p className="text-[10px] text-gray-400 mt-0.5 leading-tight">{entry.description}</p>
        )}
      </div>

      {/* Value / editor */}
      {isEditing ? (
        <EditRow
          entryKey={entryKey}
          entry={entry}
          tenantId={tenantId}
          adminToken={adminToken}
          onCancel={onCancelEdit}
          onSaved={() => { onCancelEdit(); onSaved() }}
        />
      ) : (
        <>
          <pre className="flex-1 text-xs font-mono text-gray-600 whitespace-pre-wrap break-all max-h-20 overflow-hidden">
            {prettyJson(entry.value)}
          </pre>

          <div className="flex gap-1.5 shrink-0">
            <button
              onClick={onEdit}
              className="px-2 py-1 text-xs border border-gray-300 rounded text-gray-600 hover:text-gray-900 hover:border-gray-400 transition-colors"
            >
              ✏ Editar
            </button>
            {override && adminToken && (
              <button
                onClick={handleDelete}
                disabled={deleting}
                className="px-2 py-1 text-xs border border-red-200 rounded text-red-500 hover:bg-red-50 disabled:opacity-40 transition-colors"
              >
                {deleting ? '…' : 'Reset'}
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
  adminToken: string
}

// ── NamespacePanel — single namespace view ─────────────────────────────────────

function NamespacePanel({
  nsId, sectionLabel, tenantId, adminToken, editingKey, setEditingKey,
}: {
  nsId:         string
  sectionLabel?: string
  tenantId:     string
  adminToken:   string
  editingKey:   string | null
  setEditingKey: (k: string | null) => void
}) {
  const { entries, loading, error, reload } = useNamespace(tenantId, nsId)
  const sortedKeys = Object.keys(entries).sort()

  return (
    <>
      {sectionLabel && (
        <div className="px-5 py-2 bg-gray-50 border-b border-gray-100 text-[10px] font-bold text-gray-500 uppercase tracking-widest">
          {sectionLabel} <code className="normal-case font-normal text-gray-400">({nsId})</code>
          {loading && <span className="ml-2 text-gray-400">…</span>}
          {error   && <span className="ml-2 text-red-500">⚠ {error}</span>}
        </div>
      )}
      {!sectionLabel && loading && (
        <div className="flex justify-center py-6"><Spinner /></div>
      )}
      {!sectionLabel && error && (
        <div className="px-5 py-2 text-xs text-red-600">⚠ {error}</div>
      )}
      {!loading && sortedKeys.length === 0 && !error && (
        <div className="px-5 py-6 text-center text-sm text-gray-400">
          Nenhuma configuração em <code className="font-mono">{nsId}</code>.
        </div>
      )}
      {sortedKeys.map(key => (
        <ConfigRow
          key={`${nsId}:${key}`}
          entryKey={key}
          entry={entries[key]}
          tenantId={tenantId}
          adminToken={adminToken}
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

export function NamespaceEditor({ tenantId, adminToken }: Props) {
  const [selectedId, setSelectedId] = useState(NAMESPACES[0].id)
  const [editingKey, setEditingKey] = useState<string | null>(null)

  const ns = NAMESPACES.find(n => n.id === selectedId)!
  const isGroup = !!(ns.namespaceIds && ns.namespaceIds.length > 0)

  return (
    <div className="flex h-full overflow-hidden">
      {/* Namespace sidebar */}
      <aside className="w-48 shrink-0 border-r border-gray-200 bg-gray-50 flex flex-col overflow-y-auto">
        {NAMESPACES.map(n => {
          const active = n.id === selectedId
          return (
            <button
              key={n.id}
              onClick={() => { setSelectedId(n.id); setEditingKey(null) }}
              className={`flex items-center gap-2.5 px-3 py-2.5 text-left text-sm transition-colors border-l-2 ${
                active
                  ? 'border-l-primary bg-white text-gray-900 font-semibold'
                  : 'border-l-transparent text-gray-600 hover:text-gray-900 hover:bg-white/60'
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
        <div className="flex items-center gap-3 px-5 py-3 border-b border-gray-200 shrink-0">
          <span className="text-base">{ns.icon}</span>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold text-gray-900">{ns.label}</p>
            <p className="text-xs text-gray-500 truncate">{ns.desc}</p>
          </div>
        </div>

        {/* Column headers */}
        <div className="flex gap-4 px-5 py-2 bg-gray-50 border-b border-gray-200 text-[10px] font-semibold text-gray-400 uppercase tracking-wide shrink-0">
          <span className="w-52 shrink-0">Chave</span>
          <span className="flex-1">Valor</span>
          <span className="w-20 shrink-0 text-right">Ações</span>
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
                  adminToken={adminToken}
                  editingKey={editingKey}
                  setEditingKey={setEditingKey}
                />
              ))
            : (
              <NamespacePanel
                nsId={ns.id}
                tenantId={tenantId}
                adminToken={adminToken}
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
