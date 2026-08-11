/**
 * SegmentList
 * Shows all per-agent participation segments within a session.
 * Each contact has no "direct" conversation — conversations happen inside segments.
 * Only active segments (ended_at === null) allow supervisor join.
 */
import React from 'react'
import { Timer, Loader2, CornerDownRight, ExternalLink } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useSessionSegments, useSessionChildren } from '../api/hooks'
import type { SessionChild } from '../api/hooks'
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
  /** S3 — abre uma sessão ORIGINADA por esta (linha "originou"). Ausente = a linha
   *  aparece sem link (a informação de que originou continua valendo). */
  onOpenChild?: (sessionId: string, channel: string) => void
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

        {/* S2 — prosa do wrap-up. Mora AQUI porque é aqui que foi gravada: o
            `segment_outcome_record` escreve por referência no segmento atendido,
            não na sessão de wrap-up que a coletou. */}
        {(segment.wrapup_summary || segment.wrapup_next_steps) && (
          <div className="mt-2 border-l-2 border-border-strong pl-2.5 space-y-0.5">
            {segment.wrapup_summary && (
              <div className="text-xs text-muted">
                <span className="text-muted-light">{t('segments.wrapupSummary')}</span>{' '}
                <span className="text-dark">{segment.wrapup_summary}</span>
              </div>
            )}
            {segment.wrapup_next_steps && (
              <div className="text-xs text-muted">
                <span className="text-muted-light">{t('segments.wrapupNextSteps')}</span>{' '}
                <span className="text-dark">{segment.wrapup_next_steps}</span>
              </div>
            )}
          </div>
        )}
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

// ── Linha "originou" (S3) ─────────────────────────────────────────────────
//
// Uma sessão originada é um EVENTO na vida do contato, não uma lista paralela —
// por isso vive na mesma timeline, ordenada por tempo. O caso que prova a regra é
// o `collect`: num bloco à parte ele viraria "um contato solto que existiu", e a
// informação principal (o atendimento ficou parado esperando por ele) sumiria.
//
// A tag classifica pelos DOIS eixos que o modelo já tem, nesta ordem:
//   is_internal          → interna  (pool interno: wrap-up, dispatch)
//   root ≠ root do pai   → processo (nasceu com `journey: new`, atravessa a fronteira)
//   caso contrário       → contato  (filha de contato dentro da mesma journey)

type ChildKind = 'internal' | 'process' | 'contact'

function childKind(child: SessionChild, parentRoot: string | null): ChildKind {
  if (child.is_internal) return 'internal'
  if (parentRoot && child.root_session_id && child.root_session_id !== parentRoot) return 'process'
  return 'contact'
}

const KIND_CLS: Record<ChildKind, string> = {
  internal: 'border border-border text-muted',
  contact:  'bg-primary-light text-primary',
  process:  'bg-surface-alt text-dark border border-border-strong',
}

