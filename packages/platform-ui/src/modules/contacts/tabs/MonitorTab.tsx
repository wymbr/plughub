/**
 * MonitorTab — visão em tempo real de todos os pools.
 *
 * Scope toggle: Sessões | Processos
 *
 * Sessões:
 *   - SSE via usePoolViews: todos os pools sempre visíveis
 *   - Pools que batem com filters.poolId ficam em destaque; demais ficam em segundo plano
 *   - Seletor de formato de visualização: heatmap | bars | donut | tiles | table
 *   - Clique em pool → painel lateral com sessões ativas → transcript inline
 *
 * Processos:
 *   - Lista de WorkflowInstances com filtro de status
 *   - Painel de detalhe à direita: timeline, suspend reason, resume token
 *   - Link para sessão origem (origin_session_id) disponível no detalhe
 */
import React, { useState, useMemo } from 'react'
import type { ContactFilters, VizFormat } from '../types'
import { usePoolViews } from '@/modules/service/api/hooks'
import { SessionList }      from '@/modules/service/components/SessionList'
import { SegmentList }      from '@/modules/service/components/SegmentList'
import { SessionTranscript } from '@/modules/service/components/SessionTranscript'
import type { PoolView, ContactSegment } from '@/modules/service/types'
import { scoreToColor, scoreToAccent, formatMs } from '@/modules/service/utils/sentiment'
import {
  useWorkflowInstances, useWorkflowInstance, cancelWorkflow,
} from '@/modules/workflows/api/hooks'
import type { WorkflowInstance, WorkflowStatus } from '@/modules/workflows/api/hooks'

interface Props {
  tenantId: string
  filters:  ContactFilters
}

// ── Viz format selector ────────────────────────────────────────────────────

const VIZ_OPTIONS: { id: VizFormat; label: string; icon: string }[] = [
  { id: 'heatmap', label: 'Heatmap',  icon: '🔥' },
  { id: 'bars',    label: 'Barras',   icon: '📊' },
  { id: 'donut',   label: 'Disco',    icon: '🍩' },
  { id: 'tiles',   label: 'Tiles %',  icon: '⬛' },
  { id: 'table',   label: 'Tabela',   icon: '📋' },
]

type DrillLevel = 'pools' | 'sessions' | 'segments' | 'transcript'

// ── Connection pill ────────────────────────────────────────────────────────

