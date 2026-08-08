/**
 * routes/channel-endpoints.ts
 * CRUD for ChannelEndpoint — maps external channel identifiers to pools.
 *
 * Each entry point (WhatsApp number, webchat slug, voice DID, etc.)
 * is a separate record that maps to exactly one pool.
 *
 * Publishes registry.changed on every write so channel-gateway can
 * invalidate its lookup cache.
 */

import { Router, Request, Response, NextFunction } from "express"
import { config }                                   from "../config"
import { prisma }                                   from "../db"
import { publishRegistryChanged }                   from "../infra/kafka"
import { generateEndpointToken }                    from "../lib/endpoint-token"
import type { ChannelEndpointDelegate, ChannelEndpointRow } from "../types/channel-endpoint"

// Typed shim until `prisma generate` is re-run with the updated schema
const channelEndpoint = (prisma as unknown as { channelEndpoint: ChannelEndpointDelegate }).channelEndpoint

export const channelEndpointsRouter = Router()

const VALID_CHANNELS = new Set(["webchat", "whatsapp", "voice", "sms", "email", "webhook"])

/**
 * Procedência (ADR adr-webhook-endpoint-single-registry, D6). Não participa da
 * resolução do endereço — governa apenas quem pode editar a linha na tela.
 */
const VALID_ORIGINS = new Set(["external", "internal", "legacy_token"])

/**
 * ⚠️ `token_hash` é MATERIAL DE CREDENCIAL e não pode sair na leitura geral — este é
 * o mesmo endpoint que a UI consome, então devolvê-lo entregaria o hash a qualquer
 * usuário da tela. Ele sai APENAS para chamador que apresente `x-service-token`, que
 * hoje é o channel-gateway (precisa dele para verificar localmente, em tempo
 * constante, sem um hop extra por disparo).
 *
 * `token_prefix` sai sempre: 16 caracteres de um segredo de 256 bits não estreitam a
 * busca de forma útil, e são o que permite ao operador saber QUAL token está na linha
 * (tela, log, rotação). Prefixo é identificação; hash é credencial.
 *
 * O default é ESCONDER: `_isServiceCaller` precisa provar o contrário. Um sanitizador
 * que só remove quando lembra de remover vaza no primeiro caminho novo.
 */
function _isServiceCaller(req: Request): boolean {
  // `config.service_token`, NÃO `process.env` direto: o middleware
  // `requireResourceWrite` lê daquela mesma fonte, e duas leituras independentes do
  // ambiente divergem em silêncio se o nome da variável mudar num lado só. A
  // divergência aqui teria modo de falha traiçoeiro — o gateway deixaria de receber
  // o `token_hash`, cairia no fail-closed e recusaria TODO disparo autenticado, com
  // a causa a dois serviços de distância do sintoma.
  const expected = config.service_token
  const provided = (req.headers["x-service-token"] as string) || ""
  return expected.length > 0 && provided === expected
}

function _sanitize(ep: ChannelEndpointRow, includeHash: boolean): Record<string, unknown> {
  const { token_hash, ...rest } = ep as ChannelEndpointRow & { token_hash?: string | null }
  return includeHash ? { ...rest, token_hash: token_hash ?? null } : rest
}

