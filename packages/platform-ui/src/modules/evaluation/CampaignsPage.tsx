/**
 * CampaignsPage.tsx
 * /evaluation/campaigns — Campaign CRUD + live dashboard
 */

import React, { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Pause, Play } from 'lucide-react'
import {
  useCampaigns,
  useForms,
  createCampaign,
  updateCampaign,
  pauseCampaign,
  resumeCampaign,
  deleteCampaign,
  seedSyntheticEvaluations,
  flushSyntheticEvaluations,
  dispatchCampaign,
  useCampaignReport,
  useCurationSamplingRules,
  saveCurationSamplingRules,
} from '@/api/evaluation-hooks'
import type {
  EvaluationCampaign,
  CampaignReport,
  CurationSamplingRule,
  CurationRuleType,
} from '@/types'
import { useAuth } from '@/auth/useAuth'

// ── Lightweight data hooks for selectors ──────────────────────────────────────

interface PoolOption { pool_id: string; description: string | null }

function usePoolOptions(tenantId: string) {
  const [pools, setPools] = useState<PoolOption[]>([])
  useEffect(() => {
    if (!tenantId) return
    fetch('/v1/operational/pools', { headers: { 'x-tenant-id': tenantId } })
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(body => setPools(body.items ?? []))
      .catch(() => setPools([]))
  }, [tenantId])
  return pools
}

interface CalendarOption { id: string; name: string }

function useCalendarOptions(tenantId: string) {
  const [calendars, setCalendars] = useState<CalendarOption[]>([])
  useEffect(() => {
    if (!tenantId) return
    fetch(`/v1/calendars?tenant_id=${encodeURIComponent(tenantId)}`)
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(rows => setCalendars(Array.isArray(rows) ? rows : []))
      .catch(() => setCalendars([]))
  }, [tenantId])
  return calendars
}

function StatusBadge({ status }: { status: string }) {
  const { t } = useTranslation('evaluation')
  const styles: Record<string, string> = {
    draft:   'bg-surface-alt text-muted',
    active:  'bg-green-light text-green-text',
    paused:  'bg-warning-light text-warning-text',
    closed:  'bg-red-light text-red-text',
  }
  const statusLabel = t(`campaigns.status.${status}`, { defaultValue: status })
  return <span className={`px-2 py-0.5 rounded text-xs font-medium ${styles[status] ?? 'bg-surface-alt'}`}>{statusLabel}</span>
}

function ProgressBar({ pct }: { pct: number }) {
  const clamp = Math.max(0, Math.min(100, pct))
  return (
    <div className="w-full bg-border rounded-full h-2">
      <div
        className="bg-primary h-2 rounded-full transition-all"
        style={{ width: `${clamp}%` }}
      />
    </div>
  )
}

// ── CampaignReport panel ───────────────────────────────────────────────────────

