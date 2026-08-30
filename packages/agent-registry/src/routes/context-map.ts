/**
 * routes/context-map.ts — o vocabulário que a tela do pool pode oferecer (D6).
 *
 * Endpoint (exige `x-tenant-id`):
 *   GET /v1/context-map/visibility-options
 *     → { namespaces[], tags[], source: "config" | "builtin", degraded_reason? }
 *
 * ── Por que a derivação NÃO acontece no platform-ui ──────────────────────────
 *
 * O `platform-ui` não depende de `@plughub/schemas` (redefine os contratos à mão em
 * `types/index.ts` — dívida registrada no `CLAUDE.md`), então derivar a lista lá
 * seria uma SEGUNDA leitura da árvore do mapa. Duas leituras é como dois
 * vocabulários nascem, e este arco existe para colapsar sete em um. A derivação
 * mora na casa do mapa (`contextVisibilityOptions`); aqui só se busca e se serve.
 *
 * ── Por que no agent-registry ────────────────────────────────────────────────
 *
 * `context_visibility` é campo de **Pool**, e Pool é deste serviço — é a este
 * serviço que a tela já fala para salvar. O padrão de buscar config do config-api
 * também já existe aqui (`validateMaskedTypeRefs`, portão de deploy do `masked`).
 *
 * ── `source` viaja na resposta, e não é adorno ───────────────────────────────
 *
 * Se o config-api não responder, o mapa EMBUTIDO assume. Isso é aceitável (a lista
 * degrada para a do código, não para vazia), mas **não pode ser mudo**: um tenant
 * que declarou campos próprios veria a lista do default e concluiria que os seus
 * sumiram. A tela avisa, porque a alternativa é o operador editar visibilidade de
 * PII contra um vocabulário que não é o dele.
 */

import { Router, Request, Response, NextFunction } from "express"
import {
  ContextMapSchema,
  DEFAULT_CONTEXT_MAP,
  contextVisibilityOptions,
  type ContextMap,
} from "@plughub/schemas"
import { config } from "../config"

export const contextMapRouter = Router()

function _getTenantId(req: Request): string {
  return (req.headers["x-tenant-id"] as string) ?? "tenant_default"
}

interface LoadedMap { map: ContextMap; source: "config" | "builtin"; reason?: string }

async function loadContextMap(tenantId: string): Promise<LoadedMap> {
  try {
    const base = config.config_api_url.replace(/\/$/, "")
    const url  = `${base}/config/masking?tenant_id=${encodeURIComponent(tenantId)}`
    const resp = await fetch(url, { signal: AbortSignal.timeout(5000) })
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const body    = await resp.json() as Record<string, unknown>
    const entries = (body["entries"] ?? body) as Record<string, unknown>
    const raw     = entries["context_map"]
    const value   = (raw && typeof raw === "object" && "value" in (raw as object))
      ? (raw as { value: unknown }).value
      : raw
    if (value == null) throw new Error("chave masking.context_map ausente")
    const parsed = ContextMapSchema.safeParse(value)
    if (!parsed.success) throw new Error("mapa não passou no schema")
    return { map: parsed.data, source: "config" }
  } catch (err) {
    return { map: DEFAULT_CONTEXT_MAP, source: "builtin", reason: String(err) }
  }
}

contextMapRouter.get("/visibility-options", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const loaded   = await loadContextMap(tenantId)
    if (loaded.source === "builtin") {
      console.warn(
        `[context-map] tenant=${tenantId} servindo opções do mapa EMBUTIDO (${loaded.reason}). ` +
        `Deixa de valer: campos declarados apenas na config do tenant não aparecem no seletor ` +
        `de context_visibility. Nenhuma política muda por isto.`,
      )
    }
    const opts = contextVisibilityOptions(loaded.map)
    return res.json({
      ...opts,
      source: loaded.source,
      ...(loaded.reason ? { degraded_reason: loaded.reason } : {}),
    })
  } catch (err) {
    return next(err)
  }
})
