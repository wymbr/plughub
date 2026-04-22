/**
 * WorkflowsPage — /workflows
 *
 * Layout:
 *   Left column: status filter + instance list (polling 10s)
 *   Right panel:
 *     - When instance selected: detail (timeline, token, cancel)
 *     - Button to open Trigger modal
 */
import React, { useState } from 'react'
import { useAuth } from '@/auth/useAuth'
import {
  useWorkflowInstances, useWorkflowInstance,
  cancelWorkflow, triggerWorkflow,
} from './api/hooks'
import type { WorkflowInstance, WorkflowStatus } from './api/hooks'

// ─── Status config ────────────────────────────────────────────────────────────

const STATUS_COLORS: Record<WorkflowStatus, string> = {
  active:    '#3b82f6',
  suspended: '#eab308',
  completed: '#22c55e',
  failed:    '#ef4444',
  timed_out: '#ef4444',
  cancelled: '#6b7280',
}

const STATUS_LABELS: Record<WorkflowStatus | 'all', string> = {
  all:       'Todos',
  active:    'Ativo',
  suspended: 'Suspenso',
  completed: 'Concluído',
  failed:    'Falhou',
  timed_out: 'Expirou',
  cancelled: 'Cancelado',
}

const SUSPEND_LABELS: Record<string, string> = {
  approval: '⏳ Aguardando Aprovação',
  input:    '✏️ Aguardando Input',
  webhook:  '🔗 Aguardando Webhook',
  timer:    '⏰ Aguardando Timer',
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function WorkflowsPage() {
  const { session } = useAuth()
  const tenantId    = session?.tenantId ?? ''

  const [filterStatus, setFilterStatus] = useState<WorkflowStatus | 'all'>('all')
  const [selectedId,   setSelectedId]   = useState<string | null>(null)
  const [showTrigger,  setShowTrigger]  = useState(false)

  const statusParam = filterStatus === 'all' ? undefined : filterStatus
  const { instances, loading, refresh } = useWorkflowInstances(tenantId, statusParam, 10_000)
  const { instance: detail }            = useWorkflowInstance(selectedId, 10_000)

  const sorted = [...instances].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  )

  async function handleCancel() {
    if (!selectedId) return
    if (!confirm('Cancelar esta instância?')) return
    try {
      await cancelWorkflow(selectedId, tenantId)
      setSelectedId(null)
      refresh()
    } catch (e) { alert(String(e)) }
  }

  return (
    <div style={page}>
      {/* Top bar */}
      <div style={topBar}>
        <div>
          <span style={{ fontWeight: 700, fontSize: 17, color: '#e2e8f0' }}>⚡ Workflows</span>
          <span style={{ marginLeft: 10, fontSize: 12, color: '#64748b' }}>
            {loading ? '⟳' : `${sorted.length} instância(s)`}
          </span>
        </div>
        <button style={btnCreate} onClick={() => setShowTrigger(true)}>+ Disparar</button>
      </div>

      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* ─── Left: List ─────────────────────────────────────────────────── */}
        <div style={leftCol}>
          {/* Status filter */}
          <div style={filterBar}>
            {(['all', 'active', 'suspended', 'completed', 'failed'] as const).map(s => (
              <button
                key={s}
                onClick={() => { setFilterStatus(s); setSelectedId(null) }}
                style={{
                  padding: '3px 10px', borderRadius: 5, fontSize: 12, cursor: 'pointer',
                  fontWeight: filterStatus === s ? 600 : 400,
                  border: filterStatus === s ? `1px solid ${s === 'all' ? '#3b82f6' : STATUS_COLORS[s as WorkflowStatus]}` : '1px solid #334155',
                  background: filterStatus === s ? (s === 'all' ? '#1e40af22' : STATUS_COLORS[s as WorkflowStatus] + '22') : 'none',
                  color: filterStatus === s ? (s === 'all' ? '#93c5fd' : STATUS_COLORS[s as WorkflowStatus]) : '#64748b',
                }}
              >
                {STATUS_LABELS[s]}
              </button>
            ))}
          </div>

          {/* Instance rows */}
          <div style={{ flex: 1, overflowY: 'auto' }}>
            {sorted.length === 0 && !loading && (
              <div style={{ padding: '40px 20px', textAlign: 'center', color: '#475569', fontSize: 13 }}>
                Nenhuma instância encontrada.
              </div>
            )}
            {sorted.map(inst => (
              <InstanceRow
                key={inst.id}
                inst={inst}
                selected={inst.id === selectedId}
                onClick={() => setSelectedId(inst.id === selectedId ? null : inst.id)}
              />
            ))}
          </div>
        </div>

        {/* ─── Right: Detail ──────────────────────────────────────────────── */}
        {detail ? (
          <InstanceDetail
            instance={detail}
            onCancel={handleCancel}
            onClose={() => setSelectedId(null)}
          />
        ) : (
          <div style={emptyDetail}>
            <div style={{ fontSize: 32, marginBottom: 12 }}>⚡</div>
            <div style={{ fontSize: 14, color: '#475569' }}>Selecione uma instância para ver os detalhes</div>
          </div>
        )}
      </div>

      {/* Trigger modal */}
      {showTrigger && (
        <TriggerModal
          tenantId={tenantId}
          installationId={session?.installationId ?? ''}
          onClose={() => setShowTrigger(false)}
          onTriggered={() => { setShowTrigger(false); refresh() }}
        />
      )}
    </div>
  )
}

