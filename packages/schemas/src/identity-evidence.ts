/**
 * identity-evidence.ts — PID-02 (2026-09-13)
 *
 * A evidência de identidade (ADR `adr-identity-door-evidence` D4/D5/D6) é um conjunto de
 * tags escalares por mecanismo, gravadas na JOURNEY:
 *
 *   core.journey.identity.<tipo>.status             not_run | pending | verified | failed | expired
 *   core.journey.identity.<tipo>.anchor_kind
 *   core.journey.identity.<tipo>.verified_at
 *   core.journey.identity.<tipo>.source             procedência da âncora
 *   core.journey.identity.<tipo>.proven_in_session  a sessão que provou
 *   core.journey.identity.<tipo>.customer_id        o cliente cuja posse foi provada (PID-09)
 *
 * **Quem verifica grava (D6)**: o único escritor é o do servidor, na mesma chamada que
 * verifica. Todo funil GENÉRICO de escrita (`context_set`, `/api/inject-context`, o
 * `IContextStore` do engine, o `write_context_tags` do Python) RECUSA estes prefixos —
 * senão um fluxo autorado, ou quem alcança a porta, grava "verificado" sem ter provado.
 *
 * Uma casa para a regra; o gêmeo Python (`py-contextstore`) é conferido por gate.
 */

export const RESERVED_IDENTITY_PREFIXES: readonly string[] = ["core.identity.", "core.journey.identity."]

export function isReservedIdentityTag(tag: string): boolean {
  return RESERVED_IDENTITY_PREFIXES.some(p => tag.startsWith(p))
}

export const IDENTITY_EVIDENCE_STATUSES = ["not_run", "pending", "verified", "failed", "expired"] as const
export type IdentityEvidenceStatus = typeof IDENTITY_EVIDENCE_STATUSES[number]

export const IDENTITY_EVIDENCE_FIELDS = ["status", "anchor_kind", "verified_at", "source", "proven_in_session", "customer_id"] as const
export type IdentityEvidenceField = typeof IDENTITY_EVIDENCE_FIELDS[number]

/**
 * Mecanismos que gravam evidência. Tipo novo entra AQUI, junto com o seu escritor.
 *
 *   otp       — `otp_verify` (mcp-server), código ao canal autoritativo
 *   whatsapp  — PID-09: a CHEGADA autenticada pela Meta, cujo `from` é o telefone
 *               autoritativo de um cliente (ADR D9). Quem produz é o adapter que recebeu a
 *               mensagem; quem grava é a rota interna do mcp-server (um escritor só).
 */
export const IDENTITY_MECHANISMS = ["otp", "whatsapp"] as const
export type IdentityMechanism = typeof IDENTITY_MECHANISMS[number]

/**
 * O que um fluxo pode EXIGIR — só `otp`. A chegada pelo WhatsApp não é exigível por nome
 * (decisão do dono, 2026-09-14): ela SATISFAZ a exigência de posse, e declarar
 * `["whatsapp"]` num N3 seria recusar o cliente que chega por outro canal e prova por OTP.
 */
export const REQUIRABLE_MECHANISMS = ["otp"] as const
export type RequirableMechanism = typeof REQUIRABLE_MECHANISMS[number]

/**
 * PID-09 — quem satisfaz cada exigência. `otp` é POSSE DO TELEFONE (ou e-mail) autoritativo
 * do cliente, e a chegada pelo WhatsApp com `from` = telefone autoritativo prova a mesma
 * posse (ADR D9: *"quando o from bate com o telefone cadastrado, é evidência de canal; se
 * for outro número, volta ao OTP"*). A equivalência mora AQUI, e não em cada N3: pisos e
 * configs seguem dizendo `["otp"]`.
 */
export const SATISFIED_BY: Readonly<Record<RequirableMechanism, readonly IdentityMechanism[]>> = {
  otp: ["otp", "whatsapp"],
}

export function identityEvidenceTag(mechanism: IdentityMechanism, field: IdentityEvidenceField): string {
  return `core.journey.identity.${mechanism}.${field}`
}

/**
 * Registro de um mecanismo. `status` é SEMPRE escrito (D4: sem ele, a ausência é ambígua
 * entre "não rodou" e "rodou e falhou"). Os campos da prova só existem quando `verified`;
 * em qualquer outro status o escritor os REMOVE, para que um `failed` de hoje não
 * conviva com o `verified_at` de uma prova anterior.
 */
export interface IdentityEvidenceRecord {
  status:             IdentityEvidenceStatus
  anchor_kind?:       string
  verified_at?:       string
  source?:            string
  proven_in_session?: string
  /**
   * PID-09 — o cliente cuja posse foi provada (id interno, não PII). Sem ele a evidência é
   * "alguém provou alguma coisa nesta sessão", e quem chega pelo próprio WhatsApp e digita o
   * CPF de outra pessoa liberaria as pendências dela. É campo da PROVA: some com ela.
   */
  customer_id?:       string
}

export const PROOF_FIELDS: readonly IdentityEvidenceField[] = ["verified_at", "source", "proven_in_session", "customer_id"]
