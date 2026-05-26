/**
 * WorkflowEditorPage — /workflows/editor
 *
 * Trigger form: select a skill, configure context, run now.
 * Left panel: available orchestrator skills.
 * Right panel: form + recent instances for selected flow.
 */
import React, { useCallback, useEffect, useState } from 'react'
import { Play } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'
import { triggerWorkflow, useWorkflowInstances } from './api/hooks'
import type { Skill } from '@/types'

// ── API helpers ────────────────────────────────────────────────────────────────

function headers(token?: string) {
  const h: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) h['Authorization'] = `Bearer ${token}`
  return h
}

async function fetchSkills(tenantId: string, token?: string): Promise<Skill[]> {
  const res = await fetch(`/v1/skills?tenant_id=${tenantId}`, { headers: headers(token) })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const body = await res.json()
  return Array.isArray(body) ? body : (body.data ?? body.skills ?? [])
}

// ── Status helpers ─────────────────────────────────────────────────────────────

const STATUS_COLORS: Record<string, string> = {
  active:    '#3b82f6',
  suspended: '#eab308',
  completed: '#22c55e',
  failed:    '#ef4444',
  timed_out: '#ef4444',
  cancelled: '#6b7280',
}

// STATUS_LABELS will be generated from i18n in the component function

// ── Main page ──────────────────────────────────────────────────────────────────

