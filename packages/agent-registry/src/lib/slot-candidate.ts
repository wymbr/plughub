/**
 * lib/slot-candidate.ts — PID-16 (2026-09-14)
 *
 * UMA casa para a pergunta "este snapshot, com esta config, pode virar o que roda neste
 * pool?". Ela era respondida em duas rotas (set-next e promote), cada uma com a sua
 * cópia dos portões, e o promote em lote seria a terceira. Três cópias de uma lista de
 * portões é como um portão novo entra em duas e falta na terceira — e a que falta é a
 * que libera, sem ficar vermelha.
 *
 * O que NÃO mora aqui: a capacidade (`deployViolationBatch`), porque ela é soma sobre o
 * TENANT e só quem conhece o lote inteiro sabe julgá-la; e o estado dos slots (`next`
 * pendente, `current` idêntico), que é pergunta de cada rota.
 *
 * O rollback continua ISENTO, por decisão antiga e repetida em cada portão: operação de
 * emergência nunca bloqueia. Quem chama isto é quem PROMOVE ou DECLARA.
 */
import { judgeMaskedDeploy } from "./masked-deploy"
import { judgeProfileSteps } from "./profile-steps"
import { judgeRequiredConfig } from "./required-config"
import { judgeIdentityFloor } from "./identity-floor"

export type SlotCandidateVerdict =
  | { kind: "ok"; warnings: string[] }
  | { kind: "block"; error: string; message?: string }

export function judgeSlotCandidate(args: {
  pool:        Record<string, unknown>
  /** A linha do skill (para `config_params`); `null` quando o skill não existe mais. */
  skill:       Record<string, unknown> | null
  snapshot:    unknown
  configJson:  unknown
  poolId:      string
  skillId:     string
  /** Rótulo do log (`set-next`, `promote`, `promote-batch`). */
  stage:       string
}): SlotCandidateVerdict {
  const { pool, skill, snapshot, poolId, skillId, stage } = args
  const configJson = args.configJson ?? {}
  const ids = { poolId, skillId: skillId || "(sem skill)" }

  // Capacity-governance item 2: deploy de skill só em pool 'ai' — pool humano não
  // pré-instancia agentes (a fila atendida vive no pool de fila, IA).
  if (pool["agent_kind"] === "human") {
    return {
      kind:  "block",
      error: "pool agent_kind 'human' não recebe deploy de skill — deploys são para pools IA",
    }
  }

  // Um slot SEM snapshot é inexecutável: o bridge roda exclusivamente o snapshot do slot
  // `current`, então promovê-lo produz um pool que parece deployado e não roda nada.
  if (snapshot == null) {
    return {
      kind:    "block",
      error:   "skill_sem_definicao",
      message:
        `O skill '${skillId}' não tem definição (flow) gravada — não há o que congelar no slot. ` +
        `Salve o fluxo no editor (ou deixe o RegistrySyncer semeá-lo do YAML) antes de fazer o deploy.`,
    }
  }

  const warnings: string[] = []

  // NIV-03 — skill que MASCARA em pool sem canal capaz.
  const masked = judgeMaskedDeploy(snapshot, pool["channel_types"], ids)
  if (masked.kind === "block") return { kind: "block", error: masked.error, message: masked.message }
  if (masked.kind === "warn") {
    console.warn(`[pool-slots:${stage}] ${masked.warning}`)
    warnings.push(masked.warning)
  }

  // CTR-01 (G1) — step que o PERFIL do pool não admite.
  const perfil = judgeProfileSteps(snapshot, pool["channel_types"], ids)
  if (perfil.kind === "block") return { kind: "block", error: perfil.error, message: perfil.message }

  // Parâmetro de deploy OBRIGATÓRIO que o slot não preencheu.
  const config = judgeRequiredConfig(skill?.["config_params"], configJson, ids)
  if (config.kind === "block") return { kind: "block", error: config.error, message: config.message }

  // PID-06 (D7) — a exigência de retomada que o slot declara contém o PISO do skill?
  const piso = judgeIdentityFloor(snapshot, configJson, ids)
  if (piso.kind === "block") return { kind: "block", error: piso.error, message: piso.message }

  return { kind: "ok", warnings }
}
