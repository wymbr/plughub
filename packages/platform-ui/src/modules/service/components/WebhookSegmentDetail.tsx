/**
 * WebhookSegmentDetail
 * Rich detail view for webhook_exec trace nodes.
 *
 * Fetches GET /sessions/{id}/pipeline-state to get:
 *   - pipeline_state.transitions[] — the step-level timeline
 *   - context{}                    — seeded ContextStore entries (input data)
 *
 * Renders:
 *   1. Context strip — input data seeded at trigger (numero_atual, etc.)
 *   2. Step timeline — each transition with step type, reason, timestamp
 *   3. Outcome banner — final result of this execution window
 */
import React, { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { apiFetch } from '@/api/apiFetch'
import { ArrowLeft, Loader2 } from 'lucide-react'
import type { TraceNode } from './WorkflowTraceList'

// ── Types ─────────────────────────────────────────────────────────────────────

interface StepTransition {
  from_step:  string
  to_step:    string
  reason:     string
  timestamp:  string
}

interface PipelineState {
  flow_id:         string
  current_step_id: string
  status:          string
  started_at:      string
  updated_at:      string
  transitions:     StepTransition[]
}

interface ContextEntry {
  value:      unknown
  confidence: number | null
  source:     string | null
  visibility: string | null
  updated_at: string | null
}

interface StepIO {
  decision?:         string
  payload?:          unknown
  child_session_id?: string
  resumed_by?:       string
}

interface PipelineStateResponse {
  pipeline_state: PipelineState | null
  context:        Record<string, ContextEntry>
  step_io?:       Record<string, StepIO>
}

interface Props {
  tenantId:  string
  node:      TraceNode
  onBack:    () => void
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtTime(iso: string | null): string {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' }) }
  catch { return iso }
}

function fmtValue(val: unknown): string {
  if (val === null || val === undefined) return '—'
  if (typeof val === 'string') return val
  return JSON.stringify(val)
}

/** `LocalizedText` do DialogForm: string pura ou `{locale: texto}`. Sem tradução
 *  disponível, devolve o fallback (o id) — nunca a primeira chave do objeto. */
function locStr(v: unknown, fallback: string): string {
  if (typeof v === 'string' && v) return v
  if (v && typeof v === 'object') {
    const o = v as Record<string, unknown>
    const pick = o['pt-BR'] ?? o['en'] ?? Object.values(o)[0]
    if (typeof pick === 'string' && pick) return pick
  }
  return fallback
}

// ── Reason label ──────────────────────────────────────────────────────────────

const REASON_STYLE: Record<string, { label: string; cls: string }> = {
  on_success:  { label: 'success',  cls: 'bg-green-50 text-green-800 border-green-200' },
  on_failure:  { label: 'failure',  cls: 'bg-red-50 text-red-800 border-red-200' },
  suspended:   { label: 'suspend',  cls: 'bg-amber-50 text-amber-800 border-amber-200' },
  resumed:     { label: 'resumed',  cls: 'bg-blue-50 text-blue-800 border-blue-200' },
  on_timeout:  { label: 'timeout',  cls: 'bg-purple-50 text-purple-800 border-purple-200' },
}

function ReasonBadge({ reason }: { reason: string }) {
  const cfg = REASON_STYLE[reason] ?? { label: reason, cls: 'bg-surface-muted text-muted border-border' }
  return (
    <span className={`text-xs px-1.5 py-0.5 rounded border font-medium ${cfg.cls}`}>
      {cfg.label}
    </span>
  )
}

// ── Step row ─────────────────────────────────────────────────────────────────

// ── S4: respostas do formulário, não um blob ─────────────────────────────────
//
// O `payload` do resume carrega `answers` — as respostas que o operador digitou.
// Renderizá-las como JSON esconde justamente o que se foi ver.
//
// ⚠️ O formulário é MUTÁVEL e a resposta é HISTÓRICA. Por isso a iteração é pelas
// CHAVES DA RESPOSTA, com o form servindo só de DICIONÁRIO DE RÓTULOS: chave que o
// formulário de hoje não conhece aparece crua (nunca com rótulo inventado), e
// pergunta que existe hoje mas não foi respondida não aparece (não houve resposta).
// A alternativa seria snapshotar o form na gravação, como o link de survey faz.

function answersOf(payload: unknown): Record<string, unknown> | null {
  if (!payload || typeof payload !== 'object') return null
  const a = (payload as Record<string, unknown>)['answers']
  if (!a || typeof a !== 'object' || Array.isArray(a)) return null
  return a as Record<string, unknown>
}

function AnswersBlock({ answers, labels }: {
  answers: Record<string, unknown>
  labels:  Record<string, string>
}) {
  const { t } = useTranslation('contacts')
  const entries = Object.entries(answers)
  if (entries.length === 0) return null
  return (
    <div className="mt-1 rounded border border-border bg-white px-2 py-1.5">
      <div className="text-xs text-muted-light mb-1">{t('trace.answers')}</div>
      <div className="space-y-1">
        {entries.map(([key, val]) => (
          <div key={key} className="text-xs">
            <div className={labels[key] ? 'text-muted' : 'text-muted font-mono'}>
              {labels[key] ?? key}
            </div>
            <div className="text-dark break-words">{fmtValue(val)}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function StepRow({ t: tr, io, isLast, labels }: {
  t: StepTransition; io?: StepIO; isLast: boolean; labels: Record<string, string>
}) {
  const { t } = useTranslation('contacts')
  const answers = answersOf(io?.payload)
  const hasIo = io && (io.decision || (io.payload !== undefined && io.payload !== null) || io.child_session_id || io.resumed_by)
  return (
    <div className="relative flex items-start gap-3 pb-4">
      {/* Vertical connector */}
      {!isLast && (
        <div className="absolute left-[7px] top-5 bottom-0 w-px bg-border" />
      )}
      {/* Dot */}
      <div className="w-3.5 h-3.5 rounded-full bg-surface-muted border-2 border-border flex-shrink-0 mt-1 z-10" />
      {/* Content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap mb-0.5">
          <span className="text-sm font-medium text-dark font-mono">{tr.from_step}</span>
          <ReasonBadge reason={tr.reason} />
        </div>
        <div className="text-xs text-muted">
          → <span className="font-mono">{tr.to_step}</span>
          <span className="ml-3">{fmtTime(tr.timestamp)}</span>
        </div>
        {/* Fase E.1: resume I/O (decision + payload recebido, child do delegate) */}
        {hasIo && (
          <div className="mt-1.5 rounded border border-border bg-surface-muted px-2 py-1 text-xs space-y-0.5">
            {io!.resumed_by && (
              <div>
                <span className="text-muted">{t('trace.resumedBy')}: </span>
                <span className="font-mono text-dark">{io!.resumed_by}</span>
              </div>
            )}
            {io!.decision && (
              <div>
                <span className="text-muted">{t('trace.resumeDecision')}: </span>
                <span className="font-mono text-dark">{io!.decision}</span>
              </div>
            )}
            {answers && <AnswersBlock answers={answers} labels={labels} />}
            {io!.payload !== undefined && io!.payload !== null && !answers && (
              <div className="break-all">
                <span className="text-muted">{t('trace.resumePayload')}: </span>
                <span className="font-mono text-dark">{fmtValue(io!.payload)}</span>
              </div>
            )}
            {io!.child_session_id && (
              <div>
                <span className="text-muted">{t('trace.childSession')}: </span>
                <span className="font-mono text-dark">…{io!.child_session_id.slice(-12)}</span>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// ── WebhookSegmentDetail (main) ───────────────────────────────────────────────

export function WebhookSegmentDetail({ tenantId, node, onBack }: Props) {
  const { t } = useTranslation('contacts')
  const [data,    setData]    = useState<PipelineStateResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState<string | null>(null)

  const fetch_ = useCallback(async () => {
    if (!tenantId || !node.session_id) return
    setLoading(true)
    try {
      const res  = await apiFetch(
        `/sessions/${encodeURIComponent(node.session_id)}/pipeline-state?tenant_id=${encodeURIComponent(tenantId)}`
      )
      const json = await res.json() as PipelineStateResponse
      setData(json)
      setError(null)
    } catch (err) {
      setError(`${err}`)
    } finally {
      setLoading(false)
    }
  }, [tenantId, node.session_id])

  useEffect(() => { fetch_() }, [fetch_])

  const ps           = data?.pipeline_state
  const ctx          = data?.context ?? {}
  const stepIo       = data?.step_io ?? {}
  const agentLabel   = node.agent_type_id.replace(/_/g, ' ').replace(/\bv\d+$/, '').trim()
  const isActive     = node.ended_at === null

  // S4 — dicionário de rótulos das respostas, do DialogForm que as coletou
  // (`session.dialog_form_id` no ctx desta sessão). É DICIONÁRIO, não fonte: quem
  // manda na lista é a resposta gravada. Form ausente/404 ⇒ mapa vazio ⇒ as chaves
  // aparecem cruas, que é a degradação honesta (nunca um rótulo inventado).
  const [answerLabels, setAnswerLabels] = useState<Record<string, string>>({})
  const formId = typeof ctx['session.dialog_form_id']?.value === 'string'
    ? ctx['session.dialog_form_id'].value as string
    : ''

  useEffect(() => {
    if (!formId) { setAnswerLabels({}); return }
    let cancelled = false
    fetch(`/v1/dialog/forms/${encodeURIComponent(formId)}?status=published`, {
      headers: { 'X-Tenant-ID': tenantId },
    })
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then((doc: { nodes?: Array<Record<string, any>> }) => {
        if (cancelled) return
        const map: Record<string, string> = {}
        for (const n of doc.nodes ?? []) {
          if (Array.isArray(n.fields)) {
            for (const f of n.fields) if (f?.id) map[f.id] = locStr(f.label, f.id)
          }
          if (n.output_key) map[n.output_key] = locStr(n.prompt ?? n.text, n.output_key)
        }
        setAnswerLabels(map)
      })
      .catch(() => { if (!cancelled) setAnswerLabels({}) })
    return () => { cancelled = true }
  }, [formId, tenantId])

  // Filter context to session.* tags seeded by webhook_trigger (most relevant for display)
  const inputTags = Object.entries(ctx)
    .filter(([tag]) => tag.startsWith('session.') && !tag.startsWith('session.copilot'))
    .sort(([a], [b]) => a.localeCompare(b))

  return (
    <div className="flex flex-col h-full overflow-hidden bg-white rounded-xl border border-border">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-border flex-shrink-0 bg-surface-muted sticky top-0 z-10">
        <button
          onClick={onBack}
          className="flex items-center gap-1 text-xs text-muted hover:text-dark border border-border rounded px-2 py-1 bg-white transition-colors"
        >
          <ArrowLeft className="w-3 h-3" aria-hidden="true" />
          {t('trace.back')}
        </button>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-dark">{agentLabel}</p>
          <p className="text-xs text-muted">{t('trace.execWindow')} · {fmtTime(node.started_at)}</p>
        </div>
        {node.outcome && (
          <span className={`text-xs font-semibold px-2 py-0.5 rounded-full flex-shrink-0 ${
            node.outcome === 'resolved'
              ? 'bg-green-light text-green-text'
              : 'bg-surface-alt text-muted'
          }`}>
            {node.outcome}
          </span>
        )}
        {isActive && (
          <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-green-light text-green-text flex-shrink-0 animate-pulse">
            {t('segments.live')}
          </span>
        )}
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-5">

        {loading && !data && (
          <div className="flex items-center justify-center py-10">
            <Loader2 className="w-5 h-5 animate-spin text-muted-light" aria-hidden="true" />
          </div>
        )}

        {error && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
            {error}
          </div>
        )}

        {/* ── Context / Input data ─────────────────────────────────────────── */}
        {inputTags.length > 0 && (
          <section>
            <h3 className="text-xs font-semibold text-muted uppercase tracking-wide mb-2">
              {t('trace.inputContext')}
            </h3>
            <div className="rounded-lg border border-border overflow-hidden">
              {inputTags.map(([tag, entry], idx) => (
                <div
                  key={tag}
                  className={`flex items-start gap-3 px-3 py-2 text-sm ${idx !== 0 ? 'border-t border-border' : ''}`}
                >
                  <span className="font-mono text-xs text-muted flex-shrink-0 pt-0.5 w-40 truncate">{tag}</span>
                  <span className="text-dark flex-1 min-w-0 break-all">
                    {fmtValue(entry.value)}
                  </span>
                  {entry.confidence !== null && entry.confidence < 1 && (
                    <span className="text-xs text-muted-light flex-shrink-0">{Math.round((entry.confidence ?? 0) * 100)}%</span>
                  )}
                </div>
              ))}
            </div>
          </section>
        )}

        {/* ── Step timeline ────────────────────────────────────────────────── */}
        {ps && (
          <section>
            <h3 className="text-xs font-semibold text-muted uppercase tracking-wide mb-3">
              {t('trace.stepTimeline')} · {ps.flow_id}
            </h3>
            <div className="pl-1">
              {ps.transitions && ps.transitions.map((tr, idx) => (
                <StepRow
                  key={`${tr.from_step}-${idx}`}
                  t={tr}
                  io={stepIo[tr.from_step]}
                  isLast={idx === ps.transitions.length - 1}
                  labels={answerLabels}
                />
              ))}
              {/* Current state node — always shown */}
              <div className="flex items-center gap-3">
                <div className={`w-3.5 h-3.5 rounded-full border-2 flex-shrink-0 z-10 ${
                  ps.status === 'completed'
                    ? 'bg-green-400 border-green-600'
                    : ps.status === 'failed'
                    ? 'bg-red-400 border-red-600'
                    : ps.status === 'suspended' || ps.status === 'in_progress'
                    ? 'bg-amber-400 border-amber-600'
                    : 'bg-border border-border-strong'
                }`} />
                <span className="text-sm font-mono font-medium text-dark">{ps.current_step_id}</span>
                <span className={`text-xs px-1.5 py-0.5 rounded ml-1 ${
                  ps.status === 'suspended'
                    ? 'bg-amber-100 text-amber-700'
                    : ps.status === 'completed'
                    ? 'bg-green-100 text-green-700'
                    : 'bg-surface-muted text-muted'
                }`}>{ps.status}</span>
              </div>
            </div>
          </section>
        )}

        {/* ── Empty pipeline state ─────────────────────────────────────────── */}
        {!loading && data && !ps && (
          <div className="rounded-lg border border-border bg-surface-muted px-4 py-6 text-center">
            <p className="text-sm text-muted">{t('trace.pipelineExpired')}</p>
            <p className="text-xs text-muted-light mt-1">{t('trace.pipelineExpiredHint')}</p>
          </div>
        )}

        {/* ── Execution metadata ───────────────────────────────────────────── */}
        <section>
          <h3 className="text-xs font-semibold text-muted uppercase tracking-wide mb-2">
            {t('trace.execMeta')}
          </h3>
          <div className="rounded-lg border border-border overflow-hidden">
            {[
              { label: t('trace.pool'),    value: node.pool_id },
              { label: t('trace.agent'),   value: node.agent_type_id },
              { label: t('trace.started'), value: fmtTime(node.started_at) },
              { label: t('trace.ended'),   value: fmtTime(node.ended_at) },
              { label: t('trace.outcome'), value: node.outcome ?? '—' },
              { label: t('trace.close'),   value: node.close_reason ?? '—' },
            ].map(({ label, value }, idx) => (
              <div key={label} className={`flex items-center gap-3 px-3 py-2 text-sm ${idx !== 0 ? 'border-t border-border' : ''}`}>
                <span className="text-muted w-24 flex-shrink-0">{label}</span>
                <span className="text-dark font-mono text-xs flex-1 min-w-0 break-all">{value}</span>
              </div>
            ))}
          </div>
        </section>

      </div>
    </div>
  )
}
