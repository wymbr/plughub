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
import {
  REQUIRABLE_MECHANISMS, SATISFIED_BY, identityEvidenceTag,
  type IdentityMechanism, type RequirableMechanism,
} from "./identity-evidence"

/** Conjunto de mecanismos (D4: exigência é CONJUNTO, nunca limiar). `[]` = não exige. */
export const ResumeRequirementSchema = z.array(z.enum(REQUIRABLE_MECHANISMS))
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
  reason:
    | "not_verified" | "other_session" | "stale" | "no_verified_at" | "unknown_mechanism"
    | "no_customer" | "other_customer"
}

/** PID-09 — clientes para os quais há prova de posse (de qualquer mecanismo) no hash. */
export function evidenceCustomers(journeyHash: Record<string, string>): string[] {
  const out = new Set<string>()
  for (const [tag, raw] of Object.entries(journeyHash)) {
    if (!/^core\.journey\.identity\.[a-z_]+\.customer_id$/.test(tag)) continue
    const v = entryValue(raw)
    if (v) out.add(v)
  }
  return [...out]
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
 * A evidência do hash de journey satisfaz a exigência PARA ESTA sessão E ESTE cliente?
 * Cada mecanismo exigido precisa de UM registro, de qualquer mecanismo que o satisfaça
 * (`SATISFIED_BY`), com `status = verified`, `proven_in_session` igual à sessão que pede,
 * `verified_at` dentro da idade máxima e `customer_id` igual ao cliente das pendências.
 * Nenhum é opcional: prova de outra sessão é o vetor (4), prova sem data não tem idade, e
 * prova de outro cliente é quem chega pelo próprio número pedindo o pedido de outra pessoa.
 *
 * `customerId` ausente ou vazio NUNCA satisfaz: sem saber de quem são as pendências, não há
 * como amarrar a prova a elas — o caminho legado por `contact_identifier` cai aqui, fechado.
 */
export function judgeResumeEvidence(
  requires:     readonly string[],
  journeyHash:  Record<string, string>,
  opts:         { sessionId: string; nowMs: number; customerId: string | undefined; maxAgeS?: number },
): { satisfied: boolean; missing: ResumeEvidenceMiss[] } {
  const maxAgeMs = (opts.maxAgeS ?? RESUME_EVIDENCE_MAX_AGE_S) * 1000
  const missing: ResumeEvidenceMiss[] = []
  // Ordem dos motivos: do mais "perto de satisfazer" ao mais longe — o motivo relatado é
  // o do registro que chegou mais longe, para o log dizer o que de fato faltou.
  const ORDEM: ResumeEvidenceMiss["reason"][] = [
    "other_customer", "no_customer", "stale", "no_verified_at", "other_session", "not_verified",
  ]
  for (const mech of requires) {
    const satisfazem = SATISFIED_BY[mech as RequirableMechanism]
    if (!satisfazem) {
      missing.push({ mechanism: mech, reason: "unknown_mechanism" })
      continue
    }
    let melhor: ResumeEvidenceMiss["reason"] | null = null
    let ok = false
    for (const m of satisfazem) {
      const motivo = motivoDoRegistro(m, journeyHash, opts, maxAgeMs)
      if (motivo === null) { ok = true; break }
      if (melhor === null || ORDEM.indexOf(motivo) < ORDEM.indexOf(melhor)) melhor = motivo
    }
    if (!ok) missing.push({ mechanism: mech, reason: melhor ?? "not_verified" })
  }
  return { satisfied: missing.length === 0, missing }
}

function motivoDoRegistro(
  m:        IdentityMechanism,
  hash:     Record<string, string>,
  opts:     { sessionId: string; nowMs: number; customerId: string | undefined },
  maxAgeMs: number,
): ResumeEvidenceMiss["reason"] | null {
  if (entryValue(hash[identityEvidenceTag(m, "status")]) !== "verified") return "not_verified"
  if (entryValue(hash[identityEvidenceTag(m, "proven_in_session")]) !== opts.sessionId) return "other_session"
  const quando = Date.parse(entryValue(hash[identityEvidenceTag(m, "verified_at")]) ?? "")
  if (Number.isNaN(quando)) return "no_verified_at"
  if (opts.nowMs - quando > maxAgeMs) return "stale"
  const cliente = entryValue(hash[identityEvidenceTag(m, "customer_id")])
  if (!cliente || !opts.customerId) return "no_customer"
  if (cliente !== opts.customerId) return "other_customer"
  return null
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
        message: `o step '${id}' tem resume_requires inválido (${origem}): ${JSON.stringify(efetiva)} — mecanismos exigíveis: ${REQUIRABLE_MECHANISMS.join(", ")}` })
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
