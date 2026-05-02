/**
 * AgentFlowDeployPage — /agent-flow/deploy
 *
 * Phase 1 — Deploy management for AgentFlow skills.
 * Phase 2 — Rollback: restore a previous yaml_snapshot and redeploy to same pools.
 *           Scheduled deploy: trigger a workflow that fires at a configured time.
 *           Graceful Handoff Monitor: track sessions still on the old version.
 *
 * Left panel  : skill list with draft/published status
 * Right panel : deployment detail — pools, deploy action, history, scheduled, handoff monitor
 *
 * Backend contract (agent-registry):
 *   GET  /v1/skills?tenant_id=...
 *   GET  /v1/pools?tenant_id=...
 *   PUT  /v1/skills/:id
 *   POST /v1/skills/:id/deploy
 *   GET  /v1/skills/:id/deployments?tenant_id=...
 *   GET  /v1/skills/:id/deployments/scheduled?tenant_id=...
 *   GET  /v1/skills/:id/handoff-status?tenant_id=...
 * Backend contract (workflow-api via /v1/workflow):
 *   POST /v1/workflow/trigger   — schedule a deploy via skill_scheduled_deploy_v1
 *   POST /v1/workflow/instances/:id/cancel — cancel a scheduled deploy
 */
import React, { useCallback, useEffect, useState } from 'react'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'

// ── Types ──────────────────────────────────────────────────────────────────────

interface Skill {
  id:              string
  name?:           string
  version?:        string
  classification?: string
  status?:         'draft' | 'published'
  published_at?:   string
  updated_at?:     string
}

interface Pool {
  pool_id:       string
  description?:  string
  channel_types?: string[]
}

interface Deployment {
  deployment_id:  string
  skill_id:       string
  version:        string
  pool_ids:       string[]
  deployed_by:    string
  deployed_at:    string
  notes?:         string | null
  yaml_snapshot?: unknown  // JSON object — the flow at deploy time
}

interface ScheduledDeploy {
  workflow_instance_id: string
  skill_id:   string
  pool_ids:   string[]
  scheduled_at: string   // ISO-8601 — when the deploy will fire
  deployed_by?: string
  notes?:       string
  status:       string
  created_at:   string
}

interface HandoffStatus {
  skill_id:        string
  deployed:        boolean
  active_sessions: number
  pool_ids:        string[]
  deployed_at:     string | null
  deployment_id?:  string
  deployed_by?:    string
}

// ── API ────────────────────────────────────────────────────────────────────────

function authHeaders(token?: string | null): Record<string, string> {
  const h: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) h['Authorization'] = `Bearer ${token}`
  return h
}

async function fetchSkills(tenantId: string, token?: string | null): Promise<Skill[]> {
  const res = await fetch(`/v1/skills?tenant_id=${tenantId}`, { headers: authHeaders(token) })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const body = await res.json()
  return Array.isArray(body) ? body : (body.data ?? body.skills ?? [])
}

async function fetchPools(tenantId: string, token?: string | null): Promise<Pool[]> {
  const res = await fetch(`/v1/pools?tenant_id=${tenantId}`, { headers: authHeaders(token) })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const body = await res.json()
  return Array.isArray(body) ? body : (body.data ?? body.pools ?? [])
}

async function fetchDeployments(skillId: string, tenantId: string, token?: string | null): Promise<Deployment[]> {
  const res = await fetch(`/v1/skills/${skillId}/deployments?tenant_id=${tenantId}`, {
    headers: authHeaders(token),
  })
  if (!res.ok) {
    if (res.status === 404) return []
    throw new Error(`HTTP ${res.status}`)
  }
  const body = await res.json()
  return Array.isArray(body) ? body : (body.data ?? body.deployments ?? [])
}

async function deploySkill(
  skillId:  string,
  poolIds:  string[],
  tenantId: string,
  token?:   string | null,
  notes?:   string,
): Promise<Deployment> {
  const res = await fetch(`/v1/skills/${skillId}/deploy`, {
    method:  'POST',
    headers: authHeaders(token),
    body:    JSON.stringify({ pool_ids: poolIds, tenant_id: tenantId, notes }),
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`HTTP ${res.status}: ${text}`)
  }
  const body = await res.json()
  return body.deployment ?? body
}

/** Phase 2 — restore the flow from a yaml_snapshot, then redeploy to the same pools. */
async function rollbackSkill(
  skillId:      string,
  dep:          Deployment,
  tenantId:     string,
  token?:       string | null,
): Promise<Deployment> {
  // Step 1 — restore the flow (PUT skill with the snapshot as the new flow)
  const putRes = await fetch(`/v1/skills/${skillId}`, {
    method:  'PUT',
    headers: authHeaders(token),
    body:    JSON.stringify({
      skill_id:  skillId,
      tenant_id: tenantId,
      flow:      dep.yaml_snapshot,
    }),
  })
  if (!putRes.ok) {
    const text = await putRes.text()
    throw new Error(`Restauração falhou HTTP ${putRes.status}: ${text}`)
  }

  // Step 2 — deploy to the same pools with a rollback note
  return deploySkill(
    skillId,
    dep.pool_ids,
    tenantId,
    token,
    `Rollback para deploy ${dep.deployment_id} (${new Date(dep.deployed_at).toLocaleString('pt-BR')})`,
  )
}

