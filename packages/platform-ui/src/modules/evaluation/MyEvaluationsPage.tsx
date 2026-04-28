/**
 * MyEvaluationsPage.tsx
 * /evaluation/my-evaluations — Portal do avaliado
 * Agents see their own evaluations and can contest results.
 */

import React, { useState } from 'react'
import { useResults, createContestation } from '@/api/evaluation-hooks'
import type { EvaluationResult, EvaluationCriterionResponse } from '@/types'
import { useAuth } from '@/auth/useAuth'

const TENANT = import.meta.env.VITE_TENANT_ID ?? 'tenant_demo'

function ScorePill({ score }: { score: number }) {
  const bg = score >= 8 ? 'bg-green-100 text-green-800' : score >= 6 ? 'bg-yellow-100 text-yellow-800' : 'bg-red-100 text-red-800'
  return <span className={`px-2 py-0.5 rounded text-sm font-bold ${bg}`}>{score.toFixed(1)}</span>
}

function EvalStatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    submitted: 'bg-blue-100 text-blue-700',
    approved: 'bg-green-100 text-green-800',
    adjusted_approved: 'bg-teal-100 text-teal-700',
    rejected: 'bg-red-100 text-red-700',
    contested: 'bg-orange-100 text-orange-700',
  }
  const labels: Record<string, string> = {
    submitted: 'Em revisão',
    approved: 'Aprovado',
    adjusted_approved: 'Aprovado c/ ajuste',
    rejected: 'Rejeitado',
    contested: 'Contestado',
  }
  return <span className={`px-2 py-0.5 rounded text-xs font-medium ${styles[status] ?? 'bg-gray-100'}`}>{labels[status] ?? status}</span>
}

// ── ContestModal ───────────────────────────────────────────────────────────────

