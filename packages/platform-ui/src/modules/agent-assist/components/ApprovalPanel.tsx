/**
 * ApprovalPanel — Fila de trabalho humano / Aprovação (ADR adr-human-approval-workflow-step, A3/A5).
 *
 * Wrapper FINO sobre o núcleo genérico <DialogFormRenderer> (kickoff
 * docs/product/approval-renderer-kickoff.md, R0). O núcleo renderiza o briefing
 * read-only + o DialogForm por `form_id` e faz o submit via resume; a aprovação
 * EMPILHA aqui as afordâncias específicas:
 *   - `decisions[]` como botões terminais mapeados ao `choice` do WORKFLOW;
 *   - auditoria de edição (antes→depois) dos campos pré-preenchidos (`field_edits`);
 *   - gate ABAC `approvals` (ver = operacao; decidir = decide).
 *
 * A decisão volta em `POST /v1/channels/webhook/resume/{token}` com
 * `payload:{ decision:"input", source:"operator", choice, edits, field_edits }`.
 * O payload cai em `$.pipeline_state.<delegate>` e o WORKFLOW roteia (choice) — o
 * aprovador escolhe a DECISÃO, nunca o passo (invariante do ADR). O núcleo é
 * canal-agnóstico; omnichannel adiado (D6).
 */
import React, { useMemo } from "react"
import { useTranslation } from "react-i18next"
import { ShieldAlert } from "lucide-react"
import { useAuth } from "../../../auth/useAuth"
import DialogFormRenderer, {
  type Snapshot,
  snapVal,
  resumeTokenOf,
  locStr,
} from "./DialogFormRenderer"

interface Decision { id: string; label: string }

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
  /** Repassado ao núcleo: saída da tarefa encerrada por outro (409 `terminal`). */
  onDismiss?: () => void
}

/**
 * True when a claimed contact is an APPROVAL task (form-fill + bounded decisions).
 * Approval is a SPECIALIZATION of form-fill (isFormFillSnapshot); the extra signal
 * is `session.decisions`. Kept here so the routing layer picks the right wrapper.
 */
export function isApprovalSnapshot(snap: Snapshot): boolean {
  return !!snapVal(snap, "session.decisions") && !!resumeTokenOf(snap)
}

export const ApprovalPanel: React.FC<ApprovalPanelProps> = ({ tenantId, poolId, instanceId, snapshot, onResolved, onDismiss }) => {
  const { t } = useTranslation("agentAssist")
  const { perms } = useAuth()
  const canView   = perms.can("approvals", "operacao")
  const canDecide = perms.can("approvals", "decide")

  const decisions: Decision[] = useMemo(() => {
    const raw = snapVal(snapshot, "session.decisions")
    if (!raw) return []
    try { return JSON.parse(raw) as Decision[] } catch { return [] }
  }, [snapshot])

  // Gate de VER: sem approvals.operacao não mostra o pacote (o inbox pull é
  // genérico e não conhece o ABAC de aprovação; aqui é a superfície que enforça).
  // Devolver à fila segue disponível pelo action bar do Console.
  if (!canView) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-2 text-warning-text p-6 text-center">
        <ShieldAlert className="w-8 h-8" aria-hidden="true" />
        <span className="text-sm">
          {t("approval.noView", { defaultValue: "You do not have permission to view this approval task (approvals.operacao required)." })}
        </span>
      </div>
    )
  }

  return (
    <DialogFormRenderer
      tenantId={tenantId}
      snapshot={snapshot}
      poolId={poolId}
      instanceId={instanceId}
      inputsDisabled={!canDecide}
      onResolved={onResolved}
      onDismiss={onDismiss}
      renderActions={({ fieldValues, baseline, formFields, busy, submit }) => (
        !canDecide ? (
          <div className="flex items-start gap-2 text-xs text-warning-text bg-warning-light border border-warning/30 rounded p-2">
            <ShieldAlert className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
            {t("approval.noPermission", { defaultValue: "You can view this task but not decide (approvals.decide required)." })}
          </div>
        ) : (
          <div className="flex flex-wrap gap-2">
            {decisions.map((d, i) => (
              <button
                key={d.id}
                type="button"
                disabled={busy !== null}
                onClick={() => submit(
                  {
                    // decision "input" → on_resume; the WORKFLOW routes on payload.choice.
                    decision: "input",
                    source:   "operator",
                    choice:   d.id,
                    edits:       fieldValues,   // after-map (funcional do workflow — inalterado)
                    // A5 auditoria: diff antes→depois por campo pré-preenchido.
                    field_edits: formFields.map(f => ({
                      field:  f.id,
                      before: baseline[f.id] ?? null,
                      after:  fieldValues[f.id] ?? null,
                    })),
                    // TODO(A5.6): popular quando o viewer de anexos renderizar refs; [] até lá.
                    attachments_viewed: [],
                  },
                  d.id,   // busyKey → spinner no botão clicado
                )}
                className={`text-sm px-4 py-2 rounded font-medium transition-colors disabled:opacity-40 ${
                  i === 0 ? "bg-primary text-white hover:bg-primary-dark" : "border border-border text-dark hover:bg-slate-50"
                }`}
              >
                {busy === d.id ? "…" : locStr(d.label, d.id)}
              </button>
            ))}
          </div>
        )
      )}
    />
  )
}

export default ApprovalPanel