async function fetchScheduledDeploys(skillId: string, tenantId: string, token?: string | null): Promise<ScheduledDeploy[]> {
  const res = await fetch(`/v1/skills/${skillId}/deployments/scheduled?tenant_id=${tenantId}`, {
    headers: authHeaders(token),
  })
  if (!res.ok) return []
  const body = await res.json()
  return Array.isArray(body) ? body : (body.scheduled_deploys ?? [])
}

async function fetchHandoffStatus(skillId: string, tenantId: string, token?: string | null): Promise<HandoffStatus | null> {
  const res = await fetch(`/v1/skills/${skillId}/handoff-status?tenant_id=${tenantId}`, {
    headers: authHeaders(token),
  })
  if (!res.ok) return null
  return res.json()
}

/**
 * Schedule a deploy via workflow (skill_scheduled_deploy_v1).
 * The workflow suspends until scheduledAt, then fires the skill_deploy MCP tool.
 */
async function scheduleSkillDeploy(
  skillId:     string,
  poolIds:     string[],
  tenantId:    string,
  scheduledAt: string,     // ISO-8601
  token?:      string | null,
  deployedBy?: string,
  notes?:      string,
): Promise<{ workflow_instance_id: string }> {
  const res = await fetch('/v1/workflow/trigger', {
    method:  'POST',
    headers: authHeaders(token),
    body:    JSON.stringify({
      tenant_id:    tenantId,
      flow_id:      'skill_scheduled_deploy_v1',
      trigger_type: 'scheduled',
      context: {
        skill_id:     skillId,
        pool_ids:     poolIds,
        deploy_at:    scheduledAt,
        deployed_by:  deployedBy ?? 'platform-ui:scheduled',
        deploy_notes: notes ?? `Scheduled deploy for ${skillId}`,
      },
    }),
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`HTTP ${res.status}: ${text}`)
  }
  const body = await res.json()
  return { workflow_instance_id: body.id ?? body.workflow_instance_id }
}

async function cancelScheduledDeploy(
  workflowInstanceId: string,
  tenantId:           string,
  token?:             string | null,
): Promise<void> {
  const res = await fetch(`/v1/workflow/instances/${workflowInstanceId}/cancel`, {
    method:  'POST',
    headers: { ...authHeaders(token), 'x-tenant-id': tenantId },
    body:    JSON.stringify({ reason: 'Cancelled by user via deploy UI' }),
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`HTTP ${res.status}: ${text}`)
  }
}

// ── Status / class helpers ─────────────────────────────────────────────────────

const STATUS_COLOR: Record<string, string> = {
  published: '#22c55e',
  draft:     '#eab308',
}
const STATUS_LABEL: Record<string, string> = {
  published: 'Publicado',
  draft:     'Rascunho',
}
const CLASS_COLOR: Record<string, string> = {
  orchestrator: '#a78bfa',
  vertical:     '#22d3ee',
  horizontal:   '#fbbf24',
}

function fmtDate(iso: string) {
  return new Date(iso).toLocaleString('pt-BR')
}

// ── Rollback Confirm Modal ─────────────────────────────────────────────────────

interface RollbackModalProps {
  dep:        Deployment
  skillId:    string
  rolling:    boolean
  error:      string | null
  onConfirm:  () => void
  onCancel:   () => void
}

