/**
 * AvaliacoesPage — /evaluation/avaliacoes
 *
 * Unified evaluation view: replaces ReviewPage + MyEvaluationsPage.
 *
 * Pattern: ContactsPage — filter bar + table + right drill-down panel.
 *
 * Filter bar (always visible):
 *   Status, "Aguardando minha ação" quick-filter, Campaign, Evaluator.
 *
 * available_actions comes from the server (Bearer JWT → ABAC) — never computed locally.
 */
import React, { useState, useEffect, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import {
  useResults,
  useCampaigns,
  useCampaignSummaries,
  type CampaignSummary,
  useContestations,
  reviewResult,
  createContestation,
  adjudicateContestation,
  // Arc 13
  useContestationThreads,
  submitHumanReview,
  submitDimensionContestation,
} from '@/api/evaluation-hooks'
import type {
  EvaluationResultWithActions,
  EvaluationCriterionResponse,
  EvaluationContestation,
  EvaluationCampaign,
  // Arc 13
  ContestationThread,
} from '@/types'


// ── Shared helpers ─────────────────────────────────────────────────────────────

function ScorePill({ score }: { score: number | string | null | undefined }) {
  // ClickHouse/NUMERIC pode chegar como string; coagimos e protegemos contra null/NaN.
  const s = Number(score)
  if (!Number.isFinite(s)) {
    return <span className="px-2 py-0.5 rounded text-sm text-muted-light">—</span>
  }
  const bg =
    s >= 0.8 ? 'bg-green-light text-green-text' :
    s >= 0.6 ? 'bg-warning-light text-warning-text' :
               'bg-red-light text-red-text'
  // Display as 0–10 scale if ≤ 1, raw otherwise
  const display = s <= 1 ? (s * 10).toFixed(1) : s.toFixed(1)
  return <span className={`px-2 py-0.5 rounded text-sm font-bold ${bg}`}>{display}</span>
}

// STATUS_STYLES and status badge rendering with i18n support
const STATUS_STYLES: Record<string, string> = {
  submitted:        'bg-primary-light text-primary',
  approved:         'bg-green-light text-green-text',
  adjusted_approved:'bg-revised-light text-revised-text',
  rejected:         'bg-red-light text-red-text',
  contested:        'bg-contested-light text-contested-text',
  locked:           'bg-surface-alt text-muted',
}

function StatusBadge({ status, t }: { status: string; t: (key: string) => string }) {
  const labelKey = `statuses.${status}`
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium ${STATUS_STYLES[status] ?? 'bg-surface-alt text-muted'}`}>
      {t(labelKey) ?? status}
    </span>
  )
}

function fmt(iso: string) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' })
}

// ── T9-A1: estado canônico (result_state + round + finalize_reason) ─────────────
type TFn = (key: string, opts?: Record<string, unknown>) => string

const RESULT_STATE_STYLES: Record<string, string> = {
  ai_review:      'bg-ai-light text-ai-text',
  open:           'bg-contested-light text-contested-text',
  under_review:   'bg-warning-light text-warning-text',
  finalized:      'bg-green-light text-green-text',
  error_rejected: 'bg-red-light text-red-text',
}

/** Badge canônica: result_state + round (open/under_review) + finalize_reason (finalized).
 *  Fallback para eval_status em linhas legadas (sem result_state). */
function ResultStateBadge({ r, t }: { r: EvaluationResultWithActions; t: TFn }) {
  const rs = r.result_state
  if (!rs) return <StatusBadge status={r.eval_status} t={t} />
  const round  = (rs === 'open' || rs === 'under_review') && r.current_round
    ? ` (r${r.current_round})` : ''
  const reason = rs === 'finalized' && r.finalize_reason
    ? ` · ${t(`finalizeReasons.${r.finalize_reason}`, { defaultValue: r.finalize_reason })}` : ''
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium ${RESULT_STATE_STYLES[rs] ?? 'bg-surface-alt text-muted'}`}>
      {t(`resultStates.${rs}`, { defaultValue: rs })}{round}{reason}
    </span>
  )
}