// ─── InstanceRow ──────────────────────────────────────────────────────────────

function InstanceRow({ inst, selected, onClick }: { inst: WorkflowInstance; selected: boolean; onClick: () => void }) {
  const color = STATUS_COLORS[inst.status]
  return (
    <div
      onClick={onClick}
      style={{
        padding: '12px 16px', borderBottom: '1px solid #1e293b', cursor: 'pointer',
        background: selected ? '#1e293b' : 'transparent',
        borderLeft: selected ? `3px solid ${color}` : '3px solid transparent',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div style={{ flex: 1 }}>
          <code style={{ fontSize: 12, fontWeight: 600, color: '#93c5fd' }}>{inst.id.slice(0, 8)}…</code>
          <div style={{ fontSize: 12, color: '#64748b', marginTop: 2 }}>{inst.flow_id}</div>
        </div>
        <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 7px', borderRadius: 4, background: color + '33', color }}>
          {inst.status === 'timed_out' ? 'expirou' : STATUS_LABELS[inst.status].toLowerCase()}
        </span>
      </div>
      <div style={{ fontSize: 11, color: '#475569', marginTop: 5 }}>
        {new Date(inst.created_at).toLocaleString('pt-BR')}
      </div>
      {inst.suspend_reason && (
        <div style={{ fontSize: 11, color: '#fbbf24', marginTop: 3 }}>
          {SUSPEND_LABELS[inst.suspend_reason] ?? inst.suspend_reason}
        </div>
      )}
    </div>
  )
}

// ─── InstanceDetail ───────────────────────────────────────────────────────────

function InstanceDetail({ instance: inst, onCancel, onClose }: {
  instance: WorkflowInstance
  onCancel: () => void
  onClose:  () => void
}) {
  const color    = STATUS_COLORS[inst.status]
  const canCancel = ['active', 'suspended'].includes(inst.status)

  return (
    <div style={detailPanel}>
      {/* Header */}
      <div style={detailHeader}>
        <div>
          <code style={{ fontSize: 12, color: '#93c5fd' }}>{inst.id}</code>
          <div style={{ fontSize: 12, color: '#64748b', marginTop: 2 }}>{inst.flow_id}</div>
        </div>
        <button onClick={onClose} style={{ background: 'none', border: 'none', color: '#475569', cursor: 'pointer', fontSize: 18 }}>✕</button>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '16px 20px' }}>
        {/* Status */}
        <Section label="Status">
          <span style={{ fontSize: 12, fontWeight: 700, padding: '3px 10px', borderRadius: 4, background: color + '33', color }}>
            {inst.status === 'timed_out' ? 'expirou' : STATUS_LABELS[inst.status]}
          </span>
          {inst.current_step && (
            <div style={{ marginTop: 8, fontSize: 12, color: '#94a3b8' }}>
              Step atual: <code style={{ color: '#e2e8f0' }}>{inst.current_step}</code>
            </div>
          )}
          {inst.outcome && (
            <div style={{ marginTop: 4, fontSize: 12, color: '#94a3b8' }}>
              Outcome: <code style={{ color: '#e2e8f0' }}>{inst.outcome}</code>
            </div>
          )}
        </Section>

        {/* Timeline */}
        <Section label="Timeline">
          <TimelineEntry dot="#22c55e" label="Criado" ts={inst.created_at} />
          {inst.suspended_at  && <TimelineEntry dot="#eab308" label="Suspenso"  ts={inst.suspended_at} />}
          {inst.resumed_at    && <TimelineEntry dot="#3b82f6" label="Retomado"  ts={inst.resumed_at} />}
          {inst.completed_at  && <TimelineEntry dot="#22c55e" label="Concluído" ts={inst.completed_at} />}
        </Section>

        {/* Suspend reason */}
        {inst.suspend_reason && (
          <Section label="Motivo de suspensão">
            <span style={{ fontSize: 12, padding: '3px 10px', borderRadius: 4, background: '#451a0322', color: '#fde047', border: '1px solid #451a03' }}>
              {SUSPEND_LABELS[inst.suspend_reason] ?? inst.suspend_reason}
            </span>
          </Section>
        )}

        {/* Resume token */}
        {inst.resume_token && (
          <Section label="Resume Token">
            <div
              style={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 4, padding: '8px 12px', fontSize: 11, fontFamily: 'monospace', color: '#94a3b8', wordBreak: 'break-all', cursor: 'pointer' }}
              onClick={() => { void navigator.clipboard.writeText(inst.resume_token!); }}
              title="Clique para copiar"
            >
              {inst.resume_token}
            </div>
            {inst.resume_expires_at && (
              <div style={{ marginTop: 4, fontSize: 11, color: '#475569' }}>
                Expira: {new Date(inst.resume_expires_at).toLocaleString('pt-BR')}
              </div>
            )}
          </Section>
        )}
      </div>

      {/* Cancel */}
      {canCancel && (
        <div style={{ padding: '12px 16px', borderTop: '1px solid #1e293b' }}>
          <button
            onClick={onCancel}
            style={{ width: '100%', padding: '8px', borderRadius: 4, border: '1px solid #ef4444', background: '#7f1d1d', color: '#fca5a5', cursor: 'pointer', fontSize: 13, fontWeight: 600 }}
          >
            Cancelar Instância
          </button>
        </div>
      )}
    </div>
  )
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 20 }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}>{label}</div>
      {children}
    </div>
  )
}

