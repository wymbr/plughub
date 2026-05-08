/**
 * AgentsPage — /contacts/agents
 *
 * Live view of agent instances grouped by agent_type.
 * Instances are read from Redis via GET /api/instances (mcp-server-plughub).
 * Redis is the source of truth for runtime state — orchestrator-bridge bootstrap
 * writes instances there; PostgreSQL agent-registry only tracks configuration.
 *
 * Sub-tabs:
 *   monitor — live instances polled every 15s
 *   list    — aggregated agent metrics (Arc 8 backend — pending)
 */
import React, { useCallback, useEffect, useState } from 'react'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'
import { listPools } from '@/api/registry'
import type { Pool } from '@/types'

// ── Runtime instance type (from Redis via mcp-server-plughub) ─────────────────
interface RuntimeInstance {
  instance_id:    string
  agent_type_id:  string
  pool_id?:       string
  pools?:         string[]
  status:         string
  current_sessions?: number
  max_concurrent?:   number
  channel_types?:    string[]
  source?:           string
  registered_at?:    string
}

// ── Status config ─────────────────────────────────────────────────────────────

const STATUS_COLOR: Record<string, string> = {
  login:    '#94a3b8',
  ready:    '#22c55e',
  busy:     '#3b82f6',
  paused:   '#eab308',
  draining: '#f97316',
  logout:   '#6b7280',
}
const STATUS_LABEL: Record<string, string> = {
  login:    'Conectando',
  ready:    'Pronto',
  busy:     'Em sessão',
  paused:   'Pausado',
  draining: 'Drenando',
  logout:   'Desconectado',
}

const ALL_STATUSES = ['login', 'ready', 'busy', 'paused', 'draining', 'logout'] as const

// ── Monitor sub-tab ───────────────────────────────────────────────────────────

