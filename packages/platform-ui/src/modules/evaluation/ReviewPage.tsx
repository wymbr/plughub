/**
 * ReviewPage.tsx
 * /evaluation/review — Fila de revisão humana
 * Supervisor vê instâncias escaladas pelo reviewer IA e pode aprovar/ajustar/rejeitar.
 */

import React, { useState } from 'react'
import { useResults, reviewResult, useContestations, adjudicateContestation } from '@/api/evaluation-hooks'
import type { EvaluationResult, EvaluationCriterionResponse, EvaluationContestation } from '@/types'

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
    submitted: 'Submetido',
    approved: 'Aprovado',
    adjusted_approved: 'Aprovado c/ ajuste',
    rejected: 'Rejeitado',
    contested: 'Contestado',
  }
  return <span className={`px-2 py-0.5 rounded text-xs font-medium ${styles[status] ?? 'bg-gray-100'}`}>{labels[status] ?? status}</span>
}

// ── CriterionRow ───────────────────────────────────────────────────────────────

function CriterionRow({ cr }: { cr: EvaluationCriterionResponse }) {
  return (
    <div className="border-b last:border-0 py-2 text-sm">
      <div className="flex items-start gap-3">
        <span className="font-mono text-xs text-gray-400 w-32 shrink-0 pt-0.5">{cr.criterion_id}</span>
        <div className="flex-1">
          {cr.na ? (
            <span className="text-gray-400 italic">N/A — {cr.na_reason}</span>
          ) : (
            <>
              <div className="flex items-center gap-2 mb-1">
                <ScorePill score={cr.value ?? 0} />
                {cr.evidence_refs && cr.evidence_refs.length > 0 && (
                  <span className="text-xs text-gray-400">evidências: [{cr.evidence_refs.join(', ')}]</span>
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

// ── ReviewModal ────────────────────────────────────────────────────────────────

interface ReviewModalProps {
  result: EvaluationResult
  adminToken: string
  onClose: () => void
  onReviewed: () => void
}

function ReviewModal({ result, adminToken, onClose, onReviewed }: ReviewModalProps) {
  const [decision, setDecision] = useState<'approved' | 'adjusted_approved' | 'rejected'>('approved')
  const [note, setNote] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const { contestations } = useContestations(TENANT, result.result_id)

  const submit = async () => {
    setSaving(true)
    setError(null)
    try {
      await reviewResult(result.result_id, { decision, round: result.current_round ?? 1, review_note: note }, adminToken)
      onReviewed()
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  const adjudicate = async (c: EvaluationContestation, status: 'upheld' | 'dismissed') => {
    try {
      await adjudicateContestation(c.contestation_id, { status, adjudicator: 'supervisor', note }, adminToken)
      onReviewed()
    } catch (e) {
      setError(String(e))
    }
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-[700px] max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center gap-3 p-4 border-b">
          <div className="flex-1">
            <h2 className="font-semibold text-gray-800">Revisão de Avaliação</h2>
            <p className="text-xs text-gray-400">Sessão: {result.session_id} · Avaliador: {result.evaluator_id}</p>
          </div>
          <ScorePill score={result.overall_score} />
          <EvalStatusBadge status={result.eval_status} />
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 ml-2">✕</button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {/* Overall observation */}
          <div className="bg-gray-50 rounded p-3">
            <div className="text-xs font-semibold text-gray-600 mb-1">Observação geral</div>
            <p className="text-sm text-gray-700">{result.overall_observation}</p>
          </div>

          {/* Highlights / Improvements */}
          <div className="grid grid-cols-2 gap-3">
            {result.highlights?.length > 0 && (
              <div>
                <div className="text-xs font-semibold text-green-700 mb-1">✓ Pontos positivos</div>
                <ul className="text-xs text-gray-600 space-y-0.5">
                  {result.highlights.map((h, i) => <li key={i}>• {h}</li>)}
                </ul>
              </div>
            )}
            {result.improvement_points?.length > 0 && (
              <div>
                <div className="text-xs font-semibold text-orange-700 mb-1">↑ Melhorias</div>
                <ul className="text-xs text-gray-600 space-y-0.5">
                  {result.improvement_points.map((p, i) => <li key={i}>• {p}</li>)}
                </ul>
              </div>
            )}
          </div>

          {result.compliance_flags?.length > 0 && (
            <div>
              <div className="text-xs font-semibold text-red-700 mb-1">⚠ Flags de conformidade</div>
              <div className="flex flex-wrap gap-1">
                {result.compliance_flags.map(f => (
                  <span key={f} className="bg-red-50 text-red-700 text-xs px-2 py-0.5 rounded">{f}</span>
                ))}
              </div>
            </div>
          )}

          {/* Criteria */}
          <div>
            <div className="text-xs font-semibold text-gray-600 mb-2">Critérios avaliados</div>
            <div className="border rounded">
              {result.criterion_responses?.map(cr => (
                <CriterionRow key={cr.criterion_id} cr={cr} />
              ))}
            </div>
          </div>

          {/* Pending contestations */}
          {contestations.filter(c => c.status === 'open').length > 0 && (
            <div className="border border-orange-200 rounded p-3 bg-orange-50">
              <div className="text-xs font-semibold text-orange-700 mb-2">Contestações abertas</div>
              {contestations.filter(c => c.status === 'open').map(c => (
                <div key={c.contestation_id} className="text-sm text-gray-700 space-y-1">
                  <p className="text-xs text-gray-500">De: {c.contested_by}</p>
                  <p>{c.reason}</p>
                  <div className="flex gap-2 mt-1">
                    <button
                      onClick={() => adjudicate(c, 'upheld')}
                      className="text-xs bg-green-100 text-green-700 px-2 py-0.5 rounded hover:bg-green-200"
                    >
                      ✓ Procedente
                    </button>
                    <button
                      onClick={() => adjudicate(c, 'dismissed')}
                      className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded hover:bg-gray-200"
                    >
                      ✕ Improcedente
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Review footer */}
        {!result.locked && (
          <div className="p-4 border-t space-y-3">
            <div className="flex gap-3">
              {(['approved', 'adjusted_approved', 'rejected'] as const).map(d => (
                <label key={d} className="flex items-center gap-1 text-sm cursor-pointer">
                  <input
                    type="radio"
                    value={d}
                    checked={decision === d}
                    onChange={() => setDecision(d)}
                  />
                  {d === 'approved' ? 'Aprovar' : d === 'adjusted_approved' ? 'Aprovar c/ ressalvas' : 'Rejeitar'}
                </label>
              ))}
            </div>
            <textarea
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm resize-none"
              rows={2}
              placeholder="Nota da revisão (obrigatória para ajuste/rejeição)"
              value={note}
              onChange={e => setNote(e.target.value)}
            />
            {error && <div className="text-red-600 text-xs">{error}</div>}
            <div className="flex justify-end gap-2">
              <button onClick={onClose} className="text-sm text-gray-500 hover:text-gray-700 px-4 py-1.5">Cancelar</button>
              <button
                onClick={submit}
                disabled={saving}
                className="bg-primary text-white text-sm px-4 py-1.5 rounded hover:bg-blue-800 disabled:opacity-50"
              >
                {saving ? 'Salvando…' : 'Confirmar decisão'}
              </button>
            </div>
          </div>
        )}
        {result.locked && (
          <div className="p-4 border-t bg-gray-50 text-xs text-gray-400 text-center">
            Esta avaliação está bloqueada — não pode ser modificada
          </div>
        )}
      </div>
    </div>
  )
}

// ── ReviewPage ─────────────────────────────────────────────────────────────────

export default function ReviewPage() {
  const [adminToken, setAdminToken] = useState('')
  const [statusFilter, setStatusFilter] = useState('submitted')
  const { results, loading, reload } = useResults(TENANT, undefined, undefined, 30_000)
  const [selected, setSelected] = useState<EvaluationResult | null>(null)

  const filtered = results.filter(r =>
    statusFilter === 'all' ? true : r.eval_status === statusFilter
  )

  return (
    <div className="flex h-full">
      {/* Sidebar */}
      <aside className="w-80 border-r flex flex-col bg-gray-50">
        <div className="p-3 border-b space-y-2">
          <input
            type="password"
            className="w-full border border-gray-300 rounded px-2 py-1 text-xs"
            placeholder="Admin token"
            value={adminToken}
            onChange={e => setAdminToken(e.target.value)}
          />
          <select
            className="w-full border border-gray-300 rounded px-2 py-1 text-sm"
            value={statusFilter}
            onChange={e => setStatusFilter(e.target.value)}
          >
            <option value="submitted">Aguardando revisão</option>
            <option value="contested">Contestados</option>
            <option value="approved">Aprovados</option>
            <option value="adjusted_approved">Ajustados</option>
            <option value="rejected">Rejeitados</option>
            <option value="all">Todos</option>
          </select>
        </div>

        <div className="flex-1 overflow-y-auto p-2 space-y-1">
          {loading && <p className="text-sm text-gray-400 p-2">Carregando…</p>}
          {filtered.map(r => (
            <button
              key={r.result_id}
              onClick={() => setSelected(r)}
              className={`w-full text-left px-3 py-2 rounded text-sm border transition-colors ${
                selected?.result_id === r.result_id
                  ? 'border-primary bg-blue-50'
                  : 'border-transparent hover:bg-gray-200'
              }`}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="font-medium text-xs text-gray-500 truncate">{r.session_id}</span>
                <ScorePill score={r.overall_score} />
              </div>
              <div className="flex items-center gap-1">
                <EvalStatusBadge status={r.eval_status} />
                {r.locked && <span className="text-xs text-gray-400">🔒</span>}
              </div>
              <div className="text-xs text-gray-400 mt-1 truncate">{r.evaluator_id}</div>
            </button>
          ))}
          {!loading && filtered.length === 0 && (
            <p className="text-xs text-gray-400 text-center py-8">
              {statusFilter === 'submitted' ? 'Nenhuma avaliação aguardando revisão ✓' : 'Nenhum resultado encontrado'}
            </p>
          )}
        </div>
      </aside>

      {/* Main */}
      <div className="flex-1 flex flex-col items-center justify-center text-gray-400">
        {selected ? (
          <ReviewModal
            result={selected}
            adminToken={adminToken}
            onClose={() => setSelected(null)}
            onReviewed={() => { setSelected(null); reload() }}
          />
        ) : (
          <>
            <div className="text-4xl mb-3">📋</div>
            <p className="text-sm">Selecione uma avaliação para revisar</p>
            <p className="text-xs text-gray-300 mt-1">{filtered.length} itens aguardando</p>
          </>
        )}
      </div>
    </div>
  )
}
