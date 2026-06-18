/**
 * CuradoriaPage.tsx
 * /evaluation/curadoria — Human curator queue for AI evaluator quality review
 * Arc 13 Fase H — Curation Module
 *
 * Shows CurationReview items pending human review of AI evaluator quality.
 * Curator actions: Approve | Recalibrate | Bias Detected
 */
import React, { useState } from 'react'
import { RefreshCw, X, Check, AlertTriangle } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import { useCurationQueue, resolveCuration, useCampaigns } from '@/api/evaluation-hooks'
import type { CurationReview, CurationResolvePayload } from '@/api/evaluation-hooks'

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

// ─── CurationCard ──────────────────────────────────────────────────────────────

interface CurationCardProps {
  review: CurationReview
  onResolve: (reviewId: string, payload: CurationResolvePayload) => Promise<void>
}

function CurationCard({ review, onResolve }: CurationCardProps) {
  const { t } = useTranslation('evaluation')
  const [drawer, setDrawer] = useState<'recalibrate' | 'bias' | null>(null)
  const [approving, setApproving] = useState(false)

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
              {triggerBadge(review.trigger)}
              {review.campaign_id && (
                <span className="text-xs text-muted-light truncate">{review.campaign_id}</span>
              )}
            </div>
            <p className="text-xs text-muted font-mono truncate">{review.evaluation_instance_id}</p>
            {signal && (
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
      </div>

      {drawer && (
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
  )

  const pendingCount = reviews.filter(r => r.status === 'pending').length

  const handleResolve = async (reviewId: string, payload: CurationResolvePayload) => {
    await resolveCuration(reviewId, tenantId, userId, payload)
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
        <div className="bg-red-light border border-red/30 text-red-text rounded p-3 text-sm">{error}</div>
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
            onResolve={handleResolve}
          />
        ))}
      </div>

    </div>
  )
}