function TimelineEntry({ dot, label, ts }: { dot: string; label: string; ts: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, fontSize: 12 }}>
      <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', backgroundColor: dot, flexShrink: 0 }} />
      <span style={{ color: '#94a3b8', minWidth: 80 }}>{label}</span>
      <span style={{ color: '#64748b' }}>{new Date(ts).toLocaleString('pt-BR')}</span>
    </div>
  )
}

// ─── TriggerModal ─────────────────────────────────────────────────────────────

function TriggerModal({ tenantId, installationId, onClose, onTriggered }: {
  tenantId:       string
  installationId: string
  onClose:        () => void
  onTriggered:    () => void
}) {
  const [flowId,   setFlowId]   = useState('')
  const [metaJson, setMetaJson] = useState('{}')
  const [error,    setError]    = useState<string | null>(null)
  const [saving,   setSaving]   = useState(false)

  async function handleTrigger() {
    if (!flowId.trim()) { setError('flow_id obrigatório'); return }
    let metadata: Record<string, unknown>
    try { metadata = JSON.parse(metaJson) }
    catch { setError('metadata deve ser JSON válido'); return }

    setSaving(true); setError(null)
    try {
      await triggerWorkflow({
        tenant_id:       tenantId,
        installation_id: installationId,
        organization_id: tenantId,
        flow_id:         flowId.trim(),
        metadata,
      })
      onTriggered()
    } catch (e) { setError(String(e)) }
    finally { setSaving(false) }
  }

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100 }}>
      <div style={{ background: '#1e293b', borderRadius: 10, padding: 24, width: 480, maxWidth: '95vw', boxShadow: '0 20px 60px rgba(0,0,0,0.6)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
          <h3 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: '#e2e8f0' }}>⚡ Disparar Workflow</h3>
          <button onClick={onClose} style={{ background: 'none', border: 'none', color: '#64748b', cursor: 'pointer', fontSize: 18 }}>✕</button>
        </div>

        <div style={{ marginBottom: 14 }}>
          <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: '#94a3b8', marginBottom: 4 }}>Flow ID *</label>
          <input
            style={inputStyle}
            value={flowId}
            onChange={e => setFlowId(e.target.value)}
            placeholder="ex: skill_portabilidade_telco_v2"
          />
        </div>

        <div style={{ marginBottom: 14 }}>
          <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: '#94a3b8', marginBottom: 4 }}>Metadata (JSON)</label>
          <textarea
            style={{ ...inputStyle, height: 100, resize: 'vertical', fontFamily: 'monospace', fontSize: 12 }}
            value={metaJson}
            onChange={e => setMetaJson(e.target.value)}
          />
        </div>

        {error && <div style={{ fontSize: 12, color: '#ef4444', marginBottom: 12 }}>⚠ {error}</div>}

        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button style={btnCreate} onClick={handleTrigger} disabled={saving}>
            {saving ? 'Disparando…' : 'Disparar'}
          </button>
          <button style={btnSecondary} onClick={onClose}>Cancelar</button>
        </div>
      </div>
    </div>
  )
}

