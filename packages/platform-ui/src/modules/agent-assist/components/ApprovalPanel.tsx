/**
 * ApprovalPanel — Fila de trabalho humano / Aprovação (ADR adr-human-approval-workflow-step, A3).
 *
 * Renderiza o PACOTE de aprovação de uma tarefa reivindicada (contato do pool
 * aprovacao_deploy). O pacote é lido do ContextStore da sessão-filho (delegate.context,
 * escrito com prefixo session.*): resumo read-only + DialogForm de campos editáveis +
 * decisões (bounded, definidas pelo workflow). Ao decidir, chama o resume do delegate:
 *   POST /v1/channels/webhook/resume/{token}  body { tenant_id, payload:{ decision:"input",
 *   source:"operator", choice, edits } }
 * O payload cai em $.pipeline_state.<delegate> e o WORKFLOW roteia (choice) — o aprovador
 * escolhe a DECISÃO, nunca o passo (invariante do ADR).
 *
 * ABAC: ver = approvals.operacao; decidir = approvals.decide (botões gated).
 * Omnichannel adiado: conteúdo (DialogForm) e retorno (payload) são canal-agnósticos.
 */
import React, { useEffect, useMemo, useState } from "react"
import { useTranslation } from "react-i18next"
import { CheckCircle2, ShieldAlert } from "lucide-react"
import { useAuth } from "../../../auth/useAuth"

type Snapshot = Record<string, { value?: unknown } | undefined> | null | undefined

interface Decision { id: string; label: string }
interface FormField {
  id: string
  label: string
  type: string
  required?: boolean
  value?: string | number | boolean
  options?: { id: string; label: string }[]
}

interface ApprovalPanelProps {
  sessionId: string
  tenantId:  string
  /** A5 — pool da task de aprovação (chave da claim lease p/ o check caller==claimant). */
  poolId?:   string
  /** A5 — instance_id deste console (`human-{userId}`) = o claimant; verificado no ingress. */
  instanceId?: string
  /** context_snapshot from supervisorState.customer_context — the package lives here. */
  snapshot:  Snapshot
  /** Called after a successful resume so the parent can drop the resolved contact. */
  onResolved?: () => void
}

function snapVal(snap: Snapshot, key: string): string | undefined {
  const v = snap?.[key]?.value
  return typeof v === "string" && v.trim() ? v : undefined
}

// The delegate writes the resume token under session.delegate_resume_token
// (confirmed as-built). Kept a fallback to workflow_resume_token for other paths.
function resumeTokenOf(snap: Snapshot): string | undefined {
  return snapVal(snap, "session.delegate_resume_token") ?? snapVal(snap, "session.workflow_resume_token")
}

/** True when a claimed contact is an APPROVAL task (carries the package). */
export function isApprovalSnapshot(snap: Snapshot): boolean {
  return !!snapVal(snap, "session.decisions") && !!resumeTokenOf(snap)
}

function locStr(x: unknown, fallback: string): string {
  if (typeof x === "string") return x
  if (x && typeof x === "object") {
    const m = x as Record<string, string>
    return m["pt-BR"] ?? m["en"] ?? Object.values(m)[0] ?? fallback
  }
  return fallback
}

