/**
 * PoolsPage — /contacts/pools
 *
 * Real-time operational view of all pools:
 *   - Capacity: available / busy / paused agents
 *   - Queue: depth + estimated wait time
 *   - SLA target
 *   - Drill-down: contacts currently waiting in the pool's queue
 *
 * Data sources:
 *   GET /v1/operational/pools            → Redis snapshots + Pool config from DB
 *   GET /v1/operational/pools/:id/queue  → ZSET queue entries
 *
 * Auto-refresh every 15 s. Snapshot age shown per row.
 *
 * Also exports PoolStatusCard for reuse in dashboard widgets (Task #35).
 */
import React, { useCallback, useEffect, useState } from 'react'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'

// ── Types ──────────────────────────────────────────────────────────────────────

interface PoolOp {
  pool_id:             string
  description:         string | null
  sla_target_ms:       number
  channel_types:       string[]
  pool_status:         string
  op_status:           'available' | 'queued' | 'empty' | 'unknown'
  available:           number
  queue_length:        number
  estimated_wait_ms:   number | null
  snapshot_age_ms:     number | null
  snapshot_updated_at: string | null
  has_snapshot:        boolean
}

interface QueueEntry {
  position:          number
  session_id:        string
  queued_at:         string
  wait_ms:           number
  estimated_wait_ms: number
}

// ── Helpers ────────────────────────────────────────────────────────────────────

function fmtMs(ms: number | null): string {
  if (ms === null || ms < 0) return '—'
  if (ms < 1000)  return `${ms}ms`
  const s = Math.round(ms / 1000)
  if (s < 60)    return `${s}s`
  const m = Math.floor(s / 60), rem = s % 60
  return `${m}m${rem > 0 ? `${rem}s` : ''}`
}

function fmtAge(ms: number | null): string {
  if (ms === null) return '—'
  const s = Math.floor(ms / 1000)
  if (s < 5)   return 'agora'
  if (s < 60)  return `${s}s atrás`
  const m = Math.floor(s / 60)
  return `${m}min atrás`
}

function statusConfig(op: PoolOp['op_status']) {
  switch (op) {
    case 'available': return { dot: '#22c55e', label: 'Disponível',  bg: '#22c55e18' }
    case 'queued':    return { dot: '#eab308', label: 'Em fila',     bg: '#eab30818' }
    case 'empty':     return { dot: '#ef4444', label: 'Sem agentes', bg: '#ef444418' }
    default:          return { dot: '#6b7280', label: 'Sem dados',   bg: '#6b728018' }
  }
}

// ── API ────────────────────────────────────────────────────────────────────────

