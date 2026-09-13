/**
 * identity-floor.ts — PID-06 (ADR `adr-identity-door-evidence` D7).
 *
 * O PORTÃO do piso de identidade da retomada. Irmão de `required-config.ts` e
 * `profile-steps.ts`: mesma pergunta ("este par (snapshot, config do slot) pode rodar?"),
 * mesmo momento (set-next e promote), mesma forma de veredicto. A regra mora em
 * `@plughub/schemas` (`judgeResumeRequirementSteps`); aqui é só onde ela recusa.
 *
 * ⚠️ Recusa, nunca ajusta: completar a config com o piso faria a tela de Deploy mostrar
 * `[]` enquanto o pool roda `["otp"]` — o valor plausível que esconde a decisão.
 * O ROLLBACK fica isento, como nas irmãs: operação de emergência nunca bloqueia.
 */
import { judgeResumeRequirementSteps } from "@plughub/schemas"

export type IdentityFloorVerdict =
  | { kind: "ok" }
  | { kind: "block"; error: string; message: string }

export function judgeIdentityFloor(
  snapshot:   unknown,
  configJson: unknown,
  ctx:        { poolId: string; skillId: string },
): IdentityFloorVerdict {
  const flow = snapshot as { steps?: unknown[] } | null
  if (!flow || !Array.isArray(flow.steps)) return { kind: "ok" }
  const violacoes = judgeResumeRequirementSteps(flow.steps, configJson)
  if (violacoes.length === 0) return { kind: "ok" }
  return {
    kind:    "block",
    error:   violacoes[0]!.error,
    message: `Deploy de '${ctx.skillId}' no pool '${ctx.poolId}' recusado — exigência de retomada: ` +
             violacoes.map(v => v.message).join("; "),
  }
}
