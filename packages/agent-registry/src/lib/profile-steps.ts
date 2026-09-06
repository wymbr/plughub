/**
 * profile-steps.ts — deploy de skill cujo fluxo usa step que o PERFIL do pool não
 * admite (CTR-01 / G1 do ADR `adr-orchestrator-specialist-contract.md`).
 *
 * Irmão de `masked-deploy.ts`, e de propósito: mesma pergunta ("este par
 * (snapshot, pool) pode rodar?"), mesmo momento (set-next e promote), mesma
 * forma de veredicto. O que muda é que aqui **não existe o meio-termo**.
 *
 * ── Por que não há ramo `warn` ──────────────────────────────────────────────
 *
 * No `masked-deploy` o desfecho depende de POR ONDE o contato chega, e isso não
 * é estático — daí o aviso para o caso parcial. Aqui o perfil é **um** para o
 * pool inteiro: ou o step é admitido, ou nenhum contato daquele pool o executa.
 * Um `warn` seria dizer *"talvez funcione"* sobre algo que já se sabe que não.
 *
 * ── Onde a lista mora, e por que não aqui ───────────────────────────────────
 *
 * A lista e a derivação do perfil vivem em `@plughub/schemas`
 * (`skill-profile.ts`), com a medição que a produziu. Este arquivo é só o
 * PORTÃO — o mesmo desenho do `capacity`/`masked-deploy`: a regra é contrato
 * compartilhado, o lugar onde ela recusa é do serviço.
 */

import {
  forbiddenStepsForProfile,
  skillProfileFor,
  type SkillProfile,
} from "@plughub/schemas"

export type ProfileStepsVerdict =
  | { kind: "ok" }
  | { kind: "block"; error: string; message: string }

/**
 * Julga um par (snapshot, canais do pool).
 *
 * ⚠️ **Snapshot ausente ou sem `steps` é `ok`, e isso não é fail-open por
 * descuido**: quem recusa deploy sem definição é o `skill_sem_definicao` do
 * `set-next`, uma linha acima. Repetir a recusa aqui daria DOIS erros diferentes
 * para o mesmo defeito, e o segundo diria a coisa errada ("perfil") sobre um
 * problema que é de definição ausente.
 */
export function judgeProfileSteps(
  snapshot:     unknown,
  channelTypes: unknown,
  ctx:          { poolId: string; skillId: string },
): ProfileStepsVerdict {
  const flow = snapshot as { steps?: unknown[] } | null
  if (!flow || !Array.isArray(flow.steps)) return { kind: "ok" }

  const canais: string[] = Array.isArray(channelTypes)
    ? (channelTypes as unknown[]).filter((c): c is string => typeof c === "string")
    : []
  const perfil: SkillProfile = skillProfileFor(canais)

  const proibidos = forbiddenStepsForProfile(
    perfil,
    flow.steps.filter((s): s is { type?: string } => !!s && typeof s === "object") as { type?: string }[],
  )
  if (proibidos.length === 0) return { kind: "ok" }

  return {
    kind:  "block",
    error: "step_fora_do_perfil",
    message:
      `O pool '${ctx.poolId}' tem perfil '${perfil}' (canais: ${canais.join(", ") || "nenhum declarado"}), ` +
      `e o fluxo de '${ctx.skillId}' usa step que esse perfil não admite: ${proibidos.join(", ")}. ` +
      (perfil === "agent"
        ? `Um pool de agente atende um cliente CONECTADO — 'suspend' e 'collect' o deixariam esperando por um sinal externo.`
        : `Um pool webhook não tem conversa do outro lado — 'menu', 'notify' e o bloco de transação mascarada pressupõem um cliente.`),
  }
}
