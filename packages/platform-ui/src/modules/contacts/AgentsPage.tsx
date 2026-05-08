/**
 * AgentsPage — /contacts/agents
 *
 * Live view of agent instances (AI and human) grouped by skill/agent type.
 * Reuses the AgentFlowMonitorPage view inline.
 *
 * Sub-tabs:
 *   monitor — live instances from orchestrator-bridge (~15s polling)
 *   list    — aggregated agent metrics table (Arc 8 backend — graceful pending state)
 */
import React, { useCallback, useEffect, useState } from 'react'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'
import type { AgentInstance } from '@/types'

// ── API ───────────────────────────────────────────────────────────────────────

async function fetchInstances(tenantId: string, token?: string | null): Promise<AgentInstance[]> {
  const h: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) h['Authorization'] = `Bearer ${token}`
  const res = await fetch(`/v1/instances?tenant_id=${tenantId}`, { headers: h })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const body = await res.json()
  return Array.isArray(body) ? body : (body.data ?? body.instances ?? [])
}

// ── Status config ─────────────────────────────────────────────────────────────

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

// ── Monitor sub-tab ───────────────────────────────────────────────────────────

function MonitorSubTab({ tenantId }: { tenantId: string }) {
  const { getAccessToken } = useAuth()
  const [instances,     setInstances]     = useState<AgentInstance[]>([])
  const [loading,       setLoading]       = useState(false)
  const [error,         setError]         = useState<string | null>(null)
  const [selectedSkill, setSelectedSkill] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const token = await getAccessToken()
      setInstances(await fetchInstances(tenantId, token))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally { setLoading(false) }
  }, [tenantId, getAccessToken])

  useEffect(() => {
    load()
    const id = setInterval(load, 15_000)
    return () => clearInterval(id)
  }, [load])

  type SkillGroup = { skillId: string; instances: AgentInstance[]; ready: number; busy: number; paused: number }
  const groups: SkillGroup[] = Object.values(
    instances.reduce<Record<string, SkillGroup>>((acc, inst) => {
      const skills: string[] = (inst as any).agent_type?.skills?.map((s: any) => s.skill_id ?? s) ?? []
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

  const displayInstances = selectedSkill
    ? groups.find(g => g.skillId === selectedSkill)?.instances ?? []
    : instances

  return (
    <div className="flex h-full overflow-hidden bg-[#0a1628] text-slate-200">
      {/* Left: skill groups */}
      <div className="w-72 flex-shrink-0 border-r border-slate-800 flex flex-col overflow-hidden">
        <div className="px-3 py-2.5 border-b border-slate-800 flex items-center justify-between flex-shrink-0">
          <span className="text-xs font-semibold text-slate-500">Por skill</span>
          {loading ? <Spinner /> : (
            <button onClick={load} className="text-xs text-slate-500 hover:text-slate-300 transition-colors">↻</button>
          )}
        </div>

        <button
          className="w-full text-left px-4 py-2.5 border-b border-slate-800 transition-colors"
          style={{ background: !selectedSkill ? '#1e293b' : 'transparent', borderLeft: !selectedSkill ? '3px solid #3b82f6' : '3px solid transparent' }}
          onClick={() => setSelectedSkill(null)}>
          <div className="text-xs font-semibold text-slate-200">Todas as instâncias</div>
          <div className="text-xs text-slate-500 mt-0.5">{instances.length} instâncias</div>
        </button>

        <div className="flex-1 overflow-y-auto">
          {groups.map(g => {
            const active = g.skillId === selectedSkill
            return (
              <button key={g.skillId} onClick={() => setSelectedSkill(active ? null : g.skillId)}
                className="w-full text-left px-4 py-2.5 border-b border-slate-800 transition-colors"
                style={{ background: active ? '#1e3a5f' : 'transparent', borderLeft: active ? '3px solid #3b82f6' : '3px solid transparent' }}>
                <div className="text-xs font-semibold" style={{ color: active ? '#93c5fd' : '#e2e8f0' }}>{g.skillId}</div>
                <div className="flex gap-1.5 mt-1 flex-wrap">
                  {g.ready  > 0 && <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: '#22c55e22', color: '#22c55e' }}>{g.ready} pronto</span>}
                  {g.busy   > 0 && <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: '#3b82f622', color: '#3b82f6' }}>{g.busy} em sessão</span>}
                  {g.paused > 0 && <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: '#eab30822', color: '#eab308' }}>{g.paused} pausado</span>}
                </div>
              </button>
            )
          })}
        </div>
      </div>

      {/* Right: instance cards */}
      <div className="flex-1 flex flex-col overflow-hidden">
        <div className="px-4 py-2.5 border-b border-slate-800 flex-shrink-0">
          <span className="text-xs font-semibold text-slate-500">
            {selectedSkill ? `Instâncias — ${selectedSkill}` : 'Todas as instâncias'}
          </span>
        </div>

        {error && (
          <div className="mx-4 mt-3 px-3 py-2 bg-red-950 border border-red-800 rounded text-xs text-red-300">
            Erro ao carregar instâncias: {error}
          </div>
        )}

        <div className="flex-1 overflow-y-auto p-4">
          {displayInstances.length === 0 && !loading ? (
            <div className="flex flex-col items-center justify-center py-16 text-slate-500 gap-2">
              <span className="text-3xl">🤖</span>
              <span className="text-sm">Nenhuma instância encontrada</span>
            </div>
          ) : (
            <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))' }}>
              {displayInstances.map(inst => {
                const color  = STATUS_COLOR[inst.status] ?? '#6b7280'
                const skills = (inst as any).agent_type?.skills?.map((s: any) => s.skill_id ?? s) ?? []
                return (
                  <div key={inst.instance_id}
                    className="rounded-lg p-3.5 border"
                    style={{ background: '#1e293b', borderColor: '#334155', borderTop: `2px solid ${color}` }}>
                    <div className="flex justify-between items-start mb-2">
                      <code className="text-xs text-blue-300 font-semibold">{inst.instance_id}</code>
                      <span className="text-[11px] font-bold px-1.5 py-0.5 rounded flex-shrink-0"
                        style={{ background: color + '22', color }}>
                        {STATUS_LABEL[inst.status] ?? inst.status}
                      </span>
                    </div>
                    {(inst as any).pool_id && (
                      <div className="text-xs text-slate-500 mb-1">Pool: <span className="text-slate-300">{(inst as any).pool_id}</span></div>
                    )}
                    {skills.length > 0 && (
                      <div className="flex gap-1 flex-wrap mt-1.5">
                        {skills.map((sk: string) => (
                          <span key={sk} className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: '#2e1065', color: '#a78bfa' }}>{sk}</span>
                        ))}
                      </div>
                    )}
                    {inst.channel_types && inst.channel_types.length > 0 && (
                      <div className="text-xs text-slate-500 mt-1.5">{inst.channel_types.join(' · ')}</div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ── List sub-tab (placeholder — Arc 8 backend pending) ────────────────────────

function ListSubTab() {
  return (
    <div className="flex flex-col items-center justify-center h-full text-gray-400 gap-3">
      <span className="text-4xl">📊</span>
      <p className="text-sm font-medium text-gray-500">Métricas consolidadas de agentes</p>
      <p className="text-xs text-center max-w-xs">
        Esta view requer o backend de analytics de agentes (Arc 8).
        Disponível após implementação de <code className="bg-gray-100 px-1 rounded">GET /reports/agent-performance/daily</code>.
      </p>
    </div>
  )
}

// ── AgentsPage ────────────────────────────────────────────────────────────────

type SubTab = 'monitor' | 'list'

export default function AgentsPage() {
  const { tenantId } = useAuth()
  const [subTab, setSubTab] = useState<SubTab>('monitor')

  if (!tenantId) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400 text-sm">
        Nenhum tenant selecionado.
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full overflow-hidden bg-white">
      {/* Header + sub-tab bar */}
      <div className="flex-shrink-0">
        <div className="px-4 pt-3 pb-0 bg-white border-b border-gray-200">
          <span className="font-bold text-gray-800 text-base block mb-2">Agentes</span>
          <div className="flex items-end gap-0">
            {([
              { id: 'monitor' as SubTab, label: 'Monitor' },
              { id: 'list'    as SubTab, label: 'Lista'   },
            ]).map(s => (
              <button key={s.id} onClick={() => setSubTab(s.id)}
                className={`px-5 py-2.5 text-sm font-medium border-b-2 transition-colors ${
                  subTab === s.id
                    ? 'border-primary text-primary'
                    : 'border-transparent text-gray-500 hover:text-gray-700'
                }`}>
                {s.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-hidden">
        {subTab === 'monitor' ? <MonitorSubTab tenantId={tenantId} /> : <ListSubTab />}
      </div>
    </div>
  )
}
