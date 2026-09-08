/**
 * require-resource-write.ts
 * G-PROBE platform-wide — gate DUAL para as mutações de config do Agent Registry.
 *
 * Aplicado aos routers de config (pools, skills, channels, channel-endpoints).
 * Leituras (GET/HEAD/OPTIONS) ficam ABERTAS. Mutações exigem:
 *   - X-Service-Token (callers internos: RegistrySyncer/bootstrap), OU
 *   - Bearer + ABAC do campo DAQUELE router (read_write) — a UI do operador.
 * No-op quando nem service_token nem jwt_secret estão configurados (postura atual,
 * sem auth — preserva dev/test). instances/operational NÃO são gateados (runtime interno).
 *
 * ⚠️ O CAMPO É POR ROUTER, e isso é a MOD-06 (corte #3 da D6, 2026-09-08)
 * ------------------------------------------------------------------------
 * Até aqui os quatro routers exigiam `config.resources` — UM campo em frente a
 * QUATRO telas, três das quais já declaravam campo próprio no menu. Medido ao vivo,
 * com o preset real de cada papel:
 *
 *   PUT  /v1/skills   com o preset do `developer`  -> 403 "requires config.resources"
 *   POST /v1/channels com `config.channels`        -> 403 "requires config.resources"
 *
 * O primeiro é defeito de produto: o Editor de Fluxo aparece no menu do developer
 * (`skill_flows.operacao`, preset admin+developer) e `skill_flows.editar` existe e diz
 * *"Criar e editar skill flows"* — mas quem salvava era `config.resources`, preset
 * ADMIN-ONLY. O developer via a tela e não conseguia gravar.
 *
 * O segundo é o defeito que o config-api já fechou no seu lado em 2026-08-27 (menu em
 * `config.platform`, backend em `config.channels`): o MESMO defeito sobrevivia aqui,
 * um store adiante, porque o censo daquele arco perguntava pelo config-api.
 *
 * Nenhum dos dois fica vermelho sozinho: um vira *"sumiu do menu"*, o outro vira
 * *"salvei e deu erro"*.
 */
import crypto from "crypto"
import type { Request, Response, NextFunction } from "express"
import { config } from "../config"

const RANK: Record<string, number> = { none: 0, read_only: 1, write_only: 1, read_write: 2 }

export function verifyHs256(token: string, secret: string): Record<string, any> {
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

/**
 * Fábrica do gate. O campo é ARGUMENTO porque é fato do ROUTER — a mesma forma do
 * `_NS_FIELD_OVERRIDES` do config-api, que resolve o campo por namespace. Um gate
 * único com o campo fixo dentro obrigaria toda tela a caber no mesmo grant, que é
 * exatamente o que a MOD-06 desfez.
 */
export function requireAbacWrite(modulo: string, campo: string) {
  return function (req: Request, res: Response, next: NextFunction): void {
    const method = req.method.toUpperCase()
    if (method === "GET" || method === "HEAD" || method === "OPTIONS") return next()

    const svc    = config.service_token
    const secret = config.jwt_secret
    if (!svc && !secret) return next()  // auth desabilitada (postura atual)

    // 1) credencial de serviço (callers internos)
    const provided = req.headers["x-service-token"]
    if (svc && provided === svc) return next()

    // 2) Bearer + ABAC `{modulo}.{campo}` (read_write)
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
    const fc = (claims["module_config"]?.[modulo]?.[campo]) ?? {}
    if ((RANK[fc["access"]] ?? 0) < RANK["read_write"]!) {
      // A recusa NOMEIA o campo: "forbidden" seco manda o operador adivinhar qual
      // dos grants falta, e com o campo por router essa adivinhação ficou pior.
      res.status(403).json({ error: "forbidden", message: `requires ${modulo}.${campo} (read_write)` })
      return
    }
    return next()
  }
}

/** Pools e agent types — os recursos de roteamento que a tela Recursos edita. */
export const requireResourceWrite = requireAbacWrite("config", "resources")