export default function WorkflowEditorPage() {
  const { t } = useTranslation('workflows')
  const { session, getAccessToken, tenantId } = useAuth()
  const installId   = session?.installationId ?? ''

  const [skills,       setSkills]       = useState<Skill[]>([])
  const [skillsLoading, setSkillsLoading] = useState(false)
  const [selectedFlow, setSelectedFlow] = useState<string>('')
  const [flowInput,    setFlowInput]    = useState<string>('')
  const [contextJson,  setContextJson]  = useState('{}')
  const [error,        setError]        = useState<string | null>(null)
  const [triggering,   setTriggering]   = useState(false)
  const [lastTriggered, setLastTriggered] = useState<string | null>(null)

  // i18n status labels
  const STATUS_LABELS: Record<string, string> = {
    active:    t('statuses.active'),
    suspended: t('statuses.suspended'),
    completed: t('statuses.completed'),
    failed:    t('statuses.failed'),
    timed_out: t('statuses.timed_out'),
    cancelled: t('statuses.cancelled'),
  }

  const { instances, loading: instLoading, refresh } = useWorkflowInstances(
    tenantId,
    undefined,
    15_000,
  )

  // Filter instances to the selected flow
  const flowInstances = selectedFlow
    ? instances.filter(i => i.flow_id === selectedFlow).slice(0, 10)
    : []

  // Load available skills
  useEffect(() => {
    if (!tenantId) return
    setSkillsLoading(true)
    getAccessToken()
      .then(token => fetchSkills(tenantId, token ?? undefined))
      .then(list => {
        // Prefer orchestrator-type skills; show all if none
        const orch = list.filter(s =>
          (s as any).classification?.type === 'orchestrator'
        )
        setSkills(orch.length > 0 ? orch : list)
      })
      .catch(() => setSkills([]))
      .finally(() => setSkillsLoading(false))
  }, [tenantId])

  function handleSelectSkill(skillId: string) {
    setSelectedFlow(skillId)
    setFlowInput(skillId)
    setError(null)
  }

  async function handleTrigger() {
    const flowId = flowInput.trim() || selectedFlow
    if (!flowId) { setError(t('editor.selectFlowId')); return }
    let context: Record<string, unknown>
    try { context = JSON.parse(contextJson) }
    catch { setError(t('editor.invalidJson')); return }

    setTriggering(true); setError(null)
    try {
      await triggerWorkflow({
        tenant_id:       tenantId,
        installation_id: installId,
        organization_id: tenantId,
        flow_id:         flowId,
        metadata:        context,
      })
      setLastTriggered(new Date().toLocaleTimeString('pt-BR'))
      refresh()
    } catch (e) {
      setError(String(e))
    } finally {
      setTriggering(false)
    }
  }

  return (
    <div style={page}>
      {/* ── Left: skill list ─────────────────────────────────────────────── */}
      <div style={leftCol}>
        <div style={colHeader}>
          <span style={{ fontWeight: 700, fontSize: 13, color: '#e2e8f0' }}>{t('editor.availableFlows')}</span>
          {skillsLoading && <Spinner />}
        </div>

        <div style={{ flex: 1, overflowY: 'auto' }}>
          {skills.length === 0 && !skillsLoading && (
            <div style={{ padding: '24px 16px', textAlign: 'center', color: '#475569', fontSize: 12 }}>
              {t('editor.noSkillsFound')}
            </div>
          )}
          {skills.map(sk => {
            const id = sk.skill_id ?? (sk as any).id
            const active = id === selectedFlow
            return (
              <button
                key={id}
                onClick={() => handleSelectSkill(id)}
                style={{
                  width: '100%', textAlign: 'left', padding: '10px 16px',
                  borderBottom: '1px solid #1e293b', cursor: 'pointer',
                  background: active ? '#1e3a5f' : 'transparent',
                  borderLeft: active ? '3px solid #3b82f6' : '3px solid transparent',
                }}
              >
                <div style={{ fontSize: 12, fontWeight: 600, color: active ? '#93c5fd' : '#e2e8f0' }}>
                  {id}
                </div>
                {sk.name && sk.name !== id && (
                  <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>{sk.name}</div>
                )}
                {(sk as any).classification?.type && (
                  <span style={{ fontSize: 10, color: '#7c3aed', background: '#2e1065', padding: '1px 5px', borderRadius: 3, marginTop: 3, display: 'inline-block' }}>
                    {(sk as any).classification.type}
                  </span>
                )}
              </button>
            )
          })}
        </div>
      </div>

      {/* ── Right: trigger form + history ────────────────────────────────── */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{ flex: 1, overflowY: 'auto', padding: 24 }}>

          {/* Form */}
          <div style={card}>
            <h2 style={{ margin: '0 0 18px', fontSize: 15, fontWeight: 700, color: '#e2e8f0' }}>
              ⚡ {t('trigger.title')}
            </h2>

            <div style={{ marginBottom: 14 }}>
              <label style={labelStyle}>{t('trigger.flowId')}</label>
              <input
                style={inputStyle}
                value={flowInput}
                onChange={e => setFlowInput(e.target.value)}
                placeholder={t('editor.flowIdPlaceholder')}
              />
            </div>

            <div style={{ marginBottom: 18 }}>
              <label style={labelStyle}>{t('trigger.context')}</label>
              <textarea
                style={{ ...inputStyle, height: 120, resize: 'vertical', fontFamily: 'monospace', fontSize: 12 }}
                value={contextJson}
                onChange={e => setContextJson(e.target.value)}
              />
              <div style={{ fontSize: 11, color: '#475569', marginTop: 4 }}>
                {t('editor.contextDescription')}
              </div>
            </div>

            {error && (
              <div style={{ fontSize: 12, color: '#ef4444', marginBottom: 12 }}>⚠ {error}</div>
            )}
            {lastTriggered && !error && (
              <div style={{ fontSize: 12, color: '#22c55e', marginBottom: 12 }}>
                ✓ {t('editor.triggeredAt')} {lastTriggered}
              </div>
            )}

            <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
              <button
                style={{
                  background: '#1e40af', border: 'none', color: '#e2e8f0', borderRadius: 6,
                  padding: '8px 20px', cursor: triggering ? 'default' : 'pointer',
                  fontSize: 13, fontWeight: 600, opacity: triggering ? 0.6 : 1,
                }}
                onClick={handleTrigger}
                disabled={triggering}
              >
                {triggering
                  ? t('editor.triggering')
                  : <><Play className="w-3 h-3 inline-block mr-1" aria-hidden="true" />{t('editor.triggerNow')}</>
                }
              </button>
              <span style={{ fontSize: 11, color: '#475569' }}>
                {t('editor.calendarNote')}
              </span>
            </div>
          </div>

          {/* Recent runs */}
          {(selectedFlow || flowInstances.length > 0) && (
            <div style={{ ...card, marginTop: 16 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
                <h3 style={{ margin: 0, fontSize: 13, fontWeight: 700, color: '#94a3b8' }}>
                  {t('editor.recentInstances')}{selectedFlow ? ` · ${selectedFlow}` : ''}
                </h3>
                {instLoading && <Spinner />}
              </div>

              {flowInstances.length === 0 ? (
                <div style={{ fontSize: 12, color: '#475569', textAlign: 'center', padding: '20px 0' }}>
                  {selectedFlow ? t('editor.noInstancesForFlow') : t('editor.selectFlowInstances')}
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {flowInstances.map(inst => {
                    const color = STATUS_COLORS[inst.status] ?? '#6b7280'
                    return (
                      <div
                        key={inst.id}
                        style={{
                          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                          padding: '8px 12px', background: '#0f172a', borderRadius: 6,
                          border: '1px solid #1e293b',
                        }}
                      >
                        <div>
                          <code style={{ fontSize: 11, color: '#93c5fd' }}>{inst.id.slice(0, 12)}…</code>
                          <div style={{ fontSize: 11, color: '#475569', marginTop: 2 }}>
                            {new Date(inst.created_at).toLocaleString('pt-BR')}
                          </div>
                        </div>
                        <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 8px', borderRadius: 4, background: color + '22', color }}>
                          {STATUS_LABELS[inst.status] ?? inst.status}
                        </span>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Styles ─────────────────────────────────────────────────────────────────────

const page: React.CSSProperties = {
  display: 'flex', height: '100%', backgroundColor: '#0a1628', color: '#e2e8f0', overflow: 'hidden',
}
const leftCol: React.CSSProperties = {
  width: 280, flexShrink: 0, borderRight: '1px solid #1e293b', display: 'flex', flexDirection: 'column', overflow: 'hidden',
}
const colHeader: React.CSSProperties = {
  padding: '12px 16px', borderBottom: '1px solid #1e293b', flexShrink: 0,
  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
}
const card: React.CSSProperties = {
  background: '#1e293b', borderRadius: 8, padding: '20px 24px', border: '1px solid #334155',
}
const labelStyle: React.CSSProperties = {
  display: 'block', fontSize: 12, fontWeight: 600, color: '#94a3b8', marginBottom: 6,
}
const inputStyle: React.CSSProperties = {
  width: '100%', background: '#0f172a', border: '1px solid #334155', borderRadius: 6,
  color: '#e2e8f0', fontSize: 13, padding: '7px 10px', outline: 'none', boxSizing: 'border-box',
}
