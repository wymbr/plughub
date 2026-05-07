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
  id:           string
  tenant_id:    string
  channel:      string
  identifier:   string
  pool_id:      string
  display_name: string
  settings:     Record<string, unknown>
  active:       boolean
  created_at:   Date
  updated_at:   Date
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

  delete(args: {
    where: Record<string, unknown>
  }): Promise<ChannelEndpointRow>
}