function ChildRow({ child, kind, onOpen }: {
  child: SessionChild
  kind:  ChildKind
  onOpen?: () => void
}) {
  const { t } = useTranslation('contacts')
  const dur = child.elapsed_time_ms ?? child.handle_time_ms
  // `journey: new` LINKA, nunca expande: expandir a subárvore desfaria o corte que
  // alguém pediu ao usar `journey: new`.
  const isProcess = kind === 'process'

  return (
    <div className="flex items-start gap-3 px-4 py-2.5 border-b border-border last:border-b-0 bg-surface-muted/40">
      <div className="w-1 self-stretch flex-shrink-0" />
      <div className="flex-1 min-w-0 border-l-2 border-border pl-2.5">
        <div className="flex items-center gap-2 flex-wrap">
          <CornerDownRight className="w-3.5 h-3.5 text-muted-light flex-shrink-0" aria-hidden="true" />
          <span className="text-xs text-muted">{t('segments.spawned')}</span>
          <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${KIND_CLS[kind]}`}>
            {t(`segments.kind.${kind}`)}
          </span>
          {/* Pool primeiro (O QUÊ), spawn_reason depois (POR QUÊ) — sozinho,
              `trigger` nomeia o mecanismo e esconde quem executou. */}
          <span className="text-sm text-dark truncate">
            {child.pool_id?.replace(/_/g, ' ') || t('segments.spawnedUnknown')}
          </span>
          {child.spawn_reason && (
            <span className="text-xs text-muted-light font-mono">{child.spawn_reason}</span>
          )}
          {dur !== null && dur !== undefined && (
            <span className="text-xs text-muted font-mono ml-auto flex-shrink-0 inline-flex items-center gap-0.5">
              <Timer className="w-3 h-3" aria-hidden="true" />{fmtDuration(dur)}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 mt-1 flex-wrap">
          {onOpen ? (
            <button onClick={onOpen}
              className="text-xs font-mono text-primary hover:underline inline-flex items-center gap-1">
              {'…' + child.session_id.slice(-14)}
              {isProcess && <ExternalLink className="w-3 h-3" aria-hidden="true" />}
            </button>
          ) : (
            <span className="text-xs font-mono text-muted-light">{'…' + child.session_id.slice(-14)}</span>
          )}
          {child.outcome && <OutcomeBadge outcome={child.outcome} />}
          {isProcess && (
            <span className="text-xs text-muted-light">{t('segments.otherJourney')}</span>
          )}
        </div>
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

export function SegmentList({ tenantId, sessionId, onSelect, onBack, canJoin = true, showBack = true, onOpenChild }: Props) {
  const { t } = useTranslation('contacts')
  const { segments, loading, error } = useSessionSegments(tenantId, sessionId)
  const { children, error: childErr } = useSessionChildren(tenantId, sessionId)

  const activeCount = segments.filter(s => s.ended_at === null).length

  // A raiz do PAI vem das próprias filhas: elas carregam `origin_session_id` (= esta
  // sessão) e o `root_session_id` delas. A que herdou a journey (`inherit`) tem a raiz
  // do pai; quem nasceu com `journey: new` tem outra. A raiz do pai é, então, a raiz
  // MAIS FREQUENTE entre as filhas — e, no caso de uma filha só, a dela mesma se for
  // interna (interna sempre herda). Sem filha, não há classificação a fazer.
  const parentRoot = React.useMemo<string | null>(() => {
    const tally = new Map<string, number>()
    for (const c of children) {
      if (c.is_internal && c.root_session_id) return c.root_session_id
      if (c.root_session_id) tally.set(c.root_session_id, (tally.get(c.root_session_id) ?? 0) + 1)
    }
    let best: string | null = null; let bestN = 0
    tally.forEach((n, root) => { if (n > bestN) { best = root; bestN = n } })
    return best
  }, [children])

  // Fusão por TEMPO — um eixo só. Segmento entra pelo `started_at`; filha, pelo
  // `opened_at` (o instante em que foi originada).
  type Row =
    | { kind: 'segment'; at: string; seg: ContactSegment }
    | { kind: 'child';   at: string; child: SessionChild }

  const rows: Row[] = React.useMemo(() => {
    const merged: Row[] = [
      ...segments.map(s => ({ kind: 'segment' as const, at: s.started_at, seg: s })),
      ...children.map(c => ({ kind: 'child' as const, at: c.opened_at ?? '', child: c })),
    ]
    return merged.sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : 0))
  }, [segments, children])

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
          {/* Dois domínios, nunca somados (guardrail ADR §7.2, um nível acima):
              segmento é participação DENTRO da sessão; originada é sessão IRMÃ. */}
          {children.length > 0 && (
            <span className="text-xs text-muted-light">· {t('segments.spawnedCount', { count: children.length })}</span>
          )}
        </div>
      </div>

      {/* Falha do fetch de filhas não pode passar por "não originou nada" */}
      {childErr && (
        <div className="px-4 py-1.5 bg-warning-light border-b border-border flex-shrink-0">
          <p className="text-xs text-warning-text">{t('segments.spawnedError', { error: childErr })}</p>
        </div>
      )}

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
        {error || rows.length === 0 || loading ? (
          <Placeholder loading={loading && rows.length === 0} error={error} />
        ) : (
          rows.map(row => (
            row.kind === 'segment' ? (
              <SegmentRow
                key={row.seg.segment_id}
                segment={row.seg}
                onClick={() => onSelect(row.seg)}
              />
            ) : (
              <ChildRow
                key={row.child.session_id}
                child={row.child}
                kind={childKind(row.child, parentRoot)}
                onOpen={onOpenChild
                  ? () => onOpenChild(row.child.session_id, row.child.channel || '')
                  : undefined}
              />
            )
          ))
        )}
      </div>
    </div>
  )
}
