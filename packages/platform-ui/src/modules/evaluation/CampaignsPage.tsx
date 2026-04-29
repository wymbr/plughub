/**
 * CampaignsPage.tsx
 * /evaluation/campaigns — Campaign CRUD + live dashboard
 */

import React, { useState } from 'react'
import {
  useCampaigns,
  useForms,
  createCampaign,
  pauseCampaign,
  resumeCampaign,
  useCampaignReport,
} from '@/api/evaluation-hooks'
import type { EvaluationCampaign, CampaignReport } from '@/types'

const TENANT = import.meta.env.VITE_TENANT_ID ?? 'tenant_demo'

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    draft:   'bg-gray-100 text-gray-600',
    active:  'bg-green-100 text-green-800',
    paused:  'bg-yellow-100 text-yellow-800',
    closed:  'bg-red-100 text-red-700',
  }
  return <span className={`px-2 py-0.5 rounded text-xs font-medium ${styles[status] ?? 'bg-gray-100'}`}>{status}</span>
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
  const { report, loading } = useCampaignReport(campaignId)

  if (loading) return <div className="text-xs text-gray-400 py-4 text-center">Carregando relatório…</div>
  if (!report) return <div className="text-xs text-gray-400 py-4 text-center">Sem dados de relatório</div>

  const pct = report.completion_pct ?? 0

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-4 gap-3">
        {[
          { label: 'Total', value: report.total, color: 'text-gray-700' },
          { label: 'Concluídas', value: report.completed, color: 'text-green-700' },
          { label: 'Pendentes', value: report.pending, color: 'text-yellow-700' },
          { label: 'Em revisão', value: report.in_review, color: 'text-blue-700' },
        ].map(k => (
          <div key={k.label} className="bg-gray-50 rounded p-3 text-center">
            <div className={`text-2xl font-bold ${k.color}`}>{k.value}</div>
            <div className="text-xs text-gray-500 mt-1">{k.label}</div>
          </div>
        ))}
      </div>

      <div>
        <div className="flex justify-between text-xs text-gray-600 mb-1">
          <span>Conclusão</span>
          <span>{pct.toFixed(1)}%</span>
        </div>
        <ProgressBar pct={pct} />
      </div>

      {report.avg_score !== null && (
        <div className="flex items-center gap-3">
          <span className="text-xs text-gray-500">Nota média:</span>
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
          <div className="text-xs font-semibold text-gray-600 mb-1">Flags frequentes</div>
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
  const { forms } = useForms(TENANT)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [formId, setFormId] = useState('')
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
    if (!name || !formId) { setError('Nome e formulário são obrigatórios'); return }
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
          <h2 className="font-semibold text-gray-800">Nova Campanha de Avaliação</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">✕</button>
        </div>

        <div className="p-4 space-y-4">
          {/* Basic info */}
          <div className="grid grid-cols-2 gap-3">
            <div className="col-span-2">
              <label className="block text-xs font-medium text-gray-600 mb-1">Nome *</label>
              <input
                className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm"
                value={name}
                onChange={e => setName(e.target.value)}
                placeholder="Ex: Avaliação SAC — Abril 2026"
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Formulário *</label>
              <select
                className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm"
                value={formId}
                onChange={e => setFormId(e.target.value)}
              >
                <option value="">Selecione um formulário</option>
                {forms.filter(f => f.status === 'active').map(f => (
                  <option key={f.form_id} value={f.form_id}>{f.name}</option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Skill de revisão</label>
              <select
                className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm"
                value={workflowSkillId}
                onChange={e => setWorkflowSkillId(e.target.value)}
              >
                <option value="">Nenhuma (sem workflow)</option>
                {WORKFLOW_SKILL_OPTIONS.map(o => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>

            <div className="col-span-2">
              <label className="block text-xs font-medium text-gray-600 mb-1">Descrição</label>
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
            <div className="text-xs font-semibold text-gray-600 mb-2">Regras de Sampling</div>
            <div className="flex gap-3 items-center">
              <select
                className="border border-gray-300 rounded px-2 py-1 text-sm"
                value={samplingMode}
                onChange={e => setSamplingMode(e.target.value as any)}
              >
                <option value="all">Todos</option>
                <option value="percentage">% das sessões</option>
                <option value="fixed">A cada N sessões</option>
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
              {samplingMode === 'percentage' && <span className="text-xs text-gray-500">(0–1, ex: 0.1 = 10%)</span>}
            </div>
          </div>

          {/* Reviewer IA */}
          <div className="border-t pt-3">
            <div className="text-xs font-semibold text-gray-600 mb-2">Reviewer IA</div>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={autoReview}
                onChange={e => setAutoReview(e.target.checked)}
              />
              Ativar revisão automática por IA
            </label>
            {autoReview && (
              <div className="flex items-center gap-2 mt-2 text-sm text-gray-600">
                <span>Escalar para humano quando nota &lt;</span>
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
              Habilitar política de contestação
            </label>

            {enableContestation && (
              <div className="bg-blue-50 border border-blue-100 rounded p-3 space-y-3 mt-2">
                <div className="grid grid-cols-3 gap-3">
                  <div>
                    <label className="block text-xs text-gray-600 mb-1">Máx. rounds</label>
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
                    <label className="block text-xs text-gray-600 mb-1">Prazo por round (h)</label>
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
                    <label className="block text-xs text-gray-600 mb-1">Alçada</label>
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
                  Bloquear resultado automaticamente ao expirar prazo
                </label>

                <p className="text-xs text-blue-700">
                  Skill: <strong>{workflowSkillId || '(nenhuma selecionada)'}</strong> — define o número real de rounds e timeouts.
                  Os valores acima são usados como fallback e exibição na UI.
                </p>
              </div>
            )}
          </div>

          {error && <div className="bg-red-50 text-red-700 text-sm p-2 rounded">{error}</div>}
        </div>

        <div className="flex justify-end gap-2 p-4 border-t">
          <button onClick={onClose} className="px-4 py-1.5 text-sm text-gray-600 hover:text-gray-800">Cancelar</button>
          <button
            onClick={submit}
            disabled={saving}
            className="bg-primary text-white px-4 py-1.5 text-sm rounded hover:bg-blue-800 disabled:opacity-50"
          >
            {saving ? 'Criando…' : 'Criar campanha'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── CampaignsPage ─────────────────────────────────────────────────────────────

export default function CampaignsPage() {
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
            placeholder="Admin token"
            value={adminToken}
            onChange={e => setAdminToken(e.target.value)}
          />
          <button
            onClick={() => setShowCreate(true)}
            className="bg-primary text-white text-xs px-2 py-1 rounded hover:bg-blue-800"
          >
            + Nova
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-2 space-y-1">
          {loading && <p className="text-sm text-gray-400 p-2">Carregando…</p>}
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
                  <span>{c.completed}/{c.total_instances} concluídas</span>
                  <span>{pct.toFixed(0)}%</span>
                </div>
              </button>
            )
          })}
          {!loading && campaigns.length === 0 && (
            <p className="text-xs text-gray-400 text-center py-6">Nenhuma campanha encontrada</p>
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
                {selected.status === 'active' ? '⏸ Pausar' : '▶ Retomar'}
              </button>
            </div>

            {actionError && <div className="bg-red-50 text-red-700 text-sm p-2 rounded">{actionError}</div>}

            {selected.description && (
              <p className="text-sm text-gray-600">{selected.description}</p>
            )}

            <div className="bg-white border rounded p-4 space-y-3">
              <h3 className="font-semibold text-gray-700">Progresso</h3>
              <ReportPanel campaignId={selected.campaign_id} />
            </div>

            <div className="grid grid-cols-2 gap-4 text-sm">
              <div className="bg-white border rounded p-3">
                <div className="text-xs font-semibold text-gray-500 mb-2">Sampling</div>
                <div className="text-gray-700 space-y-1">
                  <div>Modo: <strong>{selected.sampling_rules?.mode ?? 'percentage'}</strong></div>
                  {selected.sampling_rules?.rate !== undefined && (
                    <div>Taxa: <strong>{(selected.sampling_rules.rate * 100).toFixed(0)}%</strong></div>
                  )}
                  {selected.sampling_rules?.every_n !== undefined && (
                    <div>A cada: <strong>{selected.sampling_rules.every_n} sessões</strong></div>
                  )}
                </div>
              </div>

              <div className="bg-white border rounded p-3">
                <div className="text-xs font-semibold text-gray-500 mb-2">Reviewer IA</div>
                <div className="text-gray-700 space-y-1">
                  <div>Auto-review: <strong>{selected.reviewer_rules?.auto_review ? 'Sim' : 'Não'}</strong></div>
                  {selected.reviewer_rules?.score_threshold !== undefined && (
                    <div>Threshold: <strong>{selected.reviewer_rules.score_threshold}</strong></div>
                  )}
                </div>
              </div>

              {/* Workflow skill */}
              <div className="bg-white border rounded p-3">
                <div className="text-xs font-semibold text-gray-500 mb-2">Skill de revisão</div>
                {selected.review_workflow_skill_id ? (
                  <div className="text-gray-700 space-y-1">
                    <div className="font-mono text-xs bg-gray-50 border rounded px-2 py-1 break-all">
                      {selected.review_workflow_skill_id}
                    </div>
                    {selected.review_workflow_skill_id === 'skill_revisao_simples_v1' && (
                      <div className="text-xs text-gray-500">Revisão simples — 1 round, 48h</div>
                    )}
                    {selected.review_workflow_skill_id === 'skill_revisao_treplica_v1' && (
                      <div className="text-xs text-gray-500">Tréplica — até 3 rounds</div>
                    )}
                  </div>
                ) : (
                  <div className="text-xs text-gray-400 italic">Sem workflow configurado</div>
                )}
              </div>

              {/* Contestation policy */}
              <div className="bg-white border rounded p-3">
                <div className="text-xs font-semibold text-gray-500 mb-2">Política de contestação</div>
                {selected.contestation_policy ? (
                  <div className="text-gray-700 space-y-1">
                    <div>Máx. rounds: <strong>{selected.contestation_policy.max_rounds}</strong></div>
                    <div>Prazo por round: <strong>{selected.contestation_policy.review_deadline_hours}h</strong></div>
                    <div>
                      Bloquear no timeout:{' '}
                      <strong>{selected.contestation_policy.auto_lock_on_timeout ? 'Sim' : 'Não'}</strong>
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
                  <div className="text-xs text-gray-400 italic">Sem política de contestação</div>
                )}
              </div>
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-gray-400 gap-3">
            <div className="text-4xl">📋</div>
            <p>Selecione uma campanha na lista</p>
            <button
              onClick={() => setShowCreate(true)}
              className="bg-primary text-white px-4 py-2 rounded text-sm hover:bg-blue-800"
            >
              + Nova Campanha
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
