/**
 * AgentFlowDeployPage — /agent-flow/deploy
 *
 * Phase 1 — Deploy management for AgentFlow skills.
 *
 * Left panel  : skill list with draft/published status
 * Right panel : deployment detail — pools, deploy action, deployment history
 *
 * Backend contract (agent-registry):
 *   GET  /v1/skills?tenant_id=...
 *     → Skill[]  (adds `status: "draft"|"published"`, `published_at?`)
 *   GET  /v1/pools?tenant_id=...
 *     → Pool[]
 *   POST /v1/skills/:id/deploy
 *     body: { pool_ids: string[], tenant_id: string }
 *     → { deployment_id, skill_id, version, pool_ids, deployed_at }
 *   GET  /v1/skills/:id/deployments?tenant_id=...
 *     → Deployment[]
 */
import React, { useCallback, useEffect, useState } from 'react'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'

// ── Types ──────────────────────────────────────────────────────────────────────

interface Skill {
  id:           string
  name?:        string
  version?:     string
  classification?: string
  status?:      'draft' | 'published'
  published_at?: string
  updated_at?:  string
}

interface Pool {
  pool_id:     string
  description?: string
  channel_types?: string[]
}

interface Deployment {
  deployment_id: string
  skill_id:      string
  version:       string
  pool_ids:      string[]
  deployed_by:   string
  deployed_at:   string
  yaml_snapshot?: string
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
    // endpoint may not exist yet in Phase 1 — return empty
    if (res.status === 404) return []
    throw new Error(`HTTP ${res.status}`)
  }
  const body = await res.json()
  return Array.isArray(body) ? body : (body.data ?? body.deployments ?? [])
}

async function deploySkill(
  skillId: string,
  poolIds: string[],
  tenantId: string,
  token?: string | null,
): Promise<Deployment> {
  const res = await fetch(`/v1/skills/${skillId}/deploy`, {
    method: 'POST',
    headers: authHeaders(token),
    body: JSON.stringify({ pool_ids: poolIds, tenant_id: tenantId }),
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`HTTP ${res.status}: ${text}`)
  }
  return res.json()
}

// ── Status helpers ─────────────────────────────────────────────────────────────

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

// ── Main page ──────────────────────────────────────────────────────────────────

export default function AgentFlowDeployPage() {
  const { session, getAccessToken } = useAuth()
  const tenantId = session?.tenantId ?? ''

  const [skills,      setSkills]      = useState<Skill[]>([])
  const [pools,       setPools]       = useState<Pool[]>([])
  const [selected,    setSelected]    = useState<Skill | null>(null)
  const [deployments, setDeployments] = useState<Deployment[]>([])
  const [selectedPools, setSelectedPools] = useState<string[]>([])

  const [loading,         setLoading]         = useState(false)
  const [loadingDetail,   setLoadingDetail]   = useState(false)
  const [deploying,       setDeploying]       = useState(false)
  const [error,           setError]           = useState<string | null>(null)
  const [deployError,     setDeployError]     = useState<string | null>(null)
  const [deploySuccess,   setDeploySuccess]   = useState<string | null>(null)
  const [filter,          setFilter]          = useState('')

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

  const selectSkill = useCallback(async (skill: Skill) => {
    setSelected(skill)
    setSelectedPools([])
    setDeployError(null)
    setDeploySuccess(null)
    setLoadingDetail(true)
    try {
      const token = await getAccessToken()
      const deps = await fetchDeployments(skill.id, tenantId, token)
      setDeployments(deps)
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
      // Refresh skill list to pick up new status + history
      const [updatedSkills, updatedDeps] = await Promise.all([
        fetchSkills(tenantId, token),
        fetchDeployments(selected.id, tenantId, token).catch(() => []),
      ])
      setSkills(updatedSkills)
      setDeployments(updatedDeps)
      // Update selected skill's status
      const freshSkill = updatedSkills.find(s => s.id === selected.id)
      if (freshSkill) setSelected(freshSkill)
    } catch (e) {
      setDeployError(e instanceof Error ? e.message : String(e))
    } finally {
      setDeploying(false)
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
              const active = selected?.id === skill.id
              const status = skill.status ?? 'draft'
              const statusColor = STATUS_COLOR[status] ?? '#6b7280'
              const classColor  = CLASS_COLOR[skill.classification ?? ''] ?? '#94a3b8'
              return (
                <button
                  key={skill.id}
                  onClick={() => selectSkill(skill)}
                  style={{
                    width: '100%', textAlign: 'left', padding: '12px 16px',
                    borderBottom: '1px solid #1e293b', cursor: 'pointer',
                    background: active ? '#1e3a5f' : 'transparent',
                    borderLeft: active ? '3px solid #3b82f6' : '3px solid transparent',
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
                      Publicado: {new Date(skill.published_at).toLocaleString('pt-BR')}
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
                        <span key={p} style={{ background: '#172554', color: '#93c5fd', padding: '1px 6px', borderRadius: 3, marginLeft: 4, fontSize: 11 }}>
                          {p}
                        </span>
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
                {loadingDetail && <div style={{ display: 'flex', justifyContent: 'center', padding: 20 }}><Spinner /></div>}
                {!loadingDetail && deployments.length === 0 && (
                  <div style={{ fontSize: 12, color: '#475569' }}>
                    Nenhum deploy registrado para este skill.
                  </div>
                )}
                {deployments.map(dep => (
                  <div key={dep.deployment_id} style={historyRow}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                      <div>
                        <code style={{ fontSize: 11, color: '#93c5fd' }}>{dep.deployment_id}</code>
                        <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>
                          v{dep.version} · por {dep.deployed_by}
                        </div>
                      </div>
                      <span style={{ fontSize: 11, color: '#64748b' }}>
                        {new Date(dep.deployed_at).toLocaleString('pt-BR')}
                      </span>
                    </div>
                    <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 6 }}>
                      {dep.pool_ids.map(p => (
                        <span key={p} style={{ fontSize: 10, background: '#172554', color: '#93c5fd', padding: '1px 6px', borderRadius: 3 }}>
                          {p}
                        </span>
                      ))}
                    </div>
                  </div>
                ))}
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