async function fetchPools(tenantId: string): Promise<PoolOp[]> {
  const res = await fetch('/v1/operational/pools', {
    headers: { 'x-tenant-id': tenantId },
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const body = await res.json()
  return body.items ?? []
}

async function fetchQueue(tenantId: string, poolId: string): Promise<QueueEntry[]> {
  const res = await fetch(`/v1/operational/pools/${poolId}/queue`, {
    headers: { 'x-tenant-id': tenantId },
  })
  if (!res.ok) return []
  const body = await res.json()
  return body.queue ?? []
}

// ── PoolStatusCard — reusable for dashboard (Task #35) ─────────────────────────

export function PoolStatusCard({ pool, compact = false }: { pool: PoolOp; compact?: boolean }) {
  const st = statusConfig(pool.op_status)
  return (
    <div
      className="rounded-xl border p-4 flex flex-col gap-3"
      style={{ background: '#111827', borderColor: '#1e293b', borderTop: `3px solid ${st.dot}` }}>

      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-sm font-bold text-slate-100 truncate">{pool.pool_id}</div>
          {pool.description && !compact && (
            <div className="text-xs text-slate-500 mt-0.5 truncate">{pool.description}</div>
          )}
        </div>
        <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full whitespace-nowrap flex-shrink-0"
          style={{ background: st.bg, color: st.dot }}>
          {st.label}
        </span>
      </div>

      {/* Metrics grid */}
      <div className="grid grid-cols-3 gap-2">
        <Metric label="Disponíveis" value={pool.available}      color="#22c55e" />
        <Metric label="Em sessão"   value={pool.available === 0 && pool.queue_length === 0 && !pool.has_snapshot ? '—' : (pool.has_snapshot ? String(pool.queue_length > 0 ? '~' : pool.available) : '—')} color="#3b82f6" />
        <Metric label="Fila"        value={pool.queue_length}   color={pool.queue_length > 0 ? '#eab308' : '#6b7280'} />
      </div>

      {/* Wait + SLA */}
      {!compact && (
        <div className="flex gap-4 text-xs text-slate-500">
          <span>Espera est.: <span className="text-slate-300">{fmtMs(pool.estimated_wait_ms)}</span></span>
          <span>SLA: <span className="text-slate-300">{fmtMs(pool.sla_target_ms)}</span></span>
        </div>
      )}

      {/* Channels */}
      {!compact && pool.channel_types.length > 0 && (
        <div className="flex gap-1 flex-wrap">
          {pool.channel_types.map(ch => (
            <span key={ch} className="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-400">{ch}</span>
          ))}
        </div>
      )}
    </div>
  )
}

function Metric({ label, value, color }: { label: string; value: number | string; color: string }) {
  return (
    <div className="flex flex-col items-center rounded-lg py-2" style={{ background: '#0f172a' }}>
      <span className="text-lg font-bold" style={{ color }}>{value}</span>
      <span className="text-[10px] text-slate-500 mt-0.5 text-center leading-tight">{label}</span>
    </div>
  )
}

// ── QueueDrilldown ─────────────────────────────────────────────────────────────

function QueueDrilldown({ tenantId, poolId, slaMs }: { tenantId: string; poolId: string; slaMs: number }) {
  const [queue,   setQueue]   = useState<QueueEntry[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    fetchQueue(tenantId, poolId)
      .then(setQueue)
      .finally(() => setLoading(false))
    const id = setInterval(() => {
      fetchQueue(tenantId, poolId).then(setQueue)
    }, 15_000)
    return () => clearInterval(id)
  }, [tenantId, poolId])

  if (loading) {
    return (
      <div className="flex items-center gap-2 py-4 px-6 text-xs text-slate-500">
        <Spinner /> Carregando fila...
      </div>
    )
  }

  if (queue.length === 0) {
    return (
      <div className="py-4 px-6 text-xs text-slate-500 italic">
        Nenhum contato em fila neste pool.
      </div>
    )
  }

  return (
    <div className="px-6 py-3">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-slate-500 border-b border-slate-800">
            <th className="text-left py-1.5 pr-4 font-medium w-12">#</th>
            <th className="text-left py-1.5 pr-4 font-medium">Sessão</th>
            <th className="text-left py-1.5 pr-4 font-medium">Aguardando</th>
            <th className="text-left py-1.5 font-medium">Espera est. (restante)</th>
          </tr>
        </thead>
        <tbody>
          {queue.map(entry => {
            const overSla = entry.wait_ms > slaMs
            return (
              <tr key={entry.session_id} className="border-b border-slate-800/50">
                <td className="py-2 pr-4 text-slate-500 font-mono">{entry.position}</td>
                <td className="py-2 pr-4 font-mono text-blue-300">{entry.session_id}</td>
                <td className="py-2 pr-4" style={{ color: overSla ? '#ef4444' : '#e2e8f0' }}>
                  {fmtMs(entry.wait_ms)}
                  {overSla && <span className="ml-1.5 text-[10px] bg-red-950 text-red-400 px-1 rounded">SLA excedido</span>}
                </td>
                <td className="py-2 text-slate-400">{fmtMs(entry.estimated_wait_ms)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── PoolsPage ──────────────────────────────────────────────────────────────────

export default function PoolsPage() {
  const { tenantId } = useAuth()

  const [pools,        setPools]        = useState<PoolOp[]>([])
  const [loading,      setLoading]      = useState(false)
  const [error,        setError]        = useState<string | null>(null)
  const [lastRefresh,  setLastRefresh]  = useState<Date | null>(null)
  const [expandedPool, setExpandedPool] = useState<string | null>(null)
  const [filterStatus, setFilterStatus] = useState<string>('')
  const [filterPool,   setFilterPool]   = useState<string>('')

  const load = useCallback(async () => {
    if (!tenantId) return
    setLoading(true); setError(null)
    try {
      const data = await fetchPools(tenantId)
      setPools(data)
      setLastRefresh(new Date())
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

  if (!tenantId) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400 text-sm">
        Nenhum tenant selecionado.
      </div>
    )
  }

  // Filter
  const filtered = pools.filter(p => {
    if (filterStatus && p.op_status !== filterStatus) return false
    if (filterPool   && p.pool_id !== filterPool)     return false
    return true
  })

  // Summary counts
  const totalAvailable = pools.reduce((s, p) => s + p.available, 0)
  const totalQueued    = pools.reduce((s, p) => s + p.queue_length, 0)
  const poolsWithQueue = pools.filter(p => p.queue_length > 0).length

  return (
    <div className="flex flex-col h-full overflow-hidden bg-[#0a1628] text-slate-200">

      {/* Header bar */}
      <div className="flex-shrink-0 px-5 pt-4 pb-3 border-b border-slate-800">
        <div className="flex items-center justify-between mb-3">
          <h1 className="text-base font-bold text-slate-100">Pools</h1>
          <div className="flex items-center gap-3">
            {lastRefresh && (
              <span className="text-[11px] text-slate-600">
                Atualizado {lastRefresh.toLocaleTimeString('pt-BR')}
              </span>
            )}
            {loading
              ? <Spinner />
              : <button onClick={load} className="text-xs text-slate-500 hover:text-slate-300 transition-colors px-2 py-1">↻ Atualizar</button>
            }
          </div>
        </div>

        {/* Summary pills */}
        <div className="flex gap-3 mb-3">
          <SummaryPill label="Agentes disponíveis" value={totalAvailable} color="#22c55e" />
          <SummaryPill label="Contatos em fila"    value={totalQueued}    color={totalQueued > 0 ? '#eab308' : '#6b7280'} />
          <SummaryPill label="Pools com fila"      value={poolsWithQueue} color={poolsWithQueue > 0 ? '#f97316' : '#6b7280'} />
          <SummaryPill label="Total de pools"      value={pools.length}   color="#3b82f6" />
        </div>

        {/* Filters */}
        <div className="flex items-center gap-3">
          <select
            value={filterPool}
            onChange={e => { setFilterPool(e.target.value); setExpandedPool(null) }}
            className="text-xs bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 text-slate-200 focus:outline-none focus:border-slate-500 min-w-[180px]"
          >
            <option value="">Todos os pools</option>
            {pools.map(p => (
              <option key={p.pool_id} value={p.pool_id}>{p.pool_id}</option>
            ))}
          </select>
          <select
            value={filterStatus}
            onChange={e => setFilterStatus(e.target.value)}
            className="text-xs bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 text-slate-200 focus:outline-none focus:border-slate-500"
          >
            <option value="">Todos os status</option>
            <option value="available">Disponível</option>
            <option value="queued">Em fila</option>
            <option value="empty">Sem agentes</option>
          </select>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="mx-5 mt-3 px-3 py-2 bg-red-950 border border-red-800 rounded text-xs text-red-300 flex-shrink-0">
          Erro ao carregar pools: {error}
        </div>
      )}

      {/* Table */}
      <div className="flex-1 overflow-y-auto">
        {filtered.length === 0 && !loading && (
          <div className="flex flex-col items-center justify-center py-16 text-slate-500 gap-2">
            <span className="text-3xl">🏊</span>
            <span className="text-sm">Nenhum pool encontrado</span>
          </div>
        )}

        <table className="w-full text-sm border-collapse">
          <thead className="sticky top-0 z-10" style={{ background: '#0f172a' }}>
            <tr className="text-slate-500 text-xs border-b border-slate-800">
              <th className="text-left px-5 py-3 font-medium">Pool</th>
              <th className="text-center px-3 py-3 font-medium w-28">Status</th>
              <th className="text-center px-3 py-3 font-medium w-24">Disponíveis</th>
              <th className="text-center px-3 py-3 font-medium w-20">Fila</th>
              <th className="text-center px-3 py-3 font-medium w-28">Espera est.</th>
              <th className="text-center px-3 py-3 font-medium w-20">SLA</th>
              <th className="text-left px-3 py-3 font-medium">Canais</th>
              <th className="text-right px-5 py-3 font-medium w-28">Snapshot</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map(pool => {
              const st      = statusConfig(pool.op_status)
              const expanded = expandedPool === pool.pool_id
              return (
                <React.Fragment key={pool.pool_id}>
                  <tr
                    className="border-b border-slate-800/60 cursor-pointer transition-colors"
                    style={{ background: expanded ? '#1e293b' : 'transparent' }}
                    onMouseEnter={e => { if (!expanded) (e.currentTarget as HTMLElement).style.background = '#111827' }}
                    onMouseLeave={e => { if (!expanded) (e.currentTarget as HTMLElement).style.background = 'transparent' }}
                    onClick={() => setExpandedPool(expanded ? null : pool.pool_id)}
                  >
                    {/* Pool name + description */}
                    <td className="px-5 py-3">
                      <div className="flex items-center gap-2">
                        <span className="text-xs" style={{ color: expanded ? '#60a5fa' : undefined }}>
                          {expanded ? '▼' : '▶'}
                        </span>
                        <div>
                          <div className="font-semibold text-slate-100 text-xs">{pool.pool_id}</div>
                          {pool.description && (
                            <div className="text-[11px] text-slate-500 truncate max-w-xs">{pool.description}</div>
                          )}
                        </div>
                      </div>
                    </td>

                    {/* Status */}
                    <td className="px-3 py-3 text-center">
                      <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full"
                        style={{ background: st.bg, color: st.dot }}>
                        {st.label}
                      </span>
                    </td>

                    {/* Available */}
                    <td className="px-3 py-3 text-center">
                      {pool.has_snapshot
                        ? <span className="text-sm font-bold" style={{ color: pool.available > 0 ? '#22c55e' : '#6b7280' }}>{pool.available}</span>
                        : <span className="text-xs text-amber-600" title="Snapshot Redis expirado — aguardando próximo evento de roteamento">⚠ sem dados</span>
                      }
                    </td>

                    {/* Queue */}
                    <td className="px-3 py-3 text-center">
                      {!pool.has_snapshot
                        ? <span className="text-xs text-slate-600">—</span>
                        : pool.queue_length > 0
                          ? <span className="text-sm font-bold text-yellow-400">{pool.queue_length}</span>
                          : <span className="text-xs text-slate-600">0</span>
                      }
                    </td>

                    {/* Estimated wait */}
                    <td className="px-3 py-3 text-center text-xs text-slate-400">
                      {fmtMs(pool.estimated_wait_ms)}
                    </td>

                    {/* SLA */}
                    <td className="px-3 py-3 text-center text-xs text-slate-400">
                      {fmtMs(pool.sla_target_ms)}
                    </td>

                    {/* Channels */}
                    <td className="px-3 py-3">
                      <div className="flex gap-1 flex-wrap">
                        {pool.channel_types.map(ch => (
                          <span key={ch} className="text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-400">{ch}</span>
                        ))}
                      </div>
                    </td>

                    {/* Snapshot age */}
                    <td className="px-5 py-3 text-right">
                      <span className={`text-[11px] ${
                        pool.snapshot_age_ms === null          ? 'text-slate-600'
                        : pool.snapshot_age_ms > 90_000        ? 'text-red-400'
                        : pool.snapshot_age_ms > 30_000        ? 'text-yellow-500'
                        : 'text-slate-500'
                      }`}>
                        {fmtAge(pool.snapshot_age_ms)}
                      </span>
                    </td>
                  </tr>

                  {/* Queue drill-down row */}
                  {expanded && (
                    <tr key={`${pool.pool_id}-queue`} className="border-b border-slate-800">
                      <td colSpan={8} className="p-0" style={{ background: '#0d1b2e' }}>
                        <div className="pl-8 border-l-2 border-blue-800 ml-5 my-1">
                          <div className="px-4 pt-2 pb-1 text-[11px] font-semibold text-slate-500 uppercase tracking-wider">
                            Contatos em fila — {pool.pool_id}
                          </div>
                          <QueueDrilldown
                            tenantId={tenantId}
                            poolId={pool.pool_id}
                            slaMs={pool.sla_target_ms}
                          />
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── SummaryPill ────────────────────────────────────────────────────────────────

function SummaryPill({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-slate-800 bg-slate-900">
      <span className="text-base font-bold" style={{ color }}>{value}</span>
      <span className="text-[11px] text-slate-500">{label}</span>
    </div>
  )
}
