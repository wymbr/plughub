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
 * ⚠️ RECUSA: `auth_required` em linha `origin=internal`.
 *
 * Era AVISO até 2026-08-10 ("os chamadores internos ainda não mandam header"), o que
 * a enquadrava como pendência de plumbing. O inventário estático dos discadores
 * (fatia 3 do arco de auth) mostrou que a premissa estava errada e que a config é
 * **inútil, não prematura**:
 *
 *   · dos dez identificadores `internal` do demo, NOVE não têm chamador algum nesta
 *     porta — os pools deles são disparados por `/v1/channels/webhook/pool/{id}`;
 *   · essa porta por POOL não passa pelo registro e **não tem onde pendurar token**
 *     (ADR §7.6.1: registrar pool seria a função identidade do pool).
 *
 * Daí o argumento, que é estrutural e não de risco: se `/v1/*` está exposto na borda,
 * a porta por pool está junto e todo pool webhook segue disparável anonimamente —
 * `auth_required` aqui é teatro. Se não está exposto, os internos são inalcançáveis
 * de fora — `auth_required` aqui é redundante. Nos DOIS ramos compra zero, e no
 * primeiro ainda custa: silencia o disparo interno em troca de nada.
 *
 * Recusamos em vez de avisar porque um portão que aceita configuração inútil e
 * perigosa ensina a ignorar o vermelho — o mesmo critério que rebaixou a validação de
 * canal a aviso na D8 (lá a config FUNCIONAVA; aqui ela quebra e não protege).
 *
 * **Reabrir isto exige mudar a premissa, não este arquivo:** dar endereço registrável
 * (logo, credenciável) à porta por pool, ou fechá-la.
 */
/**
 * ⚠️ RECUSA: `auth_required` em canal que não é `webhook`.
 *
 * A flag só é LIDA por `_check_endpoint_auth`, no channel-gateway, e essa função só é
 * chamada nas duas rotas de webhook (`/channel/webhook/{slug}` e
 * `/v1/channels/webhook/{identifier}`). Webchat, WhatsApp, voz, SMS e e-mail resolvem
 * o endpoint por `resolve_pool`, que **não consulta `auth_required`** — cada um tem o
 * seu próprio handshake (JWT por tenant no webchat, assinatura no WhatsApp).
 *
 * Aceitar a flag ali gravaria uma linha que **afirma proteção que ninguém aplica**, e
 * a tela mostraria "token" nela. É o mesmo critério do §7.9, e o pior dos dois mundos:
 * anônimo declarado é honesto; protegido-mentiroso convida a parar de procurar.
 *
 * Estender enforcement aos demais canais é arco próprio (handshakes distintos), não
 * uma linha aqui.
 */
const NON_WEBHOOK_AUTH_REFUSAL = (channel: string) => ({
  error: `auth_required não é aplicável ao canal '${channel}'`,
  reason: "auth_enforced_on_webhook_only",
  detail:
    `A verificação de X-Webhook-Token só roda nas rotas de webhook do channel-gateway; ` +
    `um endpoint '${channel}' com a flag ligada apareceria como protegido na tela sem que ` +
    `nada verificasse a credencial. O canal '${channel}' tem handshake próprio. ` +
    `Ver ADR adr-webhook-endpoint-single-registry §7.10.`,
})

const INTERNAL_AUTH_REFUSAL = {
  error: "auth_required não é aplicável a endpoint origin=internal",
  reason: "internal_endpoint_auth_is_topology",
  detail:
    "Endereço interno é protegido pela TOPOLOGIA (restrição do prefixo /v1/* na borda), " +
    "não por token. Ligar auth_required aqui silencia o disparo interno e não fecha nada: " +
    "o mesmo pool continua acionável por /v1/channels/webhook/pool/{id}, que não passa " +
    "pelo registro e não pode carregar credencial. Ver ADR adr-webhook-endpoint-single-registry §7.9.",
} as const

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

    // ── Autenticação ─────────────────────────────────────────────────────────
    // Quando ligada na criação, o token é gerado AQUI e devolvido em claro UMA vez —
    // não existe "recuperar token", só rotacionar. Ligar sem gerar deixaria o endpoint
    // num estado que exige credencial e não tem nenhuma: recusa tudo, e a recusa não
    // diz isso.
    if (body.auth_required === true && origin === "internal") {
      return res.status(422).json(INTERNAL_AUTH_REFUSAL)
    }
    if (body.auth_required === true && body.channel !== "webhook") {
      return res.status(422).json(NON_WEBHOOK_AUTH_REFUSAL(body.channel))
    }

    // ── Fatia 4 · webhook EXTERNO exige decisão EXPLÍCITA (ADR §7.10) ────────
    //
    // Nem default ON nem default OFF: **sem default**. O route serve dois chamadores
    // de naturezas diferentes e não consegue distingui-los — o operador pela UI RECEBE
    // o token (o corpo do 201 é a única janela em que ele existe), e o `RegistrySyncer`
    // faz o POST a partir do YAML e **descarta o corpo**. Um default ON criaria, em
    // instalação limpa, endpoint exigindo um token que ninguém recebeu: 401 permanente,
    // sem caminho de recuperação. Um default OFF é o opt-in que a fatia 1 já
    // diagnosticou como frágil — proteção que ninguém liga.
    //
    // "Este chamador consegue guardar um segredo?" é informação que só existe NO
    // CHAMADOR. Então o route para de adivinhar e exige a declaração. Mesma família do
    // "declarado, não derivado" da Fase B: quando a autoridade está fora, importe-a em
    // vez de inferi-la.
    //
    // Escopo estreito de propósito: só `webhook` (é o único canal onde a flag é
    // aplicada) e só `external` (em `internal` a resposta já é a recusa acima, §7.9).
    if (body.channel === "webhook" && origin === "external" && body.auth_required === undefined) {
      return res.status(422).json({
        error: "auth_required é obrigatório para endpoint webhook de origem external",
        reason: "explicit_auth_decision_required",
        detail:
          "Declare auth_required: true (o 201 devolve o token EM CLARO, uma única vez — " +
          "guarde-o na hora) ou false (endpoint anônimo, acionável por qualquer um que " +
          "alcance o gateway). Não há default: quem cria pela UI consegue receber o token, " +
          "quem provisiona por YAML/script não — e este route não distingue os dois. " +
          "Ver ADR adr-webhook-endpoint-single-registry §7.10.",
      })
    }

    const authRequired = body.auth_required === true
    const token = authRequired ? generateEndpointToken() : null

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

    // Mesmo veredicto do create, e esta é a metade que importa mais: a tela já não
    // oferece o botão em linha `internal` (Fase D), mas read-only da TELA não é
    // read-only da API (§7.6.4) — sem esta checagem o caminho continuava aberto por
    // curl, que é exatamente como todo disparo deste arco foi feito.
    if (existing.origin === "internal") {
      return res.status(422).json(INTERNAL_AUTH_REFUSAL)
    }
    // Simétrico ao create: gerar token numa linha webchat/whatsapp/voz ligaria uma flag
    // que nenhuma rota daqueles canais consulta. Sem esta metade, o caminho continuava
    // aberto por PUT-equivalente — a mesma lição do §7.9 sobre read-only da tela.
    if (existing.channel !== "webhook") {
      return res.status(422).json(NON_WEBHOOK_AUTH_REFUSAL(existing.channel))
    }

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
