/**
 * ConfigPanel.tsx
 * Platform Configuration viewer/editor for the Operator Console.
 *
 * Layout:
 *   Left (28%)  — namespace list (sidebar)
 *   Right (72%) — key/value table for selected namespace, inline JSON editor
 *
 * Features:
 *   - Resolved view: shows effective value (tenant override wins over global)
 *   - Edit mode: inline JSON textarea with syntax validation
 *   - Save: PUT /config/{ns}/{key} with X-Admin-Token
 *   - Delete override: remove tenant-specific value (falls back to global)
 *   - Scope toggle: view as "__global__" vs current tenant
 */
import React, { useState, useCallback, useMemo } from 'react'
import { useConfigAll, putConfig, deleteConfig, type ConfigEntry } from '../api/config-hooks'

interface Props {
  tenantId: string
  onBack:   () => void
}

// ── Namespace colour map ──────────────────────────────────────────────────────

const NS_COLORS: Record<string, string> = {
  sentiment: '#22c55e',
  routing:   '#3b82f6',
  session:   '#a855f7',
  consumer:  '#f59e0b',
  dashboard: '#06b6d4',
  webchat:   '#ec4899',
  masking:   '#ef4444',
  quota:     '#f97316',
}

function nsColor(ns: string): string {
  return NS_COLORS[ns] ?? '#64748b'
}

// ── JSON value display / edit ─────────────────────────────────────────────────

function prettyJson(v: unknown): string {
  return JSON.stringify(v, null, 2)
}

function isScalar(v: unknown): boolean {
  return typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean'
}

function ScalarBadge({ value }: { value: unknown }) {
  const s = String(value)
  const color =
    typeof value === 'number' ? '#94a3b8' :
    value === true ? '#22c55e' :
    value === false ? '#ef4444' :
    '#e2e8f0'
  return (
    <span
      style={{
        fontFamily: 'monospace',
        fontSize: 12,
        color,
        background: '#1e293b',
        padding: '2px 8px',
        borderRadius: 4,
      }}
    >
      {s}
    </span>
  )
}

// ── EditDrawer — inline JSON editor ──────────────────────────────────────────

