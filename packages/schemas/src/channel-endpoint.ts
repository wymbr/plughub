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
])
export type ChannelEndpointChannel = z.infer<typeof ChannelEndpointChannelSchema>

/**
 * ChannelEndpoint — a single external entry point mapped to a pool.
 *
 * identifier semantics per channel:
 *   webchat   — URL slug, e.g. "support", "sales" → URL: {host}/webchat/{slug}
 *   whatsapp  — E.164 phone number, e.g. "+5511999999999"
 *   voice     — DID / E.164, e.g. "+5511000000"
 *   sms       — short code or long code, e.g. "55119"
 *   email     — address, e.g. "support@company.com"
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

export const UpdateChannelEndpointSchema = ChannelEndpointSchema.omit({
  id: true,
  tenant_id: true,
  channel: true,
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