function RollbackConfirmModal({ dep, skillId, rolling, error, onConfirm, onCancel }: RollbackModalProps) {
  return (
    <div style={overlay}>
      <div style={modalBox}>
        <div style={{ fontSize: 18, fontWeight: 700, color: '#f8fafc', marginBottom: 4 }}>
          ↩ Confirmar rollback
        </div>
        <div style={{ fontSize: 12, color: '#64748b', marginBottom: 16 }}>
          Esta operação sobrescreve o flow atual e faz um novo deploy.
        </div>

        <div style={infoRow}>
          <span style={infoLabel}>Skill</span>
          <code style={infoVal}>{skillId}</code>
        </div>
        <div style={infoRow}>
          <span style={infoLabel}>Versão</span>
          <span style={infoVal}>v{dep.version}</span>
        </div>
        <div style={infoRow}>
          <span style={infoLabel}>Deploy original</span>
          <span style={infoVal}>{fmtDate(dep.deployed_at)}</span>
        </div>
        <div style={infoRow}>
          <span style={infoLabel}>Deploy ID</span>
          <code style={{ ...infoVal, fontSize: 10 }}>{dep.deployment_id}</code>
        </div>
        <div style={{ ...infoRow, alignItems: 'flex-start' }}>
          <span style={infoLabel}>Pools</span>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
            {dep.pool_ids.map(p => (
              <span key={p} style={poolChip}>{p}</span>
            ))}
          </div>
        </div>

        {!dep.yaml_snapshot && (
          <div style={{ marginTop: 12, padding: '8px 12px', background: '#431407', color: '#fb923c', borderRadius: 4, fontSize: 12 }}>
            ⚠️ Este deploy não possui snapshot do flow — não é possível fazer rollback.
          </div>
        )}

        {error && (
          <div style={{ marginTop: 12, padding: '8px 12px', background: '#7f1d1d', color: '#fca5a5', borderRadius: 4, fontSize: 12 }}>
            {error}
          </div>
        )}

        <div style={{ display: 'flex', gap: 10, marginTop: 20, justifyContent: 'flex-end' }}>
          <button
            onClick={onCancel}
            disabled={rolling}
            style={{ ...btnSecondary, padding: '8px 20px' }}
          >
            Cancelar
          </button>
          <button
            onClick={onConfirm}
            disabled={rolling || !dep.yaml_snapshot}
            style={{
              padding: '8px 20px', borderRadius: 6, fontSize: 13, fontWeight: 600,
              background: rolling || !dep.yaml_snapshot ? '#1e293b' : '#b45309',
              color: rolling || !dep.yaml_snapshot ? '#475569' : '#fff',
              border: 'none',
              cursor: rolling || !dep.yaml_snapshot ? 'not-allowed' : 'pointer',
              transition: 'all .15s',
            }}
          >
            {rolling ? '⟳ Revertendo…' : '↩ Confirmar rollback'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Main page ──────────────────────────────────────────────────────────────────

export default function AgentFlowDeployPage() {
  const { session, getAccessToken, tenantId } = useAuth()

  const [skills,      setSkills]      = useState<Skill[]>([])
  const [pools,       setPools]       = useState<Pool[]>([])
  const [selected,    setSelected]    = useState<Skill | null>(null)
  const [deployments, setDeployments] = useState<Deployment[]>([])
  const [selectedPools, setSelectedPools] = useState<string[]>([])

  const [loading,       setLoading]       = useState(false)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [deploying,     setDeploying]     = useState(false)
  const [error,         setError]         = useState<string | null>(null)
  const [deployError,   setDeployError]   = useState<string | null>(null)
  const [deploySuccess, setDeploySuccess] = useState<string | null>(null)
  const [filter,        setFilter]        = useState('')

  // Rollback state
  const [rollbackTarget, setRollbackTarget] = useState<Deployment | null>(null)
  const [rollingBack,    setRollingBack]    = useState(false)
  const [rollbackError,  setRollbackError]  = useState<string | null>(null)

  // Scheduled deploy state
  const [scheduledDeploys,   setScheduledDeploys]   = useState<ScheduledDeploy[]>([])
  const [scheduleAt,         setScheduleAt]          = useState('')  // datetime-local value
  const [scheduling,         setScheduling]          = useState(false)
  const [scheduleError,      setScheduleError]       = useState<string | null>(null)
  const [scheduleSuccess,    setScheduleSuccess]     = useState<string | null>(null)
  const [cancellingId,       setCancellingId]        = useState<string | null>(null)

  // Handoff monitor state
  const [handoffStatus,    setHandoffStatus]    = useState<HandoffStatus | null>(null)
  const [handoffPolling,   setHandoffPolling]   = useState(false)

  const load = useCallback(async () => {
    if (!tenantId) return
    setLoading(true); setError(null)
    try {
      const token = await getAccessToken()
      const [s, p] = await Promise.all([
        fetchSkills(tenantId, token),
        fetchPools(tenantId, token).catch(() => [] as Pool[]),
      ])
      setSkills(s)
      setPools(p)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [tenantId])

  useEffect(() => { load() }, [load])

  // Poll handoff status every 10 s when a published skill is selected
  useEffect(() => {
    if (!selected || !handoffStatus?.deployed) return
    setHandoffPolling(true)
    const id = setInterval(async () => {
      try {
        const token = await getAccessToken()
        const fresh = await fetchHandoffStatus(selected.id, tenantId, token)
        setHandoffStatus(fresh)
      } catch { /* silent — stale data is fine */ }
    }, 10_000)
    return () => { clearInterval(id); setHandoffPolling(false) }
  }, [selected?.id, handoffStatus?.deployed, tenantId])

  const selectSkill = useCallback(async (skill: Skill) => {
    setSelected(skill)
    setSelectedPools([])
    setDeployError(null)
    setDeploySuccess(null)
    setRollbackError(null)
    setScheduleError(null)
    setScheduleSuccess(null)
    setHandoffStatus(null)
    setHandoffPolling(false)
    setLoadingDetail(true)
    try {
      const token = await getAccessToken()
      const [deps, scheduled, handoff] = await Promise.all([
        fetchDeployments(skill.id, tenantId, token),
        fetchScheduledDeploys(skill.id, tenantId, token).catch(() => [] as ScheduledDeploy[]),
        fetchHandoffStatus(skill.id, tenantId, token).catch(() => null),
      ])
      setDeployments(deps)
      setScheduledDeploys(scheduled)
      setHandoffStatus(handoff)
    } catch {
      setDeployments([])
    } finally {
      setLoadingDetail(false)
    }
  }, [tenantId])

  const handleDeploy = async () => {
    if (!selected || selectedPools.length === 0) return
    setDeploying(true); setDeployError(null); setDeploySuccess(null)
    try {
      const token = await getAccessToken()
      const dep = await deploySkill(selected.id, selectedPools, tenantId, token)
      setDeploySuccess(`Deploy realizado com sucesso (ID: ${dep.deployment_id})`)
      const [updatedSkills, updatedDeps] = await Promise.all([
        fetchSkills(tenantId, token),
        fetchDeployments(selected.id, tenantId, token).catch(() => []),
      ])
      setSkills(updatedSkills)
      setDeployments(updatedDeps)
      const freshSkill = updatedSkills.find(s => s.id === selected.id)
      if (freshSkill) setSelected(freshSkill)
    } catch (e) {
      setDeployError(e instanceof Error ? e.message : String(e))
    } finally {
      setDeploying(false)
    }
  }

  const handleRollbackConfirm = async () => {
    if (!rollbackTarget || !selected) return
    setRollingBack(true); setRollbackError(null)
    try {
      const token = await getAccessToken()
      const dep = await rollbackSkill(selected.id, rollbackTarget, tenantId, token)
      setRollbackTarget(null)
      setDeploySuccess(`Rollback realizado com sucesso (novo deploy: ${dep.deployment_id})`)
      const [updatedSkills, updatedDeps] = await Promise.all([
        fetchSkills(tenantId, token),
        fetchDeployments(selected.id, tenantId, token).catch(() => []),
      ])
      setSkills(updatedSkills)
      setDeployments(updatedDeps)
      const freshSkill = updatedSkills.find(s => s.id === selected.id)
      if (freshSkill) setSelected(freshSkill)
    } catch (e) {
      setRollbackError(e instanceof Error ? e.message : String(e))
    } finally {
      setRollingBack(false)
    }
  }

  const handleScheduleDeploy = async () => {
    if (!selected || selectedPools.length === 0 || !scheduleAt) return
    setScheduling(true); setScheduleError(null); setScheduleSuccess(null)
    try {
      const token = await getAccessToken()
      const scheduledAtIso = new Date(scheduleAt).toISOString()
      const result = await scheduleSkillDeploy(
        selected.id,
        selectedPools,
        tenantId,
        scheduledAtIso,
        token,
        session?.name ?? 'platform-ui:scheduled',
      )
      setScheduleSuccess(`Deploy agendado com sucesso (workflow: ${result.workflow_instance_id})`)
      setScheduleAt('')
      const freshScheduled = await fetchScheduledDeploys(selected.id, tenantId, token).catch(
        () => [] as ScheduledDeploy[],
      )
      setScheduledDeploys(freshScheduled)
    } catch (e) {
      setScheduleError(e instanceof Error ? e.message : String(e))
    } finally {
      setScheduling(false)
    }
  }

  const handleCancelScheduled = async (workflowInstanceId: string) => {
    if (!selected) return
    setCancellingId(workflowInstanceId)
    setScheduleError(null)
    try {
      const token = await getAccessToken()
      await cancelScheduledDeploy(workflowInstanceId, tenantId, token)
      const freshScheduled = await fetchScheduledDeploys(selected.id, tenantId, token).catch(
        () => [] as ScheduledDeploy[],
      )
      setScheduledDeploys(freshScheduled)
    } catch (e) {
      setScheduleError(e instanceof Error ? e.message : String(e))
    } finally {
      setCancellingId(null)
    }
  }

  const togglePool = (poolId: string) => {
    setSelectedPools(prev =>
      prev.includes(poolId) ? prev.filter(p => p !== poolId) : [...prev, poolId]
    )
  }

  const filtered = skills.filter(s =>
    !filter ||
    s.id.toLowerCase().includes(filter.toLowerCase()) ||
    (s.name ?? '').toLowerCase().includes(filter.toLowerCase())
  )

  return (
    <div style={page}>
      {/* Rollback confirm modal */}
      {rollbackTarget && (
        <RollbackConfirmModal
          dep={rollbackTarget}
          skillId={selected?.id ?? ''}
          rolling={rollingBack}
          error={rollbackError}
          onConfirm={handleRollbackConfirm}
          onCancel={() => { setRollbackTarget(null); setRollbackError(null) }}
        />
      )}

      {/* Top bar */}
      <div style={topBar}>
        <div>
          <span style={{ fontWeight: 700, fontSize: 17, color: '#e2e8f0' }}>🚀 Deploy de AgentFlow</span>
          <span style={{ marginLeft: 10, fontSize: 12, color: '#64748b' }}>
            {loading ? '⟳' : `${skills.length} skill(s)`}
          </span>
        </div>
        <button style={btnSecondary} onClick={load} disabled={loading}>↻ Atualizar</button>
      </div>

      {error && (
        <div style={{ padding: '10px 20px', background: '#7f1d1d', color: '#fca5a5', fontSize: 12 }}>
          Erro ao carregar: {error}
        </div>
      )}

      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* ─── Left: skill list ────────────────────────────────────── */}
        <div style={leftCol}>
          <div style={colHeader}>
            <input
              value={filter}
              onChange={e => setFilter(e.target.value)}
              placeholder="Buscar skill…"
              style={searchInput}
            />
            {loading && <Spinner />}
          </div>

          <div style={{ flex: 1, overflowY: 'auto' }}>
            {filtered.length === 0 && !loading && (
              <div style={{ padding: '40px 16px', textAlign: 'center', color: '#475569', fontSize: 13 }}>
                Nenhum skill encontrado
              </div>
            )}
            {filtered.map(skill => {
              const active      = selected?.id === skill.id
              const status      = skill.status ?? 'draft'
              const statusColor = STATUS_COLOR[status] ?? '#6b7280'
              const classColor  = CLASS_COLOR[skill.classification ?? ''] ?? '#94a3b8'
              return (
                <button
                  key={skill.id}
                  onClick={() => selectSkill(skill)}
                  style={{
                    width: '100%', textAlign: 'left', padding: '12px 16px',
                    borderBottom: '1px solid #1e293b', cursor: 'pointer',
                    background:  active ? '#1e3a5f' : 'transparent',
                    borderLeft:  active ? '3px solid #3b82f6' : '3px solid transparent',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <code style={{ fontSize: 11, color: active ? '#93c5fd' : '#e2e8f0', fontWeight: 600 }}>
                      {skill.id}
                    </code>
                    <span style={{ fontSize: 10, fontWeight: 700, padding: '1px 6px', borderRadius: 3, background: statusColor + '22', color: statusColor }}>
                      {STATUS_LABEL[status] ?? status}
                    </span>
                  </div>
                  <div style={{ display: 'flex', gap: 6, marginTop: 4 }}>
                    {skill.classification && (
                      <span style={{ fontSize: 10, padding: '1px 5px', borderRadius: 3, background: classColor + '22', color: classColor }}>
                        {skill.classification}
                      </span>
                    )}
                    {skill.version && (
                      <span style={{ fontSize: 10, color: '#475569' }}>v{skill.version}</span>
                    )}
                  </div>
                  {skill.published_at && (
                    <div style={{ fontSize: 10, color: '#475569', marginTop: 3 }}>
                      Publicado: {fmtDate(skill.published_at)}
                    </div>
                  )}
                </button>
              )
            })}
          </div>
        </div>

        {/* ─── Right: deploy panel ─────────────────────────────────── */}
        {selected ? (
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
            {/* Header */}
            <div style={{ padding: '16px 20px', borderBottom: '1px solid #1e293b', flexShrink: 0 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <div>
                  <code style={{ fontSize: 13, color: '#93c5fd', fontWeight: 600 }}>{selected.id}</code>
                  {selected.name && (
                    <div style={{ fontSize: 12, color: '#64748b', marginTop: 2 }}>{selected.name}</div>
                  )}
                </div>
                <span style={{ fontSize: 11, fontWeight: 700, padding: '3px 10px', borderRadius: 4,
                  background: (STATUS_COLOR[selected.status ?? 'draft'] ?? '#6b7280') + '22',
                  color: STATUS_COLOR[selected.status ?? 'draft'] ?? '#6b7280',
                }}>
                  {STATUS_LABEL[selected.status ?? 'draft'] ?? selected.status}
                </span>
              </div>
            </div>

            <div style={{ flex: 1, overflowY: 'auto', padding: '20px' }}>
              {/* Pool selection */}
              <Section label="Selecionar pools para deploy">
                {pools.length === 0 && (
                  <div style={{ fontSize: 12, color: '#475569' }}>Nenhum pool disponível</div>
                )}
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 8, marginTop: 8 }}>
                  {pools.map(pool => {
                    const checked = selectedPools.includes(pool.pool_id)
                    return (
                      <label
                        key={pool.pool_id}
                        style={{
                          display: 'flex', alignItems: 'flex-start', gap: 10, padding: '10px 12px',
                          background: checked ? '#1e3a5f' : '#1e293b',
                          border: `1px solid ${checked ? '#3b82f6' : '#334155'}`,
                          borderRadius: 6, cursor: 'pointer',
                        }}
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => togglePool(pool.pool_id)}
                          style={{ marginTop: 2, accentColor: '#3b82f6' }}
                        />
                        <div>
                          <div style={{ fontSize: 12, fontWeight: 600, color: checked ? '#93c5fd' : '#e2e8f0' }}>
                            {pool.pool_id}
                          </div>
                          {pool.description && (
                            <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>{pool.description}</div>
                          )}
                          {pool.channel_types && pool.channel_types.length > 0 && (
                            <div style={{ fontSize: 10, color: '#475569', marginTop: 3 }}>
                              {pool.channel_types.join(' · ')}
                            </div>
                          )}
                        </div>
                      </label>
                    )
                  })}
                </div>
              </Section>

              {/* Deploy action */}
              <Section label="Executar deploy">
                <div style={{ marginBottom: 12 }}>
                  {selectedPools.length === 0 ? (
                    <div style={{ fontSize: 12, color: '#64748b' }}>
                      Selecione ao menos um pool para ativar o deploy.
                    </div>
                  ) : (
                    <div style={{ fontSize: 12, color: '#94a3b8' }}>
                      Pools selecionados:{' '}
                      {selectedPools.map(p => (
                        <span key={p} style={poolChip}>{p}</span>
                      ))}
                    </div>
                  )}
                </div>

                {deployError && (
                  <div style={{ padding: '8px 12px', background: '#7f1d1d', color: '#fca5a5', borderRadius: 4, fontSize: 12, marginBottom: 10 }}>
                    {deployError}
                  </div>
                )}
                {deploySuccess && (
                  <div style={{ padding: '8px 12px', background: '#052e16', color: '#4ade80', borderRadius: 4, fontSize: 12, marginBottom: 10 }}>
                    ✓ {deploySuccess}
                  </div>
                )}

                <button
                  onClick={handleDeploy}
                  disabled={deploying || selectedPools.length === 0}
                  style={{
                    padding: '10px 24px', borderRadius: 6, fontSize: 14, fontWeight: 600,
                    background: selectedPools.length > 0 && !deploying ? '#2563eb' : '#1e293b',
                    color: selectedPools.length > 0 && !deploying ? '#fff' : '#475569',
                    border: 'none', cursor: selectedPools.length > 0 && !deploying ? 'pointer' : 'not-allowed',
                    transition: 'all .15s',
                  }}
                >
                  {deploying ? '⟳ Publicando…' : '🚀 Publicar agora'}
                </button>

                <div style={{ fontSize: 11, color: '#475569', marginTop: 8 }}>
                  O skill será publicado nos pools selecionados. Sessões em andamento continuarão com a versão anterior até o encerramento.
                </div>
              </Section>

              {/* Deployment history */}
              <Section label="Histórico de deploys">
                {loadingDetail && (
                  <div style={{ display: 'flex', justifyContent: 'center', padding: 20 }}>
                    <Spinner />
                  </div>
                )}
                {!loadingDetail && deployments.length === 0 && (
                  <div style={{ fontSize: 12, color: '#475569' }}>
                    Nenhum deploy registrado para este skill.
                  </div>
                )}

                {deployments.map((dep, idx) => {
                  const isLatest      = idx === 0
                  const hasSnapshot   = Boolean(dep.yaml_snapshot)
                  const canRollback   = !isLatest && hasSnapshot
                  const isRollbackRow = dep.notes?.startsWith('Rollback para deploy')

                  return (
                    <div key={dep.deployment_id} style={historyRow}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
                        <div style={{ minWidth: 0, flex: 1 }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                            <code style={{ fontSize: 11, color: '#93c5fd' }}>{dep.deployment_id}</code>
                            {isLatest && (
                              <span style={{ fontSize: 9, fontWeight: 700, padding: '1px 5px', borderRadius: 3, background: '#14532d', color: '#4ade80', textTransform: 'uppercase' }}>
                                atual
                              </span>
                            )}
                            {isRollbackRow && (
                              <span style={{ fontSize: 9, fontWeight: 700, padding: '1px 5px', borderRadius: 3, background: '#451a03', color: '#fb923c', textTransform: 'uppercase' }}>
                                rollback
                              </span>
                            )}
                          </div>
                          <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>
                            v{dep.version} · por {dep.deployed_by}
                          </div>
                          {dep.notes && (
                            <div style={{ fontSize: 10, color: '#475569', marginTop: 2, fontStyle: 'italic' }}>
                              {dep.notes}
                            </div>
                          )}
                        </div>

                        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6, flexShrink: 0 }}>
                          <span style={{ fontSize: 11, color: '#64748b', whiteSpace: 'nowrap' }}>
                            {fmtDate(dep.deployed_at)}
                          </span>
                          {/* Rollback button — only for non-latest entries with a snapshot */}
                          {!isLatest && (
                            <button
                              onClick={() => { setRollbackTarget(dep); setRollbackError(null); setDeploySuccess(null) }}
                              disabled={!canRollback}
                              title={
                                !hasSnapshot
                                  ? 'Snapshot indisponível para este deploy'
                                  : 'Restaurar este flow e fazer novo deploy nos mesmos pools'
                              }
                              style={{
                                fontSize: 11, fontWeight: 600, padding: '3px 10px', borderRadius: 4,
                                background:  canRollback ? '#431407' : '#1e293b',
                                color:       canRollback ? '#fb923c' : '#475569',
                                border:     `1px solid ${canRollback ? '#92400e' : '#334155'}`,
                                cursor:      canRollback ? 'pointer' : 'not-allowed',
                                transition:  'all .15s',
                                whiteSpace:  'nowrap',
                              }}
                            >
                              ↩ Rollback
                            </button>
                          )}
                        </div>
                      </div>

                      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 6 }}>
                        {dep.pool_ids.map(p => (
                          <span key={p} style={poolChip}>{p}</span>
                        ))}
                      </div>
                    </div>
                  )
                })}

                {deployments.length > 0 && (
                  <div style={{ fontSize: 11, color: '#334155', marginTop: 8 }}>
                    O rollback restaura o flow daquele deploy e publica nos mesmos pools.
                    Sessões em andamento continuam na versão anterior até o encerramento.
                  </div>
                )}
              </Section>

              {/* ── Scheduled deploy ──────────────────────────────────── */}
              <Section label="Agendar deploy">
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  <div style={{ fontSize: 12, color: '#94a3b8' }}>
                    Agende o deploy para ser executado automaticamente em um horário futuro.
                    Os pools selecionados na seção acima serão utilizados.
                  </div>

                  {selectedPools.length === 0 && (
                    <div style={{ fontSize: 12, color: '#64748b', padding: '8px 12px', background: '#1e293b', borderRadius: 4 }}>
                      ⚠️ Selecione ao menos um pool acima antes de agendar.
                    </div>
                  )}

                  <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                    <input
                      type="datetime-local"
                      value={scheduleAt}
                      onChange={e => setScheduleAt(e.target.value)}
                      min={new Date(Date.now() + 60_000).toISOString().slice(0, 16)}
                      style={{
                        flex: 1, minWidth: 180, background: '#0f1f35',
                        border: '1px solid #334155', borderRadius: 5,
                        padding: '7px 10px', color: '#e2e8f0', fontSize: 12,
                      }}
                    />
                    <button
                      onClick={handleScheduleDeploy}
                      disabled={scheduling || selectedPools.length === 0 || !scheduleAt}
                      style={{
                        padding: '8px 18px', borderRadius: 6, fontSize: 13, fontWeight: 600,
                        background: (!scheduling && selectedPools.length > 0 && scheduleAt) ? '#7c3aed' : '#1e293b',
                        color:      (!scheduling && selectedPools.length > 0 && scheduleAt) ? '#fff' : '#475569',
                        border: 'none',
                        cursor: (!scheduling && selectedPools.length > 0 && scheduleAt) ? 'pointer' : 'not-allowed',
                        whiteSpace: 'nowrap',
                        transition: 'all .15s',
                      }}
                    >
                      {scheduling ? '⟳ Agendando…' : '⏰ Agendar'}
                    </button>
                  </div>

                  {scheduleError && (
                    <div style={{ padding: '8px 12px', background: '#7f1d1d', color: '#fca5a5', borderRadius: 4, fontSize: 12 }}>
                      {scheduleError}
                    </div>
                  )}
                  {scheduleSuccess && (
                    <div style={{ padding: '8px 12px', background: '#052e16', color: '#4ade80', borderRadius: 4, fontSize: 12 }}>
                      ✓ {scheduleSuccess}
                    </div>
                  )}

                  {/* Pending list */}
                  {scheduledDeploys.length > 0 && (
                    <div style={{ marginTop: 4 }}>
                      <div style={{ fontSize: 11, fontWeight: 600, color: '#64748b', marginBottom: 6 }}>
                        Deploys agendados pendentes ({scheduledDeploys.filter(s => s.status === 'active' || s.status === 'suspended').length}):
                      </div>
                      {scheduledDeploys
                        .filter(sd => sd.status === 'active' || sd.status === 'suspended')
                        .map(sd => (
                          <div
                            key={sd.workflow_instance_id}
                            style={{
                              background: '#1e293b', border: '1px solid #334155', borderRadius: 6,
                              padding: '10px 14px', marginBottom: 6,
                              display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 10,
                            }}
                          >
                            <div style={{ minWidth: 0, flex: 1 }}>
                              <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 4 }}>
                                {(sd.pool_ids ?? []).map(p => (
                                  <span key={p} style={poolChip}>{p}</span>
                                ))}
                              </div>
                              <div style={{ fontSize: 11, color: '#94a3b8' }}>
                                ⏰ {sd.scheduled_at ? fmtDate(sd.scheduled_at) : '–'}
                              </div>
                              {sd.deployed_by && (
                                <div style={{ fontSize: 10, color: '#475569', marginTop: 2 }}>
                                  por {sd.deployed_by}
                                </div>
                              )}
                            </div>
                            <button
                              onClick={() => handleCancelScheduled(sd.workflow_instance_id)}
                              disabled={cancellingId === sd.workflow_instance_id}
                              style={{
                                fontSize: 11, padding: '4px 10px', borderRadius: 4,
                                background: '#7f1d1d', color: '#fca5a5',
                                border: '1px solid #991b1b', cursor: 'pointer',
                                whiteSpace: 'nowrap', flexShrink: 0,
                              }}
                            >
                              {cancellingId === sd.workflow_instance_id ? '⟳' : '✕ Cancelar'}
                            </button>
                          </div>
                        ))}
                    </div>
                  )}
                </div>
              </Section>

              {/* ── Handoff monitor ───────────────────────────────────── */}
              <Section label={`Monitor de handoff${handoffPolling ? ' ⟳' : ''}`}>
                {!handoffStatus && (
                  <div style={{ fontSize: 12, color: '#475569' }}>
                    Nenhum deploy ativo detectado para este skill.
                  </div>
                )}
                {handoffStatus && !handoffStatus.deployed && (
                  <div style={{ fontSize: 12, color: '#64748b', padding: '10px 12px', background: '#1e293b', borderRadius: 6 }}>
                    Este skill ainda não foi publicado em nenhum pool.
                  </div>
                )}
                {handoffStatus && handoffStatus.deployed && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                    {/* KPI row */}
                    <div style={{ display: 'flex', gap: 10 }}>
                      <div style={metricCard}>
                        <div style={{
                          fontSize: 28, fontWeight: 700,
                          color: handoffStatus.active_sessions === 0 ? '#4ade80' : '#fbbf24',
                          lineHeight: 1,
                        }}>
                          {handoffStatus.active_sessions}
                        </div>
                        <div style={{ fontSize: 11, color: '#64748b', marginTop: 4 }}>
                          Sessões na versão anterior
                        </div>
                      </div>
                      <div style={metricCard}>
                        <div style={{ fontSize: 12, fontWeight: 600, color: '#93c5fd' }}>
                          {handoffStatus.deployed_at ? fmtDate(handoffStatus.deployed_at) : '–'}
                        </div>
                        <div style={{ fontSize: 11, color: '#64748b', marginTop: 4 }}>Último deploy</div>
                        {handoffStatus.deployed_by && (
                          <div style={{ fontSize: 10, color: '#475569', marginTop: 2 }}>
                            por {handoffStatus.deployed_by}
                          </div>
                        )}
                      </div>
                    </div>

                    {/* Convergence indicator */}
                    <div style={{ background: '#1e293b', borderRadius: 6, padding: '12px 14px' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                        <span style={{ fontSize: 12, color: '#94a3b8', fontWeight: 600 }}>Convergência</span>
                        {handoffStatus.active_sessions === 0 ? (
                          <span style={{ fontSize: 12, fontWeight: 700, color: '#4ade80' }}>✓ Completa</span>
                        ) : (
                          <span style={{ fontSize: 12, color: '#fbbf24' }}>
                            {handoffStatus.active_sessions} sessão(ões) migrando…
                          </span>
                        )}
                      </div>
                      <div style={{ height: 8, background: '#0f172a', borderRadius: 4, overflow: 'hidden' }}>
                        <div style={{
                          height: '100%',
                          width: handoffStatus.active_sessions === 0 ? '100%' : '15%',
                          background: handoffStatus.active_sessions === 0 ? '#22c55e' : '#fbbf24',
                          borderRadius: 4,
                          transition: 'width 1.2s ease, background 1.2s ease',
                        }} />
                      </div>
                      <div style={{ fontSize: 10, color: '#475569', marginTop: 8 }}>
                        Atualizado automaticamente a cada 10 s.
                        Sessões em andamento migram para a nova versão ao encerrar.
                      </div>
                    </div>

                    {/* Affected pools */}
                    <div>
                      <div style={{ fontSize: 11, color: '#64748b', marginBottom: 4 }}>Pools afetados:</div>
                      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                        {handoffStatus.pool_ids.map(p => (
                          <span key={p} style={poolChip}>{p}</span>
                        ))}
                      </div>
                    </div>
                  </div>
                )}
              </Section>
            </div>
          </div>
        ) : (
          <div style={emptyDetail}>
            <div style={{ fontSize: 40, marginBottom: 12 }}>🚀</div>
            <div style={{ fontSize: 14, color: '#475569', textAlign: 'center', maxWidth: 280 }}>
              Selecione um skill para configurar e executar o deploy nos pools desejados
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 24 }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 10 }}>
        {label}
      </div>
      {children}
    </div>
  )
}