function EditDrawer({
  entry,
  tenantId,
  adminToken,
  onClose,
  onSaved,
}: {
  entry:      ConfigEntry
  tenantId:   string
  adminToken: string
  onClose:    () => void
  onSaved:    () => void
}) {
  const [raw,      setRaw]      = useState(prettyJson(entry.value))
  const [saving,   setSaving]   = useState(false)
  const [jsonError, setJsonError] = useState<string | null>(null)
  const [scope,    setScope]    = useState<'global' | 'tenant'>('global')

  const validate = useCallback((text: string): unknown | null => {
    try {
      return JSON.parse(text)
    } catch {
      return null
    }
  }, [])

  const handleChange = useCallback((text: string) => {
    setRaw(text)
    const parsed = validate(text)
    setJsonError(parsed === null ? 'Invalid JSON' : null)
  }, [validate])

  const handleSave = useCallback(async () => {
    const value = validate(raw)
    if (value === null) {
      setJsonError('Cannot save — invalid JSON')
      return
    }
    setSaving(true)
    try {
      await putConfig(
        entry.namespace,
        entry.key,
        value,
        scope === 'global' ? null : tenantId,
        entry.description,
        adminToken,
      )
      onSaved()
      onClose()
    } catch (err) {
      setJsonError(String(err))
    } finally {
      setSaving(false)
    }
  }, [raw, validate, entry, scope, tenantId, adminToken, onSaved, onClose])

  return (
    <div
      style={{
        position:   'fixed',
        top:        0,
        right:      0,
        bottom:     0,
        width:      480,
        background: '#0d1117',
        borderLeft: '1px solid #1e293b',
        zIndex:     100,
        display:    'flex',
        flexDirection: 'column',
        boxShadow:  '-8px 0 32px rgba(0,0,0,0.5)',
      }}
    >
      {/* Header */}
      <div style={{ padding: '16px 20px', borderBottom: '1px solid #1e293b' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <div style={{ fontSize: 11, color: nsColor(entry.namespace), fontWeight: 700, textTransform: 'uppercase', letterSpacing: 1 }}>
              {entry.namespace}
            </div>
            <div style={{ fontSize: 15, fontWeight: 700, color: '#e2e8f0', fontFamily: 'monospace', marginTop: 2 }}>
              {entry.key}
            </div>
          </div>
          <button
            onClick={onClose}
            style={{ background: 'none', border: 'none', color: '#64748b', cursor: 'pointer', fontSize: 20 }}
          >
            ×
          </button>
        </div>
        {entry.description && (
          <div style={{ marginTop: 8, fontSize: 11, color: '#475569', lineHeight: 1.5 }}>
            {entry.description}
          </div>
        )}
      </div>

      {/* Scope selector */}
      <div style={{ padding: '10px 20px', borderBottom: '1px solid #1e293b', display: 'flex', gap: 8 }}>
        {(['global', 'tenant'] as const).map(s => (
          <button
            key={s}
            onClick={() => setScope(s)}
            style={{
              padding: '4px 12px',
              borderRadius: 4,
              border: scope === s ? '1px solid #3b82f6' : '1px solid #334155',
              background: scope === s ? '#0d47a1' : 'transparent',
              color: scope === s ? '#93c5fd' : '#64748b',
              cursor: 'pointer',
              fontSize: 11,
              fontWeight: scope === s ? 700 : 400,
            }}
          >
            {s === 'global' ? '🌐 Global default' : `🏢 Tenant: ${tenantId}`}
          </button>
        ))}
      </div>

      {/* Editor */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', padding: 20, gap: 8 }}>
        <div style={{ fontSize: 11, color: '#64748b', fontWeight: 600 }}>VALUE (JSON)</div>
        <textarea
          value={raw}
          onChange={e => handleChange(e.target.value)}
          spellCheck={false}
          style={{
            flex:       1,
            background: '#0f172a',
            border:     jsonError ? '1px solid #ef4444' : '1px solid #334155',
            borderRadius: 6,
            color:      '#e2e8f0',
            fontFamily: 'monospace',
            fontSize:   12,
            padding:    12,
            resize:     'none',
            outline:    'none',
            lineHeight: 1.6,
          }}
        />
        {jsonError && (
          <div style={{ fontSize: 11, color: '#ef4444' }}>{jsonError}</div>
        )}
      </div>

      {/* Footer */}
      <div style={{ padding: '12px 20px', borderTop: '1px solid #1e293b', display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
        <button
          onClick={onClose}
          style={{
            padding: '6px 16px',
            borderRadius: 4,
            border: '1px solid #334155',
            background: 'transparent',
            color: '#94a3b8',
            cursor: 'pointer',
            fontSize: 12,
          }}
        >
          Cancel
        </button>
        <button
          onClick={handleSave}
          disabled={saving || !!jsonError}
          style={{
            padding: '6px 16px',
            borderRadius: 4,
            border: 'none',
            background: saving || jsonError ? '#1e293b' : '#2563eb',
            color: saving || jsonError ? '#475569' : '#fff',
            cursor: saving || jsonError ? 'not-allowed' : 'pointer',
            fontSize: 12,
            fontWeight: 600,
          }}
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
      </div>
    </div>
  )
}

// ── ConfigRow ─────────────────────────────────────────────────────────────────

function ConfigRow({
  entry,
  tenantId,
  adminToken,
  onEdit,
  onDeleted,
}: {
  entry:      ConfigEntry
  tenantId:   string
  adminToken: string
  onEdit:     () => void
  onDeleted:  () => void
}) {
  const [deleting, setDeleting] = useState(false)

  const isOverride = entry.tenant_id !== '__global__' && entry.tenant_id !== null

  const handleDelete = useCallback(async () => {
    if (!confirm(`Delete override for ${entry.namespace}.${entry.key}?`)) return
    setDeleting(true)
    try {
      await deleteConfig(entry.namespace, entry.key, tenantId, adminToken)
      onDeleted()
    } catch {
      setDeleting(false)
    }
  }, [entry, tenantId, adminToken, onDeleted])

  return (
    <div
      style={{
        display:    'flex',
        alignItems: 'flex-start',
        gap:        12,
        padding:    '10px 16px',
        borderBottom: '1px solid #0f172a',
        background: isOverride ? '#0a1628' : 'transparent',
      }}
    >
      {/* Key */}
      <div style={{ width: 180, flexShrink: 0 }}>
        <div style={{ fontSize: 12, fontWeight: 600, color: '#94a3b8', fontFamily: 'monospace' }}>
          {entry.key}
        </div>
        {isOverride && (
          <div style={{ fontSize: 10, color: '#3b82f6', marginTop: 2 }}>tenant override</div>
        )}
      </div>

      {/* Value */}
      <div style={{ flex: 1, minWidth: 0 }}>
        {isScalar(entry.value) ? (
          <ScalarBadge value={entry.value} />
        ) : (
          <pre
            style={{
              margin:    0,
              fontSize:  11,
              color:     '#64748b',
              fontFamily: 'monospace',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-all',
              maxHeight:  60,
              overflow:  'hidden',
            }}
          >
            {prettyJson(entry.value)}
          </pre>
        )}
        {entry.description && (
          <div style={{ fontSize: 10, color: '#334155', marginTop: 4, lineHeight: 1.4 }}>
            {entry.description}
          </div>
        )}
      </div>

      {/* Actions */}
      <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
        <button
          onClick={onEdit}
          style={{
            padding: '3px 10px',
            borderRadius: 3,
            border: '1px solid #334155',
            background: 'transparent',
            color: '#64748b',
            cursor: 'pointer',
            fontSize: 11,
          }}
        >
          Edit
        </button>
        {isOverride && (
          <button
            onClick={handleDelete}
            disabled={deleting}
            style={{
              padding: '3px 10px',
              borderRadius: 3,
              border: '1px solid #7f1d1d',
              background: 'transparent',
              color: '#ef4444',
              cursor: deleting ? 'not-allowed' : 'pointer',
              fontSize: 11,
            }}
          >
            {deleting ? '…' : 'Reset'}
          </button>
        )}
      </div>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export function ConfigPanel({ tenantId, onBack }: Props) {
  const [selectedNs,  setSelectedNs]  = useState<string>('sentiment')
  const [editEntry,   setEditEntry]   = useState<ConfigEntry | null>(null)
  const [adminToken,  setAdminToken]  = useState('')
  const [tokenInput,  setTokenInput]  = useState('')
  const [tokenSaved,  setTokenSaved]  = useState(false)

  const { config, loading, error, refresh } = useConfigAll(tenantId)

  const namespaces = useMemo(
    () => Object.keys(config ?? {}).sort(),
    [config]
  )

  const nsEntries: ConfigEntry[] = useMemo(() => {
    const ns = config?.[selectedNs]
    if (!ns) return []
    return Object.values(ns).sort((a, b) => a.key.localeCompare(b.key))
  }, [config, selectedNs])

  const saveToken = useCallback(() => {
    setAdminToken(tokenInput)
    setTokenSaved(true)
  }, [tokenInput])

  return (
    <div style={{ display: 'flex', flex: 1, overflow: 'hidden', position: 'relative' }}>

      {/* ── Namespace sidebar ─────────────────────────────────────────────── */}
      <div
        style={{
          width:        220,
          borderRight:  '1px solid #1e293b',
          display:      'flex',
          flexDirection: 'column',
          overflow:     'hidden',
          flexShrink:   0,
        }}
      >
        {/* Toolbar */}
        <div
          style={{
            display:    'flex',
            alignItems: 'center',
            gap:        8,
            padding:    '10px 16px',
            borderBottom: '1px solid #1e293b',
          }}
        >
          <button
            onClick={onBack}
            style={{
              padding:  '4px 10px',
              borderRadius: 4,
              border:   '1px solid #334155',
              background: '#0f172a',
              color:    '#94a3b8',
              cursor:   'pointer',
              fontSize: 11,
            }}
          >
            ← Back
          </button>
          <span style={{ fontSize: 11, fontWeight: 700, color: '#64748b', textTransform: 'uppercase', letterSpacing: 0.5 }}>
            Config
          </span>
        </div>

        {/* Namespace list */}
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {loading && (
            <div style={{ padding: 20, fontSize: 12, color: '#475569' }}>Loading…</div>
          )}
          {error && (
            <div style={{ padding: 16, fontSize: 11, color: '#ef4444' }}>
              Error: config-api unavailable
            </div>
          )}
          {namespaces.map(ns => {
            const count = Object.keys(config?.[ns] ?? {}).length
            const active = ns === selectedNs
            const color = nsColor(ns)
            return (
              <div
                key={ns}
                onClick={() => setSelectedNs(ns)}
                style={{
                  display:   'flex',
                  alignItems: 'center',
                  gap:       10,
                  padding:   '10px 16px',
                  cursor:    'pointer',
                  borderBottom: '1px solid #0f172a',
                  background: active ? '#1e293b' : 'transparent',
                  borderLeft: active ? `3px solid ${color}` : '3px solid transparent',
                }}
              >
                <div
                  style={{
                    width:        8,
                    height:       8,
                    borderRadius: '50%',
                    background:   color,
                    flexShrink:   0,
                  }}
                />
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 12, fontWeight: active ? 700 : 400, color: active ? '#e2e8f0' : '#94a3b8' }}>
                    {ns}
                  </div>
                  <div style={{ fontSize: 10, color: '#334155', marginTop: 1 }}>{count} keys</div>
                </div>
              </div>
            )
          })}
        </div>

        {/* Admin token input */}
        <div
          style={{
            padding: '10px 12px',
            borderTop: '1px solid #1e293b',
            background: '#090e1a',
          }}
        >
          <div style={{ fontSize: 10, color: '#475569', fontWeight: 600, marginBottom: 4 }}>
            ADMIN TOKEN (for edits)
          </div>
          <div style={{ display: 'flex', gap: 4 }}>
            <input
              type="password"
              value={tokenInput}
              onChange={e => { setTokenInput(e.target.value); setTokenSaved(false) }}
              placeholder="secret"
              style={{
                flex:     1,
                background: '#1e293b',
                border:   '1px solid #334155',
                borderRadius: 4,
                color:    '#e2e8f0',
                fontSize: 10,
                padding:  '3px 6px',
                outline:  'none',
                fontFamily: 'monospace',
              }}
            />
            <button
              onClick={saveToken}
              style={{
                padding:  '3px 8px',
                borderRadius: 4,
                border:   tokenSaved ? '1px solid #22c55e' : '1px solid #334155',
                background: 'transparent',
                color:    tokenSaved ? '#22c55e' : '#64748b',
                cursor:   'pointer',
                fontSize: 10,
              }}
            >
              {tokenSaved ? '✓' : 'Set'}
            </button>
          </div>
        </div>
      </div>

      {/* ── Key/value table ───────────────────────────────────────────────── */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {/* Table header */}
        <div
          style={{
            display:    'flex',
            alignItems: 'center',
            gap:        12,
            padding:    '10px 16px',
            borderBottom: '1px solid #1e293b',
          }}
        >
          <div
            style={{
              width:        10,
              height:       10,
              borderRadius: '50%',
              background:   nsColor(selectedNs),
            }}
          />
          <span
            style={{
              fontSize:   13,
              fontWeight: 700,
              color:      '#e2e8f0',
            }}
          >
            {selectedNs}
          </span>
          <span style={{ fontSize: 11, color: '#334155' }}>
            {nsEntries.length} keys
          </span>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
            <span style={{ fontSize: 10, color: '#334155' }}>tenant: {tenantId}</span>
            <button
              onClick={refresh}
              style={{
                padding: '3px 10px',
                borderRadius: 4,
                border: '1px solid #334155',
                background: 'transparent',
                color: '#64748b',
                cursor: 'pointer',
                fontSize: 11,
              }}
            >
              ↺ Refresh
            </button>
          </div>
        </div>

        {/* Column headers */}
        <div
          style={{
            display:    'flex',
            gap:        12,
            padding:    '6px 16px',
            background: '#090e1a',
            borderBottom: '1px solid #1e293b',
            fontSize:   10,
            fontWeight: 600,
            color:      '#334155',
            textTransform: 'uppercase',
            letterSpacing: 0.5,
          }}
        >
          <div style={{ width: 180, flexShrink: 0 }}>KEY</div>
          <div style={{ flex: 1 }}>VALUE</div>
          <div style={{ width: 80, flexShrink: 0, textAlign: 'right' }}>ACTIONS</div>
        </div>

        {/* Rows */}
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {nsEntries.map(entry => (
            <ConfigRow
              key={`${entry.namespace}.${entry.key}`}
              entry={entry}
              tenantId={tenantId}
              adminToken={adminToken}
              onEdit={() => setEditEntry(entry)}
              onDeleted={refresh}
            />
          ))}
          {!loading && nsEntries.length === 0 && (
            <div style={{ padding: 40, textAlign: 'center', fontSize: 12, color: '#334155' }}>
              No config keys found for namespace "{selectedNs}".
            </div>
          )}
        </div>
      </div>

      {/* ── Edit drawer ────────────────────────────────────────────────────── */}
      {editEntry && (
        <EditDrawer
          entry={editEntry}
          tenantId={tenantId}
          adminToken={adminToken}
          onClose={() => setEditEntry(null)}
          onSaved={() => { void refresh() }}
        />
      )}
    </div>
  )
}