// ─────────────────────────────────────────────
// GET /v1/channel-endpoints
// List endpoints for tenant, optionally filtered by channel / pool_id / active
// ─────────────────────────────────────────────
channelEndpointsRouter.get("/", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId       = _getTenantId(req)
    const channel         = req.query["channel"]           as string | undefined
    const identifier      = req.query["identifier"]        as string | undefined
    const poolId          = req.query["pool_id"]           as string | undefined
    const activeQ         = req.query["active"]            as string | undefined
    const gatewayConfigId = req.query["gateway_config_id"] as string | undefined

    const where: Record<string, unknown> = { tenant_id: tenantId }
    if (channel)              where["channel"]          = channel
    if (identifier)           where["identifier"]       = identifier
    if (poolId)               where["pool_id"]          = poolId
    if (activeQ !== undefined) where["active"]          = activeQ === "true"
    if (gatewayConfigId)      where["gateway_config_id"] = gatewayConfigId

    const endpoints = await channelEndpoint.findMany({
      where,
      orderBy: [{ channel: "asc" }, { identifier: "asc" }],
    })

    const includeHash = _isServiceCaller(req)
    return res.json({ endpoints: endpoints.map(e => _sanitize(e, includeHash)) })
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// GET /v1/channel-endpoints/:id
// ─────────────────────────────────────────────
channelEndpointsRouter.get("/:id", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const id       = req.params["id"]!

    const ep = await channelEndpoint.findFirst({ where: { id, tenant_id: tenantId } })
    if (!ep) return res.status(404).json({ error: "Channel endpoint not found" })

    return res.json(_sanitize(ep, _isServiceCaller(req)))
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// POST /v1/channel-endpoints
// ─────────────────────────────────────────────
channelEndpointsRouter.post("/", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const body     = req.body as {
      channel:            string
      identifier:         string
      pool_id:            string
      display_name:       string
      settings?:          Record<string, unknown>
      active?:            boolean
      gateway_config_id?: string | null
      origin?:            string
      auth_required?:     boolean
    }

    if (!body.channel || !VALID_CHANNELS.has(body.channel)) {
      return res.status(400).json({
        error: `invalid channel — must be one of: ${[...VALID_CHANNELS].join(", ")}`,
      })
    }
    if (!body.identifier?.trim())   return res.status(400).json({ error: "identifier is required" })
    if (!body.pool_id?.trim())      return res.status(400).json({ error: "pool_id is required" })
    if (!body.display_name?.trim()) return res.status(400).json({ error: "display_name is required" })

    const origin = (body.origin ?? "external").trim()
    if (!VALID_ORIGINS.has(origin)) {
      return res.status(400).json({
        error: `invalid origin — must be one of: ${[...VALID_ORIGINS].join(", ")}`,
      })
    }

    const identifier = body.identifier.trim()
    const poolId     = body.pool_id.trim()

    // Enforce uniqueness (tenant, channel, identifier) — Prisma unique constraint will also catch it.
    // D8: a mensagem precisa DIZER qual linha colidiu; violação de constraint crua não
    // responde "colidiu com o quê", que é a única informação útil para quem cadastra.
    const existing = await channelEndpoint.findFirst({
      where: { tenant_id: tenantId, channel: body.channel, identifier },
    })
    if (existing) {
      return res.status(409).json({
        error:
          `A channel endpoint for ${body.channel}/${identifier} already exists ` +
          `— aponta para o pool '${existing.pool_id}' (${existing.display_name}, ` +
          `origin=${existing.origin}, id=${existing.id})`,
        conflict: {
          id:           existing.id,
          pool_id:      existing.pool_id,
          display_name: existing.display_name,
          origin:       existing.origin,
        },
      })
    }

    // ── D8 · guard de destino ────────────────────────────────────────────────
    //
    // POOL INEXISTENTE **REPROVA**. Endpoint que aparece na tela e não serve é pior
    // que endpoint ausente: tem aparência de conferido. Não há defesa para apontar
    // para pool que não existe, e até 2026-08-07 este POST validava só PRESENÇA de
    // campo — buraco medido na Fase A do ADR.
    const pool = await prisma.pool.findUnique({
      where: { pool_id_tenant_id: { pool_id: poolId, tenant_id: tenantId } },
    })
    if (!pool) {
      return res.status(400).json({
        error: `pool '${poolId}' não existe neste tenant — endpoint apontaria para destino inexistente`,
      })
    }

    // CANAL NÃO DECLARADO É **AVISO**, não reprovação. Refutado ao vivo em 2026-08-07:
    // `crm-callback` → `retencao_humano` (`[webchat, whatsapp]`) recebeu o disparo, foi
    // entregue a um humano, atendido e encerrado. `router.py:86-92` explica — com
    // `pool_id` explícito, `pools = [pool]` SEM filtro de canal; o filtro vive só no ramo
    // legado de DESCOBERTA. Canal é hard filter sobre DESCOBRIR pool, não sobre pool
    // ENDEREÇADO, e um ChannelEndpoint é precisamente um endereçamento. Reprovar aqui
    // quebraria configuração que funciona — e um portão que reprova o que funciona
    // ensina a ignorar o vermelho.
    const declaredChannels = (pool.channel_types ?? []) as string[]
    if (!declaredChannels.includes(body.channel)) {
      console.warn(
        `[channel-endpoints] AVISO ${body.channel}/${identifier} → pool '${poolId}' ` +
        `não declara '${body.channel}' em channel_types (${JSON.stringify(declaredChannels)}). ` +
        `FUNCIONA (pool endereçado não passa pelo filtro de canal), mas diverge do que a ` +
        `descoberta e o Monitor usam. Higiene, não falha.`,
      )
    }

    // ── Autenticação opcional ────────────────────────────────────────────────
    // `auth_required` nasce false. Quando o operador o liga na criação, o token é
    // gerado AQUI e devolvido em claro UMA vez — não existe "recuperar token", só
    // rotacionar. Ligar sem gerar deixaria o endpoint num estado que exige
    // credencial e não tem nenhuma: recusa tudo, e a recusa não diz isso.
    const authRequired = body.auth_required === true
    const token = authRequired ? generateEndpointToken() : null

    if (authRequired && origin === "internal") {
      // Aviso, não recusa: é config que o operador escolheu, e pode ser legítima
      // num ambiente onde os chamadores internos já carreguem credencial. Mas HOJE
      // nenhum carrega (`workflow_trigger`, o proxy da workflow-api e o bridge
      // disparam sem header), então ligar isto num endpoint interno silencia o
      // disparo interno. Melhor dizer antes do que depurar 401 depois.
      console.warn(
        `[channel-endpoints] AVISO ${body.channel}/${identifier}: auth_required=true ` +
        `num endpoint origin=internal. Os chamadores internos (workflow_trigger, ` +
        `proxy da workflow-api, orchestrator-bridge) NÃO enviam token hoje — este ` +
        `endpoint passará a recusar disparo interno até que eles carreguem a credencial.`,
      )
    }

    const ep = await channelEndpoint.create({
      data: {
        tenant_id:         tenantId,
        channel:           body.channel,
        identifier,
        pool_id:           poolId,
        display_name:      body.display_name.trim(),
        settings:          body.settings ?? {},
        active:            body.active ?? true,
        gateway_config_id: body.gateway_config_id ?? null,
        origin,
        auth_required:     authRequired,
        token_hash:        token?.hash   ?? null,
        token_prefix:      token?.prefix ?? null,
      },
    })

    await publishRegistryChanged(tenantId, "channel_endpoint", ep.id, "created")

    // O `plain` vai no corpo do 201 e em lugar nenhum mais — não é persistido, não
    // é logado, não volta num GET. É a única janela em que ele existe.
    const payload = _sanitize(ep, false)
    if (token) (payload as Record<string, unknown>)["token"] = token.plain
    return res.status(201).json(payload)
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// PUT /v1/channel-endpoints/:id
// Partial update — channel and identifier are immutable after creation
// ─────────────────────────────────────────────
channelEndpointsRouter.put("/:id", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const id       = req.params["id"]!
    const body     = req.body as {
      pool_id?:            string
      display_name?:       string
      settings?:           Record<string, unknown>
      active?:             boolean
      gateway_config_id?:  string | null
    }

    const existing = await channelEndpoint.findFirst({ where: { id, tenant_id: tenantId } })
    if (!existing) return res.status(404).json({ error: "Channel endpoint not found" })

    const updates: Record<string, unknown> = {}
    if (body.pool_id           !== undefined) updates["pool_id"]           = body.pool_id.trim()
    if (body.display_name      !== undefined) updates["display_name"]      = body.display_name.trim()
    if (body.settings          !== undefined) updates["settings"]          = body.settings
    if (body.active            !== undefined) updates["active"]            = body.active
    if (body.gateway_config_id !== undefined) updates["gateway_config_id"] = body.gateway_config_id ?? null

    if (Object.keys(updates).length === 0) {
      return res.status(400).json({ error: "No updatable fields provided" })
    }

    const updated = await channelEndpoint.update({ where: { id }, data: updates })

    await publishRegistryChanged(tenantId, "channel_endpoint", id, "updated")

    return res.json(updated)
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// POST /v1/channel-endpoints/:id/token
// Gera (ou ROTACIONA) o token do endpoint e liga `auth_required`.
//
// Rotacionar INVALIDA o token anterior imediatamente — não há período de graça com
// dois tokens válidos. É uma limitação consciente: sobreposição exigiria guardar N
// hashes e uma data de expiração por hash, e o caso que a justifica (rotação sem
// downtime numa integração de terceiro) ainda não existe. Quando existir, o formato
// da coluna muda; o contrato desta rota, não.
// ─────────────────────────────────────────────
channelEndpointsRouter.post("/:id/token", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const id       = req.params["id"]!

    const existing = await channelEndpoint.findFirst({ where: { id, tenant_id: tenantId } })
    if (!existing) return res.status(404).json({ error: "Channel endpoint not found" })

    const token = generateEndpointToken()
    const updated = await channelEndpoint.update({
      where: { id },
      data: {
        auth_required: true,
        token_hash:    token.hash,
        token_prefix:  token.prefix,
      },
    })

    await publishRegistryChanged(tenantId, "channel_endpoint", id, "updated")

    const payload = _sanitize(updated, false) as Record<string, unknown>
    payload["token"] = token.plain
    payload["warning"] =
      "Guarde este token agora — ele não é armazenado e não pode ser recuperado. " +
      "O token anterior (se havia) foi invalidado."
    return res.status(200).json(payload)
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// DELETE /v1/channel-endpoints/:id/token
// Revoga a credencial E desliga `auth_required`.
//
// ⚠️ POR QUE DESLIGA JUNTO, em vez de só apagar o token. `auth_required=true` sem
// `token_hash` é um estado IMPOSSÍVEL DE SATISFAZER: o endpoint aparece ativo na
// tela e recusa 100% dos disparos, e a recusa (401) diz "sua credencial está errada"
// quando a verdade é "este endpoint não tem credencial nenhuma". Revogar deixando
// esse estado seria fabricar exatamente a classe de defeito que este arco passou a
// sessão inteira removendo — a recusa muda, com a causa errada.
//
// A resposta DIZ que o endpoint ficou anônimo. Quem revoga precisa saber que o
// endereço voltou a aceitar qualquer um, não descobrir depois.
// ─────────────────────────────────────────────
channelEndpointsRouter.delete("/:id/token", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const id       = req.params["id"]!

    const existing = await channelEndpoint.findFirst({ where: { id, tenant_id: tenantId } })
    if (!existing) return res.status(404).json({ error: "Channel endpoint not found" })

    const updated = await channelEndpoint.update({
      where: { id },
      data: { auth_required: false, token_hash: null, token_prefix: null },
    })

    await publishRegistryChanged(tenantId, "channel_endpoint", id, "updated")

    return res.json({
      ...(_sanitize(updated, false) as Record<string, unknown>),
      warning:
        "Token revogado e autenticação DESLIGADA — este endpoint voltou a aceitar " +
        "disparos anônimos. Para mantê-lo protegido, gere um token novo.",
    })
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// DELETE /v1/channel-endpoints/:id
// ─────────────────────────────────────────────
channelEndpointsRouter.delete("/:id", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const id       = req.params["id"]!

    const existing = await channelEndpoint.findFirst({ where: { id, tenant_id: tenantId } })
    if (!existing) return res.status(404).json({ error: "Channel endpoint not found" })

    await channelEndpoint.delete({ where: { id } })
    await publishRegistryChanged(tenantId, "channel_endpoint", id, "deleted")

    return res.status(204).send()
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────
function _getTenantId(req: Request): string {
  return (req.headers["x-tenant-id"] as string) ?? "tenant_default"
}
