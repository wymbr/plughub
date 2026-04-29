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
import { useAuth } from '@/auth/useAuth'
import {
  useResults,
  useCampaigns,
  useContestations,
  reviewResult,
  createContestation,
  adjudicateContestation,
} from '@/api/evaluation-hooks'
import type {
  EvaluationResultWithActions,
  EvaluationCriterionResponse,
  EvaluationContestation,
  EvaluationCampaign,
} from '@/types'

const TENANT = import.meta.env.VITE_TENANT_ID ?? 'tenant_demo'

// ── Shared helpers ─────────────────────────────────────────────────────────────

function ScorePill({ score }: { score: number }) {
  const bg =
    score >= 0.8 ? 'bg-green-100 text-green-800' :
    score >= 0.6 ? 'bg-yellow-100 text-yellow-800' :
                   'bg-red-100 text-red-800'
  // Display as 0–10 scale if ≤ 1, raw otherwise
  const display = score <= 1 ? (score * 10).toFixed(1) : score.toFixed(1)
  return <span className={`px-2 py-0.5 rounded text-sm font-bold ${bg}`}>{display}</span>
}

const STATUS_LABELS: Record<string, string> = {
  submitted:        'Submetido',
  approved:         'Aprovado',
  adjusted_approved:'Aprovado c/ ajuste',
  rejected:         'Rejeitado',
  contested:        'Contestado',
  locked:           'Bloqueado',
}

