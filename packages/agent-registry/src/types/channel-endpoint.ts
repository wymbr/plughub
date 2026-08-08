/**
 * channel-endpoint.ts
 * Minimal type shim for ChannelEndpoint until `prisma generate` is run
 * in a network-connected environment.
 *
 * After running `prisma generate`, these types are superseded by the
 * auto-generated @prisma/client types. Remove this file once the
 * generated client includes ChannelEndpoint.
 */

export interface ChannelEndpointRow {
  id:                string
  tenant_id:         string
  channel:           string
  identifier:        string
  pool_id:           string
  display_name:      string
  settings:          Record<string, unknown>
  active:            boolean
  /** external | internal | legacy_token — ADR webhook-endpoint-single-registry, D6 */
  origin:            string
  /** Exige `X-Webhook-Token` no disparo. Default false — ver a migration. */
  auth_required:     boolean
  /** SHA-256 do token. CREDENCIAL: nunca sai na leitura geral (ver `_sanitize`). */
  token_hash:        string | null
  /** 16 primeiros chars do token em claro — identificação, não credencial. */
  token_prefix:      string | null
  gateway_config_id: string | null   // optional FK to GatewayConfig
  created_at:        Date
  updated_at:        Date
}

/** Typed accessor returned by the prisma shim */
export interface ChannelEndpointDelegate {
  findMany(args: {
    where?:   Record<string, unknown>
    orderBy?: Array<Record<string, string>>
  }): Promise<ChannelEndpointRow[]>

  findFirst(args: {
    where: Record<string, unknown>
  }): Promise<ChannelEndpointRow | null>

  create(args: {
    data: Omit<ChannelEndpointRow, 'id' | 'created_at' | 'updated_at'>
  }): Promise<ChannelEndpointRow>

  update(args: {
    where: Record<string, unknown>
    data:  Partial<Omit<ChannelEndpointRow, 'id' | 'tenant_id' | 'channel' | 'identifier' | 'created_at'>>
  }): Promise<ChannelEndpointRow>

  updateMany(args: {
    where: Record<string, unknown>
    data:  Partial<Omit<ChannelEndpointRow, 'id' | 'created_at'>>
  }): Promise<{ count: number }>

  delete(args: {
    where: Record<string, unknown>
  }): Promise<ChannelEndpointRow>
}
