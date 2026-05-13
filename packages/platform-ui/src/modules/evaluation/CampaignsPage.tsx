/**
 * CampaignsPage.tsx
 * /evaluation/campaigns — Campaign CRUD + live dashboard
 */

import React, { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import {
  useCampaigns,
  useForms,
  createCampaign,
  pauseCampaign,
  resumeCampaign,
  useCampaignReport,
} from '@/api/evaluation-hooks'
import type { EvaluationCampaign, CampaignReport } from '@/types'
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
    draft:   'bg-gray-100 text-gray-600',
    active:  'bg-green-100 text-green-800',
    paused:  'bg-yellow-100 text-yellow-800',
    closed:  'bg-red-100 text-red-700',
  }
  const statusLabel = t(`campaigns.status.${status}`, { defaultValue: status })
  return <span className={`px-2 py-0.5 rounded text-xs font-medium ${styles[status] ?? 'bg-gray-100'}`}>{statusLabel}</span>
}

function ProgressBar({ pct }: { pct: number }) {
  const clamp = Math.max(0, Math.min(100, pct))
  return (
    <div className="w-full bg-gray-200 rounded-full h-2">
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

  if (loading) return <div className="text-xs text-gray-400 py-4 text-center">{t('campaigns.loading')}</div>
  if (!report) return <div className="text-xs text-gray-400 py-4 text-center">{t('campaigns.noReport')}</div>

  const pct = report.completion_pct ?? 0

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-4 gap-3">
        {[
          { label: t('campaigns.total'), value: report.total, color: 'text-gray-700' },
          { label: t('campaigns.completed'), value: report.completed, color: 'text-green-700' },
          { label: t('campaigns.pending'), value: report.pending, color: 'text-yellow-700' },
          { label: t('campaigns.underReview'), value: report.in_review, color: 'text-blue-700' },
        ].map(k => (
          <div key={k.label} className="bg-gray-50 rounded p-3 text-center">
            <div className={`text-2xl font-bold ${k.color}`}>{k.value}</div>
            <div className="text-xs text-gray-500 mt-1">{k.label}</div>
          </div>
        ))}
      </div>

      <div>
        <div className="flex justify-between text-xs text-gray-600 mb-1">
          <span>{t('campaigns.conclusion')}</span>
          <span>{pct.toFixed(1)}%</span>
        </div>
        <ProgressBar pct={pct} />
      </div>

      {report.avg_score !== null && (
        <div className="flex items-center gap-3">
          <span className="text-xs text-gray-500">{t('campaigns.avgScore')}</span>
          <span className="text-lg font-bold text-primary">{report.avg_score?.toFixed(2)}</span>
          {report.score_p25 !== null && report.score_p75 !== null && (
            <span className="text-xs text-gray-400">
              P25: {report.score_p25?.toFixed(1)} · P75: {report.score_p75?.toFixed(1)}
            </span>
          )}
        </div>
      )}

      {report.top_flags && report.top_flags.length > 0 && (
        <div>
          <div className="text-xs font-semibold text-gray-600 mb-1">{t('campaigns.frequentFlags')}</div>
          <div className="flex flex-wrap gap-1">
            {report.top_flags.map(f => (
              <span key={f} className="bg-red-50 text-red-700 text-xs px-2 py-0.5 rounded">{f}</span>
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
}

const WORKFLOW_SKILL_OPTIONS = [
  { value: 'skill_revisao_simples_v1',   label: 'Revisão simples (1 round, 48h)' },
  { value: 'skill_revisao_treplica_v1',  label: 'Tréplica (até 3 rounds, 48h/72h)' },
]

const AUTHORITY_OPTIONS = [
  { value: 'supervisor', label: 'Supervisor' },
  { value: 'manager',    label: 'Gerente' },
  { value: 'director',   label: 'Diretor' },
]

function CreateModal({ onClose, onCreated, adminToken }: CreateModalProps) {
  const { t } = useTranslation('evaluation')
  const { tenantId: TENANT } = useAuth()
  const { forms } = useForms(TENANT)
  const poolOptions     = usePoolOptions(TENANT)
  const calendarOptions = useCalendarOptions(TENANT)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [formId, setFormId] = useState('')
  const [evaluationPoolId,     setEvaluationPoolId]     = useState('')
  const [evaluationCalendarId, setEvaluationCalendarId] = useState('')
  const [samplingMode, setSamplingMode] = useState<'all' | 'percentage' | 'fixed'>('percentage')
  const [samplingRate, setSamplingRate] = useState('0.1')
  const [autoReview, setAutoReview] = useState(true)
  const [scoreThreshold, setScoreThreshold] = useState('7')

  // Contestation / workflow fields
  const [workflowSkillId, setWorkflowSkillId] = useState('skill_revisao_simples_v1')
  const [enableContestation, setEnableContestation] = useState(false)
  const [maxRounds, setMaxRounds] = useState('1')
  const [reviewDeadlineHours, setReviewDeadlineHours] = useState('48')
  const [authorityLevel, setAuthorityLevel] = useState<'supervisor' | 'manager' | 'director'>('supervisor')
  const [autoLockOnTimeout, setAutoLockOnTimeout] = useState(true)

  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    if (!name || !formId) { setError(t('campaigns.modal.errorRequired')); return }
    setSaving(true)
    setError(null)
    try {
      await createCampaign({
        tenant_id: TENANT,
        form_id: formId,
        name,
        description,
        status: 'draft',
        review_workflow_skill_id: workflowSkillId || undefined,
        evaluation_pool_id:     evaluationPoolId     || undefined,
        evaluation_calendar_id: evaluationCalendarId || undefined,
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
          contestation_roles:    ['supervisor', 'admin'],
          max_rounds:            parseInt(maxRounds),
          review_deadline_hours: parseInt(reviewDeadlineHours),
          auto_lock_on_timeout:  autoLockOnTimeout,
        } : undefined,
      }, adminToken)
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
          <h2 className="font-semibold text-gray-800">{t('campaigns.modal.title')}</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">{t('campaigns.modal.close')}</button>
        </div>

        <div className="p-4 space-y-4">
          {/* Basic info */}
          <div className="grid grid-cols-2 gap-3">
            <div className="col-span-2">
              <label className="block text-xs font-medium text-gray-600 mb-1">{t('campaigns.modal.nameLabel')}</label>
              <input
                className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm"
                value={name}
                onChange={e => setName(e.target.value)}
                placeholder={t('campaigns.modal.nameExample')}
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">{t('campaigns.modal.formLabel')}</label>
              <select
                className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm"
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
              <label className="block text-xs font-medium text-gray-600 mb-1">{t('campaigns.modal.reviewSkillLabel')}</label>
              <select
                className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm"
                value={workflowSkillId}
                onChange={e => setWorkflowSkillId(e.target.value)}
              >
                <option value="">{t('campaigns.modal.noWorkflow')}</option>
                {WORKFLOW_SKILL_OPTIONS.map(o => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>

            {/* Evaluation pool */}
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">{t('campaigns.modal.evaluationPoolLabel')}</label>
              <select
                className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm"
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

            {/* SLA calendar */}
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">{t('campaigns.modal.evaluationCalendarLabel')}</label>
              <select
                className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm"
                value={evaluationCalendarId}
                onChange={e => setEvaluationCalendarId(e.target.value)}
              >
                <option value="">{t('campaigns.modal.selectCalendar')}</option>
                {calendarOptions.map(c => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            </div>

            <div className="col-span-2">
              <label className="block text-xs font-medium text-gray-600 mb-1">{t('campaigns.modal.descriptionLabel')}</label>
              <textarea
                className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm resize-none"
                rows={2}
                value={description}
                onChange={e => setDescription(e.target.value)}
              />
            </div>
          </div>

          {/* Sampling */}
          <div className="border-t pt-3">
            <div className="text-xs font-semibold text-gray-600 mb-2">{t('campaigns.modal.samplingRules')}</div>
            <div className="flex gap-3 items-center">
              <select
                className="border border-gray-300 rounded px-2 py-1 text-sm"
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
                  className="w-20 border border-gray-300 rounded px-2 py-1 text-sm text-center"
                  value={samplingRate}
                  onChange={e => setSamplingRate(e.target.value)}
                />
              )}
              {samplingMode === 'percentage' && <span className="text-xs text-gray-500">{t('campaigns.modal.samplingHint')}</span>}
            </div>
          </div>

          {/* Reviewer IA */}
          <div className="border-t pt-3">
            <div className="text-xs font-semibold text-gray-600 mb-2">{t('campaigns.modal.reviewerIA')}</div>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={autoReview}
                onChange={e => setAutoReview(e.target.checked)}
              />
              {t('campaigns.modal.enableAutoReview')}
            </label>
            {autoReview && (
              <div className="flex items-center gap-2 mt-2 text-sm text-gray-600">
                <span>{t('campaigns.modal.escalateThreshold')}</span>
                <input
                  type="number"
                  min={1}
                  max={10}
                  step={0.5}
                  className="w-16 border border-gray-300 rounded px-2 py-0.5 text-center"
                  value={scoreThreshold}
                  onChange={e => setScoreThreshold(e.target.value)}
                />
              </div>
            )}
          </div>

          {/* Contestation policy */}
          <div className="border-t pt-3">
            <label className="flex items-center gap-2 text-sm font-semibold text-gray-600 mb-2">
              <input
                type="checkbox"
                checked={enableContestation}
                onChange={e => setEnableContestation(e.target.checked)}
              />
              {t('campaigns.modal.enableContestation')}
            </label>

            {enableContestation && (
              <div className="bg-blue-50 border border-blue-100 rounded p-3 space-y-3 mt-2">
                <div className="grid grid-cols-3 gap-3">
                  <div>
                    <label className="block text-xs text-gray-600 mb-1">{t('campaigns.modal.maxRounds')}</label>
                    <input
                      type="number"
                      min={1}
                      max={5}
                      className="w-full border border-gray-300 rounded px-2 py-1 text-sm text-center"
                      value={maxRounds}
                      onChange={e => setMaxRounds(e.target.value)}
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-600 mb-1">{t('campaigns.modal.reviewDeadline')}</label>
                    <input
                      type="number"
                      min={1}
                      max={720}
                      step={1}
                      className="w-full border border-gray-300 rounded px-2 py-1 text-sm text-center"
                      value={reviewDeadlineHours}
                      onChange={e => setReviewDeadlineHours(e.target.value)}
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-600 mb-1">{t('campaigns.modal.authority')}</label>
                    <select
                      className="w-full border border-gray-300 rounded px-2 py-1 text-sm"
                      value={authorityLevel}
                      onChange={e => setAuthorityLevel(e.target.value as any)}
                    >
                      {AUTHORITY_OPTIONS.map(o => (
                        <option key={o.value} value={o.value}>{o.label}</option>
                      ))}
                    </select>
                  </div>
                </div>

                <label className="flex items-center gap-2 text-sm text-gray-700">
                  <input
                    type="checkbox"
                    checked={autoLockOnTimeout}
                    onChange={e => setAutoLockOnTimeout(e.target.checked)}
                  />
                  {t('campaigns.modal.autoLockTimeout')}
                </label>

                <p className="text-xs text-blue-700">
                  {t('campaigns.modal.skillInfo', { skill: workflowSkillId || '(nenhuma selecionada)' })}
                </p>
              </div>
            )}
          </div>

          {error && <div className="bg-red-50 text-red-700 text-sm p-2 rounded">{error}</div>}
        </div>

        <div className="flex justify-end gap-2 p-4 border-t">
          <button onClick={onClose} className="px-4 py-1.5 text-sm text-gray-600 hover:text-gray-800">{t('campaigns.modal.cancel')}</button>
          <button
            onClick={submit}
            disabled={saving}
            className="bg-primary text-white px-4 py-1.5 text-sm rounded hover:bg-blue-800 disabled:opacity-50"
          >
            {saving ? t('campaigns.modal.submit', { context: 'saving' }) : t('campaigns.modal.submit', { context: 'default' })}
          </button>
        </div>
      </div>
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

  return (
    <div className="flex h-full">
      {/* Sidebar */}
      <aside className="w-80 border-r flex flex-col bg-gray-50">
        <div className="p-3 border-b flex gap-2">
          <input
            className="flex-1 border border-gray-300 rounded px-2 py-1 text-xs"
            type="password"
            placeholder={t('campaigns.sidebar.adminTokenPlaceholder')}
            value={adminToken}
            onChange={e => setAdminToken(e.target.value)}
          />
          <button
            onClick={() => setShowCreate(true)}
            className="bg-primary text-white text-xs px-2 py-1 rounded hover:bg-blue-800"
          >
            {t('campaigns.sidebar.newButton')}
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-2 space-y-1">
          {loading && <p className="text-sm text-gray-400 p-2">{t('campaigns.sidebar.loading')}</p>}
          {campaigns.map(c => {
            const pct = c.total_instances > 0 ? (c.completed / c.total_instances) * 100 : 0
            return (
              <button
                key={c.campaign_id}
                onClick={() => setSelected(c)}
                className={`w-full text-left px-3 py-2 rounded text-sm transition-colors border ${
                  selected?.campaign_id === c.campaign_id
                    ? 'border-primary bg-blue-50'
                    : 'border-transparent hover:bg-gray-200'
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="font-medium truncate text-gray-800">{c.name}</span>
                  <StatusBadge status={c.status} />
                </div>
                <ProgressBar pct={pct} />
                <div className="flex justify-between text-xs text-gray-400 mt-1">
                  <span>{c.completed}/{c.total_instances} {t('campaigns.completed').toLowerCase()}</span>
                  <span>{pct.toFixed(0)}%</span>
                </div>
              </button>
            )
          })}
          {!loading && campaigns.length === 0 && (
            <p className="text-xs text-gray-400 text-center py-6">{t('campaigns.sidebar.noCampaigns')}</p>
          )}
        </div>
      </aside>

      {/* Detail */}
      <div className="flex-1 overflow-y-auto p-6">
        {selected ? (
          <div className="space-y-6 max-w-2xl">
            <div className="flex items-center gap-3">
              <h1 className="text-xl font-bold text-gray-800 flex-1">{selected.name}</h1>
              <StatusBadge status={selected.status} />
              <button
                onClick={() => toggleStatus(selected)}
                className={`text-xs px-3 py-1 rounded border ${
                  selected.status === 'active'
                    ? 'border-yellow-300 text-yellow-700 hover:bg-yellow-50'
                    : 'border-green-300 text-green-700 hover:bg-green-50'
                }`}
              >
                {selected.status === 'active' ? `⏸ ${t('campaigns.pause')}` : `▶ ${t('campaigns.resume')}`}
              </button>
            </div>

            {actionError && <div className="bg-red-50 text-red-700 text-sm p-2 rounded">{actionError}</div>}

            {selected.description && (
              <p className="text-sm text-gray-600">{selected.description}</p>
            )}

            <div className="bg-white border rounded p-4 space-y-3">
              <h3 className="font-semibold text-gray-700">{t('campaigns.progress')}</h3>
              <ReportPanel campaignId={selected.campaign_id} />
            </div>

            <div className="grid grid-cols-2 gap-4 text-sm">
              <div className="bg-white border rounded p-3">
                <div className="text-xs font-semibold text-gray-500 mb-2">{t('campaigns.detail.sampling')}</div>
                <div className="text-gray-700 space-y-1">
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
                <div className="text-xs font-semibold text-gray-500 mb-2">{t('campaigns.detail.reviewerIA')}</div>
                <div className="text-gray-700 space-y-1">
                  <div>{t('campaigns.detail.autoReview', { enabled: selected.reviewer_rules?.auto_review ? 'Yes' : 'No' })}</div>
                  {selected.reviewer_rules?.score_threshold !== undefined && (
                    <div>{t('campaigns.detail.threshold', { threshold: selected.reviewer_rules.score_threshold })}</div>
                  )}
                </div>
              </div>

              {/* Workflow skill */}
              <div className="bg-white border rounded p-3">
                <div className="text-xs font-semibold text-gray-500 mb-2">{t('campaigns.detail.skillReview')}</div>
                {selected.review_workflow_skill_id ? (
                  <div className="text-gray-700 space-y-1">
                    <div className="font-mono text-xs bg-gray-50 border rounded px-2 py-1 break-all">
                      {selected.review_workflow_skill_id}
                    </div>
                    {selected.review_workflow_skill_id === 'skill_revisao_simples_v1' && (
                      <div className="text-xs text-gray-500">{WORKFLOW_SKILL_OPTIONS[0].label}</div>
                    )}
                    {selected.review_workflow_skill_id === 'skill_revisao_treplica_v1' && (
                      <div className="text-xs text-gray-500">{WORKFLOW_SKILL_OPTIONS[1].label}</div>
                    )}
                  </div>
                ) : (
                  <div className="text-xs text-gray-400 italic">{t('campaigns.detail.noWorkflow')}</div>
                )}
              </div>

              {/* Evaluation pool */}
              <div className="bg-white border rounded p-3">
                <div className="text-xs font-semibold text-gray-500 mb-2">{t('campaigns.detail.evaluationPool')}</div>
                {selected.evaluation_pool_id ? (
                  <div className="font-mono text-xs bg-gray-50 border rounded px-2 py-1 break-all text-gray-700">
                    {selected.evaluation_pool_id}
                  </div>
                ) : (
                  <div className="text-xs text-gray-400 italic">{t('campaigns.detail.noPool')}</div>
                )}
              </div>

              {/* SLA calendar */}
              <div className="bg-white border rounded p-3">
                <div className="text-xs font-semibold text-gray-500 mb-2">{t('campaigns.detail.evaluationCalendar')}</div>
                {selected.evaluation_calendar_id ? (
                  <div className="font-mono text-xs bg-gray-50 border rounded px-2 py-1 break-all text-gray-700">
                    {selected.evaluation_calendar_id}
                  </div>
                ) : (
                  <div className="text-xs text-gray-400 italic">{t('campaigns.detail.noCalendar')}</div>
                )}
              </div>

              {/* Contestation policy */}
              <div className="bg-white border rounded p-3">
                <div className="text-xs font-semibold text-gray-500 mb-2">{t('campaigns.detail.contestationPolicy')}</div>
                {selected.contestation_policy ? (
                  <div className="text-gray-700 space-y-1">
                    <div>{t('campaigns.detail.maxRounds', { rounds: selected.contestation_policy.max_rounds })}</div>
                    <div>{t('campaigns.detail.reviewDeadline', { hours: selected.contestation_policy.review_deadline_hours })}</div>
                    <div>
                      {t('campaigns.detail.autoLock', { enabled: selected.contestation_policy.auto_lock_on_timeout ? 'Yes' : 'No' })}
                    </div>
                    {selected.contestation_policy.rounds && selected.contestation_policy.rounds.length > 0 && (
                      <div className="mt-2 space-y-1">
                        {selected.contestation_policy.rounds.map(r => (
                          <div key={r.round_number} className="text-xs bg-gray-50 rounded px-2 py-1">
                            Round {r.round_number}: alçada <strong>{r.authority_level}</strong>, {r.review_deadline_hours}h
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="text-xs text-gray-400 italic">{t('campaigns.detail.noPolicy')}</div>
                )}
              </div>
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-gray-400 gap-3">
            <div className="text-4xl">{t('campaigns.empty.emptyTitle')}</div>
            <p>{t('campaigns.empty.selectCampaign')}</p>
            <button
              onClick={() => setShowCreate(true)}
              className="bg-primary text-white px-4 py-2 rounded text-sm hover:bg-blue-800"
            >
              {t('campaigns.empty.newCampaignButton')}
            </button>
          </div>
        )}
      </div>

      {showCreate && (
        <CreateModal
          adminToken={adminToken}
          onClose={() => setShowCreate(false)}
          onCreated={() => { setShowCreate(false); reload() }}
        />
      )}
    </div>
  )
}
