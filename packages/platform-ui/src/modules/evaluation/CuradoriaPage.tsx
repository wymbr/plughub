/**
 * CuradoriaPage.tsx
 * /evaluation/curadoria — Human curator queue for AI evaluator quality review
 * Arc 13 Fase H — Curation Module
 *
 * Shows CurationReview items pending human review of AI evaluator quality.
 * Curator actions: Approve | Recalibrate | Bias Detected
 */
import React, { useEffect, useState } from 'react'
import { RefreshCw, X, Check, AlertTriangle, EyeOff } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import {
  useCurationQueue, resolveCuration, useCampaigns,
  getBlindContext, blindRescore, blindResolve, useResultTranscript,
} from '@/api/evaluation-hooks'
import type {
  CurationReview, CurationResolvePayload,
  BlindContext, BlindRescoreReveal, BlindCriterionResponse,
  BlindFormCriterion, BlindDimensionDiff, TranscriptMessage,
} from '@/api/evaluation-hooks'

function critLabel(c: BlindFormCriterion): string {
  return c.label || c.name || c.criterion_id
}
function critNaAllowed(c: BlindFormCriterion): boolean {
  return Boolean(c.na_allowed || c.allow_na || c.allows_na)
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function triggerBadge(trigger: string) {
  const parts = trigger.split(',')
  const colorMap: Record<string, string> = {
    score_extremes:  'bg-contested-light text-contested-text',
    deploy_baseline: 'bg-primary-light text-primary',
    score_outlier:   'bg-ai-light text-ai-text',
    na_excess:       'bg-warning-light text-warning-text',
    random_baseline: 'bg-surface-alt text-muted',
    reviewer_signal: 'bg-red-light text-red-text',
  }
  return (
    <div className="flex flex-wrap gap-1">
      {parts.map(p => (
        <span key={p} className={`text-xs px-2 py-0.5 rounded-full font-medium ${colorMap[p] ?? 'bg-surface-alt text-muted'}`}>
          {p.replace(/_/g, ' ')}
        </span>
      ))}
    </div>
  )
}

function severityDot(severity: string) {
  const colors: Record<string, string> = {
    low:    'bg-warning',
    medium: 'bg-contested',
    high:   'bg-red',
  }
  return (
    <span className={`inline-block w-2 h-2 rounded-full ${colors[severity] ?? 'bg-border-strong'}`} title={severity} />
  )
}

// ─── RecalibrateDrawer ────────────────────────────────────────────────────────

interface RecalibrateDrawerProps {
  review: CurationReview
  isBias: boolean
  onClose: () => void
  onSubmit: (payload: CurationResolvePayload) => Promise<void>
}

function RecalibrateDrawer({ review, isBias, onClose, onSubmit }: RecalibrateDrawerProps) {
  const { t } = useTranslation('evaluation')
  const signal = review.calibration_signal
  const [noteText,     setNoteText]     = useState(signal?.observation ?? '')
  const [curatorNotes, setCuratorNotes] = useState('')
  const [dimensionId,  setDimensionId]  = useState(signal?.dimension_id ?? '')
  const [criterionId,  setCriterionId]  = useState(signal?.criterion_id ?? '')
  const [evaluatorId,  setEvaluatorId]  = useState(signal?.evaluator_id ?? '')
  const [skillVersion, setSkillVersion] = useState(signal?.skill_version ?? '')
  const [severity,     setSeverity]     = useState<string>(
    isBias ? 'high' : (signal?.severity ?? 'medium')
  )
  const [saving, setSaving] = useState(false)
  const [err,    setErr]    = useState<string | null>(null)

  const handleSubmit = async () => {
    if (!noteText.trim())    { setErr(t('curation.drawer.errorNote'));         return }
    if (!dimensionId.trim()) { setErr(t('curation.drawer.errorDimension'));    return }
    if (!evaluatorId.trim()) { setErr(t('curation.drawer.errorEvaluator'));    return }
    if (!skillVersion.trim()){ setErr(t('curation.drawer.errorSkillVersion')); return }
    setSaving(true)
    setErr(null)
    try {
      await onSubmit({
        status:                 isBias ? 'bias_flagged' : 'recalibrated',
        curator_notes:          curatorNotes || undefined,
        calibration_note_text:  noteText,
        dimension_id:           dimensionId,
        criterion_id:           criterionId || undefined,
        evaluator_id:           evaluatorId,
        skill_version:          skillVersion,
        severity,
      })
      onClose()
    } catch (e) {
      setErr(String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} />
      <div className="relative w-full max-w-lg bg-white shadow-xl flex flex-col h-full overflow-y-auto">

        <div className="flex items-center justify-between p-4 border-b">
          <div>
            <h2 className="font-semibold text-dark">
              {isBias
                ? <><AlertTriangle className="w-4 h-4 inline mr-1 text-red-text" aria-hidden="true" />{t('curation.drawer.titleBias')}</>
                : <><RefreshCw className="w-4 h-4 inline mr-1 text-warning" aria-hidden="true" />{t('curation.drawer.titleRecalibrate')}</>
              }
            </h2>
            <p className="text-xs text-muted mt-0.5">{review.id}</p>
          </div>
          <button onClick={onClose} className="text-muted-light hover:text-muted" aria-label={t('curation.drawer.cancel')}>
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="flex-1 p-4 space-y-4">
          {signal && (
            <div className="bg-warning-light border border-warning/30 rounded p-3 space-y-1">
              <div className="flex items-center gap-1.5 text-xs font-medium text-warning-text">
                {severityDot(signal.severity)}
                <span>{t('curation.aiSignalLabel')}</span>
              </div>
              <p className="text-sm text-warning-text">{signal.observation}</p>
            </div>
          )}

          <div className="space-y-3">
            <label className="block">
              <span className="text-sm font-medium text-dark">{t('curation.drawer.noteLabel')}</span>
              <p className="text-xs text-muted-light mb-1">{t('curation.drawer.noteHint')}</p>
              <textarea
                className="w-full border rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary/40 resize-none"
                rows={5}
                value={noteText}
                onChange={e => setNoteText(e.target.value)}
                placeholder={t('curation.drawer.notePlaceholder')}
              />
            </label>

            <div className="grid grid-cols-2 gap-3">
              <label className="block">
                <span className="text-sm font-medium text-dark">{t('curation.drawer.dimensionLabel')}</span>
                <input
                  type="text"
                  className="w-full border rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary/40 mt-1"
                  value={dimensionId}
                  onChange={e => setDimensionId(e.target.value)}
                  placeholder={t('curation.drawer.dimensionPlaceholder')}
                />
              </label>
              <label className="block">
                <span className="text-sm font-medium text-dark">{t('curation.drawer.criterionLabel')}</span>
                <input
                  type="text"
                  className="w-full border rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary/40 mt-1"
                  value={criterionId}
                  onChange={e => setCriterionId(e.target.value)}
                  placeholder={t('curation.drawer.criterionPlaceholder')}
                />
              </label>
            </div>
            <p className="text-xs text-muted-light -mt-1">{t('curation.drawer.criterionHint')}</p>

            <div className="grid grid-cols-2 gap-3">
              <label className="block">
                <span className="text-sm font-medium text-dark">{t('curation.drawer.evaluatorLabel')}</span>
                <input
                  type="text"
                  className="w-full border rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary/40 mt-1"
                  value={evaluatorId}
                  onChange={e => setEvaluatorId(e.target.value)}
                  placeholder={t('curation.drawer.evaluatorPlaceholder')}
                />
              </label>
              <label className="block">
                <span className="text-sm font-medium text-dark">{t('curation.drawer.skillVersionLabel')}</span>
                <input
                  type="text"
                  className="w-full border rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary/40 mt-1"
                  value={skillVersion}
                  onChange={e => setSkillVersion(e.target.value)}
                  placeholder={t('curation.drawer.skillVersionPlaceholder')}
                />
              </label>
            </div>

            <label className="block">
              <span className="text-sm font-medium text-dark">{t('curation.drawer.severityLabel')}</span>
              <select
                className="w-full border rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary/40 mt-1"
                value={severity}
                onChange={e => setSeverity(e.target.value)}
                disabled={isBias}
              >
                <option value="low">{t('curation.drawer.severityLow')}</option>
                <option value="medium">{t('curation.drawer.severityMedium')}</option>
                <option value="high">{isBias ? t('curation.drawer.severityHighBias') : t('curation.drawer.severityHigh')}</option>
              </select>
            </label>

            <label className="block">
              <span className="text-sm font-medium text-dark">{t('curation.drawer.curatorNotesLabel')}</span>
              <textarea
                className="w-full border rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary/40 resize-none mt-1"
                rows={2}
                value={curatorNotes}
                onChange={e => setCuratorNotes(e.target.value)}
                placeholder={t('curation.drawer.curatorNotesPlaceholder')}
              />
            </label>
          </div>

          {err && <p className="text-sm text-red-text">{err}</p>}
        </div>

        <div className="border-t p-4 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm border rounded hover:bg-surface-muted"
            disabled={saving}
          >
            {t('curation.drawer.cancel')}
          </button>
          <button
            onClick={handleSubmit}
            disabled={saving}
            className={`px-4 py-2 text-sm rounded text-white font-medium transition-colors ${
              isBias
                ? 'bg-red hover:bg-red-text disabled:opacity-40'
                : 'bg-warning hover:bg-warning-text disabled:opacity-40'
            }`}
          >
            {saving
              ? t('curation.drawer.publishing')
              : isBias
                ? t('curation.drawer.submitBias')
                : t('curation.drawer.submitRecalibrate')
            }
          </button>
        </div>

      </div>
    </div>
  )
}

// ─── BlindScoreDrawer (R8c — re-pontuação cega) ────────────────────────────────

interface BlindScoreDrawerProps {
  review:     CurationReview
  tenantId:   string
  userId:     string
  onClose:    () => void
  onResolved: () => void
}

function diffPct(d: number | null): string {
  return d == null ? '—' : `${Math.round(d * 100)}%`
}

function BlindMessage({ m }: { m: TranscriptMessage }) {
  const isCustomer = (m.author_role ?? '').toLowerCase() === 'customer'
  return (
    <div className={`flex ${isCustomer ? 'justify-start' : 'justify-end'}`}>
      <div className={`max-w-[85%] rounded-lg px-3 py-2 ${isCustomer ? 'bg-surface-alt' : 'bg-primary-light'}`}>
        <div className="text-[11px] text-muted-light mb-0.5">{m.author_role || m.author_id || '—'}</div>
        <div className="text-sm text-dark whitespace-pre-wrap break-words">{m.content}</div>
      </div>
    </div>
  )
}

function BlindScoreDrawer({ review, tenantId, userId, onClose, onResolved }: BlindScoreDrawerProps) {
  const { t } = useTranslation('evaluation')
  const { getAccessToken } = useAuth()
  const [ctx,    setCtx]    = useState<BlindContext | null>(null)
  const [reveal, setReveal] = useState<BlindRescoreReveal | null>(null)
  const [resp,   setResp]   = useState<Record<string, BlindCriterionResponse>>({})
  const [loading, setLoading] = useState(true)
  const [busy,    setBusy]    = useState(false)
  const [err,     setErr]     = useState<string | null>(null)
  const [jwt,     setJwt]     = useState('')
  const [scope,   setScope]   = useState<'segment' | 'contact'>('segment')
  // resolve controls
  const [severity, setSeverity] = useState('medium')
  const [flagBias, setFlagBias] = useState(false)
  const [notes,    setNotes]    = useState('')

  useEffect(() => { getAccessToken().then(tok => setJwt(tok ?? '')).catch(() => {}) }, [getAccessToken])

  useEffect(() => {
    if (!jwt) return            // espera o Bearer (ABAC curar) antes de buscar
    let alive = true
    getBlindContext(review.id, tenantId, jwt)
      .then(c => {
        if (!alive) return
        setCtx(c)
        if (c.already_rescored && c.blind_result) {
          setReveal({
            blind_result:        c.blind_result,
            severity_min:        0,
            ai_overall_score:    c.blind_result.ai_overall_score,
            blind_overall_score: c.blind_result.blind_overall_score,
            per_dimension_diffs: c.blind_result.per_dimension_diffs,
          })
        }
      })
      .catch(e => alive && setErr(String(e)))
      .finally(() => alive && setLoading(false))
    return () => { alive = false }
  }, [review.id, tenantId, jwt])

  // Conversation (masked) — the curator scores AGAINST this, AI scores hidden.
  const { data: transcript, loading: tLoading, error: tError } =
    useResultTranscript(ctx?.result_id ?? null, tenantId, scope, jwt)
  const messages = transcript?.messages ?? []

  const setCrit = (cid: string, patch: Partial<BlindCriterionResponse>) =>
    setResp(prev => ({ ...prev, [cid]: { ...prev[cid], ...patch, criterion_id: cid } }))

  const handleRescore = async () => {
    setBusy(true); setErr(null)
    try {
      const responses = Object.values(resp).filter(
        r => r.na || r.score != null || r.boolean_value != null || r.choice_value != null,
      )
      if (responses.length === 0) { setErr(t('curation.blind.errEmpty')); setBusy(false); return }
      setReveal(await blindRescore(review.id, tenantId, userId, responses, jwt))
    } catch (e) {
      setErr(String(e))
    } finally {
      setBusy(false)
    }
  }

  const handleResolve = async () => {
    setBusy(true); setErr(null)
    try {
      await blindResolve(review.id, tenantId, userId, {
        curator_notes: notes || undefined, severity, flag_bias: flagBias,
      }, jwt)
      onResolved()
      onClose()
    } catch (e) {
      setErr(String(e))
    } finally {
      setBusy(false)
    }
  }

  const dims = ctx?.form?.dimensions ?? []
  const diffs = reveal?.per_dimension_diffs ?? []
  const disagreements = diffs.filter(d => d.disagree).length

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} />
      <div className="relative w-full max-w-5xl bg-white shadow-xl flex flex-col h-full">

        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b">
          <div>
            <h2 className="font-semibold text-dark">
              <EyeOff className="w-4 h-4 inline mr-1 text-primary" aria-hidden="true" />
              {reveal ? t('curation.blind.titleReveal') : t('curation.blind.titleScore')}
            </h2>
            <p className="text-xs text-muted mt-0.5">{review.evaluation_instance_id}</p>
          </div>
          <button onClick={onClose} className="text-muted-light hover:text-muted" aria-label={t('curation.drawer.cancel')}>
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Two panes: transcript (left) | scoring/diff (right) */}
        <div className="flex-1 flex min-h-0">

          {/* LEFT — conversation (masked) */}
          <div className="w-1/2 border-r flex flex-col min-h-0">
            <div className="flex items-center gap-2 px-3 py-2 border-b bg-surface-muted">
              <span className="text-sm font-semibold text-dark">{t('curation.blind.conversation')}</span>
              <span className="text-[11px] px-1.5 py-0.5 rounded bg-warning-light text-warning-text">🔒 {t('curation.blind.masked')}</span>
              <div className="ml-auto flex rounded border border-border-strong overflow-hidden text-xs">
                {(['segment', 'contact'] as const).map(s => (
                  <button
                    key={s}
                    onClick={() => setScope(s)}
                    className={`px-2 py-1 transition-colors ${scope === s ? 'bg-primary text-white' : 'bg-white text-muted hover:bg-surface-muted'}`}
                  >
                    {t(`curation.blind.scope_${s}`)}
                  </button>
                ))}
              </div>
            </div>
            <div className="flex-1 overflow-y-auto p-3 space-y-2">
              {tLoading && <div className="text-xs text-muted-light text-center py-8">⟳ {t('curation.loading')}</div>}
              {tError && <div className="text-xs text-red-text bg-red-light border border-red/20 rounded p-2">{String(tError)}</div>}
              {!tLoading && !tError && messages.length === 0 && (
                <div className="text-sm text-muted-light text-center py-8">{t('curation.blind.noMessages')}</div>
              )}
              {messages.map(m => <BlindMessage key={m.stream_entry_id} m={m} />)}
            </div>
          </div>

          {/* RIGHT — scoring (blind) / reveal (diff) */}
          <div className="w-1/2 flex flex-col min-h-0">
            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              {loading && <p className="text-sm text-muted-light">{t('curation.loading')}</p>}
              {err && <p className="text-sm text-red-text">{err}</p>}

              {/* SCORE PHASE — criteria inputs, AI score hidden */}
              {!loading && !reveal && (
                <>
                  <div className="bg-primary-light border border-primary/20 rounded p-3 text-xs text-primary">
                    {t('curation.blind.scoreHint')}
                  </div>
                  {dims.map(dim => (
                    <div key={dim.dimension_id} className="border rounded-lg p-3 space-y-3">
                      <div className="text-sm font-semibold text-dark">{dim.label || dim.name || dim.dimension_id}</div>
                      {(dim.criteria ?? []).map(c => {
                        const r = resp[c.criterion_id] || { criterion_id: c.criterion_id }
                        const type = c.type || 'score'
                        const na = Boolean(r.na)
                        return (
                          <div key={c.criterion_id} className="flex items-start justify-between gap-3">
                            <div className="flex-1 min-w-0">
                              <p className="text-sm text-dark">{critLabel(c)}</p>
                              <p className="text-xs text-muted-light">{c.criterion_id}</p>
                            </div>
                            <div className="flex items-center gap-2 flex-shrink-0">
                              {type === 'boolean' && (
                                <select
                                  disabled={na}
                                  className="border rounded px-2 py-1 text-sm disabled:opacity-40"
                                  value={r.boolean_value == null ? '' : String(r.boolean_value)}
                                  onChange={e => setCrit(c.criterion_id, { boolean_value: e.target.value === 'true', na: false })}
                                >
                                  <option value="">—</option>
                                  <option value="true">{t('curation.blind.yes')}</option>
                                  <option value="false">{t('curation.blind.no')}</option>
                                </select>
                              )}
                              {type === 'choice' && (
                                <select
                                  disabled={na}
                                  className="border rounded px-2 py-1 text-sm disabled:opacity-40"
                                  value={r.choice_value ?? ''}
                                  onChange={e => setCrit(c.criterion_id, { choice_value: e.target.value, na: false })}
                                >
                                  <option value="">—</option>
                                  {Object.keys(c.choice_scores ?? {}).map(k => <option key={k} value={k}>{k}</option>)}
                                </select>
                              )}
                              {(type === 'score' || type === 'auto_computed') && (
                                <input
                                  type="number"
                                  disabled={na}
                                  min={c.min_score ?? 0}
                                  max={c.max_score ?? 10}
                                  step="0.5"
                                  className="border rounded px-2 py-1 text-sm w-20 disabled:opacity-40"
                                  value={r.score ?? ''}
                                  onChange={e => setCrit(c.criterion_id, { score: e.target.value === '' ? undefined : Number(e.target.value), na: false })}
                                />
                              )}
                              {critNaAllowed(c) && (
                                <label className="flex items-center gap-1 text-xs text-muted">
                                  <input type="checkbox" checked={na} onChange={e => setCrit(c.criterion_id, { na: e.target.checked })} />
                                  {t('curation.blind.na')}
                                </label>
                              )}
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  ))}
                </>
              )}

              {/* REVEAL PHASE — AI vs human diff per dimension */}
              {reveal && (
                <>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="bg-surface-alt border rounded p-3 text-center">
                      <div className="text-xs text-muted">{t('curation.blind.aiOverall')}</div>
                      <div className="text-xl font-bold text-dark">{reveal.ai_overall_score?.toFixed(2) ?? '—'}</div>
                    </div>
                    <div className="bg-primary-light border border-primary/20 rounded p-3 text-center">
                      <div className="text-xs text-primary">{t('curation.blind.humanOverall')}</div>
                      <div className="text-xl font-bold text-primary">{reveal.blind_overall_score?.toFixed(2) ?? '—'}</div>
                    </div>
                  </div>

                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-xs text-muted border-b">
                        <th className="text-left py-1">{t('curation.blind.dimension')}</th>
                        <th className="text-right py-1">{t('curation.blind.ai')}</th>
                        <th className="text-right py-1">{t('curation.blind.human')}</th>
                        <th className="text-right py-1">{t('curation.blind.diff')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {diffs.map((d: BlindDimensionDiff) => (
                        <tr key={d.dimension_id} className={`border-b ${d.disagree ? 'bg-red-light' : ''}`}>
                          <td className="py-1 text-dark">
                            {d.dimension_id}
                            {d.disagree && (
                              <span className="ml-2 text-xs px-1.5 py-0.5 rounded-full bg-red text-white">
                                {t('curation.blind.disagree')}
                              </span>
                            )}
                          </td>
                          <td className="py-1 text-right text-muted">{d.ai_score?.toFixed(1) ?? '—'}</td>
                          <td className="py-1 text-right text-dark font-medium">{d.human_score?.toFixed(1) ?? '—'}</td>
                          <td className="py-1 text-right">{diffPct(d.diff)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>

                  {review.status === 'pending' ? (
                    <div className="border-t pt-3 space-y-3">
                      <p className="text-xs text-muted">
                        {disagreements > 0
                          ? t('curation.blind.willRecalibrate', { count: disagreements })
                          : t('curation.blind.willApprove')}
                      </p>
                      {disagreements > 0 && (
                        <>
                          <label className="flex items-center gap-2 text-sm">
                            <input type="checkbox" checked={flagBias} onChange={e => setFlagBias(e.target.checked)} />
                            {t('curation.blind.flagBias')}
                          </label>
                          <select
                            className="w-full border rounded px-3 py-2 text-sm"
                            value={severity}
                            onChange={e => setSeverity(e.target.value)}
                            disabled={flagBias}
                          >
                            <option value="low">{t('curation.drawer.severityLow')}</option>
                            <option value="medium">{t('curation.drawer.severityMedium')}</option>
                            <option value="high">{t('curation.drawer.severityHigh')}</option>
                          </select>
                          <textarea
                            className="w-full border rounded px-3 py-2 text-sm resize-none"
                            rows={2}
                            value={notes}
                            onChange={e => setNotes(e.target.value)}
                            placeholder={t('curation.blind.notesPlaceholder')}
                          />
                        </>
                      )}
                    </div>
                  ) : (
                    <p className="text-xs text-muted border-t pt-3">{t('curation.blind.alreadyResolved', { status: review.status })}</p>
                  )}
                </>
              )}
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="border-t p-4 flex justify-end gap-2">
          <button onClick={onClose} disabled={busy} className="px-4 py-2 text-sm border rounded hover:bg-surface-muted">
            {t('curation.drawer.cancel')}
          </button>
          {!reveal && !loading && (
            <button
              onClick={handleRescore}
              disabled={busy}
              className="px-4 py-2 text-sm rounded text-white font-medium bg-primary hover:bg-primary/90 disabled:opacity-40"
            >
              {busy ? t('curation.blind.revealing') : t('curation.blind.reveal')}
            </button>
          )}
          {reveal && review.status === 'pending' && (
            <button
              onClick={handleResolve}
              disabled={busy}
              className="px-4 py-2 text-sm rounded text-white font-medium bg-primary hover:bg-primary/90 disabled:opacity-40"
            >
              {busy ? t('curation.blind.resolving') : t('curation.blind.resolve')}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

// ─── CurationCard ──────────────────────────────────────────────────────────────

interface CurationCardProps {
  review: CurationReview
  tenantId: string
  userId: string
  onResolve: (reviewId: string, payload: CurationResolvePayload) => Promise<void>
  onReload: () => void
}

function CurationCard({ review, tenantId, userId, onResolve, onReload }: CurationCardProps) {
  const { t } = useTranslation('evaluation')
  const [drawer, setDrawer] = useState<'recalibrate' | 'bias' | 'blind' | null>(null)
  const [approving, setApproving] = useState(false)
  const isBlind = review.mode === 'blind'

  const handleApprove = async () => {
    setApproving(true)
    try {
      await onResolve(review.id, { status: 'approved' })
    } finally {
      setApproving(false)
    }
  }

  const signal = review.calibration_signal

  return (
    <>
      <div className="bg-white border rounded-lg p-4 space-y-3 hover:shadow-sm transition-shadow">
        <div className="flex items-start justify-between gap-3">
          <div className="space-y-1.5 flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              {isBlind ? (
                <span className="text-xs px-2 py-0.5 rounded-full font-medium bg-primary-light text-primary inline-flex items-center gap-1">
                  <EyeOff className="w-3 h-3" aria-hidden="true" />{t('curation.blind.badge')}
                </span>
              ) : triggerBadge(review.trigger)}
              {review.campaign_id && (
                <span className="text-xs text-muted-light truncate">{review.campaign_id}</span>
              )}
              {isBlind && review.expired_at && (
                <span className="text-xs px-2 py-0.5 rounded-full bg-surface-alt text-muted">{t('curation.blind.expired')}</span>
              )}
            </div>
            <p className="text-xs text-muted font-mono truncate">{review.evaluation_instance_id}</p>
            {signal && !isBlind && (
              <div className="bg-warning-light border border-warning/20 rounded px-3 py-2 space-y-0.5">
                <div className="flex items-center gap-1.5 text-xs font-medium text-warning-text">
                  {severityDot(signal.severity)}
                  <span>{t('curation.aiSignalCard', { dimension: signal.dimension_id })}</span>
                </div>
                <p className="text-xs text-warning-text line-clamp-2">{signal.observation}</p>
              </div>
            )}
          </div>
          <div className="text-xs text-muted-light flex-shrink-0 text-right">
            {new Date(review.created_at).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' })}
          </div>
        </div>

        {isBlind ? (
          <div className="flex gap-2 pt-1 border-t">
            <button
              onClick={() => setDrawer('blind')}
              className="flex-1 py-1.5 text-xs rounded border border-primary/30 text-primary hover:bg-primary-light font-medium transition-colors"
            >
              <EyeOff className="w-3 h-3 inline mr-1" aria-hidden="true" />
              {review.status === 'pending' ? t('curation.blind.scoreCta') : t('curation.blind.viewCta')}
            </button>
          </div>
        ) : (
          <div className="flex gap-2 pt-1 border-t">
            <button
              onClick={handleApprove}
              disabled={approving}
              className="flex-1 py-1.5 text-xs rounded border border-green/30 text-green-text hover:bg-green-light disabled:opacity-50 font-medium transition-colors"
            >
              {approving ? t('curation.approving') : <><Check className="w-3 h-3 inline mr-1" aria-hidden="true" />{t('curation.approve')}</>}
            </button>
            <button
              onClick={() => setDrawer('recalibrate')}
              className="flex-1 py-1.5 text-xs rounded border border-warning/30 text-warning-text hover:bg-warning-light font-medium transition-colors"
            >
              <RefreshCw className="w-3 h-3 inline mr-1" aria-hidden="true" />{t('curation.recalibrate')}
            </button>
            <button
              onClick={() => setDrawer('bias')}
              className="flex-1 py-1.5 text-xs rounded border border-red/30 text-red-text hover:bg-red-light font-medium transition-colors"
            >
              <AlertTriangle className="w-3 h-3 inline mr-1" aria-hidden="true" />{t('curation.bias')}
            </button>
          </div>
        )}
      </div>

      {drawer === 'blind' && (
        <BlindScoreDrawer
          review={review}
          tenantId={tenantId}
          userId={userId}
          onClose={() => setDrawer(null)}
          onResolved={onReload}
        />
      )}
      {(drawer === 'recalibrate' || drawer === 'bias') && (
        <RecalibrateDrawer
          review={review}
          isBias={drawer === 'bias'}
          onClose={() => setDrawer(null)}
          onSubmit={payload => onResolve(review.id, payload)}
        />
      )}
    </>
  )
}

// ─── CuradoriaPage ─────────────────────────────────────────────────────────────

export default function CuradoriaPage() {
  const { t } = useTranslation('evaluation')
  const { session } = useAuth()
  const tenantId = session?.tenantId ?? ''
  const userId   = session?.userId   ?? ''
  const jwt      = session?.accessToken ?? ''   // ABAC curar (Bearer)

  const [campaignFilter, setCampaignFilter] = useState('')
  const [statusFilter,   setStatusFilter]   = useState('pending')

  const { campaigns } = useCampaigns(tenantId)
  const { reviews, total, loading, error, reload } = useCurationQueue(
    tenantId,
    {
      status:      statusFilter || undefined,
      campaign_id: campaignFilter || undefined,
      limit:       100,
    },
    15_000,  // poll every 15s
    jwt,
  )

  const pendingCount = reviews.filter(r => r.status === 'pending').length

  const handleResolve = async (reviewId: string, payload: CurationResolvePayload) => {
    await resolveCuration(reviewId, tenantId, userId, payload, jwt)
    reload()
  }

  const emptyStatus = statusFilter === 'pending'
    ? t('curation.emptyPending')
    : t('curation.emptyOther')

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-6">

      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-dark">{t('curation.title')}</h1>
          <p className="text-sm text-muted mt-0.5">{t('curation.subtitle')}</p>
        </div>
        <button
          onClick={reload}
          className="text-sm text-muted hover:text-dark border rounded px-3 py-1.5"
        >
          {t('curation.refresh')}
        </button>
      </div>

      {/* KPI strip */}
      <div className="grid grid-cols-3 gap-3">
        {[
          { label: t('curation.kpi.pending'),     value: pendingCount, color: 'text-contested' },
          { label: t('curation.kpi.totalLoaded'), value: total,        color: 'text-dark' },
          { label: t('curation.kpi.filter'),      value: statusFilter, color: 'text-primary' },
        ].map(k => (
          <div key={k.label} className="bg-white border rounded-lg p-3 text-center">
            <div className={`text-2xl font-bold ${k.color}`}>{k.value}</div>
            <div className="text-xs text-muted mt-0.5">{k.label}</div>
          </div>
        ))}
      </div>

      {/* Filters */}
      <div className="flex gap-3">
        <select
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value)}
          className="border rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary/40"
        >
          <option value="pending">{t('curation.filters.pending')}</option>
          <option value="approved">{t('curation.filters.approved')}</option>
          <option value="recalibrated">{t('curation.filters.recalibrated')}</option>
          <option value="bias_flagged">{t('curation.filters.bias_flagged')}</option>
          <option value="">{t('curation.filters.all')}</option>
        </select>

        <select
          value={campaignFilter}
          onChange={e => setCampaignFilter(e.target.value)}
          className="border rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary/40 flex-1 min-w-0"
        >
          <option value="">{t('curation.filters.allCampaigns')}</option>
          {campaigns.map(c => (
            <option key={c.campaign_id} value={c.campaign_id}>{c.name}</option>
          ))}
        </select>
      </div>

      {/* List */}
      {loading && reviews.length === 0 && (
        <div className="text-center text-sm text-muted-light py-8">{t('curation.loading')}</div>
      )}

      {error && (
        <div className="bg-red-light border border-red/30 text-red-text rounded p-3 text-sm">
          {String(error).includes('403') ? t('curation.noPermission') : error}
        </div>
      )}

      {!loading && reviews.length === 0 && !error && (
        <div className="text-center py-12 text-muted-light">
          <div className="mb-2 flex justify-center"><Check className="w-10 h-10 text-muted-light" aria-hidden="true" /></div>
          <p className="text-sm">{t('curation.empty', { status: emptyStatus })}</p>
        </div>
      )}

      <div className="space-y-3">
        {reviews.map(review => (
          <CurationCard
            key={review.id}
            review={review}
            tenantId={tenantId}
            userId={userId}
            onResolve={handleResolve}
            onReload={reload}
          />
        ))}
      </div>

    </div>
  )
}
