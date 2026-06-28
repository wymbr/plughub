/**
 * require-knowledge-access.ts
 * G-PROBE platform-wide — gate DUAL para o surface REST de knowledge.
 *
 *   - X-Service-Token (caller interno: publish de CalibrationNote da evaluation-api), OU
 *   - Bearer + ABAC `evaluation.gerir_rubrica` (read p/ search, read_write p/ snippets) — a UI.
 * No-op quando service_token e jwt_secret estão vazios (preserva o modo dev sem auth).
 * O /admin/* e os MCP tools NÃO passam por aqui (gate próprio / transporte MCP).
 */
import crypto from "crypto"
import type { Request, Response, NextFunction, RequestHandler } from "express"

const RANK: Record<string, number> = { none: 0, read_only: 1, write_only: 1, read_write: 2 }

export interface KnowledgeGateOpts {
  serviceToken: string
  jwtSecret:    string
}

function verifyHs256(token: string, secret: string): Record<string, any> {
  const parts = token.split(".")
  if (parts.length !== 3) throw new Error("malformed token")
  const [h, p, sig] = parts as [string, string, string]
  const expected = crypto.createHmac("sha256", secret).update(`${h}.${p}`).digest("base64url")
  const a = Buffer.from(sig)
  const b = Buffer.from(expected)
  if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) throw new Error("invalid signature")
  const payload = JSON.parse(Buffer.from(p, "base64url").toString("utf8")) as Record<string, any>
  if (payload["exp"] && Number(payload["exp"]) < Math.floor(Date.now() / 1000)) {
    throw new Error("token expired")
  }
  return payload
}

/** Middleware factory: `write` exige read_write em evaluation.gerir_rubrica; senão read_only. */
export function requireKnowledgeAccess(opts: KnowledgeGateOpts, write: boolean): RequestHandler {
  return (req: Request, res: Response, next: NextFunction): void => {
    const { serviceToken: svc, jwtSecret: secret } = opts
    if (!svc && !secret) return next()  // auth desabilitada (dev)

    const provided = req.headers["x-service-token"]
    if (svc && provided === svc) return next()

    const auth = (req.headers["authorization"] as string | undefined) ?? ""
    if (!auth.startsWith("Bearer ")) {
      res.status(401).json({ error: "unauthorized", message: "missing service token or Bearer" })
      return
    }
    if (!secret) {
      res.status(503).json({ error: "jwt_not_configured" })
      return
    }
    let claims: Record<string, any>
    try {
      claims = verifyHs256(auth.slice("Bearer ".length), secret)
    } catch (e) {
      res.status(401).json({ error: "invalid_token", message: e instanceof Error ? e.message : "invalid" })
      return
    }
    const need = write ? "read_write" : "read_only"
    const fc = (claims["module_config"]?.["evaluation"]?.["gerir_rubrica"]) ?? {}
    if ((RANK[fc["access"]] ?? 0) < RANK[need]!) {
      res.status(403).json({ error: "forbidden", message: `requires evaluation.gerir_rubrica (${need})` })
      return
    }
    return next()
  }
}