const STATUS_STYLES: Record<string, string> = {
  submitted:        'bg-blue-100 text-blue-700',
  approved:         'bg-green-100 text-green-800',
  adjusted_approved:'bg-teal-100 text-teal-700',
  rejected:         'bg-red-100 text-red-700',
  contested:        'bg-orange-100 text-orange-700',
  locked:           'bg-gray-100 text-gray-500',
}

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium ${STATUS_STYLES[status] ?? 'bg-gray-100 text-gray-500'}`}>
      {STATUS_LABELS[status] ?? status}
    </span>
  )
}

function fmt(iso: string) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' })
}

// ── CriterionRow ───────────────────────────────────────────────────────────────

function CriterionRow({ cr }: { cr: EvaluationCriterionResponse }) {
  return (
    <div className="border-b last:border-0 py-2 px-3 text-sm">
      <div className="flex items-start gap-3">
        <span className="font-mono text-xs text-gray-400 w-36 shrink-0 pt-0.5">{cr.criterion_id}</span>
        <div className="flex-1">
          {cr.na ? (
            <span className="text-gray-400 italic">N/A — {cr.na_reason}</span>
          ) : (
            <>
              <div className="flex items-center gap-2 mb-1">
                <ScorePill score={cr.value ?? 0} />
                {cr.evidence_refs && cr.evidence_refs.length > 0 && (
                  <span className="text-xs text-gray-400">refs: [{cr.evidence_refs.join(', ')}]</span>
                )}
              </div>
              <p className="text-gray-600 text-xs leading-relaxed">{cr.justification}</p>
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
    if (!noteOk) { setError('Adicione ao menos uma nota de revisão para ajuste/rejeição'); return }
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
        <div className="border border-orange-200 rounded bg-orange-50">
          <div className="text-xs font-semibold text-orange-700 px-3 pt-3 pb-1">
            ⚑ Contestações abertas
          </div>
          {openContestations.map(c => {
            const parsed = parseContestationReason(c.reason)
            return (
              <div key={c.contestation_id} className="px-3 pb-3 space-y-2">
                <p className="text-xs text-gray-500">De: <strong>{c.contested_by}</strong></p>
                {parsed.map((p, i) => (
                  <div key={i} className={`rounded p-2 text-xs space-y-1 ${p.criterion_id ? 'bg-white border border-orange-100' : 'bg-orange-100'}`}>
                    {p.criterion_id && (
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-gray-500">{p.criterion_id}</span>
                        <span className="text-gray-400">{p.score_label}</span>
                      </div>
                    )}
                    {p.system_evaluation && (
                      <p className="text-gray-500 italic">Avaliado como: {p.system_evaluation}</p>
                    )}
                    <p className="text-orange-800 font-medium">{p.disagreement || p.criterion_id === '' ? p.disagreement : '—'}</p>
                  </div>
                ))}
                <div className="flex gap-2 pt-1">
                  <button
                    onClick={() => adjudicate(c, 'accepted')}
                    className="text-xs bg-green-100 text-green-700 px-2 py-0.5 rounded hover:bg-green-200"
                  >✓ Procedente</button>
                  <button
                    onClick={() => adjudicate(c, 'rejected')}
                    className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded hover:bg-gray-200"
                  >✕ Improcedente</button>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Decision */}
      <div>
        <div className="text-xs font-semibold text-gray-600 mb-2">Decisão</div>
        <div className="flex gap-4">
          {(['approved', 'adjusted_approved', 'rejected'] as const).map(d => (
            <label key={d} className="flex items-center gap-1.5 text-sm cursor-pointer">
              <input type="radio" value={d} checked={decision === d} onChange={() => setDecision(d)} />
              <span className={d === 'approved' ? 'text-green-700' : d === 'adjusted_approved' ? 'text-teal-700' : 'text-red-700'}>
                {d === 'approved' ? '✓ Aprovar' : d === 'adjusted_approved' ? '~ Aprovar c/ ressalvas' : '✕ Rejeitar'}
              </span>
            </label>
          ))}
        </div>
      </div>

      {/* Per-criterion review notes */}
      {criteria.length > 0 && (
        <div>
          <div className="text-xs font-semibold text-gray-600 mb-2">
            Notas por critério
            <span className="text-gray-400 font-normal ml-1">(opcional — preencha os que desejar comentar)</span>
          </div>
          <div className="space-y-2">
            {criteria.map(cr => {
              const scoreVal = cr.value !== null && cr.value !== undefined
                ? (cr.value <= 1 ? (cr.value * 10).toFixed(1) : cr.value.toFixed(1))
                : null
              return (
                <div key={cr.criterion_id} className="border rounded p-2 bg-gray-50">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-mono text-xs text-gray-500">{cr.criterion_id}</span>
                    {scoreVal !== null && (
                      <span className={`text-xs font-bold px-1.5 py-0.5 rounded ${
                        cr.value! >= 0.8 ? 'bg-green-100 text-green-800' :
                        cr.value! >= 0.6 ? 'bg-yellow-100 text-yellow-800' :
                                           'bg-red-100 text-red-800'
                      }`}>{scoreVal}/10</span>
                    )}
                    {cr.na && <span className="text-xs text-gray-400 italic">N/A</span>}
                  </div>
                  <textarea
                    className="w-full border border-gray-200 rounded px-2 py-1.5 text-xs resize-none bg-white"
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
        <label className="text-xs font-semibold text-gray-600 mb-1 block">
          Nota geral{requiresNote && <span className="text-red-500 ml-1">*</span>}
        </label>
        <textarea
          className="w-full border border-gray-300 rounded px-3 py-2 text-sm resize-none"
          rows={2}
          placeholder={requiresNote ? 'Obrigatório para ajuste ou rejeição' : 'Observação geral (opcional)'}
          value={generalNote}
          onChange={e => setGeneralNote(e.target.value)}
        />
      </div>

      {error && <div className="text-red-600 text-xs bg-red-50 border border-red-100 rounded p-2">{error}</div>}

      <button
        onClick={submit}
        disabled={saving || !noteOk}
        className="bg-primary text-white text-sm px-4 py-1.5 rounded hover:bg-blue-800 disabled:opacity-50 w-full"
      >
        {saving ? 'Salvando…' : 'Confirmar revisão'}
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
  const charCount = state.justification.trim().length
  const tooShort  = state.checked && charCount > 0 && charCount < MIN_CONTEST_CHARS
  const scoreVal  = cr.value !== null && cr.value !== undefined
    ? (cr.value <= 1 ? (cr.value * 10).toFixed(1) : cr.value.toFixed(1))
    : null

  return (
    <div className={`border rounded mb-2 transition-colors ${state.checked ? 'border-orange-300 bg-orange-50' : 'border-gray-200 bg-white'}`}>
      {/* Criterion header — always visible */}
      <label className="flex items-start gap-3 p-3 cursor-pointer select-none">
        <input
          type="checkbox"
          checked={state.checked}
          onChange={onToggle}
          className="mt-0.5 accent-orange-500"
        />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-mono text-xs text-gray-500">{cr.criterion_id}</span>
            {cr.na ? (
              <span className="text-xs text-gray-400 italic">N/A</span>
            ) : scoreVal !== null ? (
              <span className={`text-xs font-bold px-1.5 py-0.5 rounded ${
                cr.value! >= 0.8 ? 'bg-green-100 text-green-800' :
                cr.value! >= 0.6 ? 'bg-yellow-100 text-yellow-800' :
                                   'bg-red-100 text-red-800'
              }`}>{scoreVal}/10</span>
            ) : null}
          </div>
          {/* AI evaluator's justification — shown as context */}
          {cr.justification && (
            <p className="text-xs text-gray-500 mt-0.5 leading-relaxed line-clamp-2">
              {cr.justification}
            </p>
          )}
        </div>
        {!cr.na && (
          <span className={`text-xs shrink-0 self-center font-medium ${state.checked ? 'text-orange-600' : 'text-gray-400'}`}>
            {state.checked ? '✓ contestar' : 'contestar'}
          </span>
        )}
      </label>

      {/* Justification input — only when checked and not NA */}
      {state.checked && !cr.na && (
        <div className="px-3 pb-3 space-y-1">
          <div className="flex items-center justify-between">
            <label className="text-xs font-semibold text-orange-700">
              Justificativa <span className="text-red-500">*</span>
            </label>
            <span className={`text-xs ${tooShort ? 'text-red-500' : charCount >= MIN_CONTEST_CHARS ? 'text-green-600' : 'text-gray-400'}`}>
              {charCount}/{MIN_CONTEST_CHARS} mín.
            </span>
          </div>
          <textarea
            className={`w-full border rounded px-3 py-2 text-sm resize-none transition-colors ${
              tooShort ? 'border-red-300' : charCount >= MIN_CONTEST_CHARS ? 'border-green-300' : 'border-orange-200'
            }`}
            rows={3}
            placeholder="Por que o score deste critério está incorreto? Cite trechos da transcrição se possível."
            value={state.justification}
            onChange={e => onJustification(e.target.value)}
            autoFocus
          />
          {tooShort && (
            <p className="text-xs text-red-500">{MIN_CONTEST_CHARS - charCount} caracteres restantes</p>
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
    if (contestedEntries.length === 0) { setError('Selecione ao menos um critério para contestar'); return }
    if (!allValid) { setError('Todas as justificativas precisam ter ao menos 30 caracteres'); return }
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
      <div className="bg-orange-50 border border-orange-200 rounded p-3">
        <p className="text-xs font-semibold text-orange-800 mb-0.5">⚑ Contestar avaliação</p>
        <p className="text-xs text-orange-700">
          Selecione os critérios com score incorreto e justifique cada um. Seu supervisor receberá a contestação com o contexto completo.
        </p>
      </div>

      {/* Criteria list */}
      {criteria.length === 0 ? (
        <p className="text-xs text-gray-400 italic text-center py-4">Sem critérios disponíveis para contestar</p>
      ) : (
        <div>
          <div className="text-xs font-semibold text-gray-600 mb-2">
            Critérios avaliados — {contestedEntries.length} selecionado{contestedEntries.length !== 1 ? 's' : ''}
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
        <div className="text-red-600 text-xs bg-red-50 border border-red-100 rounded p-2">{error}</div>
      )}

      <div className="flex gap-2">
        <button
          onClick={onCancel}
          className="flex-1 text-sm px-4 py-1.5 rounded border border-gray-300 text-gray-600 hover:bg-gray-50"
        >
          Cancelar
        </button>
        <button
          onClick={submit}
          disabled={saving || !allValid}
          className="flex-1 bg-orange-600 text-white text-sm px-4 py-1.5 rounded hover:bg-orange-700 disabled:opacity-50"
        >
          {saving ? 'Enviando…' : `Enviar contestação${contestedEntries.length > 0 ? ` (${contestedEntries.length})` : ''}`}
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
  result: EvaluationResultWithActions
  jwtToken: string
  adminToken: string
  userId: string
  onClose: () => void
  onAction: () => void
}) {
  const [mode, setMode] = useState<'view' | 'review' | 'contest'>('view')
  const canReview  = result.available_actions?.includes('review')
  const canContest = result.available_actions?.includes('contest')

  return (
    <aside className="w-[480px] border-l flex flex-col bg-white overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 p-3 border-b bg-gray-50">
        <div className="flex-1 min-w-0">
          <div className="font-semibold text-sm text-gray-800 truncate">Sessão: {result.session_id}</div>
          <div className="text-xs text-gray-400 truncate">Avaliador: {result.evaluator_id}</div>
        </div>
        <ScorePill score={result.overall_score} />
        <StatusBadge status={result.eval_status} />
        {result.locked && <span title="Bloqueado">🔒</span>}
        <button onClick={onClose} className="text-gray-400 hover:text-gray-600 ml-1 text-lg leading-none">✕</button>
      </div>

      {/* Action bar */}
      {(canReview || canContest) && !result.locked && (
        <div className="flex gap-2 px-3 py-2 border-b bg-blue-50">
          {canReview && (
            <button
              onClick={() => setMode(m => m === 'review' ? 'view' : 'review')}
              className={`text-xs px-3 py-1 rounded font-medium border transition-colors ${
                mode === 'review'
                  ? 'bg-primary text-white border-primary'
                  : 'bg-white text-primary border-primary hover:bg-blue-50'
              }`}
            >
              ✓ Revisar
            </button>
          )}
          {canContest && (
            <button
              onClick={() => setMode(m => m === 'contest' ? 'view' : 'contest')}
              className={`text-xs px-3 py-1 rounded font-medium border transition-colors ${
                mode === 'contest'
                  ? 'bg-orange-600 text-white border-orange-600'
                  : 'bg-white text-orange-600 border-orange-600 hover:bg-orange-50'
              }`}
            >
              ⚑ Contestar
            </button>
          )}
          {result.action_required && (
            <span className="text-xs text-gray-500 self-center ml-auto">
              Aguardando: {result.action_required === 'review' ? 'revisão' : 'contestação'}
              {result.deadline_at && ` · prazo ${fmt(result.deadline_at)}`}
            </span>
          )}
        </div>
      )}

      {/* Body */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* Overview */}
        {result.overall_observation && (
          <div className="bg-gray-50 rounded p-3">
            <div className="text-xs font-semibold text-gray-600 mb-1">Observação geral</div>
            <p className="text-sm text-gray-700">{result.overall_observation}</p>
          </div>
        )}

        {/* Highlights / Improvements */}
        <div className="grid grid-cols-2 gap-3">
          {(result.highlights ?? []).length > 0 && (
            <div>
              <div className="text-xs font-semibold text-green-700 mb-1">✓ Pontos positivos</div>
              <ul className="text-xs text-gray-600 space-y-0.5">
                {result.highlights.map((h, i) => <li key={i}>• {h}</li>)}
              </ul>
            </div>
          )}
          {(result.improvement_points ?? []).length > 0 && (
            <div>
              <div className="text-xs font-semibold text-orange-700 mb-1">↑ Melhorias</div>
              <ul className="text-xs text-gray-600 space-y-0.5">
                {result.improvement_points.map((p, i) => <li key={i}>• {p}</li>)}
              </ul>
            </div>
          )}
        </div>

        {/* Compliance flags */}
        {(result.compliance_flags ?? []).length > 0 && (
          <div>
            <div className="text-xs font-semibold text-red-700 mb-1">⚠ Flags</div>
            <div className="flex flex-wrap gap-1">
              {result.compliance_flags.map(f => (
                <span key={f} className="bg-red-50 text-red-700 text-xs px-2 py-0.5 rounded">{f}</span>
              ))}
            </div>
          </div>
        )}

        {/* Criteria — hidden when in contest mode (ContestPanel renders its own interactive list) */}
        {mode !== 'contest' && (result.criterion_responses ?? []).length > 0 && (
          <div>
            <div className="text-xs font-semibold text-gray-600 mb-2">Critérios avaliados</div>
            <div className="border rounded">
              {result.criterion_responses.map(cr => (
                <CriterionRow key={cr.criterion_id} cr={cr} />
              ))}
            </div>
          </div>
        )}

        {/* Metadata */}
        <div className="text-xs text-gray-400 space-y-0.5">
          <div>Campanha: {result.campaign_id ?? '—'}</div>
          <div>Round atual: {result.current_round ?? 0}</div>
          {result.lock_reason && <div>Motivo do bloqueio: {result.lock_reason}</div>}
          <div>Criado em: {fmt(result.created_at)}</div>
        </div>

        {/* Action panels */}
        {mode === 'review' && (
          <ReviewPanel
            result={result}
            jwtToken={jwtToken}
            adminToken={adminToken}
            onDone={() => { setMode('view'); onAction() }}
          />
        )}
        {mode === 'contest' && (
          <ContestPanel
            result={result}
            userId={userId}
            jwtToken={jwtToken}
            onDone={() => { setMode('view'); onAction() }}
            onCancel={() => setMode('view')}
          />
        )}
      </div>
    </aside>
  )
}

// ── Main Page ──────────────────────────────────────────────────────────────────

const STATUS_OPTIONS = [
  { value: '', label: 'Todos os status' },
  { value: 'submitted', label: 'Submetido' },
  { value: 'approved', label: 'Aprovado' },
  { value: 'adjusted_approved', label: 'Aprovado c/ ajuste' },
  { value: 'rejected', label: 'Rejeitado' },
  { value: 'contested', label: 'Contestado' },
  { value: 'locked', label: 'Bloqueado' },
]

export default function AvaliacoesPage() {
  const { session, getAccessToken } = useAuth()
  const [jwtToken, setJwtToken]       = useState('')
  const [adminToken, setAdminToken]   = useState('')
  const [selected, setSelected]       = useState<EvaluationResultWithActions | null>(null)

  // Filters
  const [statusFilter, setStatusFilter]     = useState('')
  const [campaignFilter, setCampaignFilter] = useState('')
  const [myActionsOnly, setMyActionsOnly]   = useState(false)

  // Resolve JWT on mount / session change
  useEffect(() => {
    getAccessToken().then(t => setJwtToken(t ?? '')).catch(() => {})
  }, [getAccessToken, session])

  // Load campaigns for the campaign filter dropdown
  const { campaigns } = useCampaigns(TENANT)

  // Build filters for useResults
  const filters = {
    evalStatus:     statusFilter    || undefined,
    campaignId:     campaignFilter  || undefined,
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

  const userId = session?.userId ?? ''

  return (
    <div className="flex flex-col h-full">
      {/* Filter bar */}
      <div className="border-b bg-white px-4 py-2 flex items-center gap-3 flex-wrap">
        <span className="text-sm font-medium text-gray-700">Avaliações</span>

        {/* Quick filter: Aguardando minha ação */}
        <button
          onClick={() => setMyActionsOnly(v => !v)}
          className={`text-xs px-3 py-1 rounded-full border font-medium transition-colors ${
            myActionsOnly
              ? 'bg-primary text-white border-primary'
              : 'bg-white text-gray-600 border-gray-300 hover:bg-gray-50'
          }`}
        >
          ⚡ Aguardando minha ação
        </button>

        {/* Status filter */}
        <select
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value)}
          className="border border-gray-300 rounded px-2 py-1 text-sm"
        >
          {STATUS_OPTIONS.map(o => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>

        {/* Campaign filter */}
        <select
          value={campaignFilter}
          onChange={e => setCampaignFilter(e.target.value)}
          className="border border-gray-300 rounded px-2 py-1 text-sm max-w-[200px]"
        >
          <option value="">Todas as campanhas</option>
          {(campaigns as EvaluationCampaign[]).map(c => (
            <option key={c.campaign_id} value={c.campaign_id}>{c.name}</option>
          ))}
        </select>

        <div className="flex-1" />

        {/* Token admin (for adjudication) */}
        <input
          type="password"
          value={adminToken}
          onChange={e => setAdminToken(e.target.value)}
          placeholder="Token admin (adjudicação)"
          className="border border-gray-300 rounded px-2 py-1 text-xs w-44"
        />

        {jwtToken
          ? <span className="text-xs text-green-600">✓ Autenticado</span>
          : <span className="text-xs text-orange-500">⚠ Faça login</span>
        }

        <button onClick={reload} className="text-xs text-gray-500 hover:text-gray-700 border border-gray-200 rounded px-2 py-1">
          ↺ Recarregar
        </button>
      </div>

      {/* Main area: table + drill-down */}
      <div className="flex flex-1 min-h-0">
        {/* Table */}
        <div className="flex-1 overflow-auto">
          {loading && (
            <div className="flex items-center justify-center h-32 text-gray-400 text-sm">Carregando…</div>
          )}
          {!loading && error && (
            <div className="p-4 text-red-600 text-sm">Erro ao carregar: {error}</div>
          )}
          {!loading && !error && displayed.length === 0 && (
            <div className="flex flex-col items-center justify-center h-48 text-gray-400">
              <span className="text-3xl mb-2">⭐</span>
              <p className="text-sm">Nenhuma avaliação encontrada</p>
              {myActionsOnly && (
                <p className="text-xs mt-1">Sem ações pendentes para você no momento</p>
              )}
            </div>
          )}
          {!loading && displayed.length > 0 && (
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="border-b bg-gray-50 text-xs text-gray-500">
                  <th className="text-left px-4 py-2 font-medium">Sessão</th>
                  <th className="text-left px-4 py-2 font-medium">Campanha</th>
                  <th className="text-left px-4 py-2 font-medium">Avaliador</th>
                  <th className="text-center px-4 py-2 font-medium">Nota</th>
                  <th className="text-left px-4 py-2 font-medium">Status</th>
                  <th className="text-left px-4 py-2 font-medium">Ações</th>
                  <th className="text-left px-4 py-2 font-medium">Data</th>
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
                          ? 'bg-blue-50 border-l-2 border-l-primary'
                          : 'hover:bg-gray-50'
                      }`}
                    >
                      <td className="px-4 py-2">
                        <code className="text-xs bg-gray-100 px-1 rounded text-gray-600 break-all">
                          {r.session_id}
                        </code>
                      </td>
                      <td className="px-4 py-2 text-xs text-gray-500 truncate max-w-[140px]">
                        {r.campaign_id ?? '—'}
                      </td>
                      <td className="px-4 py-2 text-xs text-gray-500 truncate max-w-[140px]">
                        {r.evaluator_id}
                      </td>
                      <td className="px-4 py-2 text-center">
                        <ScorePill score={r.overall_score} />
                      </td>
                      <td className="px-4 py-2">
                        <div className="flex items-center gap-1">
                          <StatusBadge status={r.eval_status} />
                          {r.locked && <span className="text-xs text-gray-400" title="Bloqueado">🔒</span>}
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
                                    ? 'bg-blue-100 text-blue-700'
                                    : 'bg-orange-100 text-orange-700'
                                }`}
                              >
                                {a === 'review' ? '✓ Revisar' : '⚑ Contestar'}
                              </span>
                            ))}
                          </div>
                        ) : (
                          <span className="text-xs text-gray-300">—</span>
                        )}
                      </td>
                      <td className="px-4 py-2 text-xs text-gray-400 whitespace-nowrap">
                        {fmt(r.created_at)}
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
