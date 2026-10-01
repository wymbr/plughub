/**
 * routes/a2a-cards.ts — GET /v1/a2a-cards/:identifier?base_url=… (AAS-03).
 *
 * Só LEITURA, e só a do card PÚBLICO: o mesmo conteúdo que o gateway publica sem
 * credencial em `{base}/a2a/{slug}/.well-known/agent-card.json`. Fora do portão de escrita,
 * como o resto das leituras do registry.
 *
 *   200 → o card
 *   404 → `{ reason }` — o motivo vai ao LOG do gateway, nunca ao chamador externo
 *   400 → `base_url` ausente ou não http(s): quem chama é que está mal configurado
 *
 * `base_url` vem do chamador (o gateway, que conhece a borda pública) porque o registry não
 * sabe por qual host o mundo o alcança — e o card precisa da URL que o cliente vai usar.
 */
import { Router, Request, Response, NextFunction } from "express"

import { A2A_SLUG_RE, resolveA2ACard } from "../lib/a2a-card"

export const a2aCardsRouter = Router()

a2aCardsRouter.get("/:identifier", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId   = (req.headers["x-tenant-id"] as string) ?? "tenant_default"
    const identifier = req.params["identifier"]!
    const baseUrl    = (req.query["base_url"] as string | undefined)?.trim() ?? ""

    if (!/^https?:\/\/[^\s/]+/.test(baseUrl)) {
      return res.status(400).json({
        error:  "base_url obrigatório (http/https): é a URL pública pela qual o cliente alcança /a2a",
        reason: "base_url_missing",
      })
    }
    // Slug fora do formato nunca foi gravado (o cadastro recusa), então é "não existe".
    if (!A2A_SLUG_RE.test(identifier)) return res.status(404).json({ reason: "endpoint_not_found" })

    const r = await resolveA2ACard(tenantId, identifier, baseUrl)
    if ("refusal" in r) {
      return res.status(404).json({ reason: r.refusal, pool_id: r.pool_id ?? null, detail: r.detail ?? null })
    }
    return res.json(r.card)
  } catch (err) {
    return next(err)
  }
})