// ─── Styles ───────────────────────────────────────────────────────────────────

const page: React.CSSProperties      = { display: 'flex', flexDirection: 'column', height: '100%', backgroundColor: '#0a1628', color: '#e2e8f0', overflow: 'hidden' }
const topBar: React.CSSProperties    = { display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 20px', borderBottom: '1px solid #1e293b', flexShrink: 0 }
const filterBar: React.CSSProperties = { display: 'flex', gap: 6, padding: '10px 16px', borderBottom: '1px solid #1e293b', flexShrink: 0, flexWrap: 'wrap' }
const leftCol: React.CSSProperties   = { width: 320, flexShrink: 0, borderRight: '1px solid #1e293b', display: 'flex', flexDirection: 'column', overflow: 'hidden' }
const detailPanel: React.CSSProperties = { flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }
const detailHeader: React.CSSProperties = { display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', padding: '14px 20px', borderBottom: '1px solid #1e293b', flexShrink: 0 }
const emptyDetail: React.CSSProperties = { flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' }
const btnCreate: React.CSSProperties   = { background: '#1e40af', border: 'none', color: '#e2e8f0', borderRadius: 6, padding: '6px 16px', cursor: 'pointer', fontSize: 13, fontWeight: 600 }
const btnSecondary: React.CSSProperties = { background: 'none', border: '1px solid #334155', color: '#94a3b8', borderRadius: 6, padding: '5px 12px', cursor: 'pointer', fontSize: 12 }
const inputStyle: React.CSSProperties  = { width: '100%', background: '#0f172a', border: '1px solid #334155', borderRadius: 6, color: '#e2e8f0', fontSize: 13, padding: '6px 10px', outline: 'none', boxSizing: 'border-box' }