function ConnectionPill({ status }: { status: string }) {
  const map: Record<string, { bg: string; text: string; label: string }> = {
    connecting: { bg: '#fef3c7', text: '#92400e', label: 'conectando' },
    connected:  { bg: '#d1fae5', text: '#065f46', label: 'ao vivo' },
    error:      { bg: '#fee2e2', text: '#991b1b', label: 'erro SSE' },
    closed:     { bg: '#f1f5f9', text: '#475569', label: 'fechado'   },
  }
  const c = map[status] ?? map.closed
  return (
    <span className="inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full"
      style={{ backgroundColor: c.bg, color: c.text }}>
      {status === 'connected' && <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse inline-block" />}
      {c.label}
    </span>
  )
}

// ── Pool highlight logic ───────────────────────────────────────────────────

function isHighlighted(pool: PoolView, filters: ContactFilters): boolean {
  if (!filters.poolId && !filters.channel) return true               // no filter → all highlighted
  if (filters.poolId && pool.pool_id !== filters.poolId) return false
  if (filters.channel && !pool.channel_types?.includes(filters.channel)) return false
  return true
}

// ── Individual pool visualizations ────────────────────────────────────────

function PoolHeatmapCard({ pool, highlighted, selected, onClick }: {
  pool: PoolView; highlighted: boolean; selected: boolean; onClick: () => void
}) {
  const bg     = scoreToColor(pool.avg_score)
  const accent = scoreToAccent(pool.avg_score)
  const hl     = highlighted ? 1 : 0.3

  return (
    <div onClick={onClick}
      className="rounded-xl p-4 cursor-pointer transition-all select-none"
      style={{
        background: bg,
        border: selected ? '2px solid #60a5fa' : '2px solid transparent',
        boxShadow: selected ? '0 0 0 3px rgba(96,165,250,0.4)' : '0 2px 8px rgba(0,0,0,0.2)',
        opacity: hl,
        minWidth: 160,
        minHeight: 120,
      }}>
      <div className="font-semibold text-sm truncate mb-2" style={{ color: accent }}>
        {pool.pool_id.replace(/_/g, ' ')}
      </div>
      <div className="flex gap-3 text-xs mt-auto" style={{ color: accent + 'cc' }}>
        <span>✅ {pool.available}</span>
        <span>⏳ {pool.queue_length}</span>
      </div>
      {pool.avg_score !== null && (
        <div className="text-xs mt-1 opacity-70" style={{ color: accent }}>
          sentimento {pool.avg_score > 0 ? '+' : ''}{pool.avg_score.toFixed(2)}
        </div>
      )}
    </div>
  )
}

function PoolBarsCard({ pool, highlighted, selected, onClick }: {
  pool: PoolView; highlighted: boolean; selected: boolean; onClick: () => void
}) {
  const total  = pool.available + pool.queue_length
  const avPct  = total > 0 ? (pool.available / total) * 100 : 0
  const qPct   = total > 0 ? (pool.queue_length / total) * 100 : 0
  const accent = scoreToAccent(pool.avg_score)

  return (
    <div onClick={onClick}
      className="bg-white rounded-xl border-2 p-4 cursor-pointer transition-all select-none hover:shadow-md"
      style={{ borderColor: selected ? '#3b82f6' : '#e5e7eb', opacity: highlighted ? 1 : 0.35 }}>
      <div className="font-semibold text-sm text-gray-700 truncate mb-3">
        {pool.pool_id.replace(/_/g, ' ')}
      </div>

      {/* Available bar */}
      <div className="mb-2">
        <div className="flex justify-between text-xs text-gray-500 mb-0.5">
          <span>Disponíveis</span><span>{pool.available}</span>
        </div>
        <div className="w-full h-2 bg-gray-100 rounded-full overflow-hidden">
          <div className="h-full rounded-full transition-all" style={{ width: `${avPct}%`, backgroundColor: '#059669' }} />
        </div>
      </div>

      {/* Queue bar */}
      <div className="mb-2">
        <div className="flex justify-between text-xs text-gray-500 mb-0.5">
          <span>Na fila</span><span>{pool.queue_length}</span>
        </div>
        <div className="w-full h-2 bg-gray-100 rounded-full overflow-hidden">
          <div className="h-full rounded-full transition-all" style={{ width: `${qPct}%`, backgroundColor: '#f59e0b' }} />
        </div>
      </div>

      {pool.avg_score !== null && (
        <div className="text-xs text-gray-400 mt-1">
          sentimento <span style={{ color: accent }}>{pool.avg_score > 0 ? '+' : ''}{pool.avg_score.toFixed(2)}</span>
        </div>
      )}
    </div>
  )
}

function PoolDonutCard({ pool, highlighted, selected, onClick }: {
  pool: PoolView; highlighted: boolean; selected: boolean; onClick: () => void
}) {
  const total   = pool.available + pool.queue_length
  const avPct   = total > 0 ? pool.available / total : 0
  const r       = 28
  const circ    = 2 * Math.PI * r
  const avDash  = avPct * circ
  const accent  = scoreToAccent(pool.avg_score)

  return (
    <div onClick={onClick}
      className="bg-white rounded-xl border-2 p-4 cursor-pointer transition-all select-none hover:shadow-md flex flex-col items-center"
      style={{ borderColor: selected ? '#3b82f6' : '#e5e7eb', opacity: highlighted ? 1 : 0.35, minWidth: 140 }}>
      <div className="font-semibold text-xs text-gray-600 truncate mb-3 w-full text-center">
        {pool.pool_id.replace(/_/g, ' ')}
      </div>

      {/* SVG donut */}
      <svg width="72" height="72" viewBox="0 0 72 72">
        <circle cx="36" cy="36" r={r} fill="none" stroke="#f3f4f6" strokeWidth="10" />
        <circle cx="36" cy="36" r={r} fill="none"
          stroke="#059669" strokeWidth="10"
          strokeDasharray={`${avDash} ${circ}`}
          strokeDashoffset={circ * 0.25}
          strokeLinecap="round" />
        <text x="36" y="36" textAnchor="middle" dominantBaseline="central"
          fontSize="13" fontWeight="700" fill="#111827">
          {Math.round(avPct * 100)}%
        </text>
      </svg>

      <div className="flex gap-2 mt-2 text-xs text-gray-500">
        <span className="flex items-center gap-0.5">
          <span className="w-2 h-2 rounded-full bg-green-500 inline-block" /> {pool.available}
        </span>
        <span className="flex items-center gap-0.5">
          <span className="w-2 h-2 rounded-full bg-amber-400 inline-block" /> {pool.queue_length}
        </span>
      </div>
    </div>
  )
}

function PoolTileCard({ pool, highlighted, selected, onClick }: {
  pool: PoolView; highlighted: boolean; selected: boolean; onClick: () => void
}) {
  const total  = pool.available + pool.queue_length
  const avPct  = total > 0 ? Math.round((pool.available / total) * 100) : 0
  const color  = avPct >= 50 ? '#059669' : avPct >= 25 ? '#d97706' : '#dc2626'

  return (
    <div onClick={onClick}
      className="rounded-xl border-2 p-5 cursor-pointer transition-all select-none hover:shadow-md flex flex-col items-center justify-center gap-1"
      style={{ borderColor: selected ? '#3b82f6' : '#e5e7eb', opacity: highlighted ? 1 : 0.35,
        backgroundColor: color + '10', minWidth: 130, minHeight: 120 }}>
      <div className="text-4xl font-black tabular-nums" style={{ color }}>
        {avPct}%
      </div>
      <div className="text-xs text-gray-500 text-center truncate w-full">
        {pool.pool_id.replace(/_/g, ' ')}
      </div>
      <div className="text-xs text-gray-400">
        {pool.available} disp · {pool.queue_length} fila
      </div>
    </div>
  )
}

function PoolTableRow({ pool, highlighted, selected, onClick }: {
  pool: PoolView; highlighted: boolean; selected: boolean; onClick: () => void
}) {
  const accent = scoreToAccent(pool.avg_score)
  const bg     = scoreToColor(pool.avg_score)

  return (
    <tr onClick={onClick}
      className="cursor-pointer transition-colors hover:bg-primary/5"
      style={{ opacity: highlighted ? 1 : 0.35, outline: selected ? '2px solid #3b82f6' : 'none' }}>
      <td className="px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="w-3 h-3 rounded-sm inline-block flex-shrink-0" style={{ background: bg }} />
          <span className="text-sm font-medium text-gray-700">{pool.pool_id.replace(/_/g, ' ')}</span>
        </div>
      </td>
      <td className="px-4 py-3 text-center">
        <span className="font-semibold text-green-700 tabular-nums">{pool.available}</span>
      </td>
      <td className="px-4 py-3 text-center">
        <span className="font-semibold text-amber-600 tabular-nums">{pool.queue_length}</span>
      </td>
      <td className="px-4 py-3 text-center text-xs text-gray-500">
        {pool.sla_target_ms ? formatMs(pool.sla_target_ms) : '—'}
      </td>
      <td className="px-4 py-3 text-center">
        {pool.avg_score !== null
          ? <span className="text-xs font-semibold" style={{ color: accent }}>
              {pool.avg_score > 0 ? '+' : ''}{pool.avg_score.toFixed(2)}
            </span>
          : <span className="text-gray-300 text-xs">—</span>}
      </td>
      <td className="px-4 py-3 text-xs text-gray-400 max-w-[140px] truncate">
        {pool.channel_types?.join(', ') ?? '—'}
      </td>
      <td className="px-4 py-3 text-gray-400 text-right">›</td>
    </tr>
  )
}

// ── Processos (workflow instances) view ────────────────────────────────────

type MonitorScope = 'sessions' | 'processes'

const WF_STATUS_COLORS: Record<WorkflowStatus, string> = {
  active:    '#3b82f6',
  suspended: '#eab308',
  completed: '#22c55e',
  failed:    '#ef4444',
  timed_out: '#ef4444',
  cancelled: '#6b7280',
}

const WF_STATUS_LABELS: Record<WorkflowStatus | 'all', string> = {
  all:       'Todos',
  active:    'Ativo',
  suspended: 'Suspenso',
  completed: 'Concluído',
  failed:    'Falhou',
  timed_out: 'Expirado',
  cancelled: 'Cancelado',
}

const WF_SUSPEND_LABELS: Record<string, string> = {
  approval: '⏳ Aprovação pendente',
  input:    '✏️ Aguardando entrada',
  webhook:  '🔗 Aguardando webhook',
  timer:    '⏰ Timer agendado',
}

function ProcessosView({ tenantId }: { tenantId: string }) {
  const [filterStatus, setFilterStatus] = useState<WorkflowStatus | 'all'>('all')
  const [selectedId,   setSelectedId]   = useState<string | null>(null)

  const statusParam = filterStatus === 'all' ? undefined : filterStatus
  const { instances, loading, refresh } = useWorkflowInstances(tenantId, statusParam, 10_000)
  const { instance: detail }            = useWorkflowInstance(selectedId, 10_000)

  const sorted = [...instances].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  )

  async function handleCancel() {
    if (!selectedId) return
    if (!confirm('Cancelar esta instância de processo?')) return
    try {
      await cancelWorkflow(selectedId, tenantId)
      setSelectedId(null)
      refresh()
    } catch (e) { alert(String(e)) }
  }

  return (
    <div className="flex h-full overflow-hidden bg-[#0a1628] text-slate-200">
      {/* Left: list */}
      <div className="w-72 flex-shrink-0 border-r border-slate-800 flex flex-col overflow-hidden">
        {/* Status filter */}
        <div className="flex flex-wrap gap-1.5 px-3 py-2.5 border-b border-slate-800 flex-shrink-0">
          {(['all', 'active', 'suspended', 'completed', 'failed'] as const).map(s => {
            const active = filterStatus === s
            const color  = s === 'all' ? '#3b82f6' : WF_STATUS_COLORS[s as WorkflowStatus]
            return (
              <button key={s} onClick={() => { setFilterStatus(s); setSelectedId(null) }}
                className="text-xs px-2.5 py-1 rounded-md font-medium transition-all"
                style={{
                  border:  `1px solid ${active ? color : '#334155'}`,
                  background: active ? color + '22' : 'transparent',
                  color:   active ? color : '#64748b',
                  fontWeight: active ? 600 : 400,
                }}>
                {WF_STATUS_LABELS[s]}
              </button>
            )
          })}
        </div>

        {/* Instance list */}
        <div className="flex-1 overflow-y-auto">
          {sorted.length === 0 && !loading && (
            <div className="flex flex-col items-center justify-center py-12 text-slate-500 text-sm gap-2">
              <span className="text-2xl">📋</span>
              <span>Nenhum processo encontrado</span>
            </div>
          )}
          {sorted.map(inst => {
            const color    = WF_STATUS_COLORS[inst.status]
            const isSelected = inst.id === selectedId
            return (
              <div key={inst.id} onClick={() => setSelectedId(inst.id === selectedId ? null : inst.id)}
                className="px-4 py-3 cursor-pointer transition-colors hover:bg-slate-800/50"
                style={{
                  borderBottom: '1px solid #1e293b',
                  background:   isSelected ? '#1e293b' : 'transparent',
                  borderLeft:   isSelected ? `3px solid ${color}` : '3px solid transparent',
                }}>
                <div className="flex justify-between items-start gap-2">
                  <div className="flex-1 min-w-0">
                    <code className="text-xs font-semibold text-blue-300">{inst.id.slice(0, 8)}…</code>
                    <div className="text-xs text-slate-500 mt-0.5 truncate">{inst.flow_id}</div>
                    {inst.origin_session_id && (
                      <div className="text-xs text-slate-600 mt-0.5 truncate font-mono">
                        sessão: …{inst.origin_session_id.slice(-10)}
                      </div>
                    )}
                  </div>
                  <span className="text-[11px] font-bold px-1.5 py-0.5 rounded flex-shrink-0"
                    style={{ background: color + '33', color }}>
                    {WF_STATUS_LABELS[inst.status] ?? inst.status}
                  </span>
                </div>
                <div className="text-[11px] text-slate-500 mt-1.5">
                  {new Date(inst.created_at).toLocaleString('pt-BR')}
                </div>
                {inst.suspend_reason && (
                  <div className="text-[11px] text-yellow-400 mt-1">
                    {WF_SUSPEND_LABELS[inst.suspend_reason] ?? inst.suspend_reason}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>

      {/* Right: detail or empty */}
      {detail ? (
        <div className="flex-1 flex flex-col overflow-hidden">
          {/* Detail header */}
          <div className="flex justify-between items-start px-5 py-3.5 border-b border-slate-800 flex-shrink-0">
            <div>
              <code className="text-xs text-blue-300">{detail.id}</code>
              <div className="text-xs text-slate-500 mt-0.5">{detail.flow_id}</div>
            </div>
            <button onClick={() => setSelectedId(null)}
              className="text-slate-500 hover:text-slate-200 text-lg leading-none">✕</button>
          </div>

          {/* Scrollable detail body */}
          <div className="flex-1 overflow-y-auto p-5 space-y-5">

            {/* Status */}
            <div>
              <div className="text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-2">Status</div>
              <span className="text-xs font-bold px-2.5 py-1 rounded"
                style={{ background: WF_STATUS_COLORS[detail.status] + '33', color: WF_STATUS_COLORS[detail.status] }}>
                {WF_STATUS_LABELS[detail.status] ?? detail.status}
              </span>
              {detail.current_step && (
                <div className="mt-2 text-xs text-slate-400">
                  Step atual: <code className="text-slate-200">{detail.current_step}</code>
                </div>
              )}
              {detail.outcome && (
                <div className="mt-1 text-xs text-slate-400">
                  Outcome: <code className="text-slate-200">{detail.outcome}</code>
                </div>
              )}
            </div>

            {/* Timeline */}
            <div>
              <div className="text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-2">Timeline</div>
              <div className="space-y-1.5">
                {[
                  { dot: '#22c55e', label: 'Criado',    ts: detail.created_at },
                  detail.suspended_at ? { dot: '#eab308', label: 'Suspenso',   ts: detail.suspended_at } : null,
                  detail.resumed_at   ? { dot: '#3b82f6', label: 'Retomado',   ts: detail.resumed_at   } : null,
                  detail.completed_at ? { dot: '#22c55e', label: 'Concluído',  ts: detail.completed_at } : null,
                ].filter(Boolean).map((entry, i) => (
                  <div key={i} className="flex items-center gap-2 text-xs">
                    <span className="w-2 h-2 rounded-full flex-shrink-0"
                      style={{ backgroundColor: entry!.dot }} />
                    <span className="text-slate-400 w-20 flex-shrink-0">{entry!.label}</span>
                    <span className="text-slate-500">{new Date(entry!.ts).toLocaleString('pt-BR')}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Suspend reason */}
            {detail.suspend_reason && (
              <div>
                <div className="text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-2">Motivo da suspensão</div>
                <span className="text-xs px-2.5 py-1 rounded border border-yellow-900/60 bg-yellow-900/20 text-yellow-300">
                  {WF_SUSPEND_LABELS[detail.suspend_reason] ?? detail.suspend_reason}
                </span>
              </div>
            )}

            {/* Resume token */}
            {detail.resume_token && (
              <div>
                <div className="text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-2">Token de retomada</div>
                <div
                  className="bg-slate-900 border border-slate-700 rounded px-3 py-2 text-[11px] font-mono text-slate-400 break-all cursor-pointer hover:border-slate-500"
                  onClick={() => void navigator.clipboard.writeText(detail.resume_token!)}
                  title="Clique para copiar">
                  {detail.resume_token}
                </div>
                {detail.resume_expires_at && (
                  <div className="mt-1 text-[11px] text-slate-600">
                    Expira em: {new Date(detail.resume_expires_at).toLocaleString('pt-BR')}
                  </div>
                )}
              </div>
            )}

            {/* Origin session link */}
            {detail.origin_session_id && (
              <div>
                <div className="text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-2">Sessão de origem</div>
                <code className="text-xs text-blue-400 font-mono">{detail.origin_session_id}</code>
              </div>
            )}
          </div>

          {/* Cancel button */}
          {['active', 'suspended'].includes(detail.status) && (
            <div className="px-4 py-3 border-t border-slate-800 flex-shrink-0">
              <button onClick={handleCancel}
                className="w-full py-2 rounded border border-red-800 bg-red-950 text-red-300 text-sm font-semibold hover:bg-red-900 transition-colors">
                Cancelar processo
              </button>
            </div>
          )}
        </div>
      ) : (
        <div className="flex-1 flex flex-col items-center justify-center text-slate-500">
          <div className="text-4xl mb-3">⚙️</div>
          <div className="text-sm">Selecione um processo para ver detalhes</div>
        </div>
      )}
    </div>
  )
}

// ── Main MonitorTab ────────────────────────────────────────────────────────

export function MonitorTab({ tenantId, filters }: Props) {
  const [scope, setScope] = useState<MonitorScope>('sessions')
  const { pools, status } = usePoolViews(tenantId)
  const [vizFormat, setVizFormat] = useState<VizFormat>('bars')
  const [drillLevel, setDrillLevel] = useState<DrillLevel>('pools')
  const [selectedPool, setSelectedPool] = useState<string | null>(null)
  const [selectedSession, setSelectedSession] = useState<string | null>(null)
  const [selectedSegment, setSelectedSegment] = useState<ContactSegment | null>(null)

  // Sort: highlighted first, then by pool_id alphabetically
  const sortedPools = useMemo(() => {
    return [...pools].sort((a, b) => {
      const ha = isHighlighted(a, filters)
      const hb = isHighlighted(b, filters)
      if (ha && !hb) return -1
      if (!ha && hb) return 1
      return a.pool_id.localeCompare(b.pool_id)
    })
  }, [pools, filters])

  const highlightedCount = useMemo(() => sortedPools.filter(p => isHighlighted(p, filters)).length, [sortedPools, filters])

  function handlePoolClick(poolId: string) {
    setSelectedPool(poolId)
    setSelectedSession(null)
    setSelectedSegment(null)
    setDrillLevel('sessions')
  }

  // Session selected → go to segment list (contacts don't have direct conversations)
  function handleSessionSelect(sid: string) {
    setSelectedSession(sid)
    setSelectedSegment(null)
    setDrillLevel('segments')
  }

  // Segment selected → show transcript for that segment
  function handleSegmentSelect(segment: ContactSegment) {
    setSelectedSegment(segment)
    setDrillLevel('transcript')
  }

  function goBackToPools() {
    setDrillLevel('pools')
    setSelectedPool(null)
    setSelectedSession(null)
    setSelectedSegment(null)
  }

  function goBackToSessions() {
    setDrillLevel('sessions')
    setSelectedSession(null)
    setSelectedSegment(null)
  }

  function goBackToSegments() {
    setDrillLevel('segments')
    setSelectedSegment(null)
  }

  const isDark = vizFormat === 'heatmap' && scope === 'sessions'

  return (
    <div className="flex flex-col h-full overflow-hidden" style={{ backgroundColor: isDark ? '#0f172a' : '#f8fafc' }}>

      {/* ── Scope toggle + toolbar ────────────────────────────────────── */}
      <div className="flex items-center gap-3 px-4 py-2.5 flex-shrink-0 border-b"
        style={{ backgroundColor: isDark ? '#0a1628' : '#ffffff', borderColor: isDark ? '#1e293b' : '#e5e7eb' }}>

        {/* Scope selector: Sessões | Processos */}
        <div className="flex items-center gap-0.5 bg-gray-100 rounded-lg p-0.5 flex-shrink-0">
          {([
            { id: 'sessions' as MonitorScope, label: 'Sessões',  icon: '📡' },
            { id: 'processes' as MonitorScope, label: 'Processos', icon: '⚙️' },
          ]).map(opt => (
            <button key={opt.id} onClick={() => setScope(opt.id)}
              className="flex items-center gap-1 px-3 py-1 rounded-md text-xs font-medium transition-all"
              style={{
                backgroundColor: scope === opt.id ? '#fff' : 'transparent',
                color:           scope === opt.id ? '#1B4F8A' : '#6b7280',
                boxShadow:       scope === opt.id ? '0 1px 3px rgba(0,0,0,0.12)' : 'none',
              }}>
              <span>{opt.icon}</span>
              <span>{opt.label}</span>
            </button>
          ))}
        </div>

        {scope === 'sessions' && (
          <>
            {/* Viz format selector */}
            <div className="flex items-center gap-0.5 bg-gray-100 rounded-lg p-0.5">
              {VIZ_OPTIONS.map(opt => (
                <button key={opt.id} onClick={() => setVizFormat(opt.id)} title={opt.label}
                  className="flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium transition-all"
                  style={{
                    backgroundColor: vizFormat === opt.id ? '#fff' : 'transparent',
                    color:           vizFormat === opt.id ? '#1B4F8A' : '#6b7280',
                    boxShadow:       vizFormat === opt.id ? '0 1px 3px rgba(0,0,0,0.12)' : 'none',
                  }}>
                  <span>{opt.icon}</span>
                  <span className="hidden sm:inline">{opt.label}</span>
                </button>
              ))}
            </div>

            <ConnectionPill status={status} />

            {/* Filter context label */}
            {(filters.poolId || filters.channel) && (
              <span className="text-xs px-2 py-0.5 rounded-full bg-primary/10 text-primary font-medium">
                {highlightedCount} de {sortedPools.length} em destaque
              </span>
            )}

            {/* Breadcrumb for drill-down */}
            {drillLevel !== 'pools' && (
              <div className="flex items-center gap-1 text-xs ml-auto flex-wrap" style={{ color: isDark ? '#94a3b8' : '#6b7280' }}>
                <button onClick={goBackToPools} className="hover:underline">Pools</button>
                <span className="mx-0.5">/</span>
                {drillLevel === 'sessions'
                  ? <span className="font-semibold" style={{ color: isDark ? '#e2e8f0' : '#111827' }}>
                      {selectedPool?.replace(/_/g, ' ')}
                    </span>
                  : <button onClick={goBackToSessions} className="hover:underline">
                      {selectedPool?.replace(/_/g, ' ')}
                    </button>
                }
                {(drillLevel === 'segments' || drillLevel === 'transcript') && selectedSession && (
                  <>
                    <span className="mx-0.5">/</span>
                    {drillLevel === 'segments'
                      ? <span className="font-semibold font-mono text-[11px]" style={{ color: isDark ? '#e2e8f0' : '#111827' }}>
                          …{selectedSession.slice(-10)}
                        </span>
                      : <button onClick={goBackToSegments} className="hover:underline font-mono text-[11px]">
                          …{selectedSession.slice(-10)}
                        </button>
                    }
                  </>
                )}
                {drillLevel === 'transcript' && selectedSegment && (
                  <>
                    <span className="mx-0.5">/</span>
                    <span className="font-semibold" style={{ color: isDark ? '#e2e8f0' : '#111827' }}>
                      {selectedSegment.role}
                      {selectedSegment.ended_at === null && (
                        <span className="ml-1 text-green-600">●</span>
                      )}
                    </span>
                  </>
                )}
              </div>
            )}
          </>
        )}
      </div>

      {/* ── Scope content ─────────────────────────────────────────────── */}
      {scope === 'processes' && (
        <div className="flex-1 overflow-hidden">
          <ProcessosView tenantId={tenantId} />
        </div>
      )}

      {scope === 'sessions' && (
      <div className="flex flex-1 overflow-hidden">

      {/* ── Left: pool grid ─────────────────────────────────────────── */}
      <div className="flex flex-col flex-1 overflow-hidden">

        {/* Pool grid or drill-down */}
        <div className="flex-1 overflow-auto p-4">

          {drillLevel === 'pools' && (
            <>
              {sortedPools.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-full gap-3"
                  style={{ color: isDark ? '#64748b' : '#9ca3af' }}>
                  <span className="text-4xl">📡</span>
                  <span className="text-sm">Aguardando dados dos pools…</span>
                  <span className="text-xs opacity-60">Snapshots chegam a cada 5s. Verifique o Routing Engine.</span>
                </div>
              ) : vizFormat === 'table' ? (
                <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
                  <table className="w-full text-sm">
                    <thead className="bg-gray-50 border-b border-gray-200">
                      <tr>
                        {['Pool','Disponíveis','Na fila','SLA alvo','Sentimento','Canais',''].map(col => (
                          <th key={col} className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wide px-4 py-2.5 whitespace-nowrap">
                            {col}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {sortedPools.map(pool => (
                        <PoolTableRow key={pool.pool_id} pool={pool}
                          highlighted={isHighlighted(pool, filters)}
                          selected={selectedPool === pool.pool_id}
                          onClick={() => handlePoolClick(pool.pool_id)} />
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="flex flex-wrap gap-4">
                  {sortedPools.map(pool => {
                    const hl  = isHighlighted(pool, filters)
                    const sel = selectedPool === pool.pool_id
                    const commonProps = { pool, highlighted: hl, selected: sel, onClick: () => handlePoolClick(pool.pool_id) }
                    return (
                      <React.Fragment key={pool.pool_id}>
                        {vizFormat === 'heatmap' && <PoolHeatmapCard {...commonProps} />}
                        {vizFormat === 'bars'    && <PoolBarsCard    {...commonProps} />}
                        {vizFormat === 'donut'   && <PoolDonutCard   {...commonProps} />}
                        {vizFormat === 'tiles'   && <PoolTileCard    {...commonProps} />}
                      </React.Fragment>
                    )
                  })}
                </div>
              )}
            </>
          )}

          {drillLevel === 'sessions' && selectedPool && (
            <div style={{ height: '100%' }}>
              <SessionList
                tenantId={tenantId}
                poolId={selectedPool}
                onSelect={handleSessionSelect}
                onBack={goBackToPools}
              />
            </div>
          )}

          {drillLevel === 'segments' && selectedSession && (
            <div style={{ height: '100%' }}>
              <SegmentList
                tenantId={tenantId}
                sessionId={selectedSession}
                onSelect={handleSegmentSelect}
                onBack={goBackToSessions}
              />
            </div>
          )}

          {drillLevel === 'transcript' && selectedSession && (
            <div style={{ height: '100%', backgroundColor: '#0f172a', borderRadius: 12, overflow: 'hidden' }}>
              <SessionTranscript
                tenantId={tenantId}
                sessionId={selectedSession}
                onBack={goBackToSegments}
                canJoin={selectedSegment?.ended_at === null}
                segment={selectedSegment ?? undefined}
              />
            </div>
          )}
        </div>
      </div>
      </div>
      )}

    </div>
  )
}
