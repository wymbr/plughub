/**
 * AgentFlowMonitorPage — /agent-flow/monitor
 *
 * Running agent instances grouped by skill.
 * Left: skill list with running instance count.
 * Right: instances for selected skill with status, pool, channel_types.
 */
import React, { useCallback, useEffect, useState } from 'react'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'
import type { AgentInstance } from '@/types'

// ── API ────────────────────────────────────────────────────────────────────────

function authHeaders(token?: string | null) {
  const h: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) h['Authorization'] = `Bearer ${token}`
  return h
}

async function fetchInstances(tenantId: string, token?: string | null): Promise<AgentInstance[]> {
  const res = await fetch(`/v1/instances?tenant_id=${tenantId}`, {
    headers: authHeaders(token),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const body = await res.json()
  return Array.isArray(body) ? body : (body.data ?? body.instances ?? [])
}

// ── Types ──────────────────────────────────────────────────────────────────────

interface SkillGroup {
  skillId:   string
  instances: AgentInstance[]
  ready:     number
  busy:      number
  paused:    number
}

// ── Status config ──────────────────────────────────────────────────────────────

const STATUS_COLOR: Record<string, string> = {
  ready:    '#22c55e',
  busy:     '#3b82f6',
  paused:   '#eab308',
  draining: '#f97316',
  offline:  '#6b7280',
}

const STATUS_LABEL: Record<string, string> = {
  ready:    'Pronto',
  busy:     'Em sessão',
  paused:   'Pausado',
  draining: 'Drenando',
  offline:  'Offline',
}

// ── Main page ──────────────────────────────────────────────────────────────────

export default function AgentFlowMonitorPage() {
  const { session, getAccessToken } = useAuth()
  const tenantId = session?.tenantId ?? ''

  const [instances,  setInstances]  = useState<AgentInstance[]>([])
  const [loading,    setLoading]    = useState(false)
  const [error,      setError]      = useState<string | null>(null)
  const [selectedSkill, setSelectedSkill] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!tenantId) return
    setLoading(true); setError(null)
    try {
      const token = await getAccessToken()
      const list  = await fetchInstances(tenantId, token)
      setInstances(list)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [tenantId])

  useEffect(() => {
    load()
    const id = setInterval(load, 15_000)
    return () => clearInterval(id)
  }, [load])

  // Group instances by skill_id from their agent_type
  const groups: SkillGroup[] = Object.values(
    instances.reduce<Record<string, SkillGroup>>((acc, inst) => {
      const skills: string[] =
        (inst as any).agent_type?.skills?.map((s: any) => s.skill_id ?? s) ?? []
      const keys = skills.length > 0 ? skills : ['(sem skill)']

      for (const sk of keys) {
        if (!acc[sk]) acc[sk] = { skillId: sk, instances: [], ready: 0, busy: 0, paused: 0 }
        acc[sk].instances.push(inst)
        if (inst.status === 'ready')  acc[sk].ready++
        if (inst.status === 'busy')   acc[sk].busy++
        if (inst.status === 'paused') acc[sk].paused++
      }
      return acc
    }, {}),
  ).sort((a, b) => b.instances.length - a.instances.length)

  const selectedGroup = groups.find(g => g.skillId === selectedSkill)
  const displayInstances = selectedGroup?.instances ?? instances

  return (
    <div style={page}>
      {/* Top bar */}
      <div style={topBar}>
        <div>
          <span style={{ fontWeight: 700, fontSize: 17, color: '#e2e8f0' }}>📡 Monitor de AgentFlow</span>
          <span style={{ marginLeft: 10, fontSize: 12, color: '#64748b' }}>
            {loading ? '⟳' : `${instances.length} instância(s)`}
          </span>
        </div>
        <button style={btnSecondary} onClick={load}>↻ Atualizar</button>
      </div>

      {error && (
        <div style={{ padding: '10px 20px', background: '#7f1d1d', color: '#fca5a5', fontSize: 12 }}>
          Erro: {error}
        </div>
      )}

      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* ─── Left: skill groups ──────────────────────────────────────── */}
        <div style={leftCol}>
          <div style={colHeader}>
            <span style={{ fontSize: 12, fontWeight: 600, color: '#94a3b8' }}>Por skill</span>
            {loading && <Spinner />}
          </div>

          <button
            style={{
              width: '100%', textAlign: 'left', padding: '10px 16px',
              borderBottom: '1px solid #1e293b', cursor: 'pointer',
              background: !selectedSkill ? '#1e293b' : 'transparent',
              borderLeft: !selectedSkill ? '3px solid #3b82f6' : '3px solid transparent',
            }}
            onClick={() => setSelectedSkill(null)}
          >
            <div style={{ fontSize: 12, fontWeight: 600, color: !selectedSkill ? '#93c5fd' : '#e2e8f0' }}>
              Todos os skills
            </div>
            <div style={{ fontSize: 11, color: '#64748b', marginTop: 2 }}>
              {instances.length} instância(s)
            </div>
          </button>

          <div style={{ flex: 1, overflowY: 'auto' }}>
            {groups.map(g => {
              const active = g.skillId === selectedSkill
              return (
                <button
                  key={g.skillId}
                  onClick={() => setSelectedSkill(active ? null : g.skillId)}
                  style={{
                    width: '100%', textAlign: 'left', padding: '10px 16px',
                    borderBottom: '1px solid #1e293b', cursor: 'pointer',
                    background: active ? '#1e3a5f' : 'transparent',
                    borderLeft: active ? '3px solid #3b82f6' : '3px solid transparent',
                  }}
                >
                  <div style={{ fontSize: 12, fontWeight: 600, color: active ? '#93c5fd' : '#e2e8f0' }}>
                    {g.skillId}
                  </div>
                  <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
                    {g.ready  > 0 && <Pill color="#22c55e" label={`${g.ready} pronto`} />}
                    {g.busy   > 0 && <Pill color="#3b82f6" label={`${g.busy} em sessão`} />}
                    {g.paused > 0 && <Pill color="#eab308" label={`${g.paused} pausado`} />}
                  </div>
                </button>
              )
            })}
          </div>
        </div>

        {/* ─── Right: instance list ────────────────────────────────────── */}
        <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
          <div style={colHeader}>
            <span style={{ fontSize: 12, fontWeight: 600, color: '#94a3b8' }}>
              {selectedSkill ? `Instâncias de ${selectedSkill}` : 'Todas as instâncias'}
            </span>
          </div>

          <div style={{ flex: 1, overflowY: 'auto' }}>
            {displayInstances.length === 0 && !loading && (
              <div style={{ padding: '40px 24px', textAlign: 'center', color: '#475569', fontSize: 13 }}>
                Nenhuma instância ativa
              </div>
            )}
            <div style={{ padding: 16, display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 12 }}>
              {displayInstances.map(inst => (
                <InstanceCard key={inst.instance_id} inst={inst} />
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── InstanceCard ───────────────────────────────────────────────────────────────

function InstanceCard({ inst }: { inst: AgentInstance }) {
  const color = STATUS_COLOR[inst.status] ?? '#6b7280'
  const skills: string[] =
    (inst as any).agent_type?.skills?.map((s: any) => s.skill_id ?? s) ?? []

  return (
    <div style={{
      background: '#1e293b', borderRadius: 8, padding: '14px 16px',
      border: '1px solid #334155', borderTop: `2px solid ${color}`,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
        <code style={{ fontSize: 12, color: '#93c5fd', fontWeight: 600 }}>{inst.instance_id}</code>
        <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 7px', borderRadius: 4, background: color + '22', color }}>
          {STATUS_LABEL[inst.status] ?? inst.status}
        </span>
      </div>

      {(inst as any).pool_id && (
        <div style={{ fontSize: 11, color: '#64748b', marginBottom: 4 }}>
          Pool: <span style={{ color: '#94a3b8' }}>{(inst as any).pool_id}</span>
        </div>
      )}

      {skills.length > 0 && (
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 6 }}>
          {skills.map(sk => (
            <span key={sk} style={{ fontSize: 10, background: '#2e1065', color: '#a78bfa', padding: '2px 6px', borderRadius: 4 }}>
              {sk}
            </span>
          ))}
        </div>
      )}

      {inst.channel_types && inst.channel_types.length > 0 && (
        <div style={{ fontSize: 11, color: '#475569', marginTop: 6 }}>
          {inst.channel_types.join(' · ')}
        </div>
      )}
    </div>
  )
}

function Pill({ color, label }: { color: string; label: string }) {
  return (
    <span style={{ fontSize: 10, padding: '1px 6px', borderRadius: 3, background: color + '22', color }}>
      {label}
    </span>
  )
}

// ── Styles ─────────────────────────────────────────────────────────────────────

const page: React.CSSProperties      = { display: 'flex', flexDirection: 'column', height: '100%', backgroundColor: '#0a1628', color: '#e2e8f0', overflow: 'hidden' }
const topBar: React.CSSProperties    = { display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 20px', borderBottom: '1px solid #1e293b', flexShrink: 0 }
const leftCol: React.CSSProperties   = { width: 280, flexShrink: 0, borderRight: '1px solid #1e293b', display: 'flex', flexDirection: 'column', overflow: 'hidden' }
const colHeader: React.CSSProperties = { padding: '10px 16px', borderBottom: '1px solid #1e293b', flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }
const btnSecondary: React.CSSProperties = { background: 'none', border: '1px solid #334155', color: '#94a3b8', borderRadius: 6, padding: '5px 12px', cursor: 'pointer', fontSize: 12 }
