/**
 * require-resource-write.ts
 * G-PROBE platform-wide — gate DUAL para as mutações de config do Agent Registry.
 *
 * Aplicado aos routers de config (pools, skills, channels, channel-endpoints, pool-slots).
 * Leituras (GET/HEAD/OPTIONS) ficam ABERTAS. Mutações exigem:
 *   - X-Service-Token (callers internos: RegistrySyncer/bootstrap), OU
 *   - Bearer + ABAC `config.resources` (read_write) — a UI do operador.
 * No-op quando nem service_token nem jwt_secret estão configurados (postura atual,
 * sem auth — preserva dev/test). instances/operational NÃO são gateados (runtime interno).
 */
import crypto from "crypto"
import type { Request, Response, NextFunction } from "express"
import { config } from "../config"

const RANK: Record<string, number> = { none: 0, read_only: 1, write_only: 1, read_write: 2 }

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

export function requireResourceWrite(req: Request, res: Response, next: NextFunction): void {
  const method = req.method.toUpperCase()
  if (method === "GET" || method === "HEAD" || method === "OPTIONS") return next()

  const svc    = config.service_token
  const secret = config.jwt_secret
  if (!svc && !secret) return next()  // auth desabilitada (postura atual)

  // 1) credencial de serviço (callers internos)
  const provided = req.headers["x-service-token"]
  if (svc && provided === svc) return next()

  // 2) Bearer + ABAC config.resources (read_write)
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
  const fc = (claims["module_config"]?.["config"]?.["resources"]) ?? {}
  if ((RANK[fc["access"]] ?? 0) < RANK["read_write"]!) {
    res.status(403).json({ error: "forbidden", message: "requires config.resources (read_write)" })
    return
  }
  return next()
}
