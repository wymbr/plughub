/**
 * AgentsPage — /contacts/agents
 *
 * Two sub-tabs:
 *   monitor  — live instances polled every 15 s (Redis via GET /api/instances)
 *   report   — daily performance metrics (GET /reports/agent-performance/daily)
 *
 * Data sources:
 *   Live instances : GET /api/instances          (mcp-server-plughub → Redis)
 *   Daily perf     : GET /reports/agent-performance/daily  (analytics-api)
 */
import React, { useCallback, useEffect, useState } from 'react'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'
import { listPools } from '@/api/registry'
import type { Pool } from '@/types'

// ── Runtime instance type (from Redis via mcp-server-plughub) ─────────────────
interface RuntimeInstance {
  instance_id:       string
  agent_type_id:     string
  pool_id?:          string
  pools?:            string[]
  status:            string
  current_sessions?: number
  max_concurrent?:   number
  channel_types?:    string[]
  source?:           string
  registered_at?:    string
}

// ── Daily performance row (from analytics-api) ────────────────────────────────
interface DailyPerfRow {
  agent_type_id:   string
  pool_id:         string
  period_date:     string
  total_sessions:  number
  avg_duration_ms: number
  resolution_rate: number
  escalation_rate: number
  transfer_rate:   number
  human_rate:      number
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

// ── Helpers ───────────────────────────────────────────────────────────────────

function isoToday(): string { return new Date().toISOString().slice(0, 10) }
function iso7DaysAgo(): string {
  const d = new Date(); d.setDate(d.getDate() - 6)
  return d.toISOString().slice(0, 10)
}
function pct(v: number): string { return `${(v * 100).toFixed(1)}%` }
function fmtDuration(ms: number): string {
  if (ms < 60_000) return `${Math.round(ms / 1000)}s`
  const m = Math.floor(ms / 60_000)
  const s = Math.round((ms % 60_000) / 1000)
  return s > 0 ? `${m}m ${s}s` : `${m}m`
}

// ── Monitor sub-tab ───────────────────────────────────────────────────────────

async function fetchRuntimeInstances(
  tenantId: string,
  poolId?:  string,
  status?:  string,
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
  const [instances,     setInstances]     = useState<RuntimeInstance[]>([])
  const [pools,         setPools]         = useState<Pool[]>([])
  const [loading,       setLoading]       = useState(false)
  const [error,         setError]         = useState<string | null>(null)
  const [selectedGroup, setSelectedGroup] = useState<string | null>(null)

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
    try { const res = await listPools(tenantId); setPools(res.items) }
    catch { /* non-fatal */ }
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
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-slate-500 whitespace-nowrap">Pool</label>
          <select value={filterPool} onChange={e => handleSetFilterPool(e.target.value)}
            className="text-xs bg-slate-900 border border-slate-700 rounded px-2 py-1 text-slate-200 focus:outline-none focus:border-slate-500 min-w-[140px]">
            <option value="">Todos</option>
            {pools.map(p => <option key={p.pool_id} value={p.pool_id}>{p.pool_id}</option>)}
          </select>
        </div>
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-slate-500 whitespace-nowrap">Status</label>
          <select value={filterStatus} onChange={e => handleSetFilterStatus(e.target.value)}
            className="text-xs bg-slate-900 border border-slate-700 rounded px-2 py-1 text-slate-200 focus:outline-none focus:border-slate-500">
            <option value="">Todos</option>
            {ALL_STATUSES.map(s => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
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
          <button
            className="w-full text-left px-4 py-2.5 border-b border-slate-800 transition-colors flex-shrink-0"
            style={{
              background: !selectedGroup ? '#1e293b' : 'transparent',
              borderLeft: !selectedGroup ? '3px solid #3b82f6' : '3px solid transparent',
            }}
            onClick={() => setSelectedGroup(null)}>
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
                    background: active ? '#1e3a5f' : 'transparent',
                    borderLeft: active ? '3px solid #3b82f6' : '3px solid transparent',
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
                        <td className="px-3 py-2.5 text-slate-400 truncate max-w-[180px]">{inst.agent_type_id}</td>
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

// ── Report sub-tab ────────────────────────────────────────────────────────────

function RateBar({ value, color }: { value: number; color: string }) {
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-16 h-1.5 rounded bg-gray-100 overflow-hidden">
        <div className="h-full rounded" style={{ width: `${(value * 100).toFixed(0)}%`, background: color }} />
      </div>
      <span className="text-[11px] tabular-nums" style={{ color }}>{pct(value)}</span>
    </div>
  )
}

function ReportSubTab({ tenantId }: { tenantId: string }) {
  const [fromDt,        setFromDt]        = useState(iso7DaysAgo)
  const [toDt,          setToDt]          = useState(isoToday)
  const [filterPool,    setFilterPool]    = useState('')
  const [filterAgent,   setFilterAgent]   = useState('')
  const [data,          setData]          = useState<DailyPerfRow[]>([])
  const [pools,         setPools]         = useState<Pool[]>([])
  const [loading,       setLoading]       = useState(false)
  const [error,         setError]         = useState<string | null>(null)
  const [sortKey,       setSortKey]       = useState<keyof DailyPerfRow>('period_date')
  const [sortAsc,       setSortAsc]       = useState(false)

  const loadPools = useCallback(async () => {
    try { const res = await listPools(tenantId); setPools(res.items) }
    catch { /* non-fatal */ }
  }, [tenantId])

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const qs = new URLSearchParams({ tenant_id: tenantId, from_dt: fromDt, to_dt: toDt })
      if (filterPool)  qs.append('pool_id', filterPool)
      if (filterAgent) qs.append('agent_type_id', filterAgent)
      const res  = await fetch(`/reports/agent-performance/daily?${qs}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const body = await res.json()
      setData(Array.isArray(body) ? body : (body.data ?? []))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setData([])
    } finally { setLoading(false) }
  }, [tenantId, fromDt, toDt, filterPool, filterAgent])

  useEffect(() => { loadPools() }, [loadPools])
  useEffect(() => { load() }, [load])

  // ── KPIs ─────────────────────────────────────────────────────────────────────
  const totalSessions  = data.reduce((s, r) => s + r.total_sessions, 0)
  const wResolution    = totalSessions > 0
    ? data.reduce((s, r) => s + r.resolution_rate * r.total_sessions, 0) / totalSessions
    : null
  const wEscalation    = totalSessions > 0
    ? data.reduce((s, r) => s + r.escalation_rate * r.total_sessions, 0) / totalSessions
    : null
  const avgDuration    = totalSessions > 0
    ? data.reduce((s, r) => s + r.avg_duration_ms * r.total_sessions, 0) / totalSessions
    : null

  // ── Unique agent types for filter dropdown ────────────────────────────────────
  const agentTypes = [...new Set(data.map(r => r.agent_type_id))].sort()

  // ── Sort ──────────────────────────────────────────────────────────────────────
  function handleSort(key: keyof DailyPerfRow) {
    if (key === sortKey) setSortAsc(a => !a)
    else { setSortKey(key); setSortAsc(false) }
  }

  const sorted = [...data].sort((a, b) => {
    const va = a[sortKey], vb = b[sortKey]
    const cmp = typeof va === 'number' && typeof vb === 'number'
      ? va - vb : String(va).localeCompare(String(vb))
    return sortAsc ? cmp : -cmp
  })

  // ── CSV export ────────────────────────────────────────────────────────────────
  function exportCsv() {
    const cols: (keyof DailyPerfRow)[] = [
      'period_date', 'agent_type_id', 'pool_id', 'total_sessions',
      'avg_duration_ms', 'resolution_rate', 'escalation_rate', 'transfer_rate', 'human_rate',
    ]
    const header = cols.join(',')
    const rows   = data.map(r => cols.map(c => r[c]).join(',')).join('\n')
    const blob   = new Blob([header + '\n' + rows], { type: 'text/csv' })
    const url    = URL.createObjectURL(blob)
    const a      = document.createElement('a')
    a.href = url; a.download = `agents_perf_${fromDt}_${toDt}.csv`; a.click()
    URL.revokeObjectURL(url)
  }

  // ── Th helper ─────────────────────────────────────────────────────────────────
  function Th({ label, k, align = 'left' }: { label: string; k: keyof DailyPerfRow; align?: string }) {
    const active = sortKey === k
    return (
      <th onClick={() => handleSort(k)}
        className={`px-3 py-2.5 font-medium text-${align} cursor-pointer select-none whitespace-nowrap hover:text-gray-700 transition-colors ${active ? 'text-primary' : 'text-gray-500'}`}>
        {label}{active ? (sortAsc ? ' ↑' : ' ↓') : ''}
      </th>
    )
  }

  return (
    <div className="flex flex-col h-full overflow-hidden bg-gray-50">

      {/* Filter bar */}
      <div className="bg-white border-b border-gray-200 px-5 py-2.5 flex items-center gap-3 flex-shrink-0 flex-wrap">
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-gray-500">De</label>
          <input type="date" value={fromDt} onChange={e => setFromDt(e.target.value)}
            className="text-xs border border-gray-300 rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-primary/40" />
        </div>
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-gray-500">Até</label>
          <input type="date" value={toDt} onChange={e => setToDt(e.target.value)}
            className="text-xs border border-gray-300 rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-primary/40" />
        </div>
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-gray-500">Pool</label>
          <select value={filterPool} onChange={e => setFilterPool(e.target.value)}
            className="text-xs border border-gray-300 rounded px-2 py-1 bg-white focus:outline-none focus:ring-1 focus:ring-primary/40 min-w-[130px]">
            <option value="">Todos</option>
            {pools.map(p => <option key={p.pool_id} value={p.pool_id}>{p.pool_id}</option>)}
          </select>
        </div>
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-gray-500">Agente</label>
          <select value={filterAgent} onChange={e => setFilterAgent(e.target.value)}
            className="text-xs border border-gray-300 rounded px-2 py-1 bg-white focus:outline-none focus:ring-1 focus:ring-primary/40 min-w-[160px]">
            <option value="">Todos</option>
            {agentTypes.map(a => <option key={a} value={a}>{a}</option>)}
          </select>
        </div>
        <div className="flex-1" />
        {loading
          ? <Spinner />
          : <button onClick={load} className="text-xs text-gray-400 hover:text-gray-600 transition-colors px-2 py-1">↻ Atualizar</button>
        }
        <button onClick={exportCsv} disabled={data.length === 0}
          className="text-xs border border-gray-200 rounded px-2.5 py-1 text-gray-600 hover:bg-gray-50 disabled:opacity-40 transition-colors">
          ↓ CSV
        </button>
      </div>

      {/* KPI strip */}
      <div className="flex gap-3 px-5 py-3 flex-shrink-0 flex-wrap">
        <div className="bg-white border border-gray-200 rounded-lg px-5 py-3 flex flex-col gap-0.5 min-w-[130px]">
          <span className="text-[11px] text-gray-400 uppercase tracking-wide">Sessões</span>
          <span className="text-2xl font-bold text-gray-800 leading-none">{totalSessions.toLocaleString('pt-BR')}</span>
        </div>
        <div className="bg-white border border-gray-200 rounded-lg px-5 py-3 flex flex-col gap-0.5 min-w-[130px]">
          <span className="text-[11px] text-gray-400 uppercase tracking-wide">Resolução média</span>
          <span className="text-2xl font-bold leading-none" style={{ color: '#059669' }}>
            {wResolution !== null ? pct(wResolution) : '—'}
          </span>
        </div>
        <div className="bg-white border border-gray-200 rounded-lg px-5 py-3 flex flex-col gap-0.5 min-w-[130px]">
          <span className="text-[11px] text-gray-400 uppercase tracking-wide">Escalonamento</span>
          <span className="text-2xl font-bold leading-none" style={{ color: wEscalation !== null && wEscalation > 0.15 ? '#DC2626' : '#1B4F8A' }}>
            {wEscalation !== null ? pct(wEscalation) : '—'}
          </span>
        </div>
        <div className="bg-white border border-gray-200 rounded-lg px-5 py-3 flex flex-col gap-0.5 min-w-[130px]">
          <span className="text-[11px] text-gray-400 uppercase tracking-wide">Tempo médio</span>
          <span className="text-2xl font-bold text-gray-800 leading-none">
            {avgDuration !== null ? fmtDuration(avgDuration) : '—'}
          </span>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="mx-5 mb-2 px-3 py-2 bg-red-50 border border-red-200 rounded text-xs text-red-600 flex-shrink-0">
          Erro ao carregar dados: {error}
        </div>
      )}

      {/* Table */}
      <div className="flex-1 overflow-auto px-5 pb-5">
        {sorted.length === 0 && !loading ? (
          <div className="flex flex-col items-center justify-center py-20 text-gray-400 gap-2">
            <span className="text-3xl">📊</span>
            <span className="text-sm">Nenhum dado no período</span>
          </div>
        ) : (
          <table className="w-full text-xs bg-white border border-gray-200 rounded-lg overflow-hidden border-separate border-spacing-0">
            <thead className="sticky top-0 z-10 bg-gray-50 border-b border-gray-200">
              <tr>
                <Th label="Data"       k="period_date" />
                <Th label="Agente"     k="agent_type_id" />
                <Th label="Pool"       k="pool_id" />
                <Th label="Sessões"    k="total_sessions"  align="right" />
                <Th label="Tempo méd." k="avg_duration_ms" align="right" />
                <th className="px-3 py-2.5 text-left text-gray-500 font-medium whitespace-nowrap">Resolução</th>
                <th className="px-3 py-2.5 text-left text-gray-500 font-medium whitespace-nowrap">Escalonamento</th>
                <th className="px-3 py-2.5 text-left text-gray-500 font-medium whitespace-nowrap">Transferência</th>
                <th className="px-3 py-2.5 text-left text-gray-500 font-medium whitespace-nowrap">Humano</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((row, i) => (
                <tr key={i} className="border-t border-gray-100 hover:bg-gray-50 transition-colors">
                  <td className="px-3 py-2.5 text-gray-500 font-mono whitespace-nowrap">{row.period_date}</td>
                  <td className="px-3 py-2.5 text-gray-700 font-mono max-w-[200px] truncate" title={row.agent_type_id}>
                    {row.agent_type_id}
                  </td>
                  <td className="px-3 py-2.5 text-gray-500 max-w-[140px] truncate" title={row.pool_id}>
                    {row.pool_id || '—'}
                  </td>
                  <td className="px-3 py-2.5 text-right text-gray-700 font-medium">
                    {row.total_sessions.toLocaleString('pt-BR')}
                  </td>
                  <td className="px-3 py-2.5 text-right text-gray-500">
                    {fmtDuration(row.avg_duration_ms)}
                  </td>
                  <td className="px-3 py-2.5">
                    <RateBar value={row.resolution_rate}  color="#059669" />
                  </td>
                  <td className="px-3 py-2.5">
                    <RateBar value={row.escalation_rate}  color={row.escalation_rate > 0.15 ? '#DC2626' : '#6b7280'} />
                  </td>
                  <td className="px-3 py-2.5">
                    <RateBar value={row.transfer_rate}    color="#2D9CDB" />
                  </td>
                  <td className="px-3 py-2.5">
                    <RateBar value={row.human_rate}       color="#D97706" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

// ── AgentsPage ────────────────────────────────────────────────────────────────

type AgentTab = 'monitor' | 'report'

export default function AgentsPage() {
  const { tenantId } = useAuth()
  const [activeTab, setActiveTab] = useState<AgentTab>('monitor')

  if (!tenantId) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400 text-sm">
        Nenhum tenant selecionado.
      </div>
    )
  }

  const tabs: { id: AgentTab; label: string }[] = [
    { id: 'monitor', label: 'Monitor' },
    { id: 'report',  label: 'Relatório' },
  ]

  return (
    <div className="flex flex-col h-full overflow-hidden bg-[#0a1628]">
      {/* Header + tab bar */}
      <div className="flex-shrink-0 px-4 pt-3 border-b border-slate-800">
        <span className="font-bold text-slate-100 text-base">Agentes</span>
        <div className="flex gap-0 mt-2">
          {tabs.map(t => (
            <button key={t.id} onClick={() => setActiveTab(t.id)}
              className="px-4 py-1.5 text-xs font-medium transition-colors border-b-2"
              style={{
                borderColor: activeTab === t.id ? '#3b82f6' : 'transparent',
                color:       activeTab === t.id ? '#93c5fd' : '#64748b',
              }}>
              {t.label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-hidden">
        {activeTab === 'monitor' && <MonitorSubTab tenantId={tenantId} />}
        {activeTab === 'report'  && <ReportSubTab  tenantId={tenantId} />}
      </div>
    </div>
  )
}
