/**
 * WebhookPanel.tsx
 * Webhook Trigger management panel.
 *
 * Layout:
 *   Left  (380px) — webhook list + "Create" button
 *   Right (flex)  — selected webhook detail + delivery log drawer
 *
 * Features:
 *   • List all webhooks for tenant with active/inactive badge
 *   • Create webhook: flow_id, description, optional context_override JSON
 *   • Show plain token exactly ONCE after create/rotate in a highlighted box
 *   • Activate / Deactivate toggle
 *   • Rotate token
 *   • Delete (with confirmation)
 *   • Delivery log: last 20 deliveries with status badge, latency, timestamp
 */
import React, { useState } from 'react'
import {
  useWebhooks,
  useWebhookDeliveries,
  createWebhook,
  patchWebhook,
  rotateWebhookToken,
  deleteWebhook,
} from '../api/webhook-hooks'
import type { Webhook, WebhookDelivery } from '../types'

interface Props {
  tenantId: string
  onBack:   () => void
}

// ── Colour palette (consistent with other panels) ────────────────────────────
const C = {
  bg:       '#0d1117',
  surface:  '#0f172a',
  border:   '#1e293b',
  border2:  '#334155',
  text:     '#e2e8f0',
  muted:    '#64748b',
  accent:   '#6366f1',   // indigo — webhooks brand colour
  accentBg: '#1e1b4b',
  green:    '#22c55e',
  red:      '#ef4444',
  amber:    '#eab308',
  amberBg:  '#451a03',
}

// ── Small helpers ─────────────────────────────────────────────────────────────

function Badge({ ok, label }: { ok: boolean; label?: string }) {
  return (
    <span style={{
      fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 10,
      background: ok ? '#052e16' : '#1c0a0a',
      color:      ok ? C.green   : C.red,
      border:     `1px solid ${ok ? '#14532d' : '#450a0a'}`,
    }}>
      {label ?? (ok ? 'Active' : 'Inactive')}
    </span>
  )
}

function StatusBadge({ code }: { code: number }) {
  const ok  = code >= 200 && code < 300
  const warn = code >= 400 && code < 500
  return (
    <span style={{
      fontSize: 10, fontWeight: 700, padding: '2px 6px', borderRadius: 4,
      background: ok ? '#052e16' : warn ? '#451a03' : '#1c0a0a',
      color:      ok ? C.green   : warn ? C.amber   : C.red,
    }}>
      {code}
    </span>
  )
}