function ReportPanel({ campaignId }: { campaignId: string }) {
  const { t } = useTranslation('evaluation')
  const { report, loading } = useCampaignReport(campaignId)

  if (loading) return <div className="text-xs text-muted-light py-4 text-center">{t('campaigns.loading')}</div>
  if (!report) return <div className="text-xs text-muted-light py-4 text-center">{t('campaigns.noReport')}</div>

  const pct = report.completion_pct ?? 0

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-4 gap-3">
        {[
          { label: t('campaigns.total'), value: report.total, color: 'text-dark' },
          { label: t('campaigns.completed'), value: report.completed, color: 'text-green-text' },
          { label: t('campaigns.pending'), value: report.pending, color: 'text-warning-text' },
          { label: t('campaigns.underReview'), value: report.in_review, color: 'text-primary' },
        ].map(k => (
          <div key={k.label} className="bg-surface-muted rounded p-3 text-center">
            <div className={`text-2xl font-bold ${k.color}`}>{k.value}</div>
            <div className="text-xs text-muted mt-1">{k.label}</div>
          </div>
        ))}
      </div>

      <div>
        <div className="flex justify-between text-xs text-muted mb-1">
          <span>{t('campaigns.conclusion')}</span>
          <span>{pct.toFixed(1)}%</span>
        </div>
        <ProgressBar pct={pct} />
      </div>

      {report.avg_score !== null && (
        <div className="flex items-center gap-3">
          <span className="text-xs text-muted">{t('campaigns.avgScore')}</span>
          <span className="text-lg font-bold text-primary">{report.avg_score?.toFixed(2)}</span>
          {report.score_p25 !== null && report.score_p75 !== null && (
            <span className="text-xs text-muted-light">
              P25: {report.score_p25?.toFixed(1)} · P75: {report.score_p75?.toFixed(1)}
            </span>
          )}
        </div>
      )}

      {report.top_flags && report.top_flags.length > 0 && (
        <div>
          <div className="text-xs font-semibold text-muted mb-1">{t('campaigns.frequentFlags')}</div>
          <div className="flex flex-wrap gap-1">
            {report.top_flags.map(f => (
              <span key={f} className="bg-red-light text-red-text text-xs px-2 py-0.5 rounded">{f}</span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ── CreateCampaignModal ────────────────────────────────────────────────────────

interface CreateModalProps {
  onClose: () => void
  onCreated: () => void
  adminToken: string
  editing?: EvaluationCampaign | null   // null/undefined = criar; objeto = editar (PUT)
}

const WORKFLOW_SKILL_VALUES = [
  'skill_revisao_simples_v1',
  'skill_revisao_treplica_v1',
] as const

const AUTHORITY_OPTIONS = [
  { value: 'supervisor', label: 'Supervisor' },
  { value: 'manager',    label: 'Gerente' },
  { value: 'director',   label: 'Diretor' },
]

const REVIEWER_TYPE_VALUES = ['human', 'ai', 'ai_then_human'] as const

// ── Default curation sampling rules ───────────────────────────────────────────

const DEFAULT_CURATION_RULES: Omit<CurationSamplingRule, 'campaign_id' | 'rule_id'>[] = [
  { rule_type: 'score_extremes',  enabled: true,  priority: 1, params: { threshold_low: 0.3, threshold_high: 0.9, sample_pct: 1.0 } },
  { rule_type: 'reviewer_signal', enabled: true,  priority: 2, params: {} },
  { rule_type: 'score_outlier',   enabled: true,  priority: 3, params: { std_devs: 2.0, sample_pct: 0.2 } },
  { rule_type: 'deploy_baseline', enabled: true,  priority: 4, params: { sample_n: 5 } },
  { rule_type: 'na_excess',       enabled: false, priority: 5, params: { na_threshold_pct: 0.3 } },
  { rule_type: 'random_baseline', enabled: false, priority: 6, params: { rate: 0.05 } },
]

// ── CurationSamplingRulesEditor ────────────────────────────────────────────────

function CurationRuleRow({
  rule,
  onChange,
}: {
  rule:     Omit<CurationSamplingRule, 'campaign_id' | 'rule_id'>
  onChange: (updated: Omit<CurationSamplingRule, 'campaign_id' | 'rule_id'>) => void
}) {
  const { t } = useTranslation('evaluation')
  const setParam = (key: string, value: number | undefined) =>
    onChange({ ...rule, params: { ...rule.params, [key]: value } })

  return (
    <div className={`border rounded p-2.5 transition-colors ${rule.enabled ? 'border-revised/30 bg-revised-light' : 'border-border bg-white'}`}>
      <div className="flex items-center gap-2 mb-1.5">
        <input
          type="checkbox"
          checked={rule.enabled}
          onChange={e => onChange({ ...rule, enabled: e.target.checked })}
          className="accent-teal-600"
        />
        <span className="text-sm font-medium text-dark">{t(`campaigns.curation.types.${rule.rule_type}`, rule.rule_type)}</span>
        <span className="ml-auto text-xs text-muted-light">#{rule.priority}</span>
      </div>

      {rule.enabled && (
        <div className="flex flex-wrap gap-2 pl-5">
          {/* score_extremes params */}
          {rule.rule_type === 'score_extremes' && (
            <>
              <label className="flex items-center gap-1 text-xs text-muted">
                {t('campaigns.curation.params.min')}
                <input type="number" min={0} max={1} step={0.05}
                  className="w-16 border border-border rounded px-1.5 py-0.5 text-xs"
                  value={rule.params.threshold_low ?? 0.3}
                  onChange={e => setParam('threshold_low', parseFloat(e.target.value))} />
              </label>
              <label className="flex items-center gap-1 text-xs text-muted">
                {t('campaigns.curation.params.max')}
                <input type="number" min={0} max={1} step={0.05}
                  className="w-16 border border-border rounded px-1.5 py-0.5 text-xs"
                  value={rule.params.threshold_high ?? 0.9}
                  onChange={e => setParam('threshold_high', parseFloat(e.target.value))} />
              </label>
              <label className="flex items-center gap-1 text-xs text-muted">
                {t('campaigns.curation.params.curatePct')}
                <input type="number" min={0.1} max={1} step={0.1}
                  className="w-16 border border-border rounded px-1.5 py-0.5 text-xs"
                  value={rule.params.sample_pct ?? 1.0}
                  onChange={e => setParam('sample_pct', parseFloat(e.target.value))} />
              </label>
            </>
          )}
          {/* deploy_baseline params */}
          {rule.rule_type === 'deploy_baseline' && (
            <label className="flex items-center gap-1 text-xs text-muted">
              {t('campaigns.curation.params.firstN')}
              <input type="number" min={1} max={50}
                className="w-16 border border-border rounded px-1.5 py-0.5 text-xs"
                value={rule.params.sample_n ?? 5}
                onChange={e => setParam('sample_n', parseInt(e.target.value))} />
            </label>
          )}
          {/* score_outlier params */}
          {rule.rule_type === 'score_outlier' && (
            <>
              <label className="flex items-center gap-1 text-xs text-muted">
                {t('campaigns.curation.params.deviations')}
                <input type="number" min={1} max={5} step={0.5}
                  className="w-16 border border-border rounded px-1.5 py-0.5 text-xs"
                  value={rule.params.std_devs ?? 2.0}
                  onChange={e => setParam('std_devs', parseFloat(e.target.value))} />
              </label>
              <label className="flex items-center gap-1 text-xs text-muted">
                {t('campaigns.curation.params.curatePct')}
                <input type="number" min={0.1} max={1} step={0.1}
                  className="w-16 border border-border rounded px-1.5 py-0.5 text-xs"
                  value={rule.params.sample_pct ?? 0.2}
                  onChange={e => setParam('sample_pct', parseFloat(e.target.value))} />
              </label>
            </>
          )}
          {/* na_excess params */}
          {rule.rule_type === 'na_excess' && (
            <label className="flex items-center gap-1 text-xs text-muted">
              {t('campaigns.curation.params.naThreshold')}
              <input type="number" min={0.1} max={1} step={0.05}
                className="w-16 border border-border rounded px-1.5 py-0.5 text-xs"
                value={rule.params.na_threshold_pct ?? 0.3}
                onChange={e => setParam('na_threshold_pct', parseFloat(e.target.value))} />
            </label>
          )}
          {/* random_baseline params */}
          {rule.rule_type === 'random_baseline' && (
            <label className="flex items-center gap-1 text-xs text-muted">
              {t('campaigns.curation.params.rate')}
              <input type="number" min={0.01} max={1} step={0.01}
                className="w-16 border border-border rounded px-1.5 py-0.5 text-xs"
                value={rule.params.rate ?? 0.05}
                onChange={e => setParam('rate', parseFloat(e.target.value))} />
            </label>
          )}
          {/* reviewer_signal — no params needed */}
          {rule.rule_type === 'reviewer_signal' && (
            <span className="text-xs text-muted-light italic">{t('campaigns.curation.params.reviewerSignalHint')}</span>
          )}
        </div>
      )}
    </div>
  )
}

function CurationSamplingRulesEditor({
  rules,
  onChange,
}: {
  rules:    Omit<CurationSamplingRule, 'campaign_id' | 'rule_id'>[]
  onChange: (rules: Omit<CurationSamplingRule, 'campaign_id' | 'rule_id'>[]) => void
}) {
  const updateRule = (idx: number, updated: Omit<CurationSamplingRule, 'campaign_id' | 'rule_id'>) => {
    const next = [...rules]
    next[idx] = updated
    onChange(next)
  }

  return (
    <div className="space-y-1.5">
      {rules.map((rule, i) => (
        <CurationRuleRow key={rule.rule_type} rule={rule} onChange={r => updateRule(i, r)} />
      ))}
    </div>
  )
}

function CreateModal({ onClose, onCreated, adminToken, editing }: CreateModalProps) {
  const { t } = useTranslation('evaluation')
  const { tenantId: TENANT } = useAuth()
  const { forms } = useForms(TENANT)
  const poolOptions     = usePoolOptions(TENANT)
  const calendarOptions = useCalendarOptions(TENANT)
  const isEdit = !!editing
  // Prefill em modo edição (o modal é montado fresco ao abrir).
  const _sr = (editing?.sampling_rules ?? {}) as { mode?: string; rate?: number; every_n?: number }
  const _rr = (editing?.reviewer_rules ?? {}) as { auto_review?: boolean; score_threshold?: number }
  const [name, setName] = useState(editing?.name ?? '')
  const [description, setDescription] = useState(editing?.description ?? '')
  const [formId, setFormId] = useState(editing?.form_id ?? '')
  const [evaluationPoolId,     setEvaluationPoolId]     = useState(editing?.evaluation_pool_id ?? '')
  const [evaluatorPool,        setEvaluatorPool]        = useState((editing as { evaluator_pool?: string } | null)?.evaluator_pool ?? '')
  const [evaluationCalendarId, setEvaluationCalendarId] = useState(editing?.evaluation_calendar_id ?? '')
  // T17 — janela de dados (period_start/period_end). API devolve ISO; o input date usa só
  // a parte YYYY-MM-DD. No submit reconverte (start=00:00:00Z, end=23:59:59Z p/ incluir o dia).
  const [periodStart, setPeriodStart] = useState((editing?.period_start ?? '').slice(0, 10))
  const [periodEnd,   setPeriodEnd]   = useState((editing?.period_end ?? '').slice(0, 10))
  const [samplingMode, setSamplingMode] = useState<'all' | 'percentage' | 'fixed'>((_sr.mode as 'all'|'percentage'|'fixed') || 'percentage')
  const [samplingRate, setSamplingRate] = useState(String(_sr.rate ?? _sr.every_n ?? '0.1'))
  const [autoReview, setAutoReview] = useState(_rr.auto_review ?? true)
  const [scoreThreshold, setScoreThreshold] = useState(String(_rr.score_threshold ?? '7'))

  // Contestation / workflow fields — prefill em modo edição (contestation_policy).
  const _cp = (editing?.contestation_policy ?? {}) as {
    max_rounds?: number; contest_deadline_hours?: number; review_deadline_hours?: number
    auto_lock_on_timeout?: boolean; reviewer_type?: string; use_business_hours?: boolean
    pre_review_enabled?: boolean; pre_review_agent_pool?: string | null
  }
  const [workflowSkillId, setWorkflowSkillId] = useState(editing?.review_workflow_skill_id ?? 'skill_revisao_simples_v1')
  const [enableContestation, setEnableContestation] = useState(!!editing?.contestation_policy)
  const [maxRounds, setMaxRounds] = useState(String(_cp.max_rounds ?? '3'))
  const [contestDeadlineHours, setContestDeadlineHours] = useState(String(_cp.contest_deadline_hours ?? '72'))
  const [reviewDeadlineHours, setReviewDeadlineHours] = useState(String(_cp.review_deadline_hours ?? '48'))
  const [authorityLevel, setAuthorityLevel] = useState<'supervisor' | 'manager' | 'director'>('supervisor')
  const [autoLockOnTimeout, setAutoLockOnTimeout] = useState(_cp.auto_lock_on_timeout ?? true)
  const [reviewerType, setReviewerType] = useState<'ai' | 'human' | 'ai_then_human'>((_cp.reviewer_type as 'ai'|'human'|'ai_then_human') || 'ai_then_human')
  const [useBusinessHours, setUseBusinessHours] = useState(_cp.use_business_hours ?? false)
  const [preReviewEnabled, setPreReviewEnabled] = useState(_cp.pre_review_enabled ?? false)
  const [preReviewPool, setPreReviewPool] = useState(_cp.pre_review_agent_pool ?? '')

  // Arc 13 — curation sampling rules
  const [curationRules, setCurationRules] = useState<Omit<CurationSamplingRule, 'campaign_id' | 'rule_id'>[]>(
    DEFAULT_CURATION_RULES
  )
  const [showCurationRules, setShowCurationRules] = useState(false)

  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // When choosing Arc 13 workflow, auto-enable contestation defaults
  const isArc13Skill = workflowSkillId === 'skill_revisao_treplica_v1'

  const submit = async () => {
    if (!name || !formId) { setError(t('campaigns.modal.errorRequired')); return }
    setSaving(true)
    setError(null)
    try {
      if (isEdit && editing) {
        // Edição (PUT) — form_id/pool não mudam; status preservado.
        await updateCampaign(editing.campaign_id, TENANT, {
          name,
          description,
          review_workflow_skill_id: workflowSkillId || undefined,
          evaluation_pool_id:     evaluationPoolId     || undefined,
          evaluator_pool:         evaluatorPool,   // '' limpa (volta ao default global)
          evaluation_calendar_id: evaluationCalendarId || undefined,
          period_start:           periodStart ? `${periodStart}T00:00:00Z` : undefined,
          period_end:             periodEnd   ? `${periodEnd}T23:59:59Z`   : undefined,
          sampling_rules: {
            mode: samplingMode,
            rate: samplingMode === 'percentage' ? parseFloat(samplingRate) : undefined,
            every_n: samplingMode === 'fixed' ? parseInt(samplingRate) : undefined,
          },
          reviewer_rules: {
            auto_review: autoReview,
            score_threshold: autoReview ? parseFloat(scoreThreshold) : undefined,
          },
          contestation_policy: enableContestation ? {
            contestation_roles:     ['supervisor', 'admin'],
            max_rounds:             parseInt(maxRounds),
            contest_deadline_hours: parseInt(contestDeadlineHours),
            review_deadline_hours:  parseInt(reviewDeadlineHours),
            auto_lock_on_timeout:   autoLockOnTimeout,
            reviewer_type:          isArc13Skill ? reviewerType : undefined,
            use_business_hours:     useBusinessHours || undefined,
            pre_review_enabled:     preReviewEnabled || undefined,
            pre_review_agent_pool:  preReviewEnabled ? preReviewPool || null : undefined,
          } : undefined,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        } as any, adminToken)
        onCreated()
        return
      }
      const campaign = await createCampaign({
        tenant_id: TENANT,
        form_id: formId,
        name,
        description,
        status: 'draft',
        review_workflow_skill_id: workflowSkillId || undefined,
        evaluation_pool_id:     evaluationPoolId     || undefined,
        evaluator_pool:         evaluatorPool,   // '' limpa (volta ao default global)
        evaluation_calendar_id: evaluationCalendarId || undefined,
        period_start:           periodStart ? `${periodStart}T00:00:00Z` : undefined,
        period_end:             periodEnd   ? `${periodEnd}T23:59:59Z`   : undefined,
        sampling_rules: {
          mode: samplingMode,
          rate: samplingMode === 'percentage' ? parseFloat(samplingRate) : undefined,
          every_n: samplingMode === 'fixed' ? parseInt(samplingRate) : undefined,
        },
        reviewer_rules: {
          auto_review: autoReview,
          score_threshold: autoReview ? parseFloat(scoreThreshold) : undefined,
        },
        contestation_policy: enableContestation ? {
          contestation_roles:     ['supervisor', 'admin'],
          max_rounds:             parseInt(maxRounds),
          contest_deadline_hours: parseInt(contestDeadlineHours),
          review_deadline_hours:  parseInt(reviewDeadlineHours),
          auto_lock_on_timeout:   autoLockOnTimeout,
          reviewer_type:          isArc13Skill ? reviewerType : undefined,
          use_business_hours:     useBusinessHours || undefined,
          pre_review_enabled:     preReviewEnabled || undefined,
          pre_review_agent_pool:  preReviewEnabled ? preReviewPool || null : undefined,
        } : undefined,
      }, adminToken)

      // Save curation sampling rules if Arc 13 and enabled
      if (isArc13Skill && showCurationRules && campaign?.campaign_id) {
        await saveCurationSamplingRules(campaign.campaign_id, curationRules, adminToken).catch(() => {})
      }

      onCreated()
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-[620px] max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between p-4 border-b">
          <h2 className="font-semibold text-dark">{isEdit ? t('campaigns.modal.editTitle', { defaultValue: 'Editar campanha' }) : t('campaigns.modal.title')}</h2>
          <button onClick={onClose} className="text-muted-light hover:text-muted">{t('campaigns.modal.close')}</button>
        </div>

        <div className="p-4 space-y-4">
          {/* Basic info */}
          <div className="grid grid-cols-2 gap-3">
            <div className="col-span-2">
              <label className="block text-xs font-medium text-muted mb-1">{t('campaigns.modal.nameLabel')}</label>
              <input
                className="w-full border border-border-strong rounded px-3 py-1.5 text-sm"
                value={name}
                onChange={e => setName(e.target.value)}
                placeholder={t('campaigns.modal.nameExample')}
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-muted mb-1">{t('campaigns.modal.formLabel')}</label>
              <select
                className="w-full border border-border-strong rounded px-3 py-1.5 text-sm"
                value={formId}
                onChange={e => setFormId(e.target.value)}
              >
                <option value="">{t('campaigns.modal.selectForm')}</option>
                {forms.filter(f => f.status === 'active').map(f => (
                  <option key={f.form_id} value={f.form_id}>{f.name}</option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-xs font-medium text-muted mb-1">{t('campaigns.modal.reviewSkillLabel')}</label>
              <select
                className="w-full border border-border-strong rounded px-3 py-1.5 text-sm"
                value={workflowSkillId}
                onChange={e => setWorkflowSkillId(e.target.value)}
              >
                <option value="">{t('campaigns.modal.noWorkflow')}</option>
                {WORKFLOW_SKILL_VALUES.map(v => (
                  <option key={v} value={v}>{t(`campaigns.workflowSkills.${v}`, v)}</option>
                ))}
              </select>
            </div>

            {/* Evaluation pool */}
            <div>
              <label className="block text-xs font-medium text-muted mb-1">{t('campaigns.modal.evaluationPoolLabel')}</label>
              <select
                className="w-full border border-border-strong rounded px-3 py-1.5 text-sm"
                value={evaluationPoolId}
                onChange={e => setEvaluationPoolId(e.target.value)}
              >
                <option value="">{t('campaigns.modal.selectPool')}</option>
                {poolOptions.map(p => (
                  <option key={p.pool_id} value={p.pool_id}>
                    {p.description ? `${p.pool_id} — ${p.description}` : p.pool_id}
                  </option>
                ))}
              </select>
            </div>

            {/* S2.2 — Evaluator pool (quem AVALIA; vazio = default global avaliacao_ia) */}
            <div>
              <label className="block text-xs font-medium text-muted mb-1">
                {t('campaigns.modal.evaluatorPoolLabel', { defaultValue: 'Pool avaliador' })}
              </label>
              <select
                className="w-full border border-border-strong rounded px-3 py-1.5 text-sm"
                value={evaluatorPool}
                onChange={e => setEvaluatorPool(e.target.value)}
              >
                <option value="">{t('campaigns.modal.evaluatorPoolDefault', { defaultValue: 'Padrão global (avaliacao_ia)' })}</option>
                {poolOptions.map(p => (
                  <option key={p.pool_id} value={p.pool_id}>
                    {p.description ? `${p.pool_id} — ${p.description}` : p.pool_id}
                  </option>
                ))}
                {evaluatorPool && !poolOptions.some(p => p.pool_id === evaluatorPool) && (
                  <option value={evaluatorPool}>{evaluatorPool}</option>
                )}
              </select>
            </div>

            {/* SLA calendar */}
            <div>
              <label className="block text-xs font-medium text-muted mb-1">{t('campaigns.modal.evaluationCalendarLabel')}</label>
              <select
                className="w-full border border-border-strong rounded px-3 py-1.5 text-sm"
                value={evaluationCalendarId}
                onChange={e => setEvaluationCalendarId(e.target.value)}
              >
                <option value="">{t('campaigns.modal.selectCalendar')}</option>
                {calendarOptions.map(c => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            </div>

            {/* T17 — janela de dados (quais sessões entram, por closed_at) */}
            <div className="col-span-2 grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-muted mb-1">{t('campaigns.modal.periodStartLabel', { defaultValue: 'Período — início (opcional)' })}</label>
                <input
                  type="date"
                  className="w-full border border-border-strong rounded px-3 py-1.5 text-sm"
                  value={periodStart}
                  onChange={e => setPeriodStart(e.target.value)}
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-muted mb-1">{t('campaigns.modal.periodEndLabel', { defaultValue: 'Período — fim (opcional)' })}</label>
                <input
                  type="date"
                  className="w-full border border-border-strong rounded px-3 py-1.5 text-sm"
                  value={periodEnd}
                  onChange={e => setPeriodEnd(e.target.value)}
                />
              </div>
              <p className="col-span-2 text-xs text-muted-light -mt-1">{t('campaigns.modal.periodHint', { defaultValue: 'Quais sessões entram, por data de fechamento. Vazio = janela aberta (streaming a partir da ativação). Início no passado = reprocessa o histórico (backfill).' })}</p>
            </div>

            <div className="col-span-2">
              <label className="block text-xs font-medium text-muted mb-1">{t('campaigns.modal.descriptionLabel')}</label>
              <textarea
                className="w-full border border-border-strong rounded px-3 py-1.5 text-sm resize-none"
                rows={2}
                value={description}
                onChange={e => setDescription(e.target.value)}
              />
            </div>
          </div>

          {/* Sampling */}
          <div className="border-t pt-3">
            <div className="text-xs font-semibold text-muted mb-2">{t('campaigns.modal.samplingRules')}</div>
            <div className="flex gap-3 items-center">
              <select
                className="border border-border-strong rounded px-2 py-1 text-sm"
                value={samplingMode}
                onChange={e => setSamplingMode(e.target.value as any)}
              >
                <option value="all">{t('campaigns.modal.samplingMode.all')}</option>
                <option value="percentage">{t('campaigns.modal.samplingMode.percentage')}</option>
                <option value="fixed">{t('campaigns.modal.samplingMode.fixed')}</option>
              </select>
              {samplingMode !== 'all' && (
                <input
                  type="number"
                  min={samplingMode === 'percentage' ? '0.01' : '1'}
                  max={samplingMode === 'percentage' ? '1' : '100'}
                  step={samplingMode === 'percentage' ? '0.05' : '1'}
                  className="w-20 border border-border-strong rounded px-2 py-1 text-sm text-center"
                  value={samplingRate}
                  onChange={e => setSamplingRate(e.target.value)}
                />
              )}
              {samplingMode === 'percentage' && <span className="text-xs text-muted">{t('campaigns.modal.samplingHint')}</span>}
            </div>
          </div>

          {/* Reviewer IA */}
          <div className="border-t pt-3">
            <div className="text-xs font-semibold text-muted mb-2">{t('campaigns.modal.reviewerIA')}</div>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={autoReview}
                onChange={e => setAutoReview(e.target.checked)}
              />
              {t('campaigns.modal.enableAutoReview')}
            </label>
            {autoReview && (
              <div className="flex items-center gap-2 mt-2 text-sm text-muted">
                <span>{t('campaigns.modal.escalateThreshold')}</span>
                <input
                  type="number"
                  min={1}
                  max={10}
                  step={0.5}
                  className="w-16 border border-border-strong rounded px-2 py-0.5 text-center"
                  value={scoreThreshold}
                  onChange={e => setScoreThreshold(e.target.value)}
                />
              </div>
            )}
          </div>

          {/* Contestation policy */}
          <div className="border-t pt-3">
            <label className="flex items-center gap-2 text-sm font-semibold text-muted mb-2">
              <input
                type="checkbox"
                checked={enableContestation}
                onChange={e => setEnableContestation(e.target.checked)}
              />
              {t('campaigns.modal.enableContestation')}
            </label>

            {enableContestation && (
              <div className="bg-primary-light border border-primary/20 rounded p-3 space-y-3 mt-2">

                {/* Arc 13 — reviewer_type */}
                {isArc13Skill && (
                  <div>
                    <label className="block text-xs font-semibold text-muted mb-1">
                      {t('campaigns.modal.reviewerType')}
                    </label>
                    <select
                      className="w-full border border-border-strong rounded px-2 py-1 text-sm bg-white"
                      value={reviewerType}
                      onChange={e => setReviewerType(e.target.value as typeof reviewerType)}
                    >
                      {REVIEWER_TYPE_VALUES.map(v => (
                        <option key={v} value={v}>{t(`campaigns.reviewerTypes.${v}`, v)}</option>
                      ))}
                    </select>
                  </div>
                )}

                {/* Deadlines grid */}
                <div className={`grid gap-3 ${isArc13Skill ? 'grid-cols-3' : 'grid-cols-3'}`}>
                  <div>
                    <label className="block text-xs text-muted mb-1">{t('campaigns.modal.maxRounds')}</label>
                    <input
                      type="number"
                      min={1}
                      max={5}
                      className="w-full border border-border-strong rounded px-2 py-1 text-sm text-center bg-white"
                      value={maxRounds}
                      onChange={e => setMaxRounds(e.target.value)}
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-muted mb-1">
                      {t('campaigns.modal.contestDeadline')}
                      <span className="text-muted-light font-normal ml-1">(h)</span>
                    </label>
                    <input
                      type="number"
                      min={1}
                      max={720}
                      className="w-full border border-border-strong rounded px-2 py-1 text-sm text-center bg-white"
                      value={contestDeadlineHours}
                      onChange={e => setContestDeadlineHours(e.target.value)}
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-muted mb-1">
                      {t('campaigns.modal.reviewDeadline')}
                      <span className="text-muted-light font-normal ml-1">(h)</span>
                    </label>
                    <input
                      type="number"
                      min={1}
                      max={720}
                      step={1}
                      className="w-full border border-border-strong rounded px-2 py-1 text-sm text-center bg-white"
                      value={reviewDeadlineHours}
                      onChange={e => setReviewDeadlineHours(e.target.value)}
                    />
                  </div>
                </div>

                <div className="flex flex-wrap gap-4">
                  <label className="flex items-center gap-2 text-sm text-dark">
                    <input
                      type="checkbox"
                      checked={autoLockOnTimeout}
                      onChange={e => setAutoLockOnTimeout(e.target.checked)}
                    />
                    {t('campaigns.modal.autoLockTimeout')}
                  </label>
                  <label className="flex items-center gap-2 text-sm text-dark">
                    <input
                      type="checkbox"
                      checked={useBusinessHours}
                      onChange={e => setUseBusinessHours(e.target.checked)}
                    />
                    {t('campaigns.modal.useBusinessHours')}
                  </label>
                </div>

                {/* Arc 13 — pre-review */}
                {isArc13Skill && (
                  <div className="border-t border-primary/30 pt-3">
                    <label className="flex items-center gap-2 text-sm font-medium text-dark mb-2">
                      <input
                        type="checkbox"
                        checked={preReviewEnabled}
                        onChange={e => setPreReviewEnabled(e.target.checked)}
                      />
                      {t('campaigns.modal.preReviewEnabled')}
                      <span className="text-xs text-muted-light font-normal">
                        {t('campaigns.modal.preReviewGateHint')}
                      </span>
                    </label>
                    {preReviewEnabled && (
                      <div>
                        <label className="block text-xs text-muted mb-1">{t('campaigns.modal.preReviewPool')}</label>
                        <select
                          className="w-full border border-border-strong rounded px-2 py-1 text-sm bg-white"
                          value={preReviewPool}
                          onChange={e => setPreReviewPool(e.target.value)}
                        >
                          <option value="">{t('campaigns.modal.selectPool')}</option>
                          {poolOptions.map(p => (
                            <option key={p.pool_id} value={p.pool_id}>
                              {p.description ? `${p.pool_id} — ${p.description}` : p.pool_id}
                            </option>
                          ))}
                        </select>
                      </div>
                    )}
                  </div>
                )}

                <p className="text-xs text-primary">
                  {t('campaigns.modal.skillInfo', { skill: workflowSkillId || t('campaigns.modal.noSkillSelected') })}
                </p>
              </div>
            )}
          </div>

          {/* Arc 13 — Curation sampling rules (only for Arc 13 workflow) */}
          {isArc13Skill && (
            <div className="border-t pt-3">
              <label className="flex items-center gap-2 text-sm font-semibold text-muted mb-2">
                <input
                  type="checkbox"
                  checked={showCurationRules}
                  onChange={e => setShowCurationRules(e.target.checked)}
                />
                {t('campaigns.modal.curationRules')}
                <span className="text-xs text-muted-light font-normal">{t('campaigns.modal.curationRulesHint')}</span>
              </label>

              {showCurationRules && (
                <div className="mt-2">
                  <CurationSamplingRulesEditor
                    rules={curationRules}
                    onChange={setCurationRules}
                  />
                </div>
              )}
            </div>
          )}

          {error && <div className="bg-red-light text-red-text text-sm p-2 rounded">{error}</div>}
        </div>

        <div className="flex justify-end gap-2 p-4 border-t">
          <button onClick={onClose} className="px-4 py-1.5 text-sm text-muted hover:text-dark">{t('campaigns.modal.cancel')}</button>
          <button
            onClick={submit}
            disabled={saving}
            className="bg-primary text-white px-4 py-1.5 text-sm rounded hover:bg-primary-dark disabled:opacity-50"
          >
            {saving
              ? t('campaigns.modal.submit_saving')
              : (isEdit ? t('campaigns.modal.submitEdit', { defaultValue: 'Salvar alterações' }) : t('campaigns.modal.submit'))}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── CurationSamplingRulesDetailPanel ──────────────────────────────────────────

function CurationSamplingRulesDetailPanel({
  campaignId,
  adminToken,
}: {
  campaignId: string
  adminToken: string
}) {
  const { t } = useTranslation('evaluation')
  const { rules: loadedRules, loading, reload } = useCurationSamplingRules(campaignId)
  const [rules, setRules] = useState<Omit<CurationSamplingRule, 'campaign_id' | 'rule_id'>[]>([])
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Sync loaded rules → local state (use defaults when campaign has none yet)
  useEffect(() => {
    if (!loading) {
      setRules(loadedRules.length > 0 ? loadedRules : DEFAULT_CURATION_RULES)
    }
  }, [loadedRules, loading])

  const save = async () => {
    setSaving(true); setError(null)
    try {
      await saveCurationSamplingRules(campaignId, rules, adminToken)
      await reload()
      setEditing(false)
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  const enabledCount = rules.filter(r => r.enabled).length

  return (
    <div className="bg-white border rounded p-4">
      <div className="flex items-center gap-2 mb-3">
        <h3 className="font-semibold text-dark flex-1">
          {t('campaigns.curation.title')}
          <span className="ml-2 text-xs font-normal text-muted-light">
            {t('campaigns.curation.activeCount', { enabled: enabledCount, total: rules.length })}
          </span>
        </h3>
        {!editing ? (
          <button
            onClick={() => setEditing(true)}
            className="text-xs px-3 py-1 border border-border-strong rounded text-muted hover:bg-surface-muted"
          >
            {t('campaigns.curation.edit')}
          </button>
        ) : (
          <div className="flex gap-2">
            <button
              onClick={() => { setEditing(false); setRules(loadedRules.length > 0 ? loadedRules : DEFAULT_CURATION_RULES) }}
              className="text-xs px-3 py-1 border border-border-strong rounded text-muted hover:bg-surface-muted"
            >
              {t('campaigns.curation.cancel')}
            </button>
            <button
              onClick={save}
              disabled={saving}
              className="text-xs px-3 py-1 bg-primary text-white rounded hover:bg-primary-dark disabled:opacity-50"
            >
              {saving ? t('campaigns.curation.saving') : t('campaigns.curation.save')}
            </button>
          </div>
        )}
      </div>

      {loading ? (
        <p className="text-xs text-muted-light">{t('campaigns.curation.loading')}</p>
      ) : editing ? (
        <CurationSamplingRulesEditor rules={rules} onChange={setRules} />
      ) : (
        <div className="space-y-1">
          {rules.map(rule => (
            <div
              key={rule.rule_type}
              className={`flex items-center gap-2 text-xs px-2 py-1.5 rounded ${
                rule.enabled ? 'bg-revised-light text-revised-text' : 'bg-surface-muted text-muted-light'
              }`}
            >
              <span>{rule.enabled ? '●' : '○'}</span>
              <span className="font-medium">{t(`campaigns.curation.types.${rule.rule_type}`, rule.rule_type)}</span>
              {rule.enabled && (
                <span className="ml-auto text-muted">
                  {rule.rule_type === 'score_extremes' && `< ${rule.params.threshold_low} | > ${rule.params.threshold_high}`}
                  {rule.rule_type === 'deploy_baseline' && t('campaigns.curation.statFirstN', { n: rule.params.sample_n })}
                  {rule.rule_type === 'score_outlier' && t('campaigns.curation.statSigma', { n: rule.params.std_devs })}
                  {rule.rule_type === 'na_excess' && t('campaigns.curation.statNaPct', { pct: ((rule.params.na_threshold_pct ?? 0) * 100).toFixed(0) })}
                  {rule.rule_type === 'random_baseline' && t('campaigns.curation.statRate', { pct: ((rule.params.rate ?? 0) * 100).toFixed(0) })}
                </span>
              )}
            </div>
          ))}
        </div>
      )}

      {error && <div className="text-red-text text-xs mt-2 bg-red-light border border-red/20 rounded p-2">{error}</div>}
    </div>
  )
}

// ── CampaignsPage ─────────────────────────────────────────────────────────────

export default function CampaignsPage() {
  const { t } = useTranslation('evaluation')
  const { tenantId: TENANT } = useAuth()
  const [adminToken, setAdminToken] = useState('')
  const { campaigns, loading, reload } = useCampaigns(TENANT, 30_000)
  const [selected, setSelected] = useState<EvaluationCampaign | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [editingCampaign, setEditingCampaign] = useState<EvaluationCampaign | null>(null)

  // Re-sincroniza `selected` com a lista quando ela recarrega (após editar, o
  // objeto antigo ficava stale → o modal de edição pré-preenchia valores velhos).
  useEffect(() => {
    if (!selected) return
    const fresh = campaigns.find(c => c.campaign_id === selected.campaign_id)
    if (fresh && fresh !== selected) setSelected(fresh)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [campaigns])
  const [actionError, setActionError] = useState<string | null>(null)

  const toggleStatus = async (c: EvaluationCampaign) => {
    setActionError(null)
    try {
      if (c.status === 'active') await pauseCampaign(c.campaign_id, adminToken)
      else await resumeCampaign(c.campaign_id, adminToken)
      reload()
    } catch (e) {
      setActionError(String(e))
    }
  }

  // S2.Q1 — avaliador fake: gera avaliações sintéticas para validar o módulo em volume.
  const [seeding, setSeeding] = useState(false)
  const [seedMsg, setSeedMsg] = useState<string | null>(null)
  const handleSeed = async (c: EvaluationCampaign, count: number) => {
    setSeeding(true); setSeedMsg(null); setActionError(null)
    try {
      const res = await seedSyntheticEvaluations(TENANT, c.campaign_id, count, adminToken)
      setSeedMsg(`${res.results_created} avaliações + ${res.nps_signals_emitted} NPS gerados`)
      reload()
    } catch (e) {
      setActionError(String(e))
    } finally {
      setSeeding(false)
    }
  }
  const handleFlush = async () => {
    if (!window.confirm('Limpar TODA a massa sintética (avaliações + NPS de teste)?')) return
    setSeeding(true); setSeedMsg(null); setActionError(null)
    try {
      await flushSyntheticEvaluations(TENANT, adminToken)
      setSeedMsg('Dados de teste limpos (Postgres + ClickHouse).')
      reload()
    } catch (e) {
      setActionError(String(e))
    } finally {
      setSeeding(false)
    }
  }
  const handleDelete = async (c: EvaluationCampaign) => {
    if (!window.confirm(`Excluir a campanha "${c.name}" e TODAS as suas avaliações? Ação irreversível.`)) return
    setActionError(null)
    try {
      await deleteCampaign(c.campaign_id, TENANT, adminToken)
      setSelected(null)
      reload()
    } catch (e) {
      setActionError(String(e))
    }
  }
  const handleDispatch = async (c: EvaluationCampaign) => {
    setSeeding(true); setSeedMsg(null); setActionError(null)
    try {
      const res = await dispatchCampaign(c.campaign_id, TENANT, adminToken)
      setSeedMsg(`${res.dispatched} avaliação(ões) despachada(s) → pool ${res.evaluator_pool}`)
    } catch (e) {
      setActionError(String(e))
    } finally {
      setSeeding(false)
    }
  }

  return (
    <div className="flex h-full">
      {/* Sidebar */}
      <aside className="w-80 border-r flex flex-col bg-surface-muted">
        <div className="p-3 border-b flex gap-2">
          <input
            className="flex-1 border border-border-strong rounded px-2 py-1 text-xs"
            type="password"
            placeholder={t('campaigns.sidebar.adminTokenPlaceholder')}
            value={adminToken}
            onChange={e => setAdminToken(e.target.value)}
          />
          <button
            onClick={() => setShowCreate(true)}
            className="bg-primary text-white text-xs px-2 py-1 rounded hover:bg-primary-dark"
          >
            {t('campaigns.sidebar.newButton')}
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-2 space-y-1">
          {loading && <p className="text-sm text-muted-light p-2">{t('campaigns.sidebar.loading')}</p>}
          {campaigns.map(c => {
            const pct = c.total_instances > 0 ? (c.completed / c.total_instances) * 100 : 0
            return (
              <button
                key={c.campaign_id}
                onClick={() => setSelected(c)}
                className={`w-full text-left px-3 py-2 rounded text-sm transition-colors border ${
                  selected?.campaign_id === c.campaign_id
                    ? 'border-primary bg-primary-light'
                    : 'border-transparent hover:bg-border'
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="font-medium truncate text-dark">{c.name}</span>
                  <StatusBadge status={c.status} />
                </div>
                <ProgressBar pct={pct} />
                <div className="flex justify-between text-xs text-muted-light mt-1">
                  <span>{c.completed}/{c.total_instances} {t('campaigns.completed').toLowerCase()}</span>
                  <span>{pct.toFixed(0)}%</span>
                </div>
              </button>
            )
          })}
          {!loading && campaigns.length === 0 && (
            <p className="text-xs text-muted-light text-center py-6">{t('campaigns.sidebar.noCampaigns')}</p>
          )}
        </div>
      </aside>

      {/* Detail */}
      <div className="flex-1 overflow-y-auto p-6">
        {selected ? (
          <div className="space-y-6 max-w-2xl">
            <div className="flex items-center gap-3">
              <h1 className="text-xl font-bold text-dark flex-1">{selected.name}</h1>
              <StatusBadge status={selected.status} />
              <button
                onClick={() => toggleStatus(selected)}
                className={`text-xs px-3 py-1 rounded border ${
                  selected.status === 'active'
                    ? 'border-warning/40 text-warning-text hover:bg-warning-light'
                    : 'border-green/40 text-green-text hover:bg-green-light'
                }`}
              >
                {selected.status === 'active'
                  ? <><Pause className="w-3.5 h-3.5 inline mr-1" aria-hidden="true" />{t('campaigns.pause')}</>
                  : <><Play  className="w-3.5 h-3.5 inline mr-1" aria-hidden="true" />{t('campaigns.resume')}</>
                }
              </button>
              {/* S2.2 — dispara a avaliação REAL das instances scheduled */}
              <button
                onClick={() => handleDispatch(selected)}
                disabled={seeding}
                title={t('campaigns.dispatchHint', { defaultValue: 'Despacha as avaliações pendentes para o pool avaliador (avaliação real)' })}
                className="text-xs px-3 py-1 rounded bg-primary text-white hover:bg-primary-dark disabled:opacity-50"
              >
                {t('campaigns.dispatch', { defaultValue: 'Rodar agora' })}
              </button>
              {/* S2.Q1 — avaliador fake: gera volume sintético p/ validar o módulo */}
              <button
                onClick={() => handleSeed(selected, 50)}
                disabled={seeding}
                title={t('campaigns.seedSyntheticHint', { defaultValue: 'Gera 50 avaliações sintéticas (teste de volume do módulo)' })}
                className="text-xs px-3 py-1 rounded border border-secondary/40 text-secondary hover:bg-secondary/10 disabled:opacity-50"
              >
                {seeding
                  ? t('campaigns.seedSyntheticBusy', { defaultValue: 'Gerando…' })
                  : t('campaigns.seedSynthetic', { defaultValue: 'Gerar avaliações de teste' })}
              </button>
              <button
                onClick={handleFlush}
                disabled={seeding}
                title={t('campaigns.flushSyntheticHint', { defaultValue: 'Apaga toda a massa sintética (avaliações + NPS de teste)' })}
                className="text-xs px-3 py-1 rounded border border-red/40 text-red-text hover:bg-red-light disabled:opacity-50"
              >
                {t('campaigns.flushSynthetic', { defaultValue: 'Limpar dados de teste' })}
              </button>
              <button
                onClick={() => { setEditingCampaign(selected); setShowCreate(true) }}
                title={t('campaigns.editHint', { defaultValue: 'Editar a campanha' })}
                className="text-xs px-3 py-1 rounded border border-border text-muted hover:text-dark hover:bg-border/50"
              >
                {t('campaigns.edit', { defaultValue: 'Editar' })}
              </button>
              <button
                onClick={() => handleDelete(selected)}
                disabled={seeding}
                title={t('campaigns.deleteHint', { defaultValue: 'Excluir a campanha e suas avaliações' })}
                className="text-xs px-3 py-1 rounded border border-red/40 text-red-text hover:bg-red-light disabled:opacity-50"
              >
                {t('campaigns.delete', { defaultValue: 'Excluir' })}
              </button>
            </div>
            {seedMsg && (
              <div className="text-xs text-green-text bg-green-light border border-green/30 rounded px-3 py-1.5">
                {seedMsg}
              </div>
            )}

            {actionError && <div className="bg-red-light text-red-text text-sm p-2 rounded">{actionError}</div>}

            {selected.description && (
              <p className="text-sm text-muted">{selected.description}</p>
            )}

            <div className="bg-white border rounded p-4 space-y-3">
              <h3 className="font-semibold text-dark">{t('campaigns.progress')}</h3>
              <ReportPanel campaignId={selected.campaign_id} />
            </div>

            <div className="grid grid-cols-2 gap-4 text-sm">
              <div className="bg-white border rounded p-3">
                <div className="text-xs font-semibold text-muted mb-2">{t('campaigns.detail.sampling')}</div>
                <div className="text-dark space-y-1">
                  <div>{t('campaigns.detail.samplingMode', { mode: selected.sampling_rules?.mode ?? 'percentage' })}</div>
                  {selected.sampling_rules?.rate !== undefined && (
                    <div>{t('campaigns.detail.samplingRate', { rate: (selected.sampling_rules.rate * 100).toFixed(0) })}</div>
                  )}
                  {selected.sampling_rules?.every_n !== undefined && (
                    <div>{t('campaigns.detail.samplingEvery', { count: selected.sampling_rules.every_n })}</div>
                  )}
                </div>
              </div>

              <div className="bg-white border rounded p-3">
                <div className="text-xs font-semibold text-muted mb-2">{t('campaigns.detail.reviewerIA')}</div>
                <div className="text-dark space-y-1">
                  <div>{t('campaigns.detail.autoReview', { enabled: selected.reviewer_rules?.auto_review ? 'Yes' : 'No' })}</div>
                  {selected.reviewer_rules?.score_threshold !== undefined && (
                    <div>{t('campaigns.detail.threshold', { threshold: selected.reviewer_rules.score_threshold })}</div>
                  )}
                </div>
              </div>

              {/* Workflow skill */}
              <div className="bg-white border rounded p-3">
                <div className="text-xs font-semibold text-muted mb-2">{t('campaigns.detail.skillReview')}</div>
                {selected.review_workflow_skill_id ? (
                  <div className="text-dark space-y-1">
                    <div className="font-mono text-xs bg-surface-muted border rounded px-2 py-1 break-all">
                      {selected.review_workflow_skill_id}
                    </div>
                    {WORKFLOW_SKILL_VALUES.includes(selected.review_workflow_skill_id as any) && (
                      <div className="text-xs text-muted">
                        {t(`campaigns.workflowSkills.${selected.review_workflow_skill_id}`, selected.review_workflow_skill_id)}
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="text-xs text-muted-light italic">{t('campaigns.detail.noWorkflow')}</div>
                )}
              </div>

              {/* Evaluation pool */}
              <div className="bg-white border rounded p-3">
                <div className="text-xs font-semibold text-muted mb-2">{t('campaigns.detail.evaluationPool')}</div>
                {selected.evaluation_pool_id ? (
                  <div className="font-mono text-xs bg-surface-muted border rounded px-2 py-1 break-all text-dark">
                    {selected.evaluation_pool_id}
                  </div>
                ) : (
                  <div className="text-xs text-muted-light italic">{t('campaigns.detail.noPool')}</div>
                )}
              </div>

              {/* S2.2 — Evaluator pool */}
              <div className="bg-white border rounded p-3">
                <div className="text-xs font-semibold text-muted mb-2">{t('campaigns.detail.evaluatorPool', { defaultValue: 'Pool avaliador' })}</div>
                {(selected as { evaluator_pool?: string }).evaluator_pool ? (
                  <div className="font-mono text-xs bg-surface-muted border rounded px-2 py-1 break-all text-dark">
                    {(selected as { evaluator_pool?: string }).evaluator_pool}
                  </div>
                ) : (
                  <div className="text-xs text-muted-light italic">{t('campaigns.detail.evaluatorPoolDefault', { defaultValue: 'Padrão global (avaliacao_ia)' })}</div>
                )}
              </div>

              {/* SLA calendar */}
              <div className="bg-white border rounded p-3">
                <div className="text-xs font-semibold text-muted mb-2">{t('campaigns.detail.evaluationCalendar')}</div>
                {selected.evaluation_calendar_id ? (
                  <div className="font-mono text-xs bg-surface-muted border rounded px-2 py-1 break-all text-dark">
                    {selected.evaluation_calendar_id}
                  </div>
                ) : (
                  <div className="text-xs text-muted-light italic">{t('campaigns.detail.noCalendar')}</div>
                )}
              </div>

              {/* T17 — janela de dados (período) */}
              <div className="bg-white border rounded p-3">
                <div className="text-xs font-semibold text-muted mb-2">{t('campaigns.detail.period', { defaultValue: 'Janela de dados (período)' })}</div>
                {selected.period_start || selected.period_end ? (
                  <div className="font-mono text-xs bg-surface-muted border rounded px-2 py-1 text-dark">
                    {(selected.period_start ?? '∞').slice(0, 10)} → {(selected.period_end ?? '∞').slice(0, 10)}
                  </div>
                ) : (
                  <div className="text-xs text-muted-light italic">{t('campaigns.detail.noPeriod', { defaultValue: 'Janela aberta (todas as sessões)' })}</div>
                )}
              </div>

              {/* Contestation policy — Arc 13 extended view */}
              <div className="bg-white border rounded p-3 col-span-2">
                <div className="text-xs font-semibold text-muted mb-2">{t('campaigns.detail.contestationPolicy')}</div>
                {selected.contestation_policy ? (
                  <div className="text-dark space-y-2">
                    <div className="grid grid-cols-3 gap-3 text-xs">
                      <div className="bg-surface-muted rounded px-2 py-1.5">
                        <div className="text-muted-light mb-0.5">{t('campaigns.detail.maxRoundsLabel')}</div>
                        <div className="font-bold text-dark">{selected.contestation_policy.max_rounds}</div>
                      </div>
                      <div className="bg-surface-muted rounded px-2 py-1.5">
                        <div className="text-muted-light mb-0.5">{t('campaigns.detail.contestDeadlineLabel')}</div>
                        <div className="font-bold text-dark">{selected.contestation_policy.contest_deadline_hours ?? 72}h</div>
                      </div>
                      <div className="bg-surface-muted rounded px-2 py-1.5">
                        <div className="text-muted-light mb-0.5">{t('campaigns.detail.reviewDeadlineLabel')}</div>
                        <div className="font-bold text-dark">{selected.contestation_policy.review_deadline_hours}h</div>
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-2 text-xs">
                      {selected.contestation_policy.reviewer_type && (
                        <span className="bg-primary-light text-primary px-2 py-0.5 rounded font-medium">
                          {t('campaigns.detail.reviewerLabel')} {t(`campaigns.reviewerTypes.${selected.contestation_policy.reviewer_type}`, selected.contestation_policy.reviewer_type)}
                        </span>
                      )}
                      {selected.contestation_policy.pre_review_enabled && (
                        <span className="bg-revised-light text-revised-text px-2 py-0.5 rounded font-medium">
                          {t('campaigns.detail.preReviewActiveLabel')}
                          {selected.contestation_policy.pre_review_agent_pool && ` (${selected.contestation_policy.pre_review_agent_pool})`}
                        </span>
                      )}
                      {selected.contestation_policy.use_business_hours && (
                        <span className="bg-surface-alt text-muted px-2 py-0.5 rounded">{t('campaigns.detail.businessHoursLabel')}</span>
                      )}
                      {selected.contestation_policy.auto_lock_on_timeout && (
                        <span className="bg-surface-alt text-muted px-2 py-0.5 rounded">{t('campaigns.detail.autoLockLabel')}</span>
                      )}
                    </div>
                  </div>
                ) : (
                  <div className="text-xs text-muted-light italic">{t('campaigns.detail.noPolicy')}</div>
                )}
              </div>
            </div>

            {/* Arc 13 — Curation sampling rules panel */}
            {selected.review_workflow_skill_id === 'skill_revisao_treplica_v1' && (
              <CurationSamplingRulesDetailPanel
                campaignId={selected.campaign_id}
                adminToken={adminToken}
              />
            )}
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-muted-light gap-3">
            <div className="text-4xl">{t('campaigns.empty.emptyTitle')}</div>
            <p>{t('campaigns.empty.selectCampaign')}</p>
            <button
              onClick={() => setShowCreate(true)}
              className="bg-primary text-white px-4 py-2 rounded text-sm hover:bg-primary-dark"
            >
              {t('campaigns.empty.newCampaignButton')}
            </button>
          </div>
        )}
      </div>

      {showCreate && (
        <CreateModal
          key={editingCampaign
            ? `edit-${editingCampaign.campaign_id}-${(editingCampaign as { updated_at?: string }).updated_at ?? ''}`
            : 'new'}
          adminToken={adminToken}
          editing={editingCampaign}
          onClose={() => { setShowCreate(false); setEditingCampaign(null) }}
          onCreated={() => { setShowCreate(false); setEditingCampaign(null); reload() }}
        />
      )}
    </div>
  )
}
