/**
 * channel-events.ts
 * Eventos de entrada e saída do Channel Gateway.
 * Fonte da verdade: plughub_spec_v1.docx seção 6
 *
 * InboundEvent   — evento normalizado recebido de qualquer canal
 * OutboundEvent  — mensagem/interação a entregar ao cliente
 * GatewayHeartbeat — heartbeat periódico (Kafka: gateway.heartbeat)
 * ChannelCapabilities — capacidades de renderização por canal
 */

import { z } from "zod"
import { ChannelSchema, MediumTypeSchema, SessionIdSchema } from "./common"
import { MessageContentSchema } from "./message"

// ─────────────────────────────────────────────
// InboundEvent — contato normalizado
// ─────────────────────────────────────────────

/**
 * InboundEvent — emitido pelo Channel Gateway quando chega um novo contato.
 * Publicado em Kafka: conversations.inbound
 *
 * O campo metadata transporta dados arbitrários da conexão (UUI em voz,
 * query-string em webchat, cabeçalhos de e-mail, etc.) sem que o Core
 * precise conhecer o formato específico de cada canal.
 */
export const InboundEventSchema = z.object({
  event_id:    z.string().uuid(),
  gateway_id:  z.string().min(1),
  channel:     ChannelSchema,
  medium:      MediumTypeSchema,
  origin:      z.string().min(1),      // ANI — número/endereço de origem
  destination: z.string().min(1),      // DNIS — número/endereço de destino
  timestamp:   z.string().datetime(),

  /**
   * Dados arbitrários da conexão inbound:
   *   voice/webrtc → UUI, trunk_id, campaign_id
   *   webchat       → url, referrer, session_token, custom_attrs
   *   whatsapp      → profile_name, wa_id
   *   email         → subject, headers
   *   sms           → operator_id
   */
  metadata:    z.record(z.unknown()).default({}),
})
export type InboundEvent = z.infer<typeof InboundEventSchema>

// ─────────────────────────────────────────────
// OutboundEvent — entrega ao cliente
// ─────────────────────────────────────────────

/**
 * OutboundEvent — instrui o Channel Gateway a entregar conteúdo ao cliente.
 * Quando interaction_id está presente, o gateway aguarda o interaction_result
 * do cliente antes de avançar (menu step).
 */
export const OutboundEventSchema = z.object({
  event_id:       z.string().uuid(),
  session_id:     SessionIdSchema,
  gateway_id:     z.string().min(1),
  channel:        ChannelSchema,
  destination:    z.string().min(1),   // endereço de entrega (número, e-mail, etc.)
  content:        MessageContentSchema,
  timestamp:      z.string().datetime(),

  /**
   * Presente em eventos de interaction_request (menu step).
   * O gateway associa o interaction_id à resposta do cliente.
   */
  interaction_id: z.string().uuid().optional(),

  /**
   * Tipo de interação nativa solicitada.
   * O gateway degrada automaticamente para texto quando o canal não suporta.
   */
  interaction_type: z.enum(["text", "button", "list", "checklist", "form"]).optional(),

  /** Opções de interação (button/list/checklist) */
  options: z.array(z.object({
    id:    z.string(),
    label: z.string(),
  })).optional(),

  /** Campos de formulário (form) */
  fields: z.array(z.object({
    id:       z.string(),
    label:    z.string(),
    type:     z.string(),
    required: z.boolean().default(false),
  })).optional(),

  /** Segundos até o timeout de coleta do cliente (0 = imediato, -1 = indefinido) */
  timeout_s: z.number().int().min(-1).optional(),
  /**
   * IDs dos campos que devem ser capturados com input mascarado.
   * Presente quando o step menu tem masked: true (step-level) ou campos
   * individuais com masked: true (field-level).
   * O Channel Gateway usa esta lista para:
   *   - Renderizar o formulário em overlay seguro (webchat)
   *   - Usar <input type="password"> para esses campos
   *   - Executar masked_fallback se o canal não suportar masked input
   */
  masked_fields: z.array(z.string()).optional(),
  /**
   * ALW-10 — `field_id` → id do TIPO do catálogo (`masking.types`), para os
   * campos mascarados. Carrega o TIPO, nunca o MODO já resolvido: resolver na
   * origem congelaria a política no instante do envio, e ela é config viva.
   *
   * Quem consome decide o eco pelo `mascara.display.echo_to_customer` daquele
   * tipo. **Ausente quando não houve campo declarado** — nunca `{}`, que
   * devolveria a ambiguidade que o campo remove.
   */
  masked_types: z.record(z.string()).optional(),
})
export type OutboundEvent = z.infer<typeof OutboundEventSchema>

// ─────────────────────────────────────────────
// ChannelCapabilities — capacidades por canal
// ─────────────────────────────────────────────

/**
 * ChannelCapabilities — declara o que cada canal suporta nativamente.
 * Registrado pelo gateway no Agent Registry na inicialização.
 * Usado pelo Channel Gateway para decidir degradação graceful vs. fallback.
 */
