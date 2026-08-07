/**
 * WorkflowsPage — /workflows
 *
 * Two tabs: Instâncias (workflow lifecycle) and Webhooks (trigger management)
 */
import React, { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import {
  useWorkflowInstances, useWorkflowInstance, triggerWorkflow,
} from './api/hooks'
import type { WorkflowInstance, WorkflowStatus } from './api/hooks'
import WebhooksTab from './WebhooksTab'

type Tab = 'instances' | 'webhooks'

// ─── Status config ────────────────────────────────────────────────────────────

const STATUS_COLORS: Record<WorkflowStatus, string> = {
  active:    '#3b82f6',
  suspended: '#eab308',
  completed: '#22c55e',
  failed:    '#ef4444',
  timed_out: '#ef4444',
  cancelled: '#6b7280',
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function WorkflowsPage() {
  const { t } = useTranslation('workflows')
  const { session, tenantId } = useAuth()

  const [activeTab,    setActiveTab]    = useState<Tab>('instances')
  const [filterStatus, setFilterStatus] = useState<WorkflowStatus | 'all'>('all')
  const [selectedId,   setSelectedId]   = useState<string | null>(null)
  const [showTrigger,  setShowTrigger]  = useState(false)

  const statusParam = filterStatus === 'all' ? undefined : filterStatus
  const { instances, loading, refresh } = useWorkflowInstances(tenantId, statusParam, 10_000)
  const { instance: detail }            = useWorkflowInstance(selectedId, 10_000)

  const sorted = [...instances].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  )

  const TABS: { key: Tab; label: string }[] = [
    { key: 'instances', label: `⚡ ${t('instance.title')}` },
    { key: 'webhooks',  label: `🔗 ${t('webhook.title')}`  },
  ]

  return (
    <div style={page}>
      {/* Tab bar */}
      <div style={{ display: 'flex', alignItems: 'center', borderBottom: '1px solid #1e293b', flexShrink: 0, paddingLeft: 20, paddingRight: 20 }}>
        {TABS.map(tab => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            style={{
              padding: '10px 16px', fontSize: 13, fontWeight: activeTab === tab.key ? 700 : 400,
              color: activeTab === tab.key ? '#93c5fd' : '#64748b',
              background: 'none', border: 'none', cursor: 'pointer',
              borderBottom: activeTab === tab.key ? '2px solid #93c5fd' : '2px solid transparent',
              marginBottom: -1,
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Webhooks tab */}
      {activeTab === 'webhooks' && (
        <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
          <WebhooksTab />
        </div>
      )}

      {/* Instances tab */}
      {activeTab === 'instances' && (<>
      {/* Top bar */}
      <div style={topBar}>
        <div>
          <span style={{ fontWeight: 700, fontSize: 17, color: '#e2e8f0' }}>⚡ {t('instance.title')}</span>
          <span style={{ marginLeft: 10, fontSize: 12, color: '#64748b' }}>
            {loading ? '⟳' : t('instance.count', { count: sorted.length })}
          </span>
        </div>
        <button style={btnCreate} onClick={() => setShowTrigger(true)}>+ {t('trigger.submit')}</button>
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
                {s === 'all' ? t('instance.statuses.all') : t(`statuses.${s}`)}
              </button>
            ))}
          </div>

          {/* Instance rows */}
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

        {/* ─── Right: Detail ──────────────────────────────────────────────── */}
        {detail ? (
          <InstanceDetail
            instance={detail}
            onClose={() => setSelectedId(null)}
            t={t}
          />
        ) : (
          <div style={emptyDetail}>
            <div style={{ fontSize: 32, marginBottom: 12 }}>⚡</div>
            <div style={{ fontSize: 14, color: '#475569' }}>{t('instance.selectForDetails')}</div>
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
          t={t}
        />
      )}
      </>)}
    </div>
  )
}

// ─── InstanceRow ──────────────────────────────────────────────────────────────

function InstanceRow({ inst, selected, onClick }: { inst: WorkflowInstance; selected: boolean; onClick: () => void }) {
  const { t } = useTranslation('workflows')
  const color = STATUS_COLORS[inst.status]
  const statusLabel = inst.status === 'timed_out' ? t('statuses.timed_out').toLowerCase() : t(`statuses.${inst.status}`).toLowerCase()
  const suspendLabel = inst.suspend_reason ? t(`suspendReasons.${inst.suspend_reason}`) ?? inst.suspend_reason : null

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
          {statusLabel}
        </span>
      </div>
      <div style={{ fontSize: 11, color: '#475569', marginTop: 5 }}>
        {new Date(inst.created_at).toLocaleString('pt-BR')}
      </div>
      {suspendLabel && (
        <div style={{ fontSize: 11, color: '#fbbf24', marginTop: 3 }}>
          {suspendLabel}
        </div>
      )}
    </div>
  )
}

