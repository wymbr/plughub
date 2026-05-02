/**
 * jwt-helper.ts
 * JWT mint helper for E2E tests.
 * Produces HS256 tokens compatible with the Channel Gateway's jwt_secret.
 */

import jwt from "jsonwebtoken"
import { randomUUID } from "crypto"

export interface WebchatTokenClaims {
  sub:        string   // contact_id
  session_id: string
  tenant_id:  string
}

/**
 * Mint a webchat JWT signed with HS256.
 * The Channel Gateway's WebchatAdapter.decode_token() expects exactly these claims.
 */
export function mintWebchatToken(params: {
  contactId:      string
  sessionId:      string
  tenantId:       string
  jwtSecret:      string
  expiresInSecs?: number
}): string {
  const { contactId, sessionId, tenantId, jwtSecret, expiresInSecs = 3600 } = params

  return jwt.sign(
    {
      sub:        contactId,
      session_id: sessionId,
      tenant_id:  tenantId,
    } satisfies WebchatTokenClaims,
    jwtSecret,
    { expiresIn: expiresInSecs, algorithm: "HS256" }
  )
}

/** Convenience: generate fresh IDs and mint a token in one call. */
export function mintFreshWebchatToken(params: {
  tenantId:  string
  jwtSecret: string
}): { token: string; contactId: string; sessionId: string } {
  const contactId = `cid-e2e-${randomUUID()}`
  const sessionId = `sess-e2e-${randomUUID()}`
  const token = mintWebchatToken({
    contactId,
    sessionId,
    tenantId: params.tenantId,
    jwtSecret: params.jwtSecret,
  })
  return { token, contactId, sessionId }
}
