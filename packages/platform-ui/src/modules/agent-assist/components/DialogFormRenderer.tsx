/**
 * DialogFormRenderer — generic collect-form surface in the Console (R0).
 *
 * The FOURTH surface of the dialog primitive (chat-runner · web page · inline
 * hook · Console inbox). When a claimed pull item is a suspended workflow that
 * carries a `dialog_form_id` + a resume token in its child-session ContextStore
 * (written with `session.*` prefix by delegate/collect), this renders:
 *
 *   1. a read-only BRIEFING panel — the context the agent reads to fill the form:
 *      an optional title/summary + the transcript of a REFERENCED session
 *      (session.briefing_session_id) fetched via /api/conversation_history/{id}
 *      (the same endpoint the pull preview already uses). For wrap-up this is the
 *      origin session; for approval it is the package summary.
 *   2. the DialogForm ITSELF — statements (read-only) + questions rendered per
 *      `interaction` (text→input, button/list→buttons, form→editable fields).
 *      The walk mirrors channel-gateway `survey_web.py` render() so the same
 *      published form renders identically across surfaces (incl. ask_when skip).
 *
 * Submit posts the captured answers to the resume ingress
 * (POST /v1/channels/webhook/resume/{token}) — the workflow reads the payload and
 * branches (routing lives in the workflow, NEVER in the form JSON). Default
 * payload is `{ source:"operator", answers:{ output_key → value } }`; the workflow
 * reads `$.pipeline_state.<step>.answers.<output_key>`.
 *
 * This core is APPROVAL-AGNOSTIC on purpose (kickoff docs/product/approval-renderer-
 * kickoff.md, R0): it renders an ARBITRARY DialogForm by form_id and knows nothing
 * about decisions[]/edits/ABAC. Approval (ApprovalPanel) stacks those affordances
 * on top via `renderActions`, and wrap-up-α (E2) consumes the same core unchanged.
 *
 * ABAC: none here. Claiming is already gated at the inbox; per-consumer gates
 * (approvals.decide, wrap-up ABAC) live in the wrapper.
 */
