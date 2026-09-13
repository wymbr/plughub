/**
 * resume-requirement.ts — PID-06 (2026-09-13)
 *
 * A exigência de RETOMADA (ADR `adr-identity-door-evidence` D5/D6/D7): o que uma pendência
 * que o cliente pode retomar exige de identidade, declarado pelo N3 no step que cria a
 * pendência, e conferido contra a evidência DA SESSÃO que pede o token.
 *
 * ⚠️ **Mora no step `customer_resumable`, não no `suspend`** — medido: o único `suspend`
 * vivo é aprovação de operador; as pendências que o cliente retoma nascem de `delegate` e
 * `collect` com `customer_resumable: true` (três em produção no dia).
 *
 * TRÊS peças, uma casa:
 *   - o CAMPO do step (`resume_requires`: conjunto literal, ou ref `$.config.<chave>`) e o
 *     PISO do skill (`resume_requires_floor`, literal — o slot não o edita);
 *   - `judgeResumeRequirementSteps` — o deploy recusa config que não contém o piso (D7);
 *   - `judgeResumeEvidence` — a liberação do token confere a evidência (D5/D6).
 * O portão de cada uma é do serviço que decide (agent-registry, mcp-server); a regra é esta.
 */
import { z } from "zod"
import { IDENTITY_MECHANISMS, identityEvidenceTag, type IdentityMechanism } from "./identity-evidence"

/** Conjunto de mecanismos (D4: exigência é CONJUNTO, nunca limiar). `[]` = não exige. */
export const ResumeRequirementSchema = z.array(z.enum(IDENTITY_MECHANISMS))
export type ResumeRequirement = z.infer<typeof ResumeRequirementSchema>

/**
 * Só `$.config.<chave>` — e isso é o que torna o piso JULGÁVEL no deploy. Um ref de
 * `@ctx`/`pipeline_state` só teria valor em runtime, e o deploy não teria o que conferir.
 */
export const RESUME_REQUIREMENT_REF = /^\$\.config\.([A-Za-z0-9_]+)$/

export const ResumeRequiresFieldSchema = z.union([
  ResumeRequirementSchema,
  z.string().regex(RESUME_REQUIREMENT_REF, "resume_requires deve ser uma lista de mecanismos ou um ref $.config.<chave>"),
])

/**
 * Idade máxima da prova para liberar o token. Política de PLATAFORMA, e curta de
 * propósito: a prova é da sessão que está pedindo AGORA — o intake verifica e pede a
 * pendência em seguida. Uma idade longa reabriria o vetor (4) com prazo.
 */
export const RESUME_EVIDENCE_MAX_AGE_S = 900

export interface ResumeEvidenceMiss {
  mechanism: string
  reason:    "not_verified" | "other_session" | "stale" | "no_verified_at" | "unknown_mechanism"
}

/** Lê o `value` de uma tag de contexto gravada como ContextEntry JSON. */
function entryValue(raw: string | undefined): string | undefined {
  if (raw === undefined) return undefined
  try {
    const v = (JSON.parse(raw) as { value?: unknown }).value
    return v === undefined || v === null ? undefined : String(v)
  } catch {
    return undefined
  }
}

/**
 * A evidência do hash de journey satisfaz a exigência PARA ESTA sessão?
 * Cada mecanismo exigido precisa de `status = verified`, `proven_in_session` igual à
 * sessão que pede, e `verified_at` dentro da idade máxima. Nenhum é opcional: prova de
 * outra sessão é o vetor (4), e prova sem data não tem idade.
 */
export function judgeResumeEvidence(
  requires:     readonly string[],
  journeyHash:  Record<string, string>,
  opts:         { sessionId: string; nowMs: number; maxAgeS?: number },
): { satisfied: boolean; missing: ResumeEvidenceMiss[] } {
  const maxAgeMs = (opts.maxAgeS ?? RESUME_EVIDENCE_MAX_AGE_S) * 1000
  const missing: ResumeEvidenceMiss[] = []
  for (const mech of requires) {
    if (!(IDENTITY_MECHANISMS as readonly string[]).includes(mech)) {
      missing.push({ mechanism: mech, reason: "unknown_mechanism" })
      continue
    }
    const m = mech as IdentityMechanism
    const status = entryValue(journeyHash[identityEvidenceTag(m, "status")])
    if (status !== "verified") { missing.push({ mechanism: mech, reason: "not_verified" }); continue }
    const quem = entryValue(journeyHash[identityEvidenceTag(m, "proven_in_session")])
    if (quem !== opts.sessionId) { missing.push({ mechanism: mech, reason: "other_session" }); continue }
    const quando = Date.parse(entryValue(journeyHash[identityEvidenceTag(m, "verified_at")]) ?? "")
    if (Number.isNaN(quando)) { missing.push({ mechanism: mech, reason: "no_verified_at" }); continue }
    if (opts.nowMs - quando > maxAgeMs) missing.push({ mechanism: mech, reason: "stale" })
  }
  return { satisfied: missing.length === 0, missing }
}

