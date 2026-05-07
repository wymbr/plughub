/**
 * SegmentList
 * Shows all per-agent participation segments within a session.
 * Each contact has no "direct" conversation — conversations happen inside segments.
 * Only active segments (ended_at === null) allow supervisor join.
 */
import React from 'react'
import { useSessionSegments } from '../api/hooks'
import type { ContactSegment, SegmentRole } from '../types'

interface Props {
  tenantId:  string
  sessionId: string
  onSelect:  (segment: ContactSegment) => void
  onBack:    () => void
  canJoin?:  boolean
}

// ── Role badge ─────────────────────────────────────────────────────────────

const ROLE_COLORS: Record<SegmentRole, { bg: string; text: string }> = {
  primary:    { bg: '#ede9fe', text: '#5b21b6' },
  specialist: { bg: '#fce7f3', text: '#9d174d' },
  supervisor: { bg: '#fef3c7', text: '#92400e' },
  evaluator:  { bg: '#d1fae5', text: '#065f46' },
  reviewer:   { bg: '#dbeafe', text: '#1e40af' },
}

function RoleBadge({ role }: { role: SegmentRole }) {
  const c = ROLE_COLORS[role] ?? { bg: '#f3f4f6', text: '#374151' }
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold"
      style={{ backgroundColor: c.bg, color: c.text }}>
      {role}
    </span>
  )
}

// ── Duration formatter ─────────────────────────────────────────────────────

function fmtDuration(ms: number | null): string {
  if (ms === null || ms < 0) return '—'
  const s = Math.floor(ms / 1000)
  if (s < 60)   return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60)   return `${m}m ${s % 60}s`
  const h = Math.floor(m / 60)
  return `${h}h ${m % 60}m`
}

function fmtTime(iso: string): string {
  try { return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) }
  catch { return iso }
}

// ── Single segment row ─────────────────────────────────────────────────────

function SegmentRow({
  segment,
  onClick,
}: {
  segment: ContactSegment
  onClick: () => void
}) {
  const isActive  = segment.ended_at === null
  const agentLabel = segment.agent_type_id.replace(/_/g, ' ').replace(/\bv\d+$/, '').trim()

  return (
    <div
      onClick={onClick}
      className="flex items-start gap-3 px-4 py-3 hover:bg-gray-50 cursor-pointer border-b border-gray-100 transition-colors last:border-b-0"
    >
      {/* Active indicator stripe */}
      <div className={`w-1 self-stretch rounded-full flex-shrink-0 mt-0.5 ${isActive ? 'bg-green-500' : 'bg-gray-200'}`} />

      {/* Content */}
      <div className="flex-1 min-w-0">
        {/* Row 1: role + agent type + active badge */}
        <div className="flex items-center gap-2 flex-wrap mb-1">
          <RoleBadge role={segment.role} />
          <span className="text-sm font-medium text-gray-800 truncate">{agentLabel}</span>
          <span className={`text-xs font-semibold px-1.5 py-0.5 rounded-full ml-auto flex-shrink-0 ${
            isActive
              ? 'bg-green-100 text-green-700'
              : 'bg-gray-100 text-gray-500'
          }`}>
            {isActive ? '● ao vivo' : 'encerrado'}
          </span>
        </div>

        {/* Row 2: timing */}
        <div className="flex items-center gap-3 text-xs text-gray-500">
          <span>⏰ {fmtTime(segment.started_at)}</span>
          {!isActive && segment.ended_at && (
            <span>→ {fmtTime(segment.ended_at)}</span>
          )}
          {segment.duration_ms !== null && (
            <span className="font-mono">⏱ {fmtDuration(segment.duration_ms)}</span>
          )}
          {isActive && (
            <span className="text-green-600 animate-pulse">em andamento</span>
          )}
        </div>

        {/* Row 3: outcome + sequence + parent */}
        <div className="flex items-center gap-2 mt-0.5 flex-wrap">
          {segment.outcome && (
            <span className="text-xs px-1.5 py-0.5 rounded bg-blue-50 text-blue-700">
              {segment.outcome}
            </span>
          )}
          {segment.sequence_index > 0 && (
            <span className="text-xs text-gray-400">handoff #{segment.sequence_index}</span>
          )}
          {segment.parent_segment_id && (
            <span className="text-xs text-gray-400">
              ↳ especialista
            </span>
          )}
          {segment.agent_type === 'human' && (
            <span className="text-xs px-1.5 py-0.5 rounded bg-amber-50 text-amber-700">humano</span>
          )}
        </div>
      </div>

      {/* Right: join indicator */}
      <div className="flex-shrink-0 flex flex-col items-end gap-1">
        {isActive ? (
          <span className="text-xs font-semibold text-indigo-600 flex items-center gap-1">
            <span>👁</span> entrar
          </span>
        ) : (
          <span className="text-xs text-gray-300">›</span>
        )}
      </div>
    </div>
  )
}

