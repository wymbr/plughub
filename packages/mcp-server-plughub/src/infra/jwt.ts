/**
 * infra/jwt.ts
 * Utilitários JWT para o ciclo de vida do agente.
 * O session_token carrega tenant_id, agent_type_id e instance_id.
 */

import jwt from "jsonwebtoken"

// ─── Tipos ────────────────────────────────────────────────────────────────────

export interface SessionTokenPayload {
  tenant_id:     string
  agent_type_id: string
  instance_id:   string
  /** Permissões MCP autorizadas — validadas localmente em invoke (~0.1ms). Spec 4.6k. */
  permissions:   string[]
  iat:           number
  exp:           number
}

// ─── Erro específico — permite distinção no handler das tools ─────────────────

export class InvalidTokenError extends Error {
  constructor(message = "JWT inválido ou expirado") {
    super(message)
    this.name = "InvalidTokenError"
  }
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function getSecret(): string {
  const s = process.env["JWT_SECRET"]
  if (!s) {
    // Em produção, JWT_SECRET deve ser definido.
    // Em dev/test, usa segredo fraco com aviso.
    if (process.env["NODE_ENV"] === "production") {
      throw new Error("JWT_SECRET não definido — obrigatório em produção")
    }
    return "dev_secret_change_in_production"
  }
  return s
}

export function signSessionToken(
  payload: Omit<SessionTokenPayload, "iat" | "exp">
): string {
  return jwt.sign(payload, getSecret(), { expiresIn: "1h" })
}

export function verifySessionToken(token: string): SessionTokenPayload {
  try {
    return jwt.verify(token, getSecret()) as SessionTokenPayload
  } catch {
    throw new InvalidTokenError()
  }
}

/**
 * Versão backward-compatible de verifySessionToken.
 * JWTs emitidos antes de 4.6k não têm permissions — defaulta para [].
 */
export function verifySessionTokenSafe(token: string): SessionTokenPayload {
  const p = verifySessionToken(token)
  if (!Array.isArray(p.permissions)) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (p as unknown as Record<string, unknown>)["permissions"] = []
  }
  return p
}

// ─── Token LIGADO À SESSÃO (PID-01, 2026-09-13) ───────────────────────────────
//
// O token acima NÃO serve para as tools de retomada, por três fatos medidos:
//   1. não carrega `session_id` — não diz qual sessão pede (ADR D6);
//   2. `agent_login` é auto-serviço: quem alcança a 3100 cunha um (CAP-10);
//   3. o caminho conversacional (bridge → skill-flow-service) nunca recebeu nenhum.
//
// Este é cunhado SÓ pelo mcp-server, a pedido do bridge na ATIVAÇÃO
// (`POST /internal/session-token`, portão `MCP_INTERNAL_SERVICE_TOKEN`), e o
// `audience` o separa do token de agente: um `session_token` do `agent_login`
// assinado com o MESMO segredo é recusado aqui, e vice-versa não importa.

export const SESSION_BOUND_AUDIENCE = "plughub:session"

export interface SessionBoundPayload {
  tenant_id:   string
  session_id:  string
  instance_id: string
  skill_id:    string
}

/** TTL = duração máxima de um contato (4 h), senão um menu longo expira o token no meio. */
export function sessionBoundTtlS(): number {
  const n = Number(process.env["SESSION_BOUND_TOKEN_TTL_S"] ?? "")
  return Number.isFinite(n) && n > 0 ? n : 14_400
}

export function signSessionBoundToken(payload: SessionBoundPayload): string {
  return jwt.sign(payload, getSecret(), { expiresIn: sessionBoundTtlS(), audience: SESSION_BOUND_AUDIENCE })
}

export function verifySessionBoundToken(token: string): SessionBoundPayload {
  try {
    const p = jwt.verify(token, getSecret(), { audience: SESSION_BOUND_AUDIENCE }) as Partial<SessionBoundPayload>
    if (!p.tenant_id || !p.session_id) throw new InvalidTokenError("token de sessao sem tenant/sessao")
    return {
      tenant_id: p.tenant_id, session_id: p.session_id,
      instance_id: p.instance_id ?? "", skill_id: p.skill_id ?? "",
    }
  } catch (e) {
    throw e instanceof InvalidTokenError ? e : new InvalidTokenError()
  }
}

/** Duração do session_token em milissegundos (para calcular TTL do Redis). */
export const SESSION_TOKEN_TTL_MS = 3_600_000 // 1h
export const SESSION_TOKEN_TTL_S  =     3_600 // 1h em segundos
