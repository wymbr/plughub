/**
 * WorkflowMonitorPage — /workflows/monitor
 *
 * Live view of all workflow instances: list + detail panel.
 * Extracted from WorkflowsPage (instances tab).
 */
import React, { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { X } from 'lucide-react'
import { useAuth } from '@/auth/useAuth'
import {
  useWorkflowInstances, useWorkflowInstance,
} from './api/hooks'
import type { WorkflowInstance, WorkflowStatus } from './api/hooks'

// ── Helpers ────────────────────────────────────────────────────────────────────

const STATUS_COLORS: Record<WorkflowStatus, string> = {
  active:    '#3b82f6',
  suspended: '#eab308',
  completed: '#22c55e',
  failed:    '#ef4444',
  timed_out: '#ef4444',
  cancelled: '#6b7280',
}

// Status and suspend labels are now i18n translated (see WorkflowMonitorPage function)

// ── Page ───────────────────────────────────────────────────────────────────────

export default function WorkflowMonitorPage() {
  const { t } = useTranslation('workflows')
  const { tenantId } = useAuth()

  const [filterStatus, setFilterStatus] = useState<WorkflowStatus | 'all'>('all')
  const [selectedId,   setSelectedId]   = useState<string | null>(null)

  // i18n status labels
  const STATUS_LABELS: Record<WorkflowStatus | 'all', string> = {
    all:       t('instance.statuses.all'),
    active:    t('statuses.active'),
    suspended: t('statuses.suspended'),
    completed: t('statuses.completed'),
    failed:    t('statuses.failed'),
    timed_out: t('statuses.timed_out'),
    cancelled: t('statuses.cancelled'),
  }

  // i18n suspend reason labels
  const SUSPEND_LABELS: Record<string, string> = {
    approval: `⏳ ${t('suspendReasons.approval')}`,
    input:    `✏️ ${t('suspendReasons.input')}`,
    webhook:  `🔗 ${t('suspendReasons.webhook')}`,
    timer:    `⏰ ${t('suspendReasons.timer')}`,
  }

  const statusParam = filterStatus === 'all' ? undefined : filterStatus
  const { instances, loading, refresh } = useWorkflowInstances(tenantId, statusParam, 10_000)
  const { instance: detail }            = useWorkflowInstance(selectedId, 10_000)

  const sorted = [...instances].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  )

  // Nested component: InstanceRow
  function InstanceRow({ inst, selected, onClick }: {
    inst: WorkflowInstance; selected: boolean; onClick: () => void
  }) {
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
            {STATUS_LABELS[inst.status] ?? inst.status}
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

  // Nested component: Section
  function Section({ label, children }: { label?: string; children: React.ReactNode }) {
    return (
      <div style={{ marginBottom: 20 }}>
        {label && <div style={{ fontSize: 11, fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}>{label}</div>}
        {children}
      </div>
    )
  }

  // Nested component: TimelineEntry
  function TimelineEntry({ dot, label, ts }: { dot: string; label: string; ts: string }) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, fontSize: 12 }}>
        <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', backgroundColor: dot, flexShrink: 0 }} />
        <span style={{ color: '#94a3b8', minWidth: 80 }}>{label}</span>
        <span style={{ color: '#64748b' }}>{new Date(ts).toLocaleString('pt-BR')}</span>
      </div>
    )
  }

  // Nested component: InstanceDetail
  function InstanceDetail({ instance: inst, onClose }: {
    instance: WorkflowInstance; onClose: () => void
  }) {
    const color    = STATUS_COLORS[inst.status]

    return (
      <div style={detailPanel}>
        <div style={detailHeader}>
          <div>
            <code style={{ fontSize: 12, color: '#93c5fd' }}>{inst.id}</code>
            <div style={{ fontSize: 12, color: '#64748b', marginTop: 2 }}>{inst.flow_id}</div>
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', color: '#475569', cursor: 'pointer', display: 'flex', alignItems: 'center' }} aria-label="Fechar"><X size={16} aria-hidden="true" /></button>
        </div>

        <div style={{ flex: 1, overflowY: 'auto', padding: '16px 20px' }}>
          <Section label="Status">
            <span style={{ fontSize: 12, fontWeight: 700, padding: '3px 10px', borderRadius: 4, background: color + '33', color }}>
              {STATUS_LABELS[inst.status] ?? inst.status}
            </span>
            {inst.current_step && (
              <div style={{ marginTop: 8, fontSize: 12, color: '#94a3b8' }}>
                {t('instance.currentStep')}: <code style={{ color: '#e2e8f0' }}>{inst.current_step}</code>
              </div>
            )}
            {inst.outcome && (
              <div style={{ marginTop: 4, fontSize: 12, color: '#94a3b8' }}>
                {t('instance.outcome')}: <code style={{ color: '#e2e8f0' }}>{inst.outcome}</code>
              </div>
            )}
          </Section>

          <Section label={t('instance.timeline')}>
            <TimelineEntry dot="#22c55e" label={t('instance.created')}    ts={inst.created_at} />
            {inst.suspended_at && <TimelineEntry dot="#eab308" label={t('instance.suspended')}  ts={inst.suspended_at} />}
            {inst.resumed_at   && <TimelineEntry dot="#3b82f6" label={t('instance.resumed')}  ts={inst.resumed_at} />}
            {inst.completed_at && <TimelineEntry dot="#22c55e" label={t('instance.completed')} ts={inst.completed_at} />}
          </Section>

          {inst.suspend_reason && (
            <Section label={t('instance.suspendReason')}>
              <span style={{ fontSize: 12, padding: '3px 10px', borderRadius: 4, background: '#45180322', color: '#fde047', border: '1px solid #451803' }}>
                {SUSPEND_LABELS[inst.suspend_reason] ?? inst.suspend_reason}
              </span>
            </Section>
          )}

          {inst.resume_token && (
            <Section label={t('instance.resumeToken')}>
              <div
                style={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 4, padding: '8px 12px', fontSize: 11, fontFamily: 'monospace', color: '#94a3b8', wordBreak: 'break-all', cursor: 'pointer' }}
                onClick={() => { void navigator.clipboard.writeText(inst.resume_token!) }}
                title={t('instance.clickToCopy')}
              >
                {inst.resume_token}
              </div>
              {inst.resume_expires_at && (
                <div style={{ marginTop: 4, fontSize: 11, color: '#475569' }}>
                  {t('instance.expiresAt')}: {new Date(inst.resume_expires_at).toLocaleString('pt-BR')}
                </div>
              )}
            </Section>
          )}
        </div>

        {/* Botão "Cancelar" REMOVIDO em 2026-08-07 (I5, lacuna 4b) — ver
            `api/hooks.ts` para o motivo medido. */}
      </div>
    )
  }

  return (
    <div style={page}>
      {/* Top bar */}
      <div style={topBar}>
        <div>
          <span style={{ fontWeight: 700, fontSize: 17, color: '#e2e8f0' }}>📡 {t('monitor')}</span>
          <span style={{ marginLeft: 10, fontSize: 12, color: '#64748b' }}>
            {loading ? '⟳' : t('instance.count', { count: sorted.length })}
          </span>
        </div>
        <button
          style={btnSecondary}
          onClick={refresh}
        >
          ↻ {t('actions.refresh')}
        </button>
      </div>

      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* ─── Left: list ─────────────────────────────────────────────── */}
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
                  border: filterStatus === s
                    ? `1px solid ${s === 'all' ? '#3b82f6' : STATUS_COLORS[s as WorkflowStatus]}`
                    : '1px solid #334155',
                  background: filterStatus === s
                    ? (s === 'all' ? '#1e40af22' : STATUS_COLORS[s as WorkflowStatus] + '22')
                    : 'none',
                  color: filterStatus === s
                    ? (s === 'all' ? '#93c5fd' : STATUS_COLORS[s as WorkflowStatus])
                    : '#64748b',
                }}
              >
                {STATUS_LABELS[s]}
              </button>
            ))}
          </div>

          <div style={{ flex: 1, overflowY: 'auto' }}>
            {sorted.length === 0 && !loading && (
              <div style={{ padding: '40px 20px', textAlign: 'center', color: '#475569', fontSize: 13 }}>
                {t('instance.noItems')}
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

        {/* ─── Right: detail ──────────────────────────────────────────── */}
        {detail ? (
          <InstanceDetail
            instance={detail}
            onClose={() => setSelectedId(null)}
          />
        ) : (
          <div style={emptyDetail}>
            <div style={{ fontSize: 32, marginBottom: 12 }}>📡</div>
            <div style={{ fontSize: 14, color: '#475569' }}>{t('instance.selectForDetails')}</div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Styles ─────────────────────────────────────────────────────────────────────

const page: React.CSSProperties        = { display: 'flex', flexDirection: 'column', height: '100%', backgroundColor: '#0a1628', color: '#e2e8f0', overflow: 'hidden' }
const topBar: React.CSSProperties      = { display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 20px', borderBottom: '1px solid #1e293b', flexShrink: 0 }
const filterBar: React.CSSProperties   = { display: 'flex', gap: 6, padding: '10px 16px', borderBottom: '1px solid #1e293b', flexShrink: 0, flexWrap: 'wrap' }
const leftCol: React.CSSProperties     = { width: 320, flexShrink: 0, borderRight: '1px solid #1e293b', display: 'flex', flexDirection: 'column', overflow: 'hidden' }
const detailPanel: React.CSSProperties = { flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }
const detailHeader: React.CSSProperties = { display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', padding: '14px 20px', borderBottom: '1px solid #1e293b', flexShrink: 0 }
const emptyDetail: React.CSSProperties = { flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' }
const btnSecondary: React.CSSProperties = { background: 'none', border: '1px solid #334155', color: '#94a3b8', borderRadius: 6, padding: '5px 12px', cursor: 'pointer', fontSize: 12 }