// ── Empty / loading / error states ────────────────────────────────────────

function Placeholder({ loading, error }: { loading: boolean; error: string | null }) {
  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 text-gray-400 py-16">
        <span className="text-2xl animate-spin">⏳</span>
        <span className="text-sm">Carregando segmentos…</span>
      </div>
    )
  }
  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 py-16 px-4">
        <span className="text-3xl">⚠️</span>
        <span className="text-sm text-red-600 font-medium text-center">Erro ao carregar segmentos</span>
        <span className="text-xs text-red-400 text-center">{error}</span>
        <span className="text-xs text-gray-400 text-center mt-1">Verifique se o analytics-api está em execução.</span>
      </div>
    )
  }
  return (
    <div className="flex flex-col items-center justify-center h-full gap-3 text-gray-400 py-16">
      <span className="text-3xl">📭</span>
      <span className="text-sm">Nenhum segmento encontrado para esta sessão.</span>
      <span className="text-xs opacity-60">Os segmentos aparecem quando agentes entram na sessão.</span>
    </div>
  )
}

// ── SegmentList (main) ─────────────────────────────────────────────────────

export function SegmentList({ tenantId, sessionId, onSelect, onBack, canJoin = true }: Props) {
  const { segments, loading, error } = useSessionSegments(tenantId, sessionId)

  const activeCount = segments.filter(s => s.ended_at === null).length

  return (
    <div className="flex flex-col h-full overflow-hidden bg-white rounded-xl border border-gray-200">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-gray-200 flex-shrink-0 bg-gray-50">
        <button
          onClick={onBack}
          className="text-xs text-gray-500 hover:text-gray-700 border border-gray-200 rounded px-2 py-1 bg-white transition-colors"
        >
          ← Contatos
        </button>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-gray-800">Segmentos</p>
          <p className="text-xs text-gray-500 font-mono truncate">
            sessão …{sessionId.slice(-12)}
          </p>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          {activeCount > 0 && (
            <span className="text-xs bg-green-100 text-green-700 font-semibold px-2 py-0.5 rounded-full">
              {activeCount} ativo{activeCount !== 1 ? 's' : ''}
            </span>
          )}
          <span className="text-xs text-gray-400">{segments.length} total</span>
        </div>
      </div>

      {/* Info note */}
      {segments.length > 0 && (
        <div className="px-4 py-2 bg-indigo-50 border-b border-indigo-100 flex-shrink-0">
          <p className="text-xs text-indigo-600">
            💡 Cada segmento representa a participação de um agente. Clique num segmento <strong>ao vivo</strong> para entrar como supervisor.
          </p>
        </div>
      )}

      {/* List */}
      <div className="flex-1 overflow-y-auto">
        {error || segments.length === 0 || loading ? (
          <Placeholder loading={loading && segments.length === 0} error={error} />
        ) : (
          segments.map(seg => (
            <SegmentRow
              key={seg.segment_id}
              segment={seg}
              onClick={() => onSelect(seg)}
            />
          ))
        )}
      </div>
    </div>
  )
}