import React, { useEffect, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import { CheckCircle2 } from "lucide-react"
import { useAuth } from "../../../auth/useAuth"
import { parseResumeConflict, resumeConflictDetails } from "../../../lib/resume-conflict"
import type { ChatMessage } from "../types"

export type Snapshot = Record<string, { value?: unknown } | undefined> | null | undefined

// ── Snapshot helpers (child-session ContextStore, session.* keys) ─────────────

export function snapVal(snap: Snapshot, key: string): string | undefined {
  const v = snap?.[key]?.value
  return typeof v === "string" && v.trim() ? v : undefined
}

// The delegate-conference path writes session.delegate_resume_token; the plain
// webhook delegate writes session.workflow_resume_token. collect (wrap-up-α, E2)
// will add its own key here — resolve generically so the core covers every path.
export function resumeTokenOf(snap: Snapshot): string | undefined {
  return (
    snapVal(snap, "session.delegate_resume_token") ??
    snapVal(snap, "session.workflow_resume_token") ??
    snapVal(snap, "session.collect_resume_token")
  )
}

/** True when a claimed contact is a FORM-FILL task (renders in this surface). */
export function isFormFillSnapshot(snap: Snapshot): boolean {
  return !!snapVal(snap, "session.dialog_form_id") && !!resumeTokenOf(snap)
}

/** Resolve a LocalizedText (string | { locale: text }) to a plain string. */
export function locStr(x: unknown, fallback = ""): string {
  if (typeof x === "string") return x
  if (x && typeof x === "object") {
    const m = x as Record<string, string>
    return m["pt-BR"] ?? m["en"] ?? Object.values(m)[0] ?? fallback
  }
  return fallback
}

// ── ask_when — declarative skip-logic (mirror of @plughub/schemas evaluateAskWhen,
//    adr-dialog-conditional-skip-logic; kept inline like survey_web.py) ─────────

interface AskWhen { field: string; op: string; value: unknown }
function awNum(x: unknown): number { return typeof x === "number" ? x : Number(x) }
function awEq(a: unknown, b: unknown): boolean {
  const na = awNum(a), nb = awNum(b)
  if (Number.isFinite(na) && Number.isFinite(nb)) return na === nb
  return String(a) === String(b)
}
function evalAskWhen(g: AskWhen | undefined, answers: Record<string, string>): boolean {
  if (!g) return true
  const a = answers[g.field]
  if (a === undefined || a === null || a === "") return false
  switch (g.op) {
    case "lt":  return awNum(a) <  awNum(g.value)
    case "lte": return awNum(a) <= awNum(g.value)
    case "gt":  return awNum(a) >  awNum(g.value)
    case "gte": return awNum(a) >= awNum(g.value)
    case "eq":  return awEq(a, g.value)
    case "ne":  return !awEq(a, g.value)
    case "in":  return Array.isArray(g.value) && g.value.some(v => awEq(a, v))
    default:    return false
  }
}

// ── DialogForm shapes (loose — the published JSON from dialog-api) ────────────

interface DialogOption { id: string; label: unknown; value?: string }
export interface DialogFormField {
  id: string
  label: unknown
  type: string
  required?: boolean
  value?: string | number | boolean
  options?: DialogOption[]
}
interface DialogNode {
  id: string
  kind: "statement" | "question"
  text?: unknown
  prompt?: unknown
  interaction?: string
  options?: DialogOption[]
  fields?: DialogFormField[]
  output_key?: string
  ask_when?: AskWhen
}
interface DialogFormDoc {
  name?: string
  default_locale?: string
  nodes?: DialogNode[]
}

// ── Render-prop state exposed to a consumer overlay (approval etc.) ───────────

export interface DialogFormActionsState {
  /** Scalar question answers, keyed by output_key. The generic submit payload. */
  answers:     Record<string, string>
  /** Editable values of `interaction:"form"` fields, keyed by field id. */
  fieldValues: Record<string, string>
  /** Initial fieldValues (pre-fill) — the immutable "before" of an edit audit. */
  baseline:    Record<string, string>
  /** Flattened `interaction:"form"` fields across the form (for edit/audit). */
  formFields:  DialogFormField[]
  /** busyKey of the in-flight submit (null = idle). */
  busy:        string | null
  /**
   * True after a 409 `terminal`: someone else closed this task and no submit can
   * ever succeed. An overlay MUST disable its own action buttons on this — the
   * core can only disable the default bar it owns.
   */
  closedElsewhere: boolean
  /** Submit a fully-formed resume payload. busyKey drives a per-button spinner. */
  submit:      (payload: Record<string, unknown>, busyKey?: string) => Promise<void>
}

interface DialogFormRendererProps {
  tenantId: string
  snapshot: Snapshot
  /** Pool of the claimed task — claimant binding for the resume ingress (A5). */
  poolId?:   string
  /** Instance of this Console (`human-{userId}`) = the claimant, verified server-side. */
  instanceId?: string
  /** Disable all inputs (e.g. approval view-only when lacking approvals.decide). */
  inputsDisabled?: boolean
  /** Called after a successful resume so the parent can drop the resolved contact. */
  onResolved?: () => void
  /**
   * Called when the agent acknowledges a task that was closed by someone else
   * (409 `terminal`). The parent must only DROP THE CARD — never re-queue.
   *
   * "Return to queue" (`work_queue/release`) é a saída errada aqui: ela devolve
   * o item à fila reservado ao dono anterior, e depois de um `terminal` o
   * trabalho não existe mais — outro agente reivindicaria trabalho MORTO e
   * levaria o mesmo 409. Não é confusão de rótulo, é reciclagem de item morto.
   *
   * MEDIDO (2026-08-04), não suposto: num encerramento REAL por supervisor
   * (resume 200 com o formulário aberto), o SET de sessões do agente vai de
   * `{sessão reivindicada}` a VAZIO e **o Console derruba o cartão sozinho**.
   * Logo (a) derrubar localmente é seguro — a vaga já foi devolvida pelo
   * árbitro, e este botão não deve devolvê-la de novo; (b) este botão é
   * RECUPERAÇÃO RARA, para quando a notícia não chegar, não o caminho normal.
   * A primeira redação afirmava (a) sem ter medido; a medição veio depois e
   * confirmou, mas a afirmação era um palpite com cara de fato.
   */
  onDismiss?: () => void
  /**
   * Replace the default submit bar. Default = a single "Submit" that posts
   * `{ source:"operator", answers }`. Approval passes decisions[] buttons here.
   */
  renderActions?: (state: DialogFormActionsState) => React.ReactNode
}

export const DialogFormRenderer: React.FC<DialogFormRendererProps> = ({
  tenantId, snapshot, poolId, instanceId, inputsDisabled, onResolved, onDismiss, renderActions,
}) => {
  const { t } = useTranslation("agentAssist")
  const { getAccessToken } = useAuth()

  const formId            = snapVal(snapshot, "session.dialog_form_id")
  const resumeToken       = resumeTokenOf(snapshot)
  const briefingSessionId = snapVal(snapshot, "session.briefing_session_id")
  const title             = snapVal(snapshot, "session.title")
  const summary           = snapVal(snapshot, "session.summary")

  const [form,        setForm]        = useState<DialogFormDoc | null>(null)
  const [answers,     setAnswers]     = useState<Record<string, string>>({})
  const [fieldValues, setFieldValues] = useState<Record<string, string>>({})
  const [baseline,    setBaseline]    = useState<Record<string, string>>({})
  const [briefing,    setBriefing]    = useState<ChatMessage[]>([])
  const [busy,        setBusy]        = useState<string | null>(null)
  const [done,        setDone]        = useState(false)
  const [error,       setError]       = useState<string | null>(null)
  /** 409 `terminal`: a tarefa acabou nas mãos de outro — reenviar é impossível. */
  const [closedElsewhere, setClosedElsewhere] = useState(false)

  // Defesa em profundidade: o estado é POR TAREFA (resume_token). O call site do
  // Console monta com `key={sessionId}` (remonta ao trocar de contato), mas um
  // consumidor que reuse a instância veria o "Submitted" e as respostas da tarefa
  // ANTERIOR grudados — e, quando as duas tarefas usam o mesmo form_id, nem o fetch
  // abaixo re-roda para limpar. Declarado ANTES do fetch: quando os dois disparam no
  // mesmo render, o fetch é quem dá a palavra final sobre fieldValues/baseline.
  useEffect(() => {
    setDone(false)
    setBusy(null)
    setError(null)
    setAnswers({})
    setClosedElsewhere(false)
  }, [resumeToken])

  // Fetch the published DialogForm (same endpoint/shape the web vehicle consumes).
  useEffect(() => {
    if (!formId) { setForm(null); return }
    let cancelled = false
    fetch(`/v1/dialog/forms/${encodeURIComponent(formId)}?status=published`, {
      headers: { "X-Tenant-ID": tenantId },
    })
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then((doc: DialogFormDoc) => {
        if (cancelled) return
        setForm(doc)
        // Pre-fill editable form-question fields from each field's `value`.
        const init: Record<string, string> = {}
        for (const n of doc.nodes ?? []) {
          if (n.kind === "question" && Array.isArray(n.fields)) {
            for (const f of n.fields) if (f.value !== undefined) init[f.id] = String(f.value)
          }
        }
        setFieldValues(init)
        setBaseline(init)   // immutable "before" of the edit audit
      })
      .catch(() => { if (!cancelled) setForm(null) })
    return () => { cancelled = true }
  }, [formId, tenantId])

  // Fetch the briefing transcript of the REFERENCED session, if any.
  useEffect(() => {
    if (!briefingSessionId) { setBriefing([]); return }
    let alive = true
    fetch(`/api/conversation_history/${encodeURIComponent(briefingSessionId)}`)
      .then(r => r.ok ? r.json() : { messages: [] })
      .then((data: { messages?: ChatMessage[] }) => { if (alive) setBriefing(data.messages ?? []) })
      .catch(() => { /* transient — briefing may be momentarily empty */ })
    return () => { alive = false }
  }, [briefingSessionId])

  // Flattened editable fields (across all form-questions) — for overlays/audit.
  const formFields = useMemo<DialogFormField[]>(() => {
    const out: DialogFormField[] = []
    for (const n of form?.nodes ?? []) {
      if (n.kind === "question" && Array.isArray(n.fields)) {
        for (const f of n.fields) out.push({ ...f, label: locStr(f.label, f.id) })
      }
    }
    return out
  }, [form])

  const submit = async (payload: Record<string, unknown>, busyKey = "submit") => {
    if (!resumeToken) { setError("no resume token"); return }
    setBusy(busyKey); setError(null)
    try {
      // JWT of the claimant → `possessed` attribution, verified server-side at the
      // ingress (signature + caller==claimant). Absent → `claimed`.
      const token = await getAccessToken()
      const res = await fetch(`/v1/channels/webhook/resume/${encodeURIComponent(resumeToken)}`, {
        method:  "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          tenant_id:   tenantId,
          pool_id:     poolId,       // claimant binding (tenant,pool,session→instance)
          instance_id: instanceId,
          payload,
        }),
      })
      if (!res.ok) {
        // O MOTIVO da recusa vem no corpo (`detail`) e é acionável: 403 por item
        // devolvido à fila ("reivindique antes de submeter") pede uma ação do
        // operador; 403 por ABAC não pede nenhuma. Exibir só o status colapsa os
        // dois no mesmo "HTTP 403" e transforma uma instrução em enigma — é
        // degradação silenciosa do motivo, na superfície onde ela custa mais.
        // Introduzido junto com o gate de posse da Fase A, que é quem passou a
        // produzir 403 com motivo útil.
        let body: unknown = null
        try {
          body = await res.json()
        } catch { /* corpo não-JSON: sobra o status, que é melhor que nada */ }

        // F2 — o 409 da Fase F carrega o `detail` como OBJETO, e o ramo acima só
        // aceita string: sem esta leitura o agente cujo item o supervisor acabou
        // de encerrar vê "HTTP 409" e mais nada, sem saber que o que digitou não
        // foi salvo. É a promessa da fase, e ela se cumpre aqui.
        const conflict = parseResumeConflict(body)
        if (conflict) {
          const head = conflict.state === "terminal"
            ? t("formFill.conflict.terminal", {
                defaultValue: "This task was already closed by someone else. Your answers were NOT saved." })
            : t("formFill.conflict.inFlight", {
                defaultValue: "Another close of this task is already under way. Your answers were NOT saved." })
          const facts = resumeConflictDetails(conflict, t)
          setError(facts ? `${head} ${facts}` : head)
          // `terminal` é irreversível: reenviar só produziria o mesmo 409. Desliga
          // o envio para não convidar a uma tentativa que não pode dar certo.
          // `in_flight` NÃO desliga — o outro encerramento ainda pode falhar e
          // soltar o lock, e aí o reenvio é legítimo.
          if (conflict.state === "terminal") setClosedElsewhere(true)
          setBusy(null)
          return
        }

        const detail = typeof (body as { detail?: unknown } | null)?.detail === "string"
          ? (body as { detail: string }).detail
          : ""
        setError(detail ? `HTTP ${res.status} — ${detail}` : `HTTP ${res.status}`)
        setBusy(null)
        return
      }
      setDone(true)
      onResolved?.()
    } catch (e) {
      setError(String(e)); setBusy(null)
    }
  }

  const actionsState: DialogFormActionsState = {
    answers, fieldValues, baseline, formFields, busy, closedElsewhere, submit,
  }

  if (done) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-2 text-green-text p-6 text-center">
        <CheckCircle2 className="w-8 h-8" aria-hidden="true" />
        <span className="text-sm">
          {t("formFill.resolved", { defaultValue: "Submitted. The workflow will continue." })}
        </span>
      </div>
    )
  }

  const locale = form?.default_locale
  const disabled = !!inputsDisabled

  // Field editor (interaction:"form" fields) — bool/select/date/money/number/text.
  const renderField = (f: DialogFormField) => (
    <div key={f.id} className="flex flex-col gap-1">
      <label className="text-xs text-muted">{locStr(f.label, f.id)}{f.required ? " *" : ""}</label>
      {f.type === "bool" ? (
        <label className="inline-flex items-center gap-2 text-sm text-dark">
          <input
            type="checkbox"
            checked={fieldValues[f.id] === "true"}
            disabled={disabled}
            onChange={e => setFieldValues(p => ({ ...p, [f.id]: e.target.checked ? "true" : "false" }))}
          />
          {t("formFill.yes", { defaultValue: "Yes" })}
        </label>
      ) : f.type === "select" ? (
        <select
          value={fieldValues[f.id] ?? ""}
          disabled={disabled}
          onChange={e => setFieldValues(p => ({ ...p, [f.id]: e.target.value }))}
          className="text-sm border border-border-strong rounded px-2 py-1.5 bg-white text-dark disabled:bg-slate-50"
        >
          <option value="">—</option>
          {(f.options ?? []).map(o => <option key={o.id} value={o.value ?? o.id}>{locStr(o.label, o.id)}</option>)}
        </select>
      ) : (
        <input
          type={f.type === "date" ? "date" : "text"}
          inputMode={f.type === "money" || f.type === "number" ? "decimal" : undefined}
          value={fieldValues[f.id] ?? ""}
          disabled={disabled}
          onChange={e => setFieldValues(p => ({ ...p, [f.id]: e.target.value }))}
          className="text-sm border border-border-strong rounded px-2 py-1.5 bg-white text-dark placeholder-muted-light disabled:bg-slate-50"
        />
      )}
    </div>
  )

  // Walk the DialogForm nodes (mirror of survey_web.py render). ask_when hides a
  // node whose guard is false, clearing any answer it left (→ NA on submit).
  const renderNodes = () => (form?.nodes ?? []).map((n, i) => {
    if (!evalAskWhen(n.ask_when, answers)) return null
    if (n.kind === "statement") {
      return (
        <div key={n.id ?? i} className="text-sm text-dark whitespace-pre-wrap leading-relaxed">
          {locStr(n.text, "")}
        </div>
      )
    }
    const ok = n.output_key ?? n.id ?? String(i)
    const it = n.interaction ?? "text"
    return (
      <div key={n.id ?? i} className="flex flex-col gap-2">
        <div className="text-sm font-medium text-dark">{locStr(n.prompt, "")}</div>
        {it === "form" && Array.isArray(n.fields) ? (
          <div className="space-y-3">{n.fields.map(renderField)}</div>
        ) : (it === "button" || it === "list" || it === "checklist") ? (
          <div className="flex flex-wrap gap-2">
            {(n.options ?? []).map(o => {
              const val = o.value ?? o.id
              const sel = answers[ok] === val
              return (
                <button
                  key={o.id}
                  type="button"
                  disabled={disabled}
                  onClick={() => setAnswers(p => ({ ...p, [ok]: val }))}
                  className={`text-sm px-3 py-1.5 rounded border transition-colors disabled:opacity-40 ${
                    sel ? "bg-primary text-white border-primary" : "border-border-strong text-dark hover:bg-slate-50"
                  }`}
                >
                  {locStr(o.label, o.id)}
                </button>
              )
            })}
          </div>
        ) : (
          <input
            type="text"
            value={answers[ok] ?? ""}
            disabled={disabled}
            onChange={e => setAnswers(p => ({ ...p, [ok]: e.target.value }))}
            className="text-sm border border-border-strong rounded px-2 py-1.5 bg-white text-dark placeholder-muted-light disabled:bg-slate-50"
          />
        )}
      </div>
    )
  })

  const hasBriefing = !!(title || summary || briefingSessionId)

  return (
    <div className="flex-1 h-full w-full overflow-hidden flex flex-col md:flex-row">
      {/* Briefing (read-only) — title/summary + referenced-session transcript. */}
      {hasBriefing && (
        <aside className="md:w-2/5 md:max-w-md border-b md:border-b-0 md:border-r border-border bg-slate-50 overflow-y-auto">
          <div className="p-4 space-y-3">
            <div className="text-2xs font-semibold text-muted uppercase tracking-wide">
              {t("formFill.briefing", { defaultValue: "Briefing" })}
            </div>
            {title && <h2 className="text-base font-semibold text-dark">{title}</h2>}
            {summary && <p className="text-sm text-dark whitespace-pre-wrap">{summary}</p>}
            {briefingSessionId && (
              <div className="pt-1">
                <div className="text-2xs font-semibold text-muted uppercase tracking-wide mb-1">
                  {t("formFill.transcript", { defaultValue: "Referenced conversation" })}
                </div>
                {briefing.length === 0 ? (
                  <div className="text-xs text-muted-light">
                    {t("formFill.transcriptEmpty", { defaultValue: "No transcript available." })}
                  </div>
                ) : (
                  <ul className="space-y-1.5">
                    {briefing.map(m => (
                      <li key={m.id} className="text-xs">
                        <span className="font-semibold text-muted">{m.author}: </span>
                        <span className="text-dark whitespace-pre-wrap">{m.text}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>
        </aside>
      )}

      {/* Form + actions. */}
      <div className="flex-1 overflow-y-auto">
        <div className="p-4 sm:p-6 max-w-2xl mx-auto space-y-5">
          {!form ? (
            <div className="text-sm text-muted-light">
              {t("formFill.noForm", { defaultValue: "Loading form…" })}
            </div>
          ) : (
            <>
              {form.name && <h2 className="text-base font-semibold text-dark">{form.name}</h2>}
              <div className="space-y-5">{renderNodes()}</div>

              {error && (
                <div className="text-xs text-red-text bg-red-light border border-red/30 rounded p-2">{error}</div>
              )}

              {/* Saída para a tarefa encerrada por outro. Fica FORA do slot de
                  `renderActions` de propósito: o overlay pode substituir o envio,
                  mas o agente não pode ficar sem saída por causa disso — e a saída
                  do action bar ("Return to queue") é justamente a que não serve. */}
              {closedElsewhere && onDismiss && (
                <button
                  type="button"
                  onClick={onDismiss}
                  className="text-sm px-4 py-2 rounded font-medium border border-border-strong text-dark hover:bg-slate-50 transition-colors"
                >
                  {t("formFill.conflict.dismiss", { defaultValue: "Remove from my list" })}
                </button>
              )}

              <div className="pt-1">
                {renderActions
                  ? renderActions(actionsState)
                  : (
                    <button
                      type="button"
                      onClick={() => submit({ decision: "input", source: "operator", answers })}
                      disabled={busy !== null || disabled || closedElsewhere}
                      className="text-sm px-4 py-2 rounded font-medium bg-primary text-white hover:bg-primary-dark transition-colors disabled:opacity-40"
                    >
                      {busy ? "…" : t("formFill.submit", { defaultValue: "Submit" })}
                    </button>
                  )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

export default DialogFormRenderer