/** Duração humanizada desde `fromIso` até agora (m / h / d). */
function elapsed(fromIso?: string | null): string {
  if (!fromIso) return '—'
  const ms = Date.now() - new Date(fromIso).getTime()
  if (!Number.isFinite(ms) || ms < 0) return '—'
  const m = Math.floor(ms / 60000)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h`
  return `${Math.floor(h / 24)}d`
}

// ── CriterionRow ───────────────────────────────────────────────────────────────

function CriterionRow({ cr }: { cr: EvaluationCriterionResponse }) {
  return (
    <div className="border-b last:border-0 py-2 px-3 text-sm">
      <div className="flex items-start gap-3">
        <span className="font-mono text-xs text-muted-light w-36 shrink-0 pt-0.5">{cr.criterion_id}</span>
        <div className="flex-1">
          {cr.na ? (
            <span className="text-muted-light italic">N/A — {cr.na_reason}</span>
          ) : (
            <>
              <div className="flex items-center gap-2 mb-1">
                <ScorePill score={cr.value ?? 0} />
                {cr.evidence_refs && cr.evidence_refs.length > 0 && (
                  <span className="text-xs text-muted-light">refs: [{cr.evidence_refs.join(', ')}]</span>
                )}
              </div>
              <p className="text-muted text-xs leading-relaxed">{cr.justification}</p>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Helpers for structured reason parsing ─────────────────────────────────────

interface ParsedCriterionContestation {
  criterion_id:       string
  score_label:        string
  system_evaluation:  string
  disagreement:       string
}

/**
 * Parse the structured reason string built by ContestPanel into per-criterion entries.
 * Falls back to a single entry with the raw text if the format isn't recognized.
 */
function parseContestationReason(reason: string): ParsedCriterionContestation[] {
  // Structured format starts with "[criterion_id] Nota atribuída:..."
  const blocks = reason.split(/\n\n---\n\n/)
  const results: ParsedCriterionContestation[] = []

  for (const block of blocks) {
    const headerMatch = block.match(/^\[(.+?)\]\s+(.+)\n/)
    if (!headerMatch) {
      // Unstructured (legacy) — show as single block
      results.push({ criterion_id: '', score_label: '', system_evaluation: '', disagreement: block.trim() })
      continue
    }
    const criterion_id  = headerMatch[1]
    const score_label   = headerMatch[2]
    const sysMatch      = block.match(/Avaliação do sistema:\s*(.+?)(?:\nDiscordância:|$)/s)
    const disMatch      = block.match(/Discordância:\s*(.+)$/s)
    results.push({
      criterion_id,
      score_label,
      system_evaluation: sysMatch?.[1]?.trim() ?? '',
      disagreement:      disMatch?.[1]?.trim() ?? '',
    })
  }
  return results
}

// ── ReviewPanel (inline in drill-down) ────────────────────────────────────────

function ReviewPanel({
  result,
  jwtToken,
  adminToken,
  onDone,
}: {
  result:     EvaluationResultWithActions
  jwtToken:   string
  adminToken: string
  onDone:     () => void
}) {
  const { t } = useTranslation('evaluation')
  const { tenantId: TENANT } = useAuth()
  const [decision, setDecision] = useState<'approved' | 'adjusted_approved' | 'rejected'>('approved')
  const [saving,   setSaving]   = useState(false)
  const [error,    setError]    = useState<string | null>(null)
  const { contestations } = useContestations(TENANT, result.result_id)

  // Per-criterion review notes — criterion_id → note text
  const [crNotes, setCrNotes] = useState<Record<string, string>>({})
  // General note for non-criterion feedback
  const [generalNote, setGeneralNote] = useState('')

  const criteria = result.criterion_responses ?? []

  /** Build review_note: structured per-criterion block + optional general note */
  const buildReviewNote = (): string => {
    const parts: string[] = []
    for (const cr of criteria) {
      const note = crNotes[cr.criterion_id]?.trim()
      if (note) {
        parts.push(`[${cr.criterion_id}] ${note}`)
      }
    }
    if (generalNote.trim()) parts.push(`[geral] ${generalNote.trim()}`)
    return parts.join('\n\n---\n\n')
  }

  const requiresNote = decision !== 'approved'
  const reviewNote   = buildReviewNote()
  const noteOk       = !requiresNote || reviewNote.length > 0

  const submit = async () => {
    if (!noteOk) { setError(t('review.noteRequired')); return }
    setSaving(true)
    setError(null)
    try {
      const backendDecision: 'approved' | 'rejected' = decision === 'rejected' ? 'rejected' : 'approved'
      await reviewResult(
        result.result_id,
        { decision: backendDecision, round: result.current_round ?? 1, review_note: reviewNote || undefined },
        jwtToken,
      )
      onDone()
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  const adjudicate = async (c: EvaluationContestation, adjDecision: 'accepted' | 'rejected') => {
    try {
      await adjudicateContestation(
        c.contestation_id,
        { decision: adjDecision, adjudicator: 'supervisor', adjudication_notes: reviewNote || undefined },
        adminToken,
      )
      onDone()
    } catch (e) { setError(String(e)) }
  }

  const openContestations = contestations.filter(c => c.status === 'open')

  return (
    <div className="border-t mt-4 pt-4 space-y-4">

      {/* Open contestations — parsed per criterion */}
      {openContestations.length > 0 && (
        <div className="border border-contested/30 rounded bg-contested-light">
          <div className="text-xs font-semibold text-contested-text px-3 pt-3 pb-1">
            ⚑ {t('review.openContestations')}
          </div>
          {openContestations.map(c => {
            const parsed = parseContestationReason(c.reason)
            return (
              <div key={c.contestation_id} className="px-3 pb-3 space-y-2">
                <p className="text-xs text-muted">{t('review.from')}: <strong>{c.contested_by}</strong></p>
                {parsed.map((p, i) => (
                  <div key={i} className={`rounded p-2 text-xs space-y-1 ${p.criterion_id ? 'bg-white border border-contested/20' : 'bg-contested-light'}`}>
                    {p.criterion_id && (
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-muted">{p.criterion_id}</span>
                        <span className="text-muted-light">{p.score_label}</span>
                      </div>
                    )}
                    {p.system_evaluation && (
                      <p className="text-muted italic">{t('review.evaluatedAs')}: {p.system_evaluation}</p>
                    )}
                    <p className="text-contested-text font-medium">{p.disagreement || p.criterion_id === '' ? p.disagreement : '—'}</p>
                  </div>
                ))}
                <div className="flex gap-2 pt-1">
                  <button
                    onClick={() => adjudicate(c, 'accepted')}
                    className="text-xs bg-green-light text-green-text px-2 py-0.5 rounded hover:bg-green/20"
                  >✓ {t('review.accepted')}</button>
                  <button
                    onClick={() => adjudicate(c, 'rejected')}
                    className="text-xs bg-surface-alt text-muted px-2 py-0.5 rounded hover:bg-border"
                  >✕ {t('review.rejected')}</button>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Decision */}
      <div>
        <div className="text-xs font-semibold text-muted mb-2">{t('review.decision')}</div>
        <div className="flex gap-4">
          {(['approved', 'adjusted_approved', 'rejected'] as const).map(d => (
            <label key={d} className="flex items-center gap-1.5 text-sm cursor-pointer">
              <input type="radio" value={d} checked={decision === d} onChange={() => setDecision(d)} />
              <span className={d === 'approved' ? 'text-green-text' : d === 'adjusted_approved' ? 'text-revised-text' : 'text-red-text'}>
                {d === 'approved' ? '✓ ' + t('review.approve') : d === 'adjusted_approved' ? '~ ' + t('review.approveWithRemarks') : '✕ ' + t('review.reject')}
              </span>
            </label>
          ))}
        </div>
      </div>

      {/* Per-criterion review notes */}
      {criteria.length > 0 && (
        <div>
          <div className="text-xs font-semibold text-muted mb-2">
            {t('review.criterionNotes')}
            <span className="text-muted-light font-normal ml-1">({t('review.criterionNotesHint')})</span>
          </div>
          <div className="space-y-2">
            {criteria.map(cr => {
              const scoreVal = cr.value !== null && cr.value !== undefined
                ? (cr.value <= 1 ? (cr.value * 10).toFixed(1) : cr.value.toFixed(1))
                : null
              return (
                <div key={cr.criterion_id} className="border rounded p-2 bg-surface-muted">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-mono text-xs text-muted">{cr.criterion_id}</span>
                    {scoreVal !== null && (
                      <span className={`text-xs font-bold px-1.5 py-0.5 rounded ${
                        cr.value! >= 0.8 ? 'bg-green-light text-green-text' :
                        cr.value! >= 0.6 ? 'bg-warning-light text-warning-text' :
                                           'bg-red-light text-red-text'
                      }`}>{scoreVal}/10</span>
                    )}
                    {cr.na && <span className="text-xs text-muted-light italic">N/A</span>}
                  </div>
                  <textarea
                    className="w-full border border-border rounded px-2 py-1.5 text-xs resize-none bg-white"
                    rows={2}
                    placeholder={`Comentário sobre "${cr.criterion_id}"…`}
                    value={crNotes[cr.criterion_id] ?? ''}
                    onChange={e => setCrNotes(prev => ({ ...prev, [cr.criterion_id]: e.target.value }))}
                  />
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* General note */}
      <div>
        <label className="text-xs font-semibold text-muted mb-1 block">
          {t('review.generalNote')}{requiresNote && <span className="text-red ml-1">*</span>}
        </label>
        <textarea
          className="w-full border border-border-strong rounded px-3 py-2 text-sm resize-none"
          rows={2}
          placeholder={requiresNote ? t('review.noteRequired') : t('review.noteOptional')}
          value={generalNote}
          onChange={e => setGeneralNote(e.target.value)}
        />
      </div>

      {error && <div className="text-red-text text-xs bg-red-light border border-red/20 rounded p-2">{error}</div>}

      <button
        onClick={submit}
        disabled={saving || !noteOk}
        className="bg-primary text-white text-sm px-4 py-1.5 rounded hover:bg-primary-dark disabled:opacity-50 w-full"
      >
        {saving ? t('review.saving') : t('review.submitReview')}
      </button>
    </div>
  )
}

// ── ContestPanel (per-criterion) ──────────────────────────────────────────────

const MIN_CONTEST_CHARS = 30

interface CriterionContestState {
  checked:      boolean
  justification: string
}

function CriterionContestRow({
  cr,
  state,
  onToggle,
  onJustification,
}: {
  cr:             EvaluationCriterionResponse
  state:          CriterionContestState
  onToggle:       () => void
  onJustification:(text: string) => void
}) {
  const { t } = useTranslation('evaluation')
  const charCount = state.justification.trim().length
  const tooShort  = state.checked && charCount > 0 && charCount < MIN_CONTEST_CHARS
  const scoreVal  = cr.value !== null && cr.value !== undefined
    ? (cr.value <= 1 ? (cr.value * 10).toFixed(1) : cr.value.toFixed(1))
    : null

  return (
    <div className={`border rounded mb-2 transition-colors ${state.checked ? 'border-contested/30 bg-contested-light' : 'border-border bg-white'}`}>
      {/* Criterion header — always visible */}
      <label className="flex items-start gap-3 p-3 cursor-pointer select-none">
        <input
          type="checkbox"
          checked={state.checked}
          onChange={onToggle}
          className="mt-0.5 accent-contested"
        />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-mono text-xs text-muted">{cr.criterion_id}</span>
            {cr.na ? (
              <span className="text-xs text-muted-light italic">N/A</span>
            ) : scoreVal !== null ? (
              <span className={`text-xs font-bold px-1.5 py-0.5 rounded ${
                cr.value! >= 0.8 ? 'bg-green-light text-green-text' :
                cr.value! >= 0.6 ? 'bg-warning-light text-warning-text' :
                                   'bg-red-light text-red-text'
              }`}>{scoreVal}/10</span>
            ) : null}
          </div>
          {/* AI evaluator's justification — shown as context */}
          {cr.justification && (
            <p className="text-xs text-muted mt-0.5 leading-relaxed line-clamp-2">
              {cr.justification}
            </p>
          )}
        </div>
        {!cr.na && (
          <span className={`text-xs shrink-0 self-center font-medium ${state.checked ? 'text-contested' : 'text-muted-light'}`}>
            {state.checked ? '✓ ' + t('contest.title') : t('contest.title')}
          </span>
        )}
      </label>

      {/* Justification input — only when checked and not NA */}
      {state.checked && !cr.na && (
        <div className="px-3 pb-3 space-y-1">
          <div className="flex items-center justify-between">
            <label className="text-xs font-semibold text-contested-text">
              {t('contest.justification')} <span className="text-red">*</span>
            </label>
            <span className={`text-xs ${tooShort ? 'text-red' : charCount >= MIN_CONTEST_CHARS ? 'text-green-text' : 'text-muted-light'}`}>
              {charCount}/{MIN_CONTEST_CHARS} {t('contest.minChars')}
            </span>
          </div>
          <textarea
            className={`w-full border rounded px-3 py-2 text-sm resize-none transition-colors ${
              tooShort ? 'border-red/30' : charCount >= MIN_CONTEST_CHARS ? 'border-green/30' : 'border-contested/30'
            }`}
            rows={3}
            placeholder={t('contest.justificationPlaceholder')}
            value={state.justification}
            onChange={e => onJustification(e.target.value)}
            autoFocus
          />
          {tooShort && (
            <p className="text-xs text-red">{t('contest.charsRemaining', { count: MIN_CONTEST_CHARS - charCount })}</p>
          )}
        </div>
      )}
    </div>
  )
}

function ContestPanel({
  result,
  userId,
  jwtToken,
  onDone,
  onCancel,
}: {
  result:   EvaluationResultWithActions
  userId:   string
  jwtToken: string
  onDone:   () => void
  onCancel: () => void
}) {
  const { t } = useTranslation('evaluation')
  const { tenantId: TENANT } = useAuth()
  const criteria = result.criterion_responses ?? []

  // Per-criterion state: criterion_id → { checked, justification }
  const [crState, setCrState] = useState<Record<string, CriterionContestState>>(() =>
    Object.fromEntries(criteria.map(cr => [cr.criterion_id, { checked: false, justification: '' }]))
  )
  const [saving, setSaving] = useState(false)
  const [error,  setError]  = useState<string | null>(null)

  const toggle = (id: string) =>
    setCrState(prev => ({ ...prev, [id]: { ...prev[id], checked: !prev[id].checked } }))

  const setJustification = (id: string, text: string) =>
    setCrState(prev => ({ ...prev, [id]: { ...prev[id], justification: text } }))

  const contested = Object.entries(crState).filter(([, s]) => s.checked && !criteria.find(c => c.criterion_id === (s as any))?.na)
  // Re-derive contested from criteria list to respect na flag
  const contestedEntries = criteria
    .filter(cr => crState[cr.criterion_id]?.checked && !cr.na)
    .map(cr => ({ cr, state: crState[cr.criterion_id] }))

  const allValid = contestedEntries.length > 0 &&
    contestedEntries.every(({ state }) => state.justification.trim().length >= MIN_CONTEST_CHARS)

  const buildReason = (): string =>
    contestedEntries.map(({ cr, state }) => {
      const scoreStr = cr.value !== null && cr.value !== undefined
        ? `Nota atribuída: ${(cr.value <= 1 ? cr.value * 10 : cr.value).toFixed(1)}/10`
        : 'Nota: N/A'
      return (
        `[${cr.criterion_id}] ${scoreStr}\n` +
        `Avaliação do sistema: ${cr.justification ?? '—'}\n` +
        `Discordância: ${state.justification.trim()}`
      )
    }).join('\n\n---\n\n')

  const submit = async () => {
    if (contestedEntries.length === 0) { setError(t('contest.selectAtLeastOne')); return }
    if (!allValid) { setError(t('contest.allJustificationsRequired')); return }
    setSaving(true)
    setError(null)
    try {
      await createContestation(
        {
          result_id:    result.result_id,
          instance_id:  result.instance_id,
          session_id:   result.session_id,
          tenant_id:    TENANT,
          contested_by: userId,
          reason:       buildReason(),
          round:        result.current_round ?? 1,
        },
        jwtToken || undefined,
      )
      onDone()
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-3">
      {/* Banner */}
      <div className="bg-contested-light border border-contested/30 rounded p-3">
        <p className="text-xs font-semibold text-contested-text mb-0.5">⚑ {t('contest.banner.title')}</p>
        <p className="text-xs text-contested-text">
          {t('contest.banner.description')}
        </p>
      </div>

      {/* Criteria list */}
      {criteria.length === 0 ? (
        <p className="text-xs text-muted-light italic text-center py-4">{t('contest.noCriteria')}</p>
      ) : (
        <div>
          <div className="text-xs font-semibold text-muted mb-2">
            {t('contest.criteriaList', { count: contestedEntries.length })}
          </div>
          {criteria.map(cr => (
            <CriterionContestRow
              key={cr.criterion_id}
              cr={cr}
              state={crState[cr.criterion_id] ?? { checked: false, justification: '' }}
              onToggle={() => toggle(cr.criterion_id)}
              onJustification={text => setJustification(cr.criterion_id, text)}
            />
          ))}
        </div>
      )}

      {error && (
        <div className="text-red-text text-xs bg-red-light border border-red/20 rounded p-2">{error}</div>
      )}

      <div className="flex gap-2">
        <button
          onClick={onCancel}
          className="flex-1 text-sm px-4 py-1.5 rounded border border-border-strong text-muted hover:bg-surface-muted"
        >
          {t('contest.cancel')}
        </button>
        <button
          onClick={submit}
          disabled={saving || !allValid}
          className="flex-1 bg-contested text-white text-sm px-4 py-1.5 rounded hover:bg-contested-text disabled:opacity-50"
        >
          {saving ? t('contest.submitting') : t('contest.submit', { count: contestedEntries.length })}
        </button>
      </div>
    </div>
  )
}

// ── Arc 13 — DimensionStateIndicator ──────────────────────────────────────────

const DIM_STATE_META: Record<string, { dot: string; label: string }> = {
  neutral:      { dot: 'bg-border-strong',   label: 'Aguardando' },
  pre_reviewed: { dot: 'bg-revised',      label: 'Pré-revisado' },
  contested:    { dot: 'bg-contested',   label: 'Contestado' },
  upheld:       { dot: 'bg-green',       label: 'Mantido' },
  revised:      { dot: 'bg-secondary',   label: 'Revisado' },
  timeout:      { dot: 'bg-border',   label: 'Timeout' },
}

function DimensionStateIndicator({ state }: { state: string }) {
  const s = DIM_STATE_META[state] ?? DIM_STATE_META.neutral
  return (
    <span className="flex items-center gap-1">
      <span className={`inline-block w-2 h-2 rounded-full shrink-0 ${s.dot}`} />
      <span className="text-xs text-muted">{s.label}</span>
    </span>
  )
}

// ── Arc 13 — DimensionThreadCard ──────────────────────────────────────────────

const ROUND_ROLE_LABELS: Record<string, string> = {
  evaluator_ai:   'Avaliador IA',
  pre_reviewer_ai:'Pré-revisor IA',
  human_agent:    'Agente Avaliado',
  reviewer_ai:    'Revisor IA',
  human_reviewer: 'Revisor Humano',
}

const DIM_BORDER: Record<string, string> = {
  contested:    'border-contested/30',
  revised:      'border-secondary/30',
  upheld:       'border-green/30',
  pre_reviewed: 'border-revised/30',
  timeout:      'border-border',
  neutral:      'border-border',
}

function DimensionThreadCard({ thread }: { thread: ContestationThread }) {
  const [expanded, setExpanded] = useState(
    thread.current_state === 'contested' || thread.current_state === 'revised',
  )

  return (
    <div className={`border rounded mb-2 ${DIM_BORDER[thread.current_state] ?? 'border-border'}`}>
      {/* Header — always visible */}
      <button
        onClick={() => setExpanded(e => !e)}
        className="w-full flex items-center gap-3 p-3 text-left hover:bg-surface-muted transition-colors"
      >
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-medium text-sm text-dark truncate">
              {thread.dimension_label ?? thread.dimension_id}
            </span>
            <DimensionStateIndicator state={thread.current_state} />
          </div>
          <div className="flex items-center gap-3 mt-0.5">
            <span className="text-xs text-muted">
              Orig: <ScorePill score={thread.original_score} />
            </span>
            {thread.current_score !== thread.original_score && (
              <span className="text-xs text-muted">
                → Atual: <ScorePill score={thread.current_score} />
              </span>
            )}
          </div>
        </div>
        <span className="text-xs text-muted-light shrink-0">
          {expanded ? '▲' : '▼'} {thread.entries.length}
        </span>
      </button>

      {/* Expanded entries */}
      {expanded && thread.entries.length > 0 && (
        <div className="border-t divide-y">
          {thread.entries.map((entry, i) => (
            <div key={i} className="px-3 py-2">
              <div className="flex items-center gap-2 mb-1 flex-wrap">
                <span className={`text-xs font-semibold ${
                  entry.round === 2    ? 'text-contested-text' :
                  entry.round >= 3    ? 'text-primary'      :
                  entry.round === 1.5 ? 'text-revised-text' :
                                        'text-muted'
                }`}>
                  Round {entry.round} — {ROUND_ROLE_LABELS[entry.author_role] ?? entry.author_role}
                </span>
                {entry.action && (
                  <span className="text-xs text-muted-light">({entry.action})</span>
                )}
                {entry.score !== null && entry.score !== undefined && (
                  <ScorePill score={entry.score} />
                )}
                <span className="text-xs text-border-strong ml-auto">{fmt(entry.submitted_at)}</span>
              </div>
              <p className="text-xs text-muted leading-relaxed">{entry.justification}</p>
              {entry.evidence_entries?.length > 0 && (
                <div className="mt-1 space-y-1">
                  {entry.evidence_entries.map((ev, j) => (
                    <div key={j} className="bg-surface-muted border border-border rounded px-2 py-1 text-xs">
                      <span className="font-mono text-border-strong">{ev.stream_entry_id}</span>
                      <p className="text-muted italic mt-0.5">"{ev.excerpt}"</p>
                      <p className="text-muted mt-0.5">{ev.relevance_note}</p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Arc 13 — HumanReviewPanel (reviewer submits upheld/revised per dimension) ─

const MIN_REVIEW_WORDS = 20

function HumanReviewPanel({
  threads,
  instanceId,
  jwtToken,
  userId,
  onDone,
  onCancel,
}: {
  threads:    ContestationThread[]
  instanceId: string
  jwtToken:   string
  userId:     string
  onDone:     () => void
  onCancel:   () => void
}) {
  const { t } = useTranslation('evaluation')
  const contestedThreads = threads.filter(th => th.current_state === 'contested')

  const [decisions, setDecisions] = useState<Record<string, {
    decision: 'upheld' | 'revised'
    score_override?: number
    justification: string
  }>>(() =>
    Object.fromEntries(contestedThreads.map(th => [th.dimension_id, { decision: 'upheld', justification: '' }]))
  )
  const [saving, setSaving] = useState(false)
  const [error,  setError]  = useState<string | null>(null)

  const wordCount = (text: string) => text.trim().split(/\s+/).filter(Boolean).length

  const allValid = contestedThreads.length > 0 && contestedThreads.every(th => {
    const d = decisions[th.dimension_id]
    if (!d || wordCount(d.justification) < MIN_REVIEW_WORDS) return false
    if (d.decision === 'revised' && (d.score_override === undefined || d.score_override === null)) return false
    return true
  })

  const submit = async () => {
    if (!allValid) { setError(t('review.allFieldsRequired')); return }
    setSaving(true); setError(null)
    try {
      await submitHumanReview(
        instanceId,
        {
          dimension_decisions: contestedThreads.map(th => {
            const d = decisions[th.dimension_id]
            return {
              dimension_id:  th.dimension_id,
              decision:      d.decision,
              score_override: d.decision === 'revised' ? d.score_override : undefined,
              justification: d.justification,
            }
          }),
          reviewer_id: userId,
        },
        jwtToken,
      )
      onDone()
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  if (contestedThreads.length === 0) {
    return (
      <p className="text-xs text-muted-light italic text-center py-4">
        {t('review.noContestedDimensions')}
      </p>
    )
  }

  return (
    <div className="space-y-4 border-t mt-4 pt-4">
      <div className="text-xs font-semibold text-dark">
        ✓ {t('review.dimensionDecisions')} ({contestedThreads.length})
      </div>

      {contestedThreads.map(th => {
        const d = decisions[th.dimension_id] ?? { decision: 'upheld' as const, justification: '' }
        const wc = wordCount(d.justification)
        const tooFew = wc < MIN_REVIEW_WORDS

        return (
          <div key={th.dimension_id} className="border rounded p-3 bg-surface-muted">
            <div className="flex items-center gap-2 mb-2">
              <span className="font-medium text-sm text-dark">
                {th.dimension_label ?? th.dimension_id}
              </span>
              <span className="text-xs text-muted">
                Score original: {(th.original_score * 10).toFixed(1)}/10
              </span>
            </div>

            {/* Upheld / Revised radio */}
            <div className="flex gap-4 mb-2">
              {(['upheld', 'revised'] as const).map(dec => (
                <label key={dec} className="flex items-center gap-1.5 text-sm cursor-pointer">
                  <input
                    type="radio"
                    value={dec}
                    checked={d.decision === dec}
                    onChange={() => setDecisions(prev => ({
                      ...prev,
                      [th.dimension_id]: { ...prev[th.dimension_id], decision: dec },
                    }))}
                  />
                  <span className={dec === 'upheld' ? 'text-green-text' : 'text-primary'}>
                    {dec === 'upheld' ? '✓ ' + t('review.uphold') : '↕ ' + t('review.revise')}
                  </span>
                </label>
              ))}
            </div>

            {/* Score override — only for 'revised' */}
            {d.decision === 'revised' && (
              <div className="mb-2">
                <label className="text-xs font-semibold text-muted mb-1 block">
                  {t('review.newScore')} (0–10) <span className="text-red">*</span>
                </label>
                <input
                  type="number"
                  min={0} max={10} step={0.1}
                  value={d.score_override !== undefined ? +(d.score_override * 10).toFixed(1) : ''}
                  onChange={e => {
                    const val = parseFloat(e.target.value)
                    setDecisions(prev => ({
                      ...prev,
                      [th.dimension_id]: {
                        ...prev[th.dimension_id],
                        score_override: isNaN(val) ? undefined : +(val / 10).toFixed(3),
                      },
                    }))
                  }}
                  className="border border-border-strong rounded px-2 py-1 text-sm w-24"
                  placeholder="0–10"
                />
              </div>
            )}

            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-xs font-semibold text-muted">
                  {t('review.justification')} <span className="text-red">*</span>
                </label>
                <span className={`text-xs ${tooFew ? 'text-red' : 'text-green-text'}`}>
                  {wc}/{MIN_REVIEW_WORDS} {t('review.minWords')}
                </span>
              </div>
              <textarea
                className={`w-full border rounded px-3 py-2 text-sm resize-none ${tooFew ? 'border-red/30' : 'border-border-strong'}`}
                rows={3}
                placeholder={t('review.justificationPlaceholder')}
                value={d.justification}
                onChange={e => setDecisions(prev => ({
                  ...prev,
                  [th.dimension_id]: { ...prev[th.dimension_id], justification: e.target.value },
                }))}
              />
            </div>
          </div>
        )
      })}

      {error && <div className="text-red-text text-xs bg-red-light border border-red/20 rounded p-2">{error}</div>}

      <div className="flex gap-2">
        <button
          onClick={onCancel}
          className="flex-1 text-sm px-4 py-1.5 rounded border border-border-strong text-muted hover:bg-surface-muted"
        >
          {t('review.cancel')}
        </button>
        <button
          onClick={submit}
          disabled={saving || !allValid}
          className="flex-1 bg-primary text-white text-sm px-4 py-1.5 rounded hover:bg-primary-dark disabled:opacity-50"
        >
          {saving ? t('review.saving') : t('review.submitDimensionReview')}
        </button>
      </div>
    </div>
  )
}

// ── Arc 13 — DimensionContestPanel13 (human agent contests specific dimensions) ─

const MIN_CONTEST_WORDS = 10

function DimensionContestPanel13({
  threads,
  instanceId,
  currentRound,
  jwtToken,
  onDone,
  onCancel,
}: {
  threads:      ContestationThread[]
  instanceId:   string
  currentRound: number
  jwtToken:     string
  onDone:       () => void
  onCancel:     () => void
}) {
  const { t } = useTranslation('evaluation')
  // Only dimensions that are contestable (neutral or pre_reviewed)
  const contestable = threads.filter(
    th => th.current_state === 'neutral' || th.current_state === 'pre_reviewed',
  )

  const [sel, setSel] = useState<Record<string, { checked: boolean; reason: string }>>(() =>
    Object.fromEntries(contestable.map(th => [th.dimension_id, { checked: false, reason: '' }]))
  )
  const [saving, setSaving] = useState(false)
  const [error,  setError]  = useState<string | null>(null)

  const wordCount = (text: string) => text.trim().split(/\s+/).filter(Boolean).length

  const checked = contestable.filter(th => sel[th.dimension_id]?.checked)
  const allValid = checked.length > 0 && checked.every(th =>
    wordCount(sel[th.dimension_id]?.reason ?? '') >= MIN_CONTEST_WORDS
  )

  const submit = async () => {
    if (!allValid) { setError(t('contest.allJustificationsRequired')); return }
    setSaving(true); setError(null)
    try {
      await submitDimensionContestation(
        instanceId,
        {
          dimension_ids: checked.map(th => th.dimension_id),
          reasons:       Object.fromEntries(checked.map(th => [th.dimension_id, sel[th.dimension_id].reason])),
          round:         currentRound,
        },
        jwtToken,
      )
      onDone()
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-3 border-t mt-4 pt-4">
      <div className="bg-contested-light border border-contested/30 rounded p-3">
        <p className="text-xs font-semibold text-contested-text mb-0.5">⚑ {t('contest.banner.title')}</p>
        <p className="text-xs text-contested-text">{t('contest.banner.description')}</p>
      </div>

      {contestable.length === 0 ? (
        <p className="text-xs text-muted-light italic text-center py-4">{t('contest.noDimensions')}</p>
      ) : (
        <div>
          <div className="text-xs font-semibold text-muted mb-2">
            {t('contest.dimensionList')} ({checked.length}/{contestable.length})
          </div>
          {contestable.map(th => {
            const s = sel[th.dimension_id] ?? { checked: false, reason: '' }
            const wc = wordCount(s.reason)
            const tooFew = s.checked && wc < MIN_CONTEST_WORDS

            return (
              <div
                key={th.dimension_id}
                className={`border rounded mb-2 transition-colors ${s.checked ? 'border-contested/30 bg-contested-light' : 'border-border bg-white'}`}
              >
                <label className="flex items-start gap-3 p-3 cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={s.checked}
                    onChange={() => setSel(prev => ({
                      ...prev,
                      [th.dimension_id]: { ...prev[th.dimension_id], checked: !prev[th.dimension_id].checked },
                    }))}
                    className="mt-0.5 accent-contested"
                  />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-medium text-sm text-dark">
                        {th.dimension_label ?? th.dimension_id}
                      </span>
                      <DimensionStateIndicator state={th.current_state} />
                      <ScorePill score={th.current_score} />
                    </div>
                    {/* Show evaluator AI's justification as context */}
                    {th.entries[0]?.justification && (
                      <p className="text-xs text-muted mt-0.5 leading-relaxed line-clamp-2">
                        {th.entries[0].justification}
                      </p>
                    )}
                  </div>
                </label>

                {s.checked && (
                  <div className="px-3 pb-3 space-y-1">
                    <div className="flex items-center justify-between">
                      <label className="text-xs font-semibold text-contested-text">
                        {t('contest.justification')} <span className="text-red">*</span>
                      </label>
                      <span className={`text-xs ${tooFew ? 'text-red' : wc >= MIN_CONTEST_WORDS ? 'text-green-text' : 'text-muted-light'}`}>
                        {wc}/{MIN_CONTEST_WORDS} {t('contest.minWords')}
                      </span>
                    </div>
                    <textarea
                      className={`w-full border rounded px-3 py-2 text-sm resize-none ${tooFew ? 'border-red/30' : 'border-contested/30'}`}
                      rows={3}
                      placeholder={t('contest.justificationPlaceholder')}
                      value={s.reason}
                      onChange={e => setSel(prev => ({
                        ...prev,
                        [th.dimension_id]: { ...prev[th.dimension_id], reason: e.target.value },
                      }))}
                      autoFocus
                    />
                    {tooFew && (
                      <p className="text-xs text-red">
                        {t('contest.wordsRemaining', { count: MIN_CONTEST_WORDS - wc })}
                      </p>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {error && <div className="text-red-text text-xs bg-red-light border border-red/20 rounded p-2">{error}</div>}

      <div className="flex gap-2">
        <button
          onClick={onCancel}
          className="flex-1 text-sm px-4 py-1.5 rounded border border-border-strong text-muted hover:bg-surface-muted"
        >
          {t('contest.cancel')}
        </button>
        <button
          onClick={submit}
          disabled={saving || !allValid}
          className="flex-1 bg-contested text-white text-sm px-4 py-1.5 rounded hover:bg-contested-text disabled:opacity-50"
        >
          {saving ? t('contest.submitting') : t('contest.submit', { count: checked.length })}
        </button>
      </div>
    </div>
  )
}

// ── DetailPanel ────────────────────────────────────────────────────────────────

function DetailPanel({
  result,
  jwtToken,
  adminToken,
  userId,
  onClose,
  onAction,
}: {
  result:     EvaluationResultWithActions
  jwtToken:   string
  adminToken: string
  userId:     string
  onClose:    () => void
  onAction:   () => void
}) {
  const { t } = useTranslation('evaluation')
  const [mode, setMode] = useState<'view' | 'review' | 'contest'>('view')

  const canReview  = result.available_actions?.includes('review')
  const canContest = result.available_actions?.includes('contest')

  // Arc 13 — load contestation threads when an instance_id is present
  const { data: threadData, loading: threadLoading, reload: reloadThreads } =
    useContestationThreads(result.instance_id ?? null, jwtToken, 0)

  // Arc 13 mode: when the instance has dimension threads
  const isArc13 = (threadData?.threads?.length ?? 0) > 0
  const threads  = threadData?.threads ?? []
  const currentRound = threadData?.current_round ?? result.current_round ?? 0

  const handleActionDone = () => {
    setMode('view')
    reloadThreads()
    onAction()
  }

  return (
    <aside className="w-[480px] border-l flex flex-col bg-white overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 p-3 border-b bg-surface-muted">
        <div className="flex-1 min-w-0">
          <div className="font-semibold text-sm text-dark truncate">
            {t('detail.session')}: {result.session_id}
          </div>
          <div className="text-xs text-muted-light truncate">
            {t('detail.evaluator')}: {result.evaluator_id}
            {isArc13 && (
              <span className="ml-2 text-revised font-medium">· Arc 13</span>
            )}
          </div>
        </div>
        <ScorePill score={result.overall_score} />
        <StatusBadge status={result.eval_status} t={t} />
        {result.locked && <span title={t('detail.locked')}>🔒</span>}
        <button onClick={onClose} className="text-muted-light hover:text-muted ml-1 text-lg leading-none">✕</button>
      </div>

      {/* Action bar */}
      {(canReview || canContest) && !result.locked && (
        <div className="flex gap-2 px-3 py-2 border-b bg-primary-light">
          {canReview && (
            <button
              onClick={() => setMode(m => m === 'review' ? 'view' : 'review')}
              className={`text-xs px-3 py-1 rounded font-medium border transition-colors ${
                mode === 'review'
                  ? 'bg-primary text-white border-primary'
                  : 'bg-white text-primary border-primary hover:bg-primary-light'
              }`}
            >
              ✓ {t('detail.review')}
            </button>
          )}
          {canContest && (
            <button
              onClick={() => setMode(m => m === 'contest' ? 'view' : 'contest')}
              className={`text-xs px-3 py-1 rounded font-medium border transition-colors ${
                mode === 'contest'
                  ? 'bg-contested text-white border-contested'
                  : 'bg-white text-contested border-contested hover:bg-contested-light'
              }`}
            >
              ⚑ {t('detail.contest')}
            </button>
          )}
          {result.action_required && (
            <span className="text-xs text-muted self-center ml-auto">
              {t('detail.awaiting')}: {result.action_required === 'review' ? t('detail.awaitingReview') : t('detail.awaitingContest')}
              {result.deadline_at && ` · ${t('detail.deadline')} ${fmt(result.deadline_at)}`}
            </span>
          )}
        </div>
      )}

      {/* Body */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">

        {/* Overview */}
        {result.overall_observation && (
          <div className="bg-surface-muted rounded p-3">
            <div className="text-xs font-semibold text-muted mb-1">{t('detail.generalObservation')}</div>
            <p className="text-sm text-dark">{result.overall_observation}</p>
          </div>
        )}

        {/* Highlights / Improvements */}
        <div className="grid grid-cols-2 gap-3">
          {(result.highlights ?? []).length > 0 && (
            <div>
              <div className="text-xs font-semibold text-green-text mb-1">✓ {t('detail.highlights')}</div>
              <ul className="text-xs text-muted space-y-0.5">
                {result.highlights.map((h, i) => <li key={i}>• {h}</li>)}
              </ul>
            </div>
          )}
          {(result.improvement_points ?? []).length > 0 && (
            <div>
              <div className="text-xs font-semibold text-contested-text mb-1">↑ {t('detail.improvements')}</div>
              <ul className="text-xs text-muted space-y-0.5">
                {result.improvement_points.map((p, i) => <li key={i}>• {p}</li>)}
              </ul>
            </div>
          )}
        </div>

        {/* Compliance flags */}
        {(result.compliance_flags ?? []).length > 0 && (
          <div>
            <div className="text-xs font-semibold text-red-text mb-1">⚠ {t('detail.flags')}</div>
            <div className="flex flex-wrap gap-1">
              {result.compliance_flags.map(f => (
                <span key={f} className="bg-red-light text-red-text text-xs px-2 py-0.5 rounded">{f}</span>
              ))}
            </div>
          </div>
        )}

        {/* ── Arc 13: Dimension threads view ────────────────────────────────── */}
        {isArc13 && mode !== 'contest' && (
          <div>
            <div className="flex items-center gap-2 mb-2">
              <span className="text-xs font-semibold text-muted">{t('detail.dimensionThreads')}</span>
              {threadLoading && <span className="text-xs text-muted-light">⟳</span>}
            </div>
            {threads.map(th => (
              <DimensionThreadCard key={th.dimension_id} thread={th} />
            ))}
          </div>
        )}

        {/* ── Arc 6 fallback: criteria list ─────────────────────────────────── */}
        {!isArc13 && mode !== 'contest' && (result.criterion_responses ?? []).length > 0 && (
          <div>
            <div className="text-xs font-semibold text-muted mb-2">{t('detail.evaluatedCriteria')}</div>
            <div className="border rounded">
              {result.criterion_responses.map(cr => (
                <CriterionRow key={cr.criterion_id} cr={cr} />
              ))}
            </div>
          </div>
        )}

        {/* Metadata */}
        <div className="text-xs text-muted-light space-y-0.5">
          <div>{t('detail.campaign')}: {result.campaign_id ?? '—'}</div>
          <div>{t('detail.currentRound')}: {currentRound}</div>
          {result.lock_reason && <div>{t('detail.lockReason')}: {result.lock_reason}</div>}
          <div>{t('detail.createdAt')}: {fmt(result.created_at)}</div>
        </div>

        {/* ── Action panels ──────────────────────────────────────────────────── */}

        {/* Review — Arc 13: HumanReviewPanel per dimension */}
        {mode === 'review' && isArc13 && result.instance_id && (
          <HumanReviewPanel
            threads={threads}
            instanceId={result.instance_id}
            jwtToken={jwtToken}
            userId={userId}
            onDone={handleActionDone}
            onCancel={() => setMode('view')}
          />
        )}

        {/* Review — Arc 6 fallback: criterion-level ReviewPanel */}
        {mode === 'review' && !isArc13 && (
          <ReviewPanel
            result={result}
            jwtToken={jwtToken}
            adminToken={adminToken}
            onDone={handleActionDone}
          />
        )}

        {/* Contest — Arc 13: DimensionContestPanel13 per dimension */}
        {mode === 'contest' && isArc13 && result.instance_id && (
          <DimensionContestPanel13
            threads={threads}
            instanceId={result.instance_id}
            currentRound={currentRound}
            jwtToken={jwtToken}
            onDone={handleActionDone}
            onCancel={() => setMode('view')}
          />
        )}

        {/* Contest — Arc 6 fallback: criterion-level ContestPanel */}
        {mode === 'contest' && !isArc13 && (
          <ContestPanel
            result={result}
            userId={userId}
            jwtToken={jwtToken}
            onDone={handleActionDone}
            onCancel={() => setMode('view')}
          />
        )}
      </div>
    </aside>
  )
}

// ── T9-A2: Nível 1 — cards de campanha ──────────────────────────────────────────

function fmtMs(ms: number | null): string {
  if (ms == null) return '—'
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m`
  return `${Math.floor(m / 60)}h`
}