// ── Styles ─────────────────────────────────────────────────────────────────────

const page: React.CSSProperties        = { display: 'flex', flexDirection: 'column', height: '100%', backgroundColor: '#0a1628', color: '#e2e8f0', overflow: 'hidden' }
const topBar: React.CSSProperties      = { display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 20px', borderBottom: '1px solid #1e293b', flexShrink: 0 }
const leftCol: React.CSSProperties     = { width: 300, flexShrink: 0, borderRight: '1px solid #1e293b', display: 'flex', flexDirection: 'column', overflow: 'hidden' }
const colHeader: React.CSSProperties   = { padding: '10px 16px', borderBottom: '1px solid #1e293b', flexShrink: 0, display: 'flex', alignItems: 'center', gap: 8 }
const searchInput: React.CSSProperties = { flex: 1, background: '#0f1f35', border: '1px solid #334155', borderRadius: 5, padding: '5px 10px', color: '#e2e8f0', fontSize: 12 }
const btnSecondary: React.CSSProperties = { background: 'none', border: '1px solid #334155', color: '#94a3b8', borderRadius: 6, padding: '5px 12px', cursor: 'pointer', fontSize: 12 }
const emptyDetail: React.CSSProperties = { flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' }
const historyRow: React.CSSProperties  = { background: '#1e293b', border: '1px solid #334155', borderRadius: 6, padding: '10px 14px', marginBottom: 8 }
const poolChip: React.CSSProperties    = { fontSize: 10, background: '#172554', color: '#93c5fd', padding: '1px 6px', borderRadius: 3 }
const overlay: React.CSSProperties     = { position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }
const modalBox: React.CSSProperties    = { background: '#0f172a', border: '1px solid #334155', borderRadius: 10, padding: '28px 32px', width: 480, maxWidth: '90vw' }
const infoRow: React.CSSProperties     = { display: 'flex', alignItems: 'center', gap: 12, padding: '6px 0', borderBottom: '1px solid #1e293b' }
const infoLabel: React.CSSProperties   = { fontSize: 11, color: '#475569', width: 110, flexShrink: 0 }
const infoVal: React.CSSProperties     = { fontSize: 12, color: '#e2e8f0' }
const metricCard: React.CSSProperties  = { flex: 1, background: '#1e293b', border: '1px solid #334155', borderRadius: 6, padding: '12px 14px' }