async function fetchRuntimeInstances(
  tenantId: string,
  poolId?: string,
  status?: string,
): Promise<RuntimeInstance[]> {
  const params = new URLSearchParams({ tenant_id: tenantId })
  if (poolId) params.append('pool_id', poolId)
  if (status) params.append('status', status)
  const res = await fetch(`/api/instances?${params}`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const body = await res.json()
  return Array.isArray(body) ? body : (body.instances ?? [])
}

function MonitorSubTab({ tenantId }: { tenantId: string }) {
  const [instances,      setInstances]      = useState<RuntimeInstance[]>([])
  const [pools,          setPools]          = useState<Pool[]>([])
  const [loading,        setLoading]        = useState(false)
  const [error,          setError]          = useState<string | null>(null)
  const [selectedGroup,  setSelectedGroup]  = useState<string | null>(null)

  // Filters — persisted in localStorage so they survive navigation
  const [filterPool,   setFilterPool]   = useState<string>(
    () => localStorage.getItem('agents.filterPool')   ?? ''
  )
  const [filterStatus, setFilterStatus] = useState<string>(
    () => localStorage.getItem('agents.filterStatus') ?? ''
  )

  const handleSetFilterPool = (v: string) => {
    setFilterPool(v); setSelectedGroup(null)
    localStorage.setItem('agents.filterPool', v)
  }
  const handleSetFilterStatus = (v: string) => {
    setFilterStatus(v); setSelectedGroup(null)
    localStorage.setItem('agents.filterStatus', v)
  }

  const loadPools = useCallback(async () => {
    try {
      const res = await listPools(tenantId)
      setPools(res.items)
    } catch { /* non-fatal */ }
  }, [tenantId])

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const data = await fetchRuntimeInstances(
        tenantId,
        filterPool   || undefined,
        filterStatus || undefined,
      )
      setInstances(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally { setLoading(false) }
  }, [tenantId, filterPool, filterStatus])

  useEffect(() => { loadPools() }, [loadPools])

  useEffect(() => {
    load()
    const id = setInterval(load, 15_000)
    return () => clearInterval(id)
  }, [load])

  // Group by agent_type_id
  type AgentGroup = {
    agentTypeId: string
    instances:   RuntimeInstance[]
    ready:       number
    busy:        number
    paused:      number
  }

  const groups: AgentGroup[] = Object.values(
    instances.reduce<Record<string, AgentGroup>>((acc, inst) => {
      const key = inst.agent_type_id ?? '(desconhecido)'
      if (!acc[key]) acc[key] = { agentTypeId: key, instances: [], ready: 0, busy: 0, paused: 0 }
      acc[key].instances.push(inst)
      if (inst.status === 'ready')  acc[key].ready++
      if (inst.status === 'busy')   acc[key].busy++
      if (inst.status === 'paused') acc[key].paused++
      return acc
    }, {}),
  ).sort((a, b) => b.instances.length - a.instances.length)

  const displayInstances = selectedGroup
    ? groups.find(g => g.agentTypeId === selectedGroup)?.instances ?? []
    : instances

  return (
    <div className="flex flex-col h-full overflow-hidden bg-[#0a1628] text-slate-200">

      {/* Filter bar */}
      <div className="flex items-center gap-3 px-4 py-2.5 border-b border-slate-800 flex-shrink-0 flex-wrap">

        {/* Pool filter */}
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-slate-500 whitespace-nowrap">Pool</label>
          <select
            value={filterPool}
            onChange={e => handleSetFilterPool(e.target.value)}
            className="text-xs bg-slate-900 border border-slate-700 rounded px-2 py-1 text-slate-200 focus:outline-none focus:border-slate-500 min-w-[140px]"
          >
            <option value="">Todos</option>
            {pools.map(p => (
              <option key={p.pool_id} value={p.pool_id}>{p.pool_id}</option>
            ))}
          </select>
        </div>

        {/* Status filter */}
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-slate-500 whitespace-nowrap">Status</label>
          <select
            value={filterStatus}
            onChange={e => handleSetFilterStatus(e.target.value)}
            className="text-xs bg-slate-900 border border-slate-700 rounded px-2 py-1 text-slate-200 focus:outline-none focus:border-slate-500"
          >
            <option value="">Todos</option>
            {ALL_STATUSES.map(s => (
              <option key={s} value={s}>{STATUS_LABEL[s]}</option>
            ))}
          </select>
        </div>

        <div className="flex-1" />

        {loading
          ? <Spinner />
          : <button onClick={load} className="text-xs text-slate-500 hover:text-slate-300 transition-colors px-2 py-1">↻ Atualizar</button>
        }

        <span className="text-xs text-slate-600">{instances.length} instâncias</span>
      </div>

      <div className="flex flex-1 overflow-hidden">

        {/* Left: agent type groups */}
        <div className="w-64 flex-shrink-0 border-r border-slate-800 flex flex-col overflow-hidden">
          <div className="px-3 py-2 border-b border-slate-800 flex-shrink-0">
            <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">Por tipo de agente</span>
          </div>

          {/* All */}
          <button
            className="w-full text-left px-4 py-2.5 border-b border-slate-800 transition-colors flex-shrink-0"
            style={{
              background:  !selectedGroup ? '#1e293b' : 'transparent',
              borderLeft:  !selectedGroup ? '3px solid #3b82f6' : '3px solid transparent',
            }}
            onClick={() => setSelectedGroup(null)}
          >
            <div className="text-xs font-semibold text-slate-200">Todos</div>
            <div className="text-xs text-slate-500 mt-0.5">{instances.length} instâncias</div>
          </button>

          <div className="flex-1 overflow-y-auto">
            {groups.map(g => {
              const active = g.agentTypeId === selectedGroup
              return (
                <button key={g.agentTypeId} onClick={() => setSelectedGroup(active ? null : g.agentTypeId)}
                  className="w-full text-left px-4 py-2.5 border-b border-slate-800 transition-colors"
                  style={{
                    background:  active ? '#1e3a5f' : 'transparent',
                    borderLeft:  active ? '3px solid #3b82f6' : '3px solid transparent',
                  }}>
                  <div className="text-xs font-semibold truncate" style={{ color: active ? '#93c5fd' : '#e2e8f0' }}>
                    {g.agentTypeId}
                  </div>
                  <div className="flex gap-1 mt-1 flex-wrap">
                    {g.ready  > 0 && <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: '#22c55e22', color: '#22c55e' }}>{g.ready} pronto</span>}
                    {g.busy   > 0 && <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: '#3b82f622', color: '#3b82f6' }}>{g.busy} em sessão</span>}
                    {g.paused > 0 && <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: '#eab30822', color: '#eab308' }}>{g.paused} pausado</span>}
                    {g.ready === 0 && g.busy === 0 && g.paused === 0 && (
                      <span className="text-[10px] text-slate-600">{g.instances.length} instância{g.instances.length !== 1 ? 's' : ''}</span>
                    )}
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
              {selectedGroup ? `Instâncias — ${selectedGroup}` : 'Todas as instâncias'}
            </span>
          </div>

          {error && (
            <div className="mx-4 mt-3 px-3 py-2 bg-red-950 border border-red-800 rounded text-xs text-red-300">
              Erro ao carregar instâncias: {error}
            </div>
          )}

          <div className="flex-1 overflow-y-auto">
            {displayInstances.length === 0 && !loading && !error ? (
              <div className="flex flex-col items-center justify-center py-16 text-slate-500 gap-2">
                <span className="text-3xl">🤖</span>
                <span className="text-sm">Nenhuma instância encontrada</span>
                {(filterPool || filterStatus) && (
                  <span className="text-xs text-slate-600">Tente remover os filtros</span>
                )}
              </div>
            ) : (
              <table className="w-full text-xs border-collapse">
                <thead className="sticky top-0 z-10" style={{ background: '#0f172a' }}>
                  <tr className="text-slate-500 border-b border-slate-800">
                    <th className="text-left px-4 py-2.5 font-medium">Instância</th>
                    <th className="text-left px-3 py-2.5 font-medium">Tipo</th>
                    <th className="text-center px-3 py-2.5 font-medium w-24">Status</th>
                    <th className="text-left px-3 py-2.5 font-medium">Pool</th>
                    <th className="text-center px-3 py-2.5 font-medium w-20">Sessões</th>
                    <th className="text-left px-3 py-2.5 font-medium">Canais</th>
                    <th className="text-right px-4 py-2.5 font-medium w-28">Desde</th>
                  </tr>
                </thead>
                <tbody>
                  {displayInstances.map(inst => {
                    const color    = STATUS_COLOR[inst.status] ?? '#6b7280'
                    const poolName = inst.pool_id ?? inst.pools?.[0] ?? '—'
                    return (
                      <tr key={inst.instance_id}
                        className="border-b border-slate-800/50 transition-colors hover:bg-slate-900/50"
                        style={{ borderLeft: `2px solid ${color}20` }}>
                        <td className="px-4 py-2.5">
                          <code className="text-blue-300 font-semibold">{inst.instance_id}</code>
                        </td>
                        <td className="px-3 py-2.5 text-slate-400 truncate max-w-[180px]">
                          {inst.agent_type_id}
                        </td>
                        <td className="px-3 py-2.5 text-center">
                          <span className="text-[11px] font-bold px-1.5 py-0.5 rounded"
                            style={{ background: color + '22', color }}>
                            {STATUS_LABEL[inst.status] ?? inst.status}
                          </span>
                        </td>
                        <td className="px-3 py-2.5 text-slate-400">{poolName}</td>
                        <td className="px-3 py-2.5 text-center text-slate-300">
                          {typeof inst.current_sessions === 'number'
                            ? `${inst.current_sessions}${inst.max_concurrent ? `/${inst.max_concurrent}` : ''}`
                            : '—'}
                        </td>
                        <td className="px-3 py-2.5">
                          <div className="flex gap-1 flex-wrap">
                            {(inst.channel_types ?? []).map(ch => (
                              <span key={ch} className="text-[10px] px-1 py-0.5 rounded bg-slate-800 text-slate-500">{ch}</span>
                            ))}
                          </div>
                        </td>
                        <td className="px-4 py-2.5 text-right text-slate-600">
                          {inst.registered_at
                            ? new Date(inst.registered_at).toLocaleTimeString('pt-BR')
                            : '—'}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

// ── AgentsPage ────────────────────────────────────────────────────────────────

export default function AgentsPage() {
  const { tenantId } = useAuth()

  if (!tenantId) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400 text-sm">
        Nenhum tenant selecionado.
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full overflow-hidden bg-[#0a1628]">
      <div className="flex-shrink-0 px-4 pt-3 pb-2 border-b border-slate-800">
        <span className="font-bold text-slate-100 text-base">Agentes</span>
      </div>
      <div className="flex-1 overflow-hidden">
        <MonitorSubTab tenantId={tenantId} />
      </div>
    </div>
  )
}