function CampaignsLevel({ campaigns, summaries, onOpen, t }: {
  campaigns: EvaluationCampaign[]
  summaries: Record<string, CampaignSummary>
  onOpen: (id: string) => void
  t: TFn
}) {
  return (
    <div className="p-6 space-y-4">
      <div>
        <h1 className="text-xl font-semibold text-dark">{t('title')}</h1>
        <p className="text-sm text-muted mt-0.5">{t('campaignsLevel.subtitle', { defaultValue: 'Selecione uma campanha para ver suas avaliações.' })}</p>
      </div>
      {campaigns.length === 0 && (
        <div className="text-sm text-muted-light">{t('campaignsLevel.empty', { defaultValue: 'Nenhuma campanha.' })}</div>
      )}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {campaigns.map(c => {
          const s  = summaries[c.campaign_id]
          const rs = s?.result_state ?? {}
          const ev = s?.evaluated ?? {}
          const period = c.period_start || c.period_end
            ? `${(c.period_start ?? '∞').slice(0, 10)} → ${(c.period_end ?? '∞').slice(0, 10)}`
            : t('campaignsLevel.openWindow', { defaultValue: 'janela aberta' })
          const chips: [string, number | undefined][] = [
            ['finalized', rs.finalized], ['open', rs.open], ['under_review', rs.under_review],
            ['ai_review', rs.ai_review], ['error_rejected', rs.error_rejected],
          ]
          return (
            <button key={c.campaign_id} onClick={() => onOpen(c.campaign_id)}
              className="text-left bg-white border rounded-lg p-4 hover:shadow-sm hover:border-primary/40 transition-shadow">
              <div className="flex items-start justify-between gap-2">
                <div className="font-medium text-dark truncate">{c.name}</div>
                <span className="text-2xl font-bold text-primary">{s?.total_results ?? 0}</span>
              </div>
              <div className="text-xs text-muted-light mt-0.5 truncate">
                {c.evaluation_pool_id ?? '—'} · {period}
              </div>
              <div className="flex flex-wrap gap-1 mt-3">
                {chips.filter(([, n]) => (n ?? 0) > 0).map(([k, n]) => (
                  <span key={k} className={`text-xs px-2 py-0.5 rounded-full font-medium ${RESULT_STATE_STYLES[k] ?? 'bg-surface-alt text-muted'}`}>
                    {t(`resultStates.${k}`, { defaultValue: k })}: {n}
                  </span>
                ))}
              </div>
              <div className="flex items-center gap-3 mt-3 text-xs text-muted">
                <span title={t('campaignsLevel.avgTime', { defaultValue: 'tempo médio' })}>⏱ {fmtMs(s?.avg_process_ms ?? null)}</span>
                <span>👤 {ev.human_agent ?? 0} · 🤖 {ev.ai_agent ?? 0}</span>
                {(s?.sla_overdue ?? 0) > 0 && <span className="text-red-text font-medium">⚠ SLA {s!.sla_overdue}</span>}
              </div>
            </button>
          )
        })}
      </div>
    </div>
  )
}

