/**
 * EvaluationDetailPage — /evaluation/evaluations/:campaignId/:resultId
 *
 * T9-C3 / blueprint D1 — nível 3 do drill-down como ROTA dedicada (tela cheia).
 * Split: ESQUERDA = formulário preenchido (render tipado por critério, reusa
 * CriterionDetail do T9-B, contra a versão fixada do form) · DIREITA = transcript
 * mascarado (T9-C2, delegado ao analytics-api), janelado pelo segmento avaliado.
 *
 * Evidência clicável (C.3): o chip `stream_entry_id` em cada critério rola e
 * destaca a mensagem correspondente no transcript. Mascarado para todos (D3).
 *
 * As AÇÕES (revisar/contestar) seguem no painel inline da AvaliacoesPage (T10);
 * esta rota foca o par formulário+transcript com evidência.
 */
import React, { useState, useEffect, useMemo, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import {
  useResult,
  useResultCriteria,
  useFormVersion,
  useContestationThreads,
  useResultTranscript,
  type TranscriptMessage,
} from '@/api/evaluation-hooks'
import type { EvaluationCriterion, CriterionResponseRow } from '@/types'
import {
  CriterionDetail, ScorePill,
  HumanReviewPanel, DimensionContestPanel13, ReviewPanel, ContestPanel,
} from './AvaliacoesPage'

type TFn = (key: string, opts?: Record<string, unknown>) => string

function fmtTime(iso: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function roleMeta(role: string | null): { icon: string; isCustomer: boolean } {
  const r = (role || '').toLowerCase()
  if (r === 'customer') return { icon: '🧑', isCustomer: true }
  if (r === 'primary' || r === 'specialist') return { icon: '🤖', isCustomer: false }
  if (r === 'human' || r === 'human_agent' || r === 'supervisor') return { icon: '👤', isCustomer: false }
  return { icon: '•', isCustomer: false }
}

// ── Transcript panel ─────────────────────────────────────────────────────────

function MessageBubble({ m, highlighted, t }: { m: TranscriptMessage; highlighted: boolean; t: TFn }) {
  const { icon, isCustomer } = roleMeta(m.author_role)
  return (
    <div
      id={`t9c-msg-${m.stream_entry_id}`}
      className={`flex ${isCustomer ? 'justify-start' : 'justify-end'}`}
    >
      <div
        className={`max-w-[78%] rounded-lg px-3 py-2 text-sm transition-all ${
          highlighted ? 'ring-2 ring-primary bg-primary-light' :
          isCustomer ? 'bg-surface-muted text-dark' : 'bg-primary-light/40 text-dark'
        }`}
      >
        <div className="flex items-center gap-1.5 mb-0.5 text-[11px] text-muted-light">
          <span>{icon}</span>
          <span className="font-medium">{m.author_role || '—'}</span>
          {m.author_id && <span className="font-mono truncate max-w-[120px]">· {m.author_id}</span>}
          <span className="ml-auto font-mono">{fmtTime(m.created_at)}</span>
        </div>
        <p className="leading-relaxed whitespace-pre-wrap break-words">{m.content || <span className="text-muted-light italic">—</span>}</p>
        <div className="text-[10px] font-mono text-border-strong mt-1">{m.stream_entry_id}</div>
      </div>
    </div>
  )
}

function TranscriptPanel({
  resultId, tenantId, jwtToken, scope, setScope, highlightedId, t,
}: {
  resultId: string
  tenantId: string
  jwtToken: string
  scope: 'segment' | 'contact'
  setScope: (s: 'segment' | 'contact') => void
  highlightedId: string | null
  t: TFn
}) {
  const { data, loading, error } = useResultTranscript(resultId, tenantId, scope, jwtToken)
  const messages = data?.messages ?? []

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex items-center gap-2 px-3 py-2 border-b bg-surface-muted">
        <span className="text-sm font-semibold text-dark">{t('transcript.title', { defaultValue: 'Transcript' })}</span>
        <span className="text-[11px] px-1.5 py-0.5 rounded bg-warning-light text-warning-text" title={t('transcript.maskedHint', { defaultValue: 'Conteúdo mascarado (revisão cega)' })}>
          🔒 {t('transcript.masked', { defaultValue: 'mascarado' })}
        </span>
        <div className="ml-auto flex rounded border border-border-strong overflow-hidden text-xs">
          {(['segment', 'contact'] as const).map(s => (
            <button
              key={s}
              onClick={() => setScope(s)}
              className={`px-2 py-1 transition-colors ${scope === s ? 'bg-primary text-white' : 'bg-white text-muted hover:bg-surface-muted'}`}
            >
              {t(`transcript.scope.${s}`, { defaultValue: s === 'segment' ? 'Segmento' : 'Contato' })}
            </button>
          ))}
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {loading && <div className="text-xs text-muted-light text-center py-8">⟳ {t('transcript.loading', { defaultValue: 'Carregando…' })}</div>}
        {error && <div className="text-xs text-red-text bg-red-light border border-red/20 rounded p-2">{t('transcript.error', { defaultValue: 'Falha ao carregar o transcript' })}: {error}</div>}
        {!loading && !error && messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-40 text-muted-light">
            <span className="text-2xl mb-1">💬</span>
            <p className="text-sm">{t('transcript.empty', { defaultValue: 'Sem mensagens nesta janela' })}</p>
          </div>
        )}
        {messages.map(m => (
          <MessageBubble key={m.stream_entry_id} m={m} highlighted={highlightedId === m.stream_entry_id} t={t} />
        ))}
      </div>
    </div>
  )
}

