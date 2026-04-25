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
export const ChannelCapabilitiesSchema = z.object({
  channel:             ChannelSchema,
  supports_buttons:    z.boolean().default(false),
  /** Limite de botões exibíveis simultaneamente (WhatsApp: 3, webchat: sem limite) */
  max_buttons:         z.number().int().positive().optional(),
  supports_lists:      z.boolean().default(false),
  supports_checklist:  z.boolean().default(false),
  supports_form:       z.boolean().default(false),
  supports_rich_media: z.boolean().default(false),  // image, video, audio
  supports_location:   z.boolean().default(false),
  supports_template:   z.boolean().default(false),
})
export type ChannelCapabilities = z.infer<typeof ChannelCapabilitiesSchema>

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
  capabilities:  z.array(ChannelCapabilitiesSchema).default([]),
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