export interface ResumeRequirementViolation {
  step_id: string
  error:
    | "resume_requires_sem_pendencia"
    | "resume_requires_ausente_na_config"
    | "resume_requires_invalido"
    | "resume_requires_abaixo_do_piso"
    | "piso_sem_resume_requires"
  message: string
}

/**
 * D7 — para cada step que declara exigência ou piso, a exigência EFETIVA (literal, ou a
 * chave de `config_json` apontada pelo ref) tem de CONTER o piso. Nunca ajusta: um
 * ajuste faria a tela mostrar `[]` enquanto roda `["otp"]`.
 */
export function judgeResumeRequirementSteps(
  steps:      readonly unknown[],
  configJson: unknown,
): ResumeRequirementViolation[] {
  const cfg = (configJson && typeof configJson === "object" ? configJson : {}) as Record<string, unknown>
  const out: ResumeRequirementViolation[] = []
  for (const s of steps) {
    if (!s || typeof s !== "object") continue
    const step = s as Record<string, unknown>
    const id = String(step["id"] ?? "?")
    const decl = step["resume_requires"]
    const floor = step["resume_requires_floor"]
    if (decl === undefined && floor === undefined) continue

    if (decl === undefined) {
      out.push({ step_id: id, error: "piso_sem_resume_requires",
        message: `o step '${id}' declara resume_requires_floor sem resume_requires — o piso não teria o que proteger` })
      continue
    }
    if (step["customer_resumable"] !== true) {
      out.push({ step_id: id, error: "resume_requires_sem_pendencia",
        message: `o step '${id}' declara resume_requires sem customer_resumable: true — nenhuma pendência de cliente nasce dele` })
      continue
    }

    let efetiva: unknown = decl
    let origem = "literal no step"
    if (typeof decl === "string") {
      const m = RESUME_REQUIREMENT_REF.exec(decl)
      if (!m) {
        out.push({ step_id: id, error: "resume_requires_invalido",
          message: `o step '${id}' usa '${decl}' — só lista de mecanismos ou $.config.<chave>` })
        continue
      }
      origem = `config_json.${m[1]}`
      if (!(m[1]! in cfg)) {
        out.push({ step_id: id, error: "resume_requires_ausente_na_config",
          message: `o step '${id}' lê resume_requires de ${decl} e o slot não tem '${m[1]}' — config ausente é erro; [] é válido quando o piso permite` })
        continue
      }
      efetiva = cfg[m[1]!]
    }
    const parsed = ResumeRequirementSchema.safeParse(efetiva)
    if (!parsed.success) {
      out.push({ step_id: id, error: "resume_requires_invalido",
        message: `o step '${id}' tem resume_requires inválido (${origem}): ${JSON.stringify(efetiva)} — mecanismos conhecidos: ${IDENTITY_MECHANISMS.join(", ")}` })
      continue
    }
    const piso = ResumeRequirementSchema.safeParse(floor ?? [])
    if (!piso.success) {
      out.push({ step_id: id, error: "resume_requires_invalido",
        message: `o step '${id}' tem resume_requires_floor inválido: ${JSON.stringify(floor)}` })
      continue
    }
    const faltam = piso.data.filter(mech => !parsed.data.includes(mech))
    if (faltam.length > 0) {
      out.push({ step_id: id, error: "resume_requires_abaixo_do_piso",
        message: `o step '${id}' exige ${JSON.stringify(parsed.data)} (${origem}), e o skill declara o piso ${JSON.stringify(piso.data)} — falta ${faltam.join(", ")}` })
    }
  }
  return out
}