export const ApprovalPanel: React.FC<ApprovalPanelProps> = ({ sessionId, tenantId, poolId, instanceId, snapshot, onResolved }) => {
  const { t } = useTranslation("agentAssist")
  const { perms, getAccessToken } = useAuth()
  const canView   = perms.can("approvals", "operacao")
  const canDecide = perms.can("approvals", "decide")

  const title       = snapVal(snapshot, "session.title") ?? t("approval.title", { defaultValue: "Approval" })
  const summary     = snapVal(snapshot, "session.summary") ?? ""
  const formId      = snapVal(snapshot, "session.dialog_form_id")
  const resumeToken = resumeTokenOf(snapshot)

  const decisions: Decision[] = useMemo(() => {
    const raw = snapVal(snapshot, "session.decisions")
    if (!raw) return []
    try { return JSON.parse(raw) as Decision[] } catch { return [] }
  }, [snapshot])

  const [fields,   setFields]   = useState<FormField[]>([])
  const [edits,    setEdits]    = useState<Record<string, string>>({})
  // A5 — snapshot do pré-preenchimento (o "antes" do diff auditado antes→depois).
  const [baseline, setBaseline] = useState<Record<string, string>>({})
  const [busy,     setBusy]     = useState<string | null>(null)
  const [done,     setDone]     = useState(false)
  const [error,    setError]    = useState<string | null>(null)

  // Load the editable fields of the DialogForm (the "form" question node).
  useEffect(() => {
    if (!formId) { setFields([]); return }
    let cancelled = false
    fetch(`/v1/dialog/forms/${encodeURIComponent(formId)}?status=published`, {
      headers: { "X-Tenant-ID": tenantId },
    })
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then((form: { nodes?: Array<{ kind: string; fields?: FormField[] }> }) => {
        if (cancelled) return
        const q = (form.nodes ?? []).find(n => n.kind === "question" && Array.isArray(n.fields) && n.fields.length)
        const fs = (q?.fields ?? []).map(f => ({ ...f, label: locStr(f.label, f.id) }))
        setFields(fs)
        // Pre-fill edits from each field's value.
        const init: Record<string, string> = {}
        for (const f of fs) if (f.value !== undefined) init[f.id] = String(f.value)
        setEdits(init)
        // A5 — guarda o pré-preenchimento como baseline imutável do diff.
        setBaseline(init)
      })
      .catch(() => { if (!cancelled) setFields([]) })
    return () => { cancelled = true }
  }, [formId, tenantId])

  async function decide(choice: string) {
    if (!resumeToken || !canDecide) return
    setBusy(choice); setError(null)
    try {
      // A5 — diff explícito antes→depois de cada campo do pacote (baseline = pré-preenchimento).
      // O webhook adapter monta ApprovalDecisionMeta.edits a partir daqui; o workflow segue
      // roteando por payload.choice e pode aplicar o after-map `edits` (inalterado).
      const fieldEdits = fields.map(f => ({
        field:  f.id,
        before: baseline[f.id] ?? null,
        after:  edits[f.id] ?? null,
      }))
      // A5 — JWT do aprovador → atribuição grau `possessed`, verificada server-side no ingress
      // (assinatura + ABAC approvals.decide + caller==claimant). Ausente → `claimed`.
      const token = await getAccessToken()
      const res = await fetch(`/v1/channels/webhook/resume/${encodeURIComponent(resumeToken)}`, {
        method:  "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          tenant_id: tenantId,
          // A5 — claimant binding: pool_id + instance_id (=`human-{userId}`) deixam o ingress
          // ler a claim lease (tenant,pool,session→instance) e exigir caller==claimant.
          pool_id:     poolId,
          instance_id: instanceId,
          // decision "input" → on_resume; the WORKFLOW routes on payload.choice.
          payload: {
            decision: "input",
            source:   "operator",
            choice,
            edits,                     // after-map (uso funcional do workflow — inalterado)
            field_edits: fieldEdits,   // A5 auditoria: antes→depois por campo
            // TODO(A5.6): popular quando o viewer de anexos renderizar refs; [] até lá.
            attachments_viewed: [],
          },
        }),
      })
      if (!res.ok) { setError(`HTTP ${res.status}`); setBusy(null); return }
      setDone(true)
      onResolved?.()
    } catch (e) {
      setError(String(e)); setBusy(null)
    }
  }

  if (done) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-2 text-green-text p-6 text-center">
        <CheckCircle2 className="w-8 h-8" aria-hidden="true" />
        <span className="text-sm">{t("approval.resolved", { defaultValue: "Decision recorded. The workflow will continue." })}</span>
      </div>
    )
  }

  // Gate de VER: sem approvals.operacao não mostra o pacote (o inbox pull é genérico
  // e não conhece o ABAC de aprovação; aqui é a superfície que enforça). Devolver à
  // fila continua disponível pelo action bar (soltar a tarefa que foi reivindicada).
  if (!canView) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-2 text-warning-text p-6 text-center">
        <ShieldAlert className="w-8 h-8" aria-hidden="true" />
        <span className="text-sm">{t("approval.noView", { defaultValue: "You do not have permission to view this approval task (approvals.operacao required)." })}</span>
      </div>
    )
  }

  return (
    <div className="flex-1 h-full w-full overflow-y-auto">
     <div className="p-4 sm:p-6 max-w-2xl mx-auto space-y-4">
      {/* Cabeçalho */}
      <div>
        <div className="text-2xs font-semibold text-primary uppercase tracking-wide">
          {t("approval.tag", { defaultValue: "Approval task" })}
        </div>
        <h2 className="text-base font-semibold text-dark">{title}</h2>
      </div>

      {/* Resumo read-only */}
      {summary && (
        <section className="bg-slate-50 border border-border rounded-lg p-3 text-sm text-dark whitespace-pre-wrap">
          {summary}
        </section>
      )}

      {/* Campos editáveis (DialogForm) */}
      {fields.length > 0 && (
        <section className="space-y-3">
          <div className="text-2xs font-semibold text-muted uppercase tracking-wide">
            {t("approval.fields", { defaultValue: "Details" })}
          </div>
          {fields.map(f => (
            <div key={f.id} className="flex flex-col gap-1">
              <label className="text-xs text-muted">{f.label}{f.required ? " *" : ""}</label>
              {f.type === "bool" ? (
                <label className="inline-flex items-center gap-2 text-sm text-dark">
                  <input
                    type="checkbox"
                    checked={edits[f.id] === "true"}
                    disabled={!canDecide}
                    onChange={e => setEdits(p => ({ ...p, [f.id]: e.target.checked ? "true" : "false" }))}
                  />
                  {t("approval.yes", { defaultValue: "Yes" })}
                </label>
              ) : f.type === "select" ? (
                <select
                  value={edits[f.id] ?? ""}
                  disabled={!canDecide}
                  onChange={e => setEdits(p => ({ ...p, [f.id]: e.target.value }))}
                  className="text-sm border border-border-strong rounded px-2 py-1.5 bg-white text-dark disabled:bg-slate-50"
                >
                  <option value="">—</option>
                  {(f.options ?? []).map(o => <option key={o.id} value={o.id}>{locStr(o.label, o.id)}</option>)}
                </select>
              ) : (
                <input
                  type={f.type === "date" ? "date" : f.type === "money" || f.type === "number" ? "text" : "text"}
                  inputMode={f.type === "money" || f.type === "number" ? "decimal" : undefined}
                  value={edits[f.id] ?? ""}
                  disabled={!canDecide}
                  onChange={e => setEdits(p => ({ ...p, [f.id]: e.target.value }))}
                  className="text-sm border border-border-strong rounded px-2 py-1.5 bg-white text-dark placeholder-muted-light disabled:bg-slate-50"
                />
              )}
            </div>
          ))}
        </section>
      )}

      {error && <div className="text-xs text-red-text bg-red-light border border-red/30 rounded p-2">{error}</div>}

      {/* Decisões (bounded, do workflow) */}
      {!canDecide ? (
        <div className="flex items-start gap-2 text-xs text-warning-text bg-warning-light border border-warning/30 rounded p-2">
          <ShieldAlert className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
          {t("approval.noPermission", { defaultValue: "You can view this task but not decide (approvals.decide required)." })}
        </div>
      ) : (
        <section className="flex flex-wrap gap-2 pt-1">
          {decisions.map((d, i) => (
            <button
              key={d.id}
              onClick={() => decide(d.id)}
              disabled={busy !== null}
              className={`text-sm px-4 py-2 rounded font-medium transition-colors disabled:opacity-40 ${
                i === 0 ? "bg-primary text-white hover:bg-primary-dark" : "border border-border text-dark hover:bg-slate-50"
              }`}
            >
              {busy === d.id ? "…" : locStr(d.label, d.id)}
            </button>
          ))}
        </section>
      )}
     </div>
    </div>
  )
}

export default ApprovalPanel
