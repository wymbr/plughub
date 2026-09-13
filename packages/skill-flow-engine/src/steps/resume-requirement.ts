/**
 * resume-requirement.ts — PID-06. Resolve a exigência de retomada de um step que cria
 * pendência de cliente (`delegate`/`collect` com `customer_resumable`).
 *
 * ⚠️ **Falha FECHADA.** Um step que DECLARA `resume_requires` e não consegue resolvê-lo
 * não cria a pendência: seguir sem a exigência seria liberar o token a qualquer um,
 * exatamente o que a declaração existe para impedir — e sem nada vermelho. O deploy
 * (`judgeIdentityFloor`) já recusa a config ausente; chegar aqui sem valor é defeito de
 * ambiente, e o step o denuncia em vez de adivinhar `[]`.
 */
import { ResumeRequirementSchema, type ResumeRequirement } from "@plughub/schemas"
import type { StepContext } from "../executor"
import { resolveInputValue } from "../interpolate"

export type ResumeRequirementResolution =
  | { kind: "absent" }
  | { kind: "ok"; value: ResumeRequirement }
  | { kind: "error"; message: string }

export async function resolveResumeRequirement(
  step: { id: string; resume_requires?: unknown },
  ctx:  StepContext,
): Promise<ResumeRequirementResolution> {
  if (step.resume_requires === undefined) return { kind: "absent" }
  const raw = typeof step.resume_requires === "string"
    ? await resolveInputValue(step.resume_requires, ctx, ctx.contextStore)
    : step.resume_requires
  const parsed = ResumeRequirementSchema.safeParse(raw)
  if (parsed.success) return { kind: "ok", value: parsed.data }
  return {
    kind: "error",
    message:
      `[resume_requires] step=${step.id} session=${ctx.sessionId}: '${JSON.stringify(step.resume_requires)}' ` +
      `resolveu para ${JSON.stringify(raw)}, que não é lista de mecanismos — a pendência NÃO foi criada ` +
      `(sem a exigência o token sairia para qualquer sessão).`,
  }
}