// ─── InstanceDetail ───────────────────────────────────────────────────────────

function InstanceDetail({ instance: inst, onClose, t }: {
  instance: WorkflowInstance
  onClose:  () => void
  t: (key: string, options?: Record<string, unknown>) => string
}) {
  const color    = STATUS_COLORS[inst.status]
  const suspendLabel = inst.suspend_reason ? t(`suspendReasons.${inst.suspend_reason}`) ?? inst.suspend_reason : null

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
        <Section label={t('instance.status')}>
          <span style={{ fontSize: 12, fontWeight: 700, padding: '3px 10px', borderRadius: 4, background: color + '33', color }}>
            {inst.status === 'timed_out' ? t('statuses.timed_out') : t(`statuses.${inst.status}`)}
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

        {/* Timeline */}
        <Section label={t('instance.timeline')}>
          <TimelineEntry dot="#22c55e" label={t('instance.created')} ts={inst.created_at} />
          {inst.suspended_at  && <TimelineEntry dot="#eab308" label={t('instance.suspended')}  ts={inst.suspended_at} />}
          {inst.resumed_at    && <TimelineEntry dot="#3b82f6" label={t('instance.resumed')}  ts={inst.resumed_at} />}
          {inst.completed_at  && <TimelineEntry dot="#22c55e" label={t('instance.completed')} ts={inst.completed_at} />}
        </Section>

        {/* Suspend reason */}
        {suspendLabel && (
          <Section label={t('instance.suspendReason')}>
            <span style={{ fontSize: 12, padding: '3px 10px', borderRadius: 4, background: '#451a0322', color: '#fde047', border: '1px solid #451a03' }}>
              {suspendLabel}
            </span>
          </Section>
        )}

        {/* Resume token */}
        {inst.resume_token && (
          <Section label={t('instance.resumeToken')}>
            <div
              style={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 4, padding: '8px 12px', fontSize: 11, fontFamily: 'monospace', color: '#94a3b8', wordBreak: 'break-all', cursor: 'pointer' }}
              onClick={() => { void navigator.clipboard.writeText(inst.resume_token!); }}
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

function TriggerModal({ tenantId, installationId, onClose, onTriggered, t }: {
  tenantId:       string
  installationId: string
  onClose:        () => void
  onTriggered:    () => void
  t: (key: string, options?: Record<string, unknown>) => string
}) {
  const [flowId,   setFlowId]   = useState('')
  const [metaJson, setMetaJson] = useState('{}')
  const [error,    setError]    = useState<string | null>(null)
  const [saving,   setSaving]   = useState(false)

  async function handleTrigger() {
    if (!flowId.trim()) { setError(t('trigger.flowId')); return }
    let metadata: Record<string, unknown>
    try { metadata = JSON.parse(metaJson) }
    catch { setError(t('trigger.context')); return }

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
          <h3 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: '#e2e8f0' }}>⚡ {t('trigger.title')}</h3>
          <button onClick={onClose} style={{ background: 'none', border: 'none', color: '#64748b', cursor: 'pointer', fontSize: 18 }}>✕</button>
        </div>

        <div style={{ marginBottom: 14 }}>
          <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: '#94a3b8', marginBottom: 4 }}>{t('trigger.flowId')} *</label>
          <input
            style={inputStyle}
            value={flowId}
            onChange={e => setFlowId(e.target.value)}
            placeholder="ex: skill_portabilidade_telco_v2"
          />
        </div>

        <div style={{ marginBottom: 14 }}>
          <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: '#94a3b8', marginBottom: 4 }}>{t('trigger.context')}</label>
          <textarea
            style={{ ...inputStyle, height: 100, resize: 'vertical', fontFamily: 'monospace', fontSize: 12 }}
            value={metaJson}
            onChange={e => setMetaJson(e.target.value)}
          />
        </div>

        {error && <div style={{ fontSize: 12, color: '#ef4444', marginBottom: 12 }}>⚠ {error}</div>}

        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button style={btnCreate} onClick={handleTrigger} disabled={saving}>
            {saving ? t('trigger.submit') + '…' : t('trigger.submit')}
          </button>
          <button style={btnSecondary} onClick={onClose}>{t('trigger.cancel')}</button>
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