function ContestModal({
  result,
  agentId,
  onClose,
  onContested,
}: {
  result: EvaluationResult
  agentId: string
  onClose: () => void
  onContested: () => void
}) {
  const [reason, setReason] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    if (!reason.trim()) { setError('Descreva o motivo da contestação'); return }
    setSaving(true)
    setError(null)
    try {
      await createContestation({
        result_id: result.result_id,
        tenant_id: TENANT,
        contested_by: agentId,
        reason: reason.trim(),
      })
      onContested()
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-[500px] p-6 space-y-4">
        <h2 className="font-semibold text-gray-800">Contestar Avaliação</h2>
        <p className="text-sm text-gray-600">
          Sessão: <code className="bg-gray-100 px-1 rounded">{result.session_id}</code> ·
          Nota: <strong>{result.overall_score.toFixed(1)}</strong>
        </p>
        <p className="text-xs text-gray-500">
          Descreva detalhadamente por que você discorda desta avaliação.
          Seu supervisor receberá a contestação para análise.
        </p>
        <textarea
          className="w-full border border-gray-300 rounded px-3 py-2 text-sm resize-none"
          rows={5}
          placeholder="Ex: O critério X foi avaliado incorretamente porque… A transcript mostra que…"
          value={reason}
          onChange={e => setReason(e.target.value)}
        />
        {error && <div className="text-red-600 text-sm">{error}</div>}
        <div className="flex justify-end gap-2">
          <button onClick={onClose} className="text-sm text-gray-500 hover:text-gray-700 px-4 py-1.5">Cancelar</button>
          <button
            onClick={submit}
            disabled={saving}
            className="bg-orange-600 text-white text-sm px-4 py-1.5 rounded hover:bg-orange-700 disabled:opacity-50"
          >
            {saving ? 'Enviando…' : 'Enviar contestação'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── ResultCard ─────────────────────────────────────────────────────────────────

function ResultCard({
  result,
  agentId,
  onContested,
}: {
  result: EvaluationResult
  agentId: string
  onContested: () => void
}) {
  const [expanded, setExpanded] = useState(false)
  const [showContest, setShowContest] = useState(false)
  const canContest = !result.locked && result.eval_status !== 'contested' && result.eval_status !== 'rejected'

  return (
    <div className="bg-white border rounded-lg overflow-hidden">
      <button
        className="w-full text-left px-4 py-3 hover:bg-gray-50 transition-colors"
        onClick={() => setExpanded(v => !v)}
      >
        <div className="flex items-center gap-3">
          <div className="flex-1">
            <div className="flex items-center gap-2 mb-1">
              <span className="font-medium text-sm text-gray-800">Sessão</span>
              <code className="text-xs bg-gray-100 px-1 rounded text-gray-500">{result.session_id}</code>
            </div>
            <div className="flex items-center gap-2">
              <EvalStatusBadge status={result.eval_status} />
              {result.locked && <span className="text-xs text-gray-400">🔒 bloqueado</span>}
              <span className="text-xs text-gray-400">{new Date(result.created_at).toLocaleDateString('pt-BR')}</span>
            </div>
          </div>
          <ScorePill score={result.overall_score} />
          <span className="text-gray-400 text-xs">{expanded ? '▲' : '▼'}</span>
        </div>
      </button>

      {expanded && (
        <div className="px-4 pb-4 space-y-3 border-t pt-3">
          <div className="bg-blue-50 rounded p-3">
            <div className="text-xs font-semibold text-blue-700 mb-1">Observação geral</div>
            <p className="text-sm text-gray-700">{result.overall_observation}</p>
          </div>

          {result.highlights?.length > 0 && (
            <div>
              <div className="text-xs font-semibold text-green-700 mb-1">Pontos positivos</div>
              <ul className="text-xs text-gray-600 space-y-0.5">
                {result.highlights.map((h, i) => <li key={i} className="flex gap-1"><span>✓</span>{h}</li>)}
              </ul>
            </div>
          )}

          {result.improvement_points?.length > 0 && (
            <div>
              <div className="text-xs font-semibold text-orange-700 mb-1">Pontos de melhoria</div>
              <ul className="text-xs text-gray-600 space-y-0.5">
                {result.improvement_points.map((p, i) => <li key={i} className="flex gap-1"><span>↑</span>{p}</li>)}
              </ul>
            </div>
          )}

          {result.compliance_flags?.length > 0 && (
            <div>
              <div className="text-xs font-semibold text-red-700 mb-1">Flags detectadas</div>
              <div className="flex flex-wrap gap-1">
                {result.compliance_flags.map(f => (
                  <span key={f} className="bg-red-50 text-red-600 text-xs px-2 py-0.5 rounded">{f}</span>
                ))}
              </div>
            </div>
          )}

          {result.criterion_responses?.length > 0 && (
            <div>
              <div className="text-xs font-semibold text-gray-600 mb-2">Critérios</div>
              <div className="border rounded divide-y text-xs">
                {result.criterion_responses.map((cr: EvaluationCriterionResponse) => (
                  <div key={cr.criterion_id} className="flex gap-3 px-3 py-2">
                    <span className="font-mono text-gray-400 w-28 shrink-0">{cr.criterion_id}</span>
                    {cr.na ? (
                      <span className="text-gray-400 italic">N/A — {cr.na_reason}</span>
                    ) : (
                      <div>
                        <span className="font-bold text-gray-700">{cr.value}/10</span>
                        <span className="text-gray-500 ml-2">{cr.justification}</span>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {canContest && (
            <div className="flex justify-end">
              <button
                onClick={() => setShowContest(true)}
                className="text-xs text-orange-600 hover:text-orange-800 border border-orange-200 rounded px-3 py-1.5"
              >
                ⚡ Contestar avaliação
              </button>
            </div>
          )}
          {result.eval_status === 'contested' && (
            <p className="text-xs text-orange-600 text-right">Contestação enviada — aguardando análise</p>
          )}
        </div>
      )}

      {showContest && (
        <ContestModal
          result={result}
          agentId={agentId}
          onClose={() => setShowContest(false)}
          onContested={() => { setShowContest(false); onContested() }}
        />
      )}
    </div>
  )
}

// ── MyEvaluationsPage ─────────────────────────────────────────────────────────

export default function MyEvaluationsPage() {
  const { session } = useAuth()
  const agentId = session?.name ?? 'agent_unknown'
  const [filterStatus, setFilterStatus] = useState('all')

  // In a real implementation, we'd filter by agent_type_id / pool_id linked to the current user.
  // For now, show all results (supervisor view) since agent identity comes from JWT.
  const { results, loading, reload } = useResults(TENANT)

  const filtered = results.filter(r =>
    filterStatus === 'all' ? true : r.eval_status === filterStatus
  )

  const avgScore = filtered.length > 0
    ? filtered.reduce((s, r) => s + r.overall_score, 0) / filtered.length
    : null

  return (
    <div className="flex flex-col h-full">
      {/* Summary bar */}
      <div className="p-4 border-b bg-gray-50 flex items-center gap-6">
        <div className="text-center">
          <div className="text-2xl font-bold text-primary">{filtered.length}</div>
          <div className="text-xs text-gray-500">Avaliações</div>
        </div>
        {avgScore !== null && (
          <div className="text-center">
            <div className="text-2xl font-bold text-gray-700">{avgScore.toFixed(1)}</div>
            <div className="text-xs text-gray-500">Nota média</div>
          </div>
        )}
        <div className="flex-1" />
        <select
          className="border border-gray-300 rounded px-3 py-1.5 text-sm"
          value={filterStatus}
          onChange={e => setFilterStatus(e.target.value)}
        >
          <option value="all">Todos os status</option>
          <option value="submitted">Em revisão</option>
          <option value="approved">Aprovados</option>
          <option value="adjusted_approved">Ajustados</option>
          <option value="rejected">Rejeitados</option>
          <option value="contested">Contestados</option>
        </select>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {loading && <p className="text-center text-gray-400 py-8">Carregando…</p>}

        {!loading && filtered.length === 0 && (
          <div className="text-center text-gray-400 py-12">
            <div className="text-3xl mb-2">📭</div>
            <p>Nenhuma avaliação encontrada</p>
            {filterStatus !== 'all' && (
              <button
                onClick={() => setFilterStatus('all')}
                className="text-xs text-primary hover:underline mt-1"
              >
                Ver todas
              </button>
            )}
          </div>
        )}

        {filtered.map(r => (
          <ResultCard
            key={r.result_id}
            result={r}
            agentId={agentId}
            onContested={reload}
          />
        ))}
      </div>
    </div>
  )
}
