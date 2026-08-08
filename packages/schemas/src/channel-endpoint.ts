/**
 * channel-endpoint.ts
 * Zod schemas for ChannelEndpoint — maps an external channel address
 * (WhatsApp number, webchat slug, voice DID, etc.) to a pool.
 *
 * Managed by agent-registry. Consumed by channel-gateway for inbound routing.
 */
import { z } from "zod"

/** Supported channel types — mirrors ChannelSchema in common.ts */
export const ChannelEndpointChannelSchema = z.enum([
  "webchat",
  "whatsapp",
  "voice",
  "sms",
  "email",
  "webhook",
])
export type ChannelEndpointChannel = z.infer<typeof ChannelEndpointChannelSchema>

/**
 * Procedência da linha (ADR adr-webhook-endpoint-single-registry, D6).
 *
 *   external     — cadastrada pelo tenant. Editável na tela.
 *   internal     — declarada no provisionamento para um pool interno. VISÍVEL e
 *                  read-only: nasce da declaração do ambiente, não do cadastro.
 *   legacy_token — migrada do registro por token (`workflow.webhooks`).
 *
 * Existe porque a D2 torna o `identifier` OPACO: nada o interpreta. Sem este campo,
 * "esta linha é interna" só seria decidível lendo o texto do identificador — a
 * semântica que a D2 retira. Não participa da resolução do endereço.
 */
export const ChannelEndpointOriginSchema = z.enum([
  "external",
  "internal",
  "legacy_token",
])
export type ChannelEndpointOrigin = z.infer<typeof ChannelEndpointOriginSchema>

/**
 * ChannelEndpoint — a single external entry point mapped to a pool.
 *
 * identifier semantics per channel:
 *   webchat   — URL slug, e.g. "support", "sales" → URL: {host}/webchat/{slug}
 *   whatsapp  — E.164 phone number, e.g. "+5511999999999"
 *   voice     — DID / E.164, e.g. "+5511000000"
 *   sms       — short code or long code, e.g. "55119"
 *   email     — address, e.g. "support@company.com"
 *   webhook   — URL slug, e.g. "salesforce", "erp" → URL: {host}/channel/webhook/{slug}
 *               NOTE: distinct from workflow webhooks (Arc 4) which trigger skill flows directly.
 *               Channel webhooks route inbound contacts to a pool via the routing engine.
 */
export const ChannelEndpointSchema = z.object({
  id:           z.string().uuid(),
  tenant_id:    z.string(),
  channel:      ChannelEndpointChannelSchema,
  identifier:   z.string().min(1),
  pool_id:      z.string(),
  display_name: z.string(),
  /** Per-instance overrides of channel-level defaults (e.g. auth_timeout_s for webchat) */
  settings:     z.record(z.unknown()).optional().default({}),
  active:       z.boolean().default(true),
  /** Procedência — governa edição na tela, NUNCA a resolução do endereço */
  origin:       ChannelEndpointOriginSchema.default("external"),
  /**
   * Exige `X-Webhook-Token` no disparo. Default **false**: ligar por padrão
   * converteria todo endpoint em uso num 401 retroativo. O antídoto do "opt-in que
   * ninguém liga" é a ausência MEDIDA (probe + tela), não o default agressivo.
   */
  auth_required: z.boolean().default(false),
  /**
   * 16 primeiros caracteres do token em claro — identificação, não credencial.
   * Permite saber QUAL token está na linha sem dar material de busca.
   *
   * ⚠️ `token_hash` NÃO aparece neste schema de propósito. Ele é material de
   * credencial e só é devolvido a chamador com `x-service-token` (o channel-gateway);
   * declará-lo aqui convidaria consumidores de UI a esperá-lo.
   */
  token_prefix:  z.string().nullable().optional(),
  created_at:   z.string().datetime().optional(),
  updated_at:   z.string().datetime().optional(),
})
export type ChannelEndpoint = z.infer<typeof ChannelEndpointSchema>

export const CreateChannelEndpointSchema = ChannelEndpointSchema.omit({
  id: true,
  created_at: true,
  updated_at: true,
})
export type CreateChannelEndpoint = z.infer<typeof CreateChannelEndpointSchema>

// `origin` sai do update junto de `channel`/`identifier`: procedência é fato de
// nascimento da linha, não atributo editável. Trocá-la depois converteria um endpoint
// declarado em cadastrado (ou o inverso) sem que nada tivesse mudado na declaração.
// `token_prefix` sai do update junto de `origin`: é derivado do token, e o token só
// muda pelas rotas dedicadas (`POST /:id/token` gera/rotaciona, `DELETE /:id/token`
// revoga). Deixá-lo editável permitiria descrever uma credencial que não existe.
export const UpdateChannelEndpointSchema = ChannelEndpointSchema.omit({
  id: true,
  tenant_id: true,
  channel: true,
  origin: true,
  token_prefix: true,
  created_at: true,
  updated_at: true,
}).partial()
export type UpdateChannelEndpoint = z.infer<typeof UpdateChannelEndpointSchema>

/** Query params for GET /v1/channel-endpoints */
export const ChannelEndpointQuerySchema = z.object({
  channel:   ChannelEndpointChannelSchema.optional(),
  pool_id:   z.string().optional(),
  active:    z.coerce.boolean().optional(),
})
export type ChannelEndpointQuery = z.infer<typeof ChannelEndpointQuerySchema>
