/**
 * SegmentList
 * Shows all per-agent participation segments within a session.
 * Each contact has no "direct" conversation — conversations happen inside segments.
 * Only active segments (ended_at === null) allow supervisor join.
 */
import React from 'react'
import { Timer, Loader2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useSessionSegments } from '../api/hooks'
import type { ContactSegment, SegmentRole } from '../types'

interface Props {
  tenantId:  string
  sessionId: string
  onSelect:  (segment: ContactSegment) => void
  onBack:    () => void
  canJoin?:  boolean
  /** When false, the built-in back button in the header is hidden.
   *  Use when the parent renders its own breadcrumb navigation. */
  showBack?: boolean
}

// ── Outcome badge ──────────────────────────────────────────────────────────

const OUTCOME_COLORS: Record<string, { bg: string; text: string }> = {
  resolved:        { bg: '#d1fae5', text: '#065f46' },
  escalated:       { bg: '#fef3c7', text: '#92400e' },
  escalated_human: { bg: '#fef3c7', text: '#92400e' },
  transferred:     { bg: '#dbeafe', text: '#1e40af' },
  abandoned:       { bg: '#fee2e2', text: '#991b1b' },
  timeout:         { bg: '#f3e8ff', text: '#6b21a8' },
}

function OutcomeBadge({ outcome }: { outcome: string }) {
  const c = OUTCOME_COLORS[outcome] ?? { bg: '#f3f4f6', text: '#374151' }
  return (
    <span className="text-xs px-1.5 py-0.5 rounded font-medium"
      style={{ backgroundColor: c.bg, color: c.text }}>
      {outcome}
    </span>
  )
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
  const { t } = useTranslation('contacts')
  const isActive  = segment.ended_at === null
  // C1 — human segments are identified by the login (email), not the synthetic
  // agent_type_id (human_agent_{pool}). AI segments keep the skill-derived label.
  const agentLabel = segment.agent_type === 'human' && segment.user_login
    ? segment.user_login
    : segment.agent_type_id.replace(/_/g, ' ').replace(/\bv\d+$/, '').trim()

  return (
    <div
      onClick={onClick}
      className="flex items-start gap-3 px-4 py-3 hover:bg-surface-muted cursor-pointer border-b border-border transition-colors last:border-b-0"
    >
      {/* Active indicator stripe */}
      <div className={`w-1 self-stretch rounded-full flex-shrink-0 mt-0.5 ${isActive ? 'bg-green' : 'bg-border'}`} />

      {/* Content */}
      <div className="flex-1 min-w-0">
        {/* Row 1: role + agent name + human indicator + active badge */}
        <div className="flex items-center gap-2 flex-wrap mb-1">
          <RoleBadge role={segment.role} />
          <span className="text-sm font-medium text-dark truncate">{agentLabel}</span>
          {segment.agent_type === 'human' && (
            <span className="text-xs px-1.5 py-0.5 rounded bg-warning-light text-warning-text font-medium flex-shrink-0">
              👤 {t('segments.human')}
            </span>
          )}
          <span className={`text-xs font-semibold px-1.5 py-0.5 rounded-full ml-auto flex-shrink-0 ${
            isActive
              ? 'bg-green-light text-green-text'
              : 'bg-surface-alt text-muted'
          }`}>
            {isActive ? t('segments.live') : t('segments.closed')}
          </span>
        </div>

        {/* Row 2: timing */}
        <div className="flex items-center gap-3 text-xs text-muted">
          <span>⏰ {fmtTime(segment.started_at)}</span>
          {!isActive && segment.ended_at && (
            <span>→ {fmtTime(segment.ended_at)}</span>
          )}
          {segment.duration_ms !== null && (
            <span className="font-mono inline-flex items-center gap-0.5"><Timer className="w-3 h-3" aria-hidden="true" />{fmtDuration(segment.duration_ms)}</span>
          )}
          {isActive && (
            <span className="text-green-text animate-pulse">{t('segments.inProgress')}</span>
          )}
        </div>

        {/* Row 3: outcome + sequence + parent */}
        <div className="flex items-center gap-2 mt-0.5 flex-wrap">
          {segment.outcome && (
            <OutcomeBadge outcome={segment.outcome} />
          )}
          {segment.sequence_index > 0 && (
            <span className="text-xs text-muted-light">{t('segments.handoff', { index: segment.sequence_index })}</span>
          )}
          {segment.parent_segment_id && (
            <span className="text-xs text-muted-light">{t('segments.specialist')}</span>
          )}
        </div>
      </div>

      {/* Right: join indicator */}
      <div className="flex-shrink-0 flex flex-col items-end gap-1">
        {isActive ? (
          <span className="text-xs font-semibold text-primary flex items-center gap-1">
            {t('segments.join')}
          </span>
        ) : (
          <span className="text-xs text-border-strong">›</span>
        )}
      </div>
    </div>
  )
}

// ── Empty / loading / error states ────────────────────────────────────────

function Placeholder({ loading, error }: { loading: boolean; error: string | null }) {
  const { t } = useTranslation('contacts')
  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 text-muted-light py-16">
        <Loader2 className="w-6 h-6 animate-spin text-muted-light" aria-hidden="true" />
        <span className="text-sm">{t('segments.loading')}</span>
      </div>
    )
  }
  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 py-16 px-4">
        <span className="text-3xl">⚠️</span>
        <span className="text-sm text-red-text font-medium text-center">{t('segments.errorTitle')}</span>
        <span className="text-xs text-red text-center">{error}</span>
        <span className="text-xs text-muted-light text-center mt-1">{t('segments.errorHint')}</span>
      </div>
    )
  }
  return (
    <div className="flex flex-col items-center justify-center h-full gap-3 text-muted-light py-16">
      <span className="text-3xl">📭</span>
      <span className="text-sm">{t('segments.empty')}</span>
      <span className="text-xs opacity-60">{t('segments.emptyHint')}</span>
    </div>
  )
}

// ── SegmentList (main) ─────────────────────────────────────────────────────

export function SegmentList({ tenantId, sessionId, onSelect, onBack, canJoin = true, showBack = true }: Props) {
  const { t } = useTranslation('contacts')
  const { segments, loading, error } = useSessionSegments(tenantId, sessionId)

  const activeCount = segments.filter(s => s.ended_at === null).length

  return (
    <div className="flex flex-col h-full overflow-hidden bg-white rounded-xl border border-border">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-border flex-shrink-0 bg-surface-muted sticky top-0 z-10">
        {showBack && (
          <button
            onClick={onBack}
            className="text-xs text-muted hover:text-dark border border-border rounded px-2 py-1 bg-white transition-colors"
          >
            {t('detail.back')}
          </button>
        )}
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-dark">{t('segments.title')}</p>
          <p className="text-xs text-muted font-mono truncate">
            {t('segments.session', { id: sessionId.slice(-12) })}
          </p>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          {activeCount > 0 && (
            <span className="text-xs bg-green-light text-green-text font-semibold px-2 py-0.5 rounded-full">
              {t('segments.active', { count: activeCount })}
            </span>
          )}
          <span className="text-xs text-muted-light">{t('segments.total', { count: segments.length })}</span>
        </div>
      </div>

      {/* Info note */}
      {segments.length > 0 && (
        <div className="px-4 py-2 bg-primary-light border-b border-primary/20 flex-shrink-0">
          <p className="text-xs text-primary">
            💡 {t('segments.hint')}
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
