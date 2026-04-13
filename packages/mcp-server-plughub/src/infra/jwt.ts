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

/** Duração do session_token em milissegundos (para calcular TTL do Redis). */
export const SESSION_TOKEN_TTL_MS = 3_600_000 // 1h
export const SESSION_TOKEN_TTL_S  =     3_600 // 1h em segundos