// ── Page ───────────────────────────────────────────────────────────────────────

export default function EvaluationDetailPage() {
  const { t } = useTranslation('evaluation')
  const { campaignId = '', resultId = '' } = useParams()
  const navigate = useNavigate()
  const { session, getAccessToken, tenantId: TENANT, currentUser } = useAuth()

  const [jwtToken, setJwtToken] = useState('')
  const [scope, setScope] = useState<'segment' | 'contact'>('segment')
  const [highlightedId, setHighlightedId] = useState<string | null>(null)
  const [mode, setMode] = useState<'view' | 'review' | 'contest'>('view')

  useEffect(() => {
    getAccessToken().then(tok => setJwtToken(tok ?? '')).catch(() => {})
  }, [getAccessToken, session])

  const { result, loading: resultLoading, error: resultError, reload: reloadResult } = useResult(resultId, TENANT, jwtToken)
  const { criteria: responses } = useResultCriteria(resultId, TENANT)
  const { form: pinnedForm } = useFormVersion(result?.form_id ?? null, result?.form_version, TENANT)
  const { data: threadData, reload: reloadThreads } = useContestationThreads(result?.instance_id ?? null, jwtToken, 0)
  const threads = threadData?.threads ?? []

  // T10-D — ações dirigidas por available_actions (server-side, ABAC + posse)
  const userId       = currentUser?.userId ?? ''
  const canReview    = result?.available_actions?.includes('review') ?? false
  const canContest   = result?.available_actions?.includes('contest') ?? false
  const isArc13      = threads.length > 0
  const currentRound = threadData?.current_round ?? result?.current_round ?? 1
  const handleActionDone = useCallback(() => {
    setMode('view'); reloadResult(); reloadThreads()
  }, [reloadResult, reloadThreads])

  // T9-B — join form (versão fixada) ∪ respostas, por criterion_id
  const respByCrit = useMemo(
    () => new Map((responses ?? []).map(r => [r.criterion_id, r])),
    [responses],
  )
  const threadByCrit = useMemo(
    () => new Map(threads.map(th => [th.dimension_id, th])),
    [threads],
  )
  const mergedCriteria = useMemo(() => {
    const out: { def: EvaluationCriterion; resp?: CriterionResponseRow }[] = []
    const dims = Array.isArray(pinnedForm?.dimensions) ? pinnedForm!.dimensions : []
    for (const dim of dims) for (const def of (dim.criteria ?? [])) {
      out.push({ def, resp: respByCrit.get(def.criterion_id) })
    }
    return out
  }, [pinnedForm, respByCrit])

  // Evidência → rola/destaca a mensagem no transcript
  const onEvidenceClick = useCallback((sid: string) => {
    setHighlightedId(sid)
    requestAnimationFrame(() => {
      document.getElementById(`t9c-msg-${sid}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    })
  }, [])

  const back = () => navigate(`/evaluation/evaluations?campaign=${encodeURIComponent(campaignId)}`)

  return (
    <div className="flex flex-col h-full">
      {/* Breadcrumb / header */}
      <div className="border-b bg-white px-4 py-2 flex items-center gap-2 flex-wrap">
        <button onClick={back} className="text-sm text-primary hover:underline">← {t('evalDetail.back', { defaultValue: 'Avaliações' })}</button>
        <span className="text-sm text-muted-light">/</span>
        <span className="text-sm font-medium text-dark truncate max-w-[260px]">
          {t('evalDetail.title', { defaultValue: 'Avaliação' })}: <code className="font-mono">{resultId}</code>
        </span>
        {result && (
          <>
            <span className="ml-2"><ScorePill score={result.overall_score} /></span>
            <span className="text-xs text-muted-light truncate">
              {t('detail.session', { defaultValue: 'Sessão' })}: <code className="font-mono">{result.session_id}</code>
              {result.segment_id && <> · seg <code className="font-mono">{result.segment_id}</code></>}
            </span>
          </>
        )}
        {/* T10-D — ações por available_actions (server-side); read-only quando vazio */}
        {result && !result.locked && (canReview || canContest) && (
          <div className="ml-auto flex gap-2">
            {canReview && (
              <button
                onClick={() => setMode(m => m === 'review' ? 'view' : 'review')}
                className={`text-xs px-3 py-1 rounded font-medium border transition-colors ${
                  mode === 'review' ? 'bg-primary text-white border-primary'
                                    : 'bg-white text-primary border-primary hover:bg-primary-light'}`}
              >✓ {t('detail.review', { defaultValue: 'Revisar' })}</button>
            )}
            {canContest && (
              <button
                onClick={() => setMode(m => m === 'contest' ? 'view' : 'contest')}
                className={`text-xs px-3 py-1 rounded font-medium border transition-colors ${
                  mode === 'contest' ? 'bg-contested text-white border-contested'
                                     : 'bg-white text-contested border-contested hover:bg-contested-light'}`}
              >⚑ {t('detail.contest', { defaultValue: 'Contestar' })}</button>
            )}
          </div>
        )}
      </div>

      {resultError && (
        <div className="m-4 text-sm text-red-text bg-red-light border border-red/20 rounded p-3">
          {t('evalDetail.loadError', { defaultValue: 'Falha ao carregar a avaliação' })}: {resultError}
        </div>
      )}

      {/* Split: critérios | transcript */}
      <div className="flex-1 flex min-h-0">
        {/* Esquerda — formulário preenchido */}
        <div className="w-[44%] min-w-[360px] border-r flex flex-col bg-white">
          <div className="px-3 py-2 border-b bg-surface-muted text-sm font-semibold text-dark">
            {t('detail.evaluatedCriteria', { defaultValue: 'Critérios avaliados' })}
            {result?.form_version != null && (
              <span className="ml-1 font-normal text-muted-light">· form v{result.form_version}</span>
            )}
          </div>
          <div className="flex-1 overflow-y-auto p-3">
            {/* T10-D — painel de ação ativo (revisar/contestar), dirigido por available_actions */}
            {result && mode === 'review' && (
              isArc13 && result.instance_id ? (
                <HumanReviewPanel
                  threads={threads} instanceId={result.instance_id}
                  jwtToken={jwtToken} userId={userId}
                  onDone={handleActionDone} onCancel={() => setMode('view')}
                />
              ) : (
                <ReviewPanel
                  result={{ ...result, criterion_responses: responses } as any}
                  jwtToken={jwtToken} adminToken="" onDone={handleActionDone}
                />
              )
            )}
            {result && mode === 'contest' && (
              isArc13 && result.instance_id ? (
                <DimensionContestPanel13
                  threads={threads} instanceId={result.instance_id}
                  currentRound={currentRound} jwtToken={jwtToken}
                  onDone={handleActionDone} onCancel={() => setMode('view')}
                />
              ) : (
                <ContestPanel
                  result={{ ...result, criterion_responses: responses } as any}
                  userId={userId} jwtToken={jwtToken}
                  onDone={handleActionDone} onCancel={() => setMode('view')}
                />
              )
            )}
            {resultLoading && <div className="text-xs text-muted-light text-center py-8">⟳ {t('transcript.loading', { defaultValue: 'Carregando…' })}</div>}
            {!resultLoading && mergedCriteria.length > 0 ? (
              <div className="border rounded">
                {mergedCriteria.map(({ def, resp }) => (
                  <CriterionDetail
                    key={def.criterion_id}
                    def={def} resp={resp}
                    thread={threadByCrit.get(def.criterion_id)}
                    t={t} onEvidenceClick={onEvidenceClick}
                  />
                ))}
              </div>
            ) : !resultLoading && (responses?.length ?? 0) > 0 ? (
              <div className="border rounded">
                {responses!.map(r => (
                  <CriterionDetail
                    key={r.criterion_id}
                    def={{ criterion_id: r.criterion_id, label: r.criterion_name || r.criterion_id, description: '', weight: 0, allows_na: false, max_score: r.max_score ?? 10 } as EvaluationCriterion}
                    resp={r} thread={threadByCrit.get(r.criterion_id)}
                    t={t} onEvidenceClick={onEvidenceClick}
                  />
                ))}
              </div>
            ) : !resultLoading && (
              <div className="text-xs text-muted-light italic px-1">{t('detail.noCriteria', { defaultValue: 'Sem critérios.' })}</div>
            )}
            <p className="text-[11px] text-muted-light mt-3">
              ↳ {t('evalDetail.evidenceHint', { defaultValue: 'Clique no id de evidência para destacar a mensagem no transcript.' })}
            </p>
          </div>
        </div>

        {/* Direita — transcript */}
        <div className="flex-1 min-w-0 bg-white">
          {resultId && (
            <TranscriptPanel
              resultId={resultId}
              tenantId={TENANT}
              jwtToken={jwtToken}
              scope={scope}
              setScope={setScope}
              highlightedId={highlightedId}
              t={t}
            />
          )}
        </div>
      </div>
    </div>
  )
}