function CopyBox({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false)

  function copy() {
    navigator.clipboard.writeText(value).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  return (
    <div style={{
      background: '#020617', border: `1px solid ${C.accent}`,
      borderRadius: 8, padding: '10px 14px', marginTop: 12,
    }}>
      <div style={{ fontSize: 11, color: C.accent, fontWeight: 700, marginBottom: 6 }}>
        ⚠ {label} — copy now, it will not be shown again
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <code style={{
          flex: 1, fontSize: 11, color: '#c7d2fe', fontFamily: 'monospace',
          wordBreak: 'break-all', lineHeight: 1.5,
        }}>
          {value}
        </code>
        <button onClick={copy} style={btnSmall(copied ? C.green : C.accent)}>
          {copied ? '✓ Copied' : 'Copy'}
        </button>
      </div>
    </div>
  )
}

function Spinner() {
  return <span style={{ color: C.muted, fontSize: 12 }}>Loading…</span>
}

// ── Button styles ─────────────────────────────────────────────────────────────

function btnSmall(color: string, bg = 'transparent'): React.CSSProperties {
  return {
    padding: '3px 10px', borderRadius: 5, border: `1px solid ${color}`,
    background: bg, color, cursor: 'pointer', fontSize: 11, fontWeight: 600,
    whiteSpace: 'nowrap',
  }
}

function btnPrimary(color = C.accent): React.CSSProperties {
  return {
    padding: '6px 16px', borderRadius: 6, border: `1px solid ${color}`,
    background: color + '22', color, cursor: 'pointer', fontSize: 13, fontWeight: 600,
  }
}

// ── Create form ───────────────────────────────────────────────────────────────

interface CreateFormProps {
  tenantId:   string
  adminToken: string
  onCreated:  (webhook: Webhook, plainToken: string) => void
  onCancel:   () => void
}

function CreateForm({ tenantId, adminToken, onCreated, onCancel }: CreateFormProps) {
  const [flowId,   setFlowId]   = useState('')
  const [desc,     setDesc]     = useState('')
  const [ctxRaw,   setCtxRaw]   = useState('{}')
  const [ctxErr,   setCtxErr]   = useState(false)
  const [saving,   setSaving]   = useState(false)
  const [error,    setError]    = useState('')

  function validateCtx(v: string) {
    setCtxRaw(v)
    try { JSON.parse(v); setCtxErr(false) } catch { setCtxErr(true) }
  }

  async function submit() {
    if (!flowId.trim()) return
    let ctx: Record<string, unknown> = {}
    try { ctx = JSON.parse(ctxRaw) } catch { setCtxErr(true); return }
    setSaving(true); setError('')
    try {
      const { webhook, token } = await createWebhook(tenantId, flowId.trim(), desc.trim(), ctx, adminToken)
      onCreated(webhook, token)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Unknown error')
    } finally {
      setSaving(false)
    }
  }

  const inputStyle: React.CSSProperties = {
    width: '100%', boxSizing: 'border-box',
    background: '#0f172a', border: `1px solid ${C.border2}`,
    borderRadius: 6, color: C.text, fontSize: 13,
    padding: '7px 10px', outline: 'none', fontFamily: 'inherit',
  }

  return (
    <div style={{
      background: C.surface, border: `1px solid ${C.border}`,
      borderRadius: 10, padding: 20,
    }}>
      <div style={{ fontSize: 14, fontWeight: 700, color: C.text, marginBottom: 16 }}>
        New Webhook
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div>
          <label style={{ fontSize: 11, color: C.muted, fontWeight: 600 }}>FLOW ID *</label>
          <input
            value={flowId} onChange={e => setFlowId(e.target.value)}
            placeholder="wf_approval_v1" style={{ ...inputStyle, marginTop: 4 }}
          />
        </div>

        <div>
          <label style={{ fontSize: 11, color: C.muted, fontWeight: 600 }}>DESCRIPTION</label>
          <input
            value={desc} onChange={e => setDesc(e.target.value)}
            placeholder="Salesforce opportunity trigger" style={{ ...inputStyle, marginTop: 4 }}
          />
        </div>

        <div>
          <label style={{ fontSize: 11, color: C.muted, fontWeight: 600 }}>
            CONTEXT OVERRIDE (JSON)
          </label>
          <textarea
            value={ctxRaw}
            onChange={e => validateCtx(e.target.value)}
            rows={3}
            style={{
              ...inputStyle, marginTop: 4, resize: 'vertical', fontFamily: 'monospace', fontSize: 12,
              borderColor: ctxErr ? C.red : C.border2,
            }}
          />
          {ctxErr && <div style={{ fontSize: 11, color: C.red, marginTop: 2 }}>Invalid JSON</div>}
        </div>

        {error && <div style={{ fontSize: 12, color: C.red }}>{error}</div>}

        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button onClick={onCancel} style={btnSmall(C.muted)}>Cancel</button>
          <button
            onClick={submit}
            disabled={saving || !flowId.trim() || ctxErr}
            style={btnPrimary()}
          >
            {saving ? 'Creating…' : 'Create Webhook'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Delivery log ──────────────────────────────────────────────────────────────

function DeliveryLog({ deliveries, loading }: { deliveries: WebhookDelivery[]; loading: boolean }) {
  function fmt(iso: string) {
    const d = new Date(iso)
    return d.toLocaleString('pt-BR', { hour12: false, timeZone: 'America/Sao_Paulo' })
  }

  return (
    <div>
      <div style={{ fontSize: 12, color: C.muted, fontWeight: 600, marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
        Delivery Log (last 20)
      </div>
      {loading && <Spinner />}
      {!loading && deliveries.length === 0 && (
        <div style={{ fontSize: 12, color: C.muted, fontStyle: 'italic' }}>No deliveries yet.</div>
      )}
      {deliveries.map(d => (
        <div key={d.id} style={{
          display: 'flex', alignItems: 'center', gap: 10,
          padding: '7px 0', borderBottom: `1px solid ${C.border}`, fontSize: 12,
        }}>
          <StatusBadge code={d.status_code} />
          <span style={{ color: C.muted, minWidth: 130 }}>{fmt(d.triggered_at)}</span>
          {d.latency_ms !== null && (
            <span style={{ color: C.muted }}>{d.latency_ms} ms</span>
          )}
          {d.instance_id && (
            <span style={{ color: C.muted, fontFamily: 'monospace', fontSize: 10 }}>
              inst {d.instance_id.slice(-8)}
            </span>
          )}
          {d.error && (
            <span style={{ color: C.red, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {d.error}
            </span>
          )}
        </div>
      ))}
    </div>
  )
}

// ── Webhook detail panel ──────────────────────────────────────────────────────

interface DetailProps {
  webhook:    Webhook
  adminToken: string
  onUpdated:  (updated: Webhook) => void
  onDeleted:  () => void
  plainToken: string | null   // set immediately after create or rotate
  clearToken: () => void
}

function WebhookDetail({ webhook, adminToken, onUpdated, onDeleted, plainToken, clearToken }: DetailProps) {
  const { deliveries, loading: dlLoading, refresh: refreshDl } = useWebhookDeliveries(webhook.id, adminToken)
  const [confirming, setConfirming] = useState<'delete' | 'rotate' | null>(null)
  const [working,    setWorking]    = useState(false)
  const [error,      setError]      = useState('')
  const [newToken,   setNewToken]   = useState<string | null>(plainToken)

  // Sync plain token when parent passes it (create case)
  React.useEffect(() => { if (plainToken) setNewToken(plainToken) }, [plainToken])

  async function toggleActive() {
    setWorking(true); setError('')
    try {
      const updated = await patchWebhook(webhook.id, { active: !webhook.active }, adminToken)
      onUpdated(updated)
    } catch (e: unknown) { setError(e instanceof Error ? e.message : 'Error') }
    finally { setWorking(false) }
  }

  async function rotate() {
    setWorking(true); setError(''); setConfirming(null)
    try {
      const { webhook: updated, token } = await rotateWebhookToken(webhook.id, adminToken)
      setNewToken(token)
      onUpdated(updated)
    } catch (e: unknown) { setError(e instanceof Error ? e.message : 'Error') }
    finally { setWorking(false) }
  }

  async function doDelete() {
    setWorking(true); setError(''); setConfirming(null)
    try {
      await deleteWebhook(webhook.id, adminToken)
      onDeleted()
    } catch (e: unknown) { setError(e instanceof Error ? e.message : 'Error') }
    finally { setWorking(false) }
  }

  function fmt(iso: string | null) {
    if (!iso) return '—'
    return new Date(iso).toLocaleString('pt-BR', { hour12: false })
  }

  const triggerUrl = `${window.location.origin}/v1/workflow/webhook/${webhook.id}`

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20, padding: '0 2px' }}>

      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
        <div>
          <div style={{ fontSize: 16, fontWeight: 700, color: C.text }}>{webhook.flow_id}</div>
          {webhook.description && (
            <div style={{ fontSize: 13, color: C.muted, marginTop: 2 }}>{webhook.description}</div>
          )}
          <div style={{ marginTop: 6 }}><Badge ok={webhook.active} /></div>
        </div>

        {/* Action buttons */}
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          <button
            onClick={toggleActive}
            disabled={working}
            style={btnSmall(webhook.active ? C.amber : C.green)}
          >
            {webhook.active ? 'Deactivate' : 'Activate'}
          </button>
          <button
            onClick={() => setConfirming('rotate')}
            disabled={working}
            style={btnSmall(C.accent)}
          >
            Rotate Token
          </button>
          <button
            onClick={() => setConfirming('delete')}
            disabled={working}
            style={btnSmall(C.red)}
          >
            Delete
          </button>
        </div>
      </div>

      {error && <div style={{ fontSize: 12, color: C.red }}>{error}</div>}

      {/* Confirmation prompts */}
      {confirming === 'rotate' && (
        <div style={{
          background: C.accentBg, border: `1px solid ${C.accent}`,
          borderRadius: 8, padding: 14, fontSize: 13,
        }}>
          <span style={{ color: C.text }}>
            Rotating the token immediately invalidates the current one. Continue?
          </span>
          <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
            <button onClick={() => setConfirming(null)} style={btnSmall(C.muted)}>Cancel</button>
            <button onClick={rotate} style={btnSmall(C.accent)}>Rotate</button>
          </div>
        </div>
      )}
      {confirming === 'delete' && (
        <div style={{
          background: '#1c0a0a', border: `1px solid ${C.red}`,
          borderRadius: 8, padding: 14, fontSize: 13,
        }}>
          <span style={{ color: C.text }}>
            Delete this webhook? All delivery records will be removed. This cannot be undone.
          </span>
          <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
            <button onClick={() => setConfirming(null)} style={btnSmall(C.muted)}>Cancel</button>
            <button onClick={doDelete} style={btnSmall(C.red)}>Delete</button>
          </div>
        </div>
      )}

      {/* Plain token reveal */}
      {newToken && (
        <div>
          <CopyBox value={newToken} label="New Webhook Token" />
          <div style={{ marginTop: 6, textAlign: 'right' }}>
            <button onClick={() => { setNewToken(null); clearToken() }} style={btnSmall(C.muted)}>
              I've saved it, dismiss
            </button>
          </div>
        </div>
      )}

      {/* Metadata */}
      <div style={{
        background: C.surface, border: `1px solid ${C.border}`,
        borderRadius: 8, padding: 16, display: 'grid',
        gridTemplateColumns: '1fr 1fr', gap: '10px 24px',
      }}>
        <Field label="Webhook ID"        value={webhook.id} mono />
        <Field label="Token prefix"      value={webhook.token_prefix} mono />
        <Field label="Trigger count"     value={String(webhook.trigger_count)} />
        <Field label="Last triggered"    value={fmt(webhook.last_triggered_at)} />
        <Field label="Created"           value={fmt(webhook.created_at)} />
        <Field label="Updated"           value={fmt(webhook.updated_at)} />
        <div style={{ gridColumn: '1 / -1' }}>
          <Field label="Trigger URL" value={triggerUrl} mono copyable />
        </div>
        {Object.keys(webhook.context_override).length > 0 && (
          <div style={{ gridColumn: '1 / -1' }}>
            <Field
              label="Context override"
              value={JSON.stringify(webhook.context_override, null, 2)}
              mono
            />
          </div>
        )}
      </div>

      {/* Delivery log */}
      <div style={{
        background: C.surface, border: `1px solid ${C.border}`,
        borderRadius: 8, padding: 16,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: C.text }}>Delivery Log</div>
          <button onClick={refreshDl} style={btnSmall(C.muted)}>Refresh</button>
        </div>
        <DeliveryLog deliveries={deliveries} loading={dlLoading} />
      </div>
    </div>
  )
}

function Field({
  label, value, mono = false, copyable = false,
}: {
  label: string; value: string; mono?: boolean; copyable?: boolean
}) {
  const [copied, setCopied] = useState(false)

  function copy() {
    navigator.clipboard.writeText(value).then(() => {
      setCopied(true); setTimeout(() => setCopied(false), 2000)
    })
  }

  return (
    <div>
      <div style={{ fontSize: 10, color: C.muted, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 2 }}>
        {label}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{
          fontSize: 12, color: C.text,
          fontFamily: mono ? 'monospace' : 'inherit',
          wordBreak: 'break-all', whiteSpace: mono ? 'pre-wrap' : 'normal',
        }}>
          {value}
        </span>
        {copyable && (
          <button onClick={copy} style={btnSmall(copied ? C.green : C.muted)}>
            {copied ? '✓' : 'Copy'}
          </button>
        )}
      </div>
    </div>
  )
}

// ── Main panel ────────────────────────────────────────────────────────────────

export function WebhookPanel({ tenantId, onBack }: Props) {
  const [adminToken,      setAdminToken]      = useState('')
  const [creating,        setCreating]        = useState(false)
  const [selectedId,      setSelectedId]      = useState<string | null>(null)
  const [pendingToken,    setPendingToken]     = useState<string | null>(null)

  const { webhooks, loading, refresh } = useWebhooks(tenantId, adminToken)

  const selected = webhooks.find(w => w.id === selectedId) ?? null

  function handleCreated(webhook: Webhook, token: string) {
    setCreating(false)
    setPendingToken(token)
    setSelectedId(webhook.id)
    refresh()
  }

  function handleUpdated(updated: Webhook) {
    // refresh list so active badge updates
    refresh()
    // keep the same webhook selected
    setSelectedId(updated.id)
  }

  function handleDeleted() {
    setSelectedId(null)
    refresh()
  }

  return (
    <div style={{ display: 'flex', flex: 1, overflow: 'hidden', background: C.bg }}>

      {/* ── Left sidebar: list ──────────────────────────────────────────────── */}
      <div style={{
        width: 380, borderRight: `1px solid ${C.border}`,
        display: 'flex', flexDirection: 'column', overflow: 'hidden',
      }}>
        {/* Toolbar */}
        <div style={{
          padding: '14px 16px', borderBottom: `1px solid ${C.border}`,
          display: 'flex', alignItems: 'center', gap: 10,
        }}>
          <button onClick={onBack} style={{ ...btnSmall(C.muted), marginRight: 4 }}>← Back</button>
          <span style={{ fontSize: 15, fontWeight: 700, color: C.text, flex: 1 }}>Webhooks</span>
          <button
            onClick={() => { setCreating(true); setSelectedId(null) }}
            style={btnPrimary()}
          >
            + New
          </button>
        </div>

        {/* Admin token input */}
        <div style={{ padding: '10px 16px', borderBottom: `1px solid ${C.border}` }}>
          <input
            type="password"
            value={adminToken}
            onChange={e => setAdminToken(e.target.value)}
            placeholder="Admin token (X-Admin-Token)"
            style={{
              width: '100%', boxSizing: 'border-box',
              background: '#0f172a', border: `1px solid ${C.border2}`,
              borderRadius: 6, color: C.text, fontSize: 12,
              padding: '5px 10px', outline: 'none', fontFamily: 'monospace',
            }}
          />
        </div>

        {/* List */}
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {loading && !webhooks.length && (
            <div style={{ padding: 16 }}><Spinner /></div>
          )}
          {!loading && !webhooks.length && adminToken && (
            <div style={{ padding: 16, fontSize: 13, color: C.muted, fontStyle: 'italic' }}>
              No webhooks yet for this tenant.
            </div>
          )}
          {!adminToken && (
            <div style={{ padding: 16, fontSize: 12, color: C.muted, fontStyle: 'italic' }}>
              Enter an admin token to manage webhooks.
            </div>
          )}
          {webhooks.map(wh => (
            <button
              key={wh.id}
              onClick={() => { setSelectedId(wh.id); setCreating(false) }}
              style={{
                width: '100%', textAlign: 'left', padding: '12px 16px',
                background: selectedId === wh.id ? '#1e1b4b' : 'transparent',
                border: 'none', borderBottom: `1px solid ${C.border}`,
                cursor: 'pointer',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                <span style={{ fontSize: 13, fontWeight: 600, color: C.text, flex: 1 }}>
                  {wh.flow_id}
                </span>
                <Badge ok={wh.active} />
              </div>
              {wh.description && (
                <div style={{
                  fontSize: 11, color: C.muted,
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  marginBottom: 4,
                }}>
                  {wh.description}
                </div>
              )}
              <div style={{ display: 'flex', gap: 12, fontSize: 11, color: C.muted }}>
                <span>🔗 {wh.trigger_count} triggers</span>
                <span style={{ fontFamily: 'monospace' }}>{wh.token_prefix}…</span>
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* ── Right content ──────────────────────────────────────────────────── */}
      <div style={{ flex: 1, overflowY: 'auto', padding: 24 }}>

        {creating && (
          <CreateForm
            tenantId   ={tenantId}
            adminToken ={adminToken}
            onCreated  ={handleCreated}
            onCancel   ={() => setCreating(false)}
          />
        )}

        {!creating && selected && (
          <WebhookDetail
            key        ={selected.id}
            webhook    ={selected}
            adminToken ={adminToken}
            onUpdated  ={handleUpdated}
            onDeleted  ={handleDeleted}
            plainToken ={selectedId === selected.id ? pendingToken : null}
            clearToken ={() => setPendingToken(null)}
          />
        )}

        {!creating && !selected && (
          <div style={{
            height: '100%', display: 'flex', flexDirection: 'column',
            alignItems: 'center', justifyContent: 'center', gap: 12,
          }}>
            <div style={{ fontSize: 40 }}>🔗</div>
            <div style={{ fontSize: 15, fontWeight: 600, color: C.muted }}>
              Select a webhook or create a new one
            </div>
            <div style={{ fontSize: 13, color: C.muted, maxWidth: 360, textAlign: 'center' }}>
              Webhooks let external systems (Salesforce, ERP, etc.) trigger
              workflows automatically via a secure authenticated URL.
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