// ── Main Page ──────────────────────────────────────────────────────────────────

export default function AvaliacoesPage() {
  const { t } = useTranslation('evaluation')
  const { session, getAccessToken, tenantId: TENANT, currentUser } = useAuth()
  const [jwtToken, setJwtToken]       = useState('')
  const [adminToken, setAdminToken]   = useState('')
  const [selected, setSelected]       = useState<EvaluationResultWithActions | null>(null)

  // T9-A2 — nível via URL: ?campaign= vazio → nível 1 (campanhas); setado → nível 2 escopado.
  const [searchParams, setSearchParams] = useSearchParams()
  const campaignId = searchParams.get('campaign') || ''

  // Filters
  const [statusFilter, setStatusFilter]     = useState('')
  const [myActionsOnly, setMyActionsOnly]   = useState(false)

  // Resolve JWT on mount / session change
  useEffect(() => {
    getAccessToken().then(t => setJwtToken(t ?? '')).catch(() => {})
  }, [getAccessToken, session])

  // Load campaigns (nível 1) + sumários agregados
  const { campaigns } = useCampaigns(TENANT)
  const { summaries } = useCampaignSummaries(TENANT, 30_000)

  // Build filters for useResults — escopado pela campanha da URL (nível 2)
  const filters = {
    evalStatus:     statusFilter || undefined,
    campaignId:     campaignId   || undefined,
    actionRequired: myActionsOnly ? ('any' as const) : undefined,
    limit: 100,
  }

  const { results, loading, error, reload } = useResults(TENANT, filters, 30_000, jwtToken)

  // When "My actions" filter is on and we have specific user-targeted results,
  // further client-side filter to results where available_actions is non-empty.
  const displayed = myActionsOnly
    ? results.filter(r => (r.available_actions?.length ?? 0) > 0)
    : results

  // Sync selected row to latest data (after reload)
  const syncSelected = useCallback(() => {
    if (selected) {
      const refreshed = results.find(r => r.result_id === selected.result_id)
      if (refreshed) setSelected(refreshed)
    }
  }, [selected, results])

  useEffect(() => { syncSelected() }, [results]) // eslint-disable-line react-hooks/exhaustive-deps

  const userId = currentUser?.userId ?? ''

  // Build status options using translations
  const STATUS_OPTIONS = [
    { value: '', label: t('filters.allStatuses') },
    { value: 'submitted', label: t('statuses.submitted') },
    { value: 'approved', label: t('statuses.approved') },
    { value: 'adjusted_approved', label: t('statuses.adjusted_approved') },
    { value: 'rejected', label: t('statuses.rejected') },
    { value: 'contested', label: t('statuses.contested') },
    { value: 'locked', label: t('statuses.locked') },
  ]

  // T9-A2 — sem ?campaign= → nível 1 (cards de campanha)
  if (!campaignId) {
    return (
      <CampaignsLevel
        campaigns={campaigns as EvaluationCampaign[]}
        summaries={summaries}
        onOpen={(id) => setSearchParams({ campaign: id })}
        t={t}
      />
    )
  }
  const selectedCampaign = (campaigns as EvaluationCampaign[]).find(c => c.campaign_id === campaignId)

  return (
    <div className="flex flex-col h-full">
      {/* Filter bar — nível 2 (escopado a uma campanha) */}
      <div className="border-b bg-white px-4 py-2 flex items-center gap-3 flex-wrap">
        <button onClick={() => setSearchParams({})} className="text-sm text-primary hover:underline">
          ← {t('campaignsLevel.back', { defaultValue: 'Campanhas' })}
        </button>
        <span className="text-sm text-muted-light">/</span>
        <span className="text-sm font-medium text-dark truncate max-w-[220px]">{selectedCampaign?.name ?? campaignId}</span>

        {/* Quick filter: Aguardando minha ação */}
        <button
          onClick={() => setMyActionsOnly(v => !v)}
          className={`text-xs px-3 py-1 rounded-full border font-medium transition-colors ${
            myActionsOnly
              ? 'bg-primary text-white border-primary'
              : 'bg-white text-muted border-border-strong hover:bg-surface-muted'
          }`}
        >
          ⚡ {t('filters.myActions')}
        </button>

        {/* Status filter */}
        <select
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value)}
          className="border border-border-strong rounded px-2 py-1 text-sm"
        >
          {STATUS_OPTIONS.map(o => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>

        <div className="flex-1" />

        {/* Token admin (for adjudication) */}
        <input
          type="password"
          value={adminToken}
          onChange={e => setAdminToken(e.target.value)}
          placeholder={t('filters.adminTokenPlaceholder')}
          className="border border-border-strong rounded px-2 py-1 text-xs w-44"
        />

        {jwtToken
          ? <span className="text-xs text-green-text">✓ {t('filters.authenticated')}</span>
          : <span className="text-xs text-warning">⚠ {t('filters.loginRequired')}</span>
        }

        <button onClick={reload} className="text-xs text-muted hover:text-dark border border-border rounded px-2 py-1">
          ↺ {t('filters.reload')}
        </button>
      </div>

      {/* Main area: table + drill-down */}
      <div className="flex flex-1 min-h-0">
        {/* Table */}
        <div className="flex-1 overflow-auto">
          {loading && (
            <div className="flex items-center justify-center h-32 text-muted-light text-sm">{t('loading')}</div>
          )}
          {!loading && error && (
            <div className="p-4 text-red-text text-sm">{t('error.loading')}: {error}</div>
          )}
          {!loading && !error && displayed.length === 0 && (
            <div className="flex flex-col items-center justify-center h-48 text-muted-light">
              <span className="text-3xl mb-2">⭐</span>
              <p className="text-sm">{t('empty.noEvaluations')}</p>
              {myActionsOnly && (
                <p className="text-xs mt-1">{t('empty.noActions')}</p>
              )}
            </div>
          )}
          {!loading && displayed.length > 0 && (
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="border-b bg-surface-muted text-xs text-muted">
                  <th className="text-left px-4 py-2 font-medium">{t('table.agent', { defaultValue: 'Agente avaliado (segmento)' })}</th>
                  <th className="text-left px-4 py-2 font-medium">{t('table.evaluator')}</th>
                  <th className="text-center px-4 py-2 font-medium">{t('table.score')}</th>
                  <th className="text-left px-4 py-2 font-medium">{t('table.status')}</th>
                  <th className="text-left px-4 py-2 font-medium">{t('table.actions')}</th>
                  <th className="text-left px-4 py-2 font-medium">{t('table.date')}</th>
                </tr>
              </thead>
              <tbody>
                {displayed.map(r => {
                  const isSelected = selected?.result_id === r.result_id
                  const hasAction  = (r.available_actions?.length ?? 0) > 0
                  return (
                    <tr
                      key={r.result_id}
                      onClick={() => setSelected(isSelected ? null : r)}
                      className={`border-b cursor-pointer transition-colors ${
                        isSelected
                          ? 'bg-primary-light border-l-2 border-l-primary'
                          : 'hover:bg-surface-muted'
                      }`}
                    >
                      <td className="px-4 py-2">
                        <div className="text-xs text-dark font-medium truncate max-w-[170px]">
                          {r.evaluated_user_id || r.segment_id || r.evaluator_id || '—'}
                        </div>
                        <div className="flex items-center gap-1 text-[11px] text-muted-light mt-0.5">
                          {r.evaluated_agent_type && (
                            <span title={r.evaluated_agent_type}>{r.evaluated_agent_type === 'human_agent' ? '👤' : '🤖'}</span>
                          )}
                          <code className="break-all truncate max-w-[150px]">{r.session_id}</code>
                        </div>
                      </td>
                      <td className="px-4 py-2 text-xs text-muted truncate max-w-[140px]">
                        {r.evaluator_id}
                      </td>
                      <td className="px-4 py-2 text-center">
                        <ScorePill score={r.overall_score} />
                      </td>
                      <td className="px-4 py-2">
                        <div className="flex items-center gap-1">
                          <ResultStateBadge r={r} t={t} />
                          {r.locked && <span className="text-xs text-muted-light" title={t('detail.locked')}>🔒</span>}
                        </div>
                      </td>
                      <td className="px-4 py-2">
                        {hasAction ? (
                          <div className="flex gap-1 flex-wrap">
                            {r.available_actions.map(a => (
                              <span
                                key={a}
                                className={`text-xs px-2 py-0.5 rounded font-medium ${
                                  a === 'review'
                                    ? 'bg-primary-light text-primary'
                                    : 'bg-contested-light text-contested-text'
                                }`}
                              >
                                {a === 'review' ? '✓ ' + t('detail.review') : '⚑ ' + t('detail.contest')}
                              </span>
                            ))}
                          </div>
                        ) : (
                          <span className="text-xs text-border-strong">—</span>
                        )}
                      </td>
                      <td className="px-4 py-2 text-xs text-muted-light whitespace-nowrap">
                        {r.result_state === 'finalized' && r.finalized_at ? (
                          <div>{fmt(r.finalized_at)}</div>
                        ) : r.deadline_at && hasAction ? (
                          <div className="text-warning-text">{t('table.deadline', { defaultValue: 'prazo' })}: {fmt(r.deadline_at)}</div>
                        ) : (
                          <div>{fmt(r.created_at)}</div>
                        )}
                        <div className="text-border-strong">{t('detail.elapsed', { defaultValue: 'no estado' })} {elapsed(r.updated_at)}</div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </div>

        {/* Drill-down panel */}
        {selected && (
          <DetailPanel
            result={selected}
            jwtToken={jwtToken}
            adminToken={adminToken}
            userId={userId}
            onClose={() => setSelected(null)}
            onAction={() => { reload(); setSelected(null) }}
          />
        )}
      </div>
    </div>
  )
}
