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

const RANK: Record<string, number> = { none: 0, read_only: 1, read_write: 2 }

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
 * ⚠️ TENANT e AUTOR são fatos da CREDENCIAL, não do header (PID-07, 2026-09-13)
 * ---------------------------------------------------------------------------
 * O gate conferia o GRANT e deixava o tenant e o autor para os headers
 * `x-tenant-id` / `x-user-id`, que os routers liam direto. Medido ao vivo: um token
 * de `tenant_outro` com `x-tenant-id: tenant_demo` GRAVOU o slot `next` de um pool
 * do tenant_demo (200) — e isso valia para os quatro routers, porque nenhum deles
 * sabia de onde o tenant vinha. E o autor: a UI nunca mandou `x-user-id`, então os
 * 34 slots gravados pela tela ficaram como `system`, e quem mandasse o header
 * escolhia o nome que quisesse.
 *
 * Regra: com Bearer, o tenant é o `tenant_id` do token. Header divergente é RECUSADO
 * (403 `tenant_mismatch`), nunca reescrito calado; header ausente é preenchido com o
 * do token, para que os routers, que continuam lendo o header, leiam o certo. O autor
 * é o `email` (ou `sub`) do token, e o `x-user-id` deixa de valer para usuário.
 * Credencial de SERVIÇO é identidade irrestrita: ela diz em nome de quem age
 * (`x-user-id`, ex. `registry-syncer`), e o tenant continua vindo do header.
 */
export interface WritePrincipal {
  kind:      "user" | "service" | "unauthenticated"
  tenant_id: string
  author:    string
}

type RequestWithPrincipal = Request & { writePrincipal?: WritePrincipal }

const DEFAULT_TENANT = "tenant_default"

function headerOf(req: Request, name: string): string | undefined {
  const v = req.headers[name]
  return typeof v === "string" && v !== "" ? v : undefined
}

/**
 * Quem escreveu. UMA casa: os routers liam cada um o seu `x-user-id`, e quatro cópias
 * da mesma leitura são quatro lugares para esquecer a credencial.
 * Fora de uma escrita que passou pelo gate (não deveria acontecer), cai no header
 * como antes — e é o censo do probe que garante que toda escrita passa pelo gate.
 */
export function authorOf(req: Request): string {
  const p = (req as RequestWithPrincipal).writePrincipal
  if (p) return p.author
  return headerOf(req, "x-user-id") ?? "system"
}

let warnedOpen = false

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

    const r      = req as RequestWithPrincipal
    const svc    = config.service_token
    const secret = config.jwt_secret
    if (!svc && !secret) {
      // auth desabilitada (dev/test). Degradação que não pode ser muda: é o estado
      // em que qualquer um grava em qualquer tenant.
      if (!warnedOpen) {
        warnedOpen = true
        console.warn(
          "[agent-registry] escrita SEM portão: nem AGENT_REGISTRY_SERVICE_TOKEN nem " +
          "PLUGHUB_JWT_SECRET configurados — tenant e autor vêm dos headers, sem verificação",
        )
      }
      r.writePrincipal = {
        kind:      "unauthenticated",
        tenant_id: headerOf(req, "x-tenant-id") ?? DEFAULT_TENANT,
        author:    headerOf(req, "x-user-id") ?? "system",
      }
      return next()
    }

    // 1) credencial de serviço (callers internos)
    const provided = req.headers["x-service-token"]
    if (svc && provided === svc) {
      r.writePrincipal = {
        kind:      "service",
        tenant_id: headerOf(req, "x-tenant-id") ?? DEFAULT_TENANT,
        author:    headerOf(req, "x-user-id") ?? "service",
      }
      return next()
    }

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

    const claimTenant = typeof claims["tenant_id"] === "string" ? claims["tenant_id"] : ""
    if (!claimTenant) {
      res.status(403).json({ error: "tenant_claim_missing", message: "token sem tenant_id — não há tenant em nome do qual gravar" })
      return
    }
    const headerTenant = headerOf(req, "x-tenant-id")
    if (headerTenant !== undefined && headerTenant !== claimTenant) {
      res.status(403).json({
        error:   "tenant_mismatch",
        message: `x-tenant-id '${headerTenant}' difere do tenant do token '${claimTenant}'`,
      })
      return
    }
    req.headers["x-tenant-id"] = claimTenant
    const author = [claims["email"], claims["sub"]].find((v) => typeof v === "string" && v !== "") as string | undefined
    if (!author) {
      // Um autor inventado seria o `system` de antes com outro nome.
      res.status(403).json({ error: "subject_claim_missing", message: "token sem email nem sub — não há autor a registrar" })
      return
    }
    r.writePrincipal = { kind: "user", tenant_id: claimTenant, author }
    return next()
  }
}

/** Pools e agent types — os recursos de roteamento que a tela Recursos edita. */
export const requireResourceWrite = requireAbacWrite("config", "resources")