/**
 * MaskedFallbackPolicy — o que fazer quando o canal NÃO sabe mascarar.
 *
 * ── O que saiu daqui, e por quê (NIV-01, 2026-09-03) ────────────────────────
 *
 * Este objeto era `ChannelCapabilitiesSchema`, e declarava CAPACIDADE por canal
 * como booleanos de config por tenant (`supports_masked_input`, `supports_buttons`,
 * `supports_lists`, …). Foi removido por três razões medidas:
 *
 *   1. **Capacidade é fato do PROTOCOLO, não config de tenant.** Ninguém faz o SMS
 *      suportar campo de senha marcando um booleano — e o campo configurável convida
 *      exatamente isso: marca-se `true` para whatsapp e a plataforma acredita.
 *   2. **Era a SEGUNDA casa do mesmo fato, com vocabulário DIFERENTE.** A casa que
 *      roda é `channel_capability_registry.CHANNEL_CAPABILITIES`, chaveada pelo
 *      `ChannelCapabilitySchema` (`skill.ts`) — o vocabulário que `collect.requires[]`
 *      usa. Aqui era `supports_X: boolean`; lá é `X ∈ caps`. Duas respostas para o
 *      mesmo fato significam que a permissiva vale.
 *   3. **As duas já DISCORDAVAM**, em `voice`: este objeto afirmava
 *      `supports_masked_input: true — DTMF nativo`, e o registry não declara
 *      `masked_input` para voz. A divergência estava dormente porque este objeto tinha
 *      **zero consumidores** — inclusive `GatewayConfig.capabilities`, que nunca foi
 *      lido. Dar-lhe um leitor sem resolver isso teria feito `voice` ganhar capacidade
 *      de mascaramento em silêncio.
 *
 * ── O que FICA, e por que não some junto ────────────────────────────────────
 *
 * *Se* o canal sabe mascarar é fato do protocolo. *O que fazer quando não sabe* é
 * decisão de tenant — e continua sendo, aqui. É a distinção que impede este conserto
 * de virar remoção cega: a política nunca foi duplicada, só o inventário.
 *
 * ⚠️ **Ainda sem leitor** — é a NIV-03 que o dá (`select_channel` devolvendo `None`
 * hoje gera `logger.warning` + `return`: o collect não acontece, o que não é entrega
 * nem recusa). Enquanto isso, este schema é declaração, não mecanismo.
 */
export const MaskedFallbackPolicySchema = z.object({
  channel: ChannelSchema,
  /**
   * Comportamento quando o canal não tem a capacidade `masked_input` e um step
   * mascarado chega.
   *   "message"  — envia mensagem configurável ao cliente (padrão MVP)
   *   "link"     — gera URL one-time para webchat seguro (Horizonte 2)
   *   "decline"  — recusa a operação com mensagem de erro
   */
  masked_fallback:         z.enum(["message", "link", "decline"]).default("message"),
  /** Mensagem ao cliente quando `masked_fallback = "message"`. Interpola `{{canal}}`. */
  masked_fallback_message: z.string().optional(),
})
export type MaskedFallbackPolicy = z.infer<typeof MaskedFallbackPolicySchema>

// ─────────────────────────────────────────────
// GatewayConfig — registro de gateway no Agent Registry
// ─────────────────────────────────────────────

/**
 * GatewayConfig — configuração de um gateway de canal.
 * Registrado via API administrativa do Agent Registry.
 * O Routing Engine exclui agentes cujo gateway excedeu o heartbeat TTL (>90s).
 */
export const GatewayConfigSchema = z.object({
  gateway_id:    z.string().min(1),
  tenant_id:     z.string().min(1),
  channels:      z.array(ChannelSchema).min(1),
  /**
   * ⚠️ Era `z.array(ChannelCapabilitiesSchema)` e **nunca foi lido por ninguém**
   * (medido na NIV-01). Passa a carregar a POLÍTICA de fallback, que é o que
   * sobreviveu daquele objeto; a CAPACIDADE mudou de casa para
   * `channel_capability_registry.py`, porque é fato do protocolo e não de tenant.
   */
  capabilities:  z.array(MaskedFallbackPolicySchema).default([]),
  /** URL base do gateway para callbacks internos (mTLS obrigatório) */
  callback_url:  z.string().url().optional(),
  metadata:      z.record(z.unknown()).default({}),
})
export type GatewayConfig = z.infer<typeof GatewayConfigSchema>

// ─────────────────────────────────────────────
// GatewayHeartbeat — sinal de vida periódico
// ─────────────────────────────────────────────

/**
 * GatewayHeartbeat — publicado a cada ~30s pelo Channel Gateway.
 * Kafka topic: gateway.heartbeat
 *
 * O Routing Engine mantém um TTL de 90s por gateway_id.
 * Agentes vinculados a gateways sem heartbeat recente são excluídos
 * da alocação (hard filter).
 */
export const GatewayHeartbeatSchema = z.object({
  gateway_id:       z.string().min(1),
  tenant_id:        z.string().min(1),
  timestamp:        z.string().datetime(),
  active_sessions:  z.number().int().nonnegative(),
  channels:         z.array(ChannelSchema),
  /** Carga normalizada 0–1 (0 = idle, 1 = capacidade máxima) */
  load:             z.number().min(0).max(1).optional(),
})
export type GatewayHeartbeat = z.infer<typeof GatewayHeartbeatSchema>
