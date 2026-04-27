/**
 * gateway-config.ts
 * Minimal type shim for GatewayConfig until `prisma generate` is run
 * in a network-connected environment.
 *
 * After running `prisma generate`, these types are superseded by the
 * auto-generated @prisma/client types. Remove this file once the
 * generated client includes GatewayConfig.
 */

export interface GatewayConfigRow {
  id:           string
  tenant_id:    string
  channel:      string
  display_name: string
  active:       boolean
  credentials:  Record<string, unknown>
  settings:     Record<string, unknown>
  created_at:   Date
  updated_at:   Date
  created_by:   string
}

/** Typed accessor returned by the prisma shim */
export interface GatewayConfigDelegate {
  findMany(args: {
    where?:   Record<string, unknown>
    orderBy?: Array<Record<string, string>>
  }): Promise<GatewayConfigRow[]>

  findFirst(args: {
    where: Record<string, unknown>
  }): Promise<GatewayConfigRow | null>

  create(args: {
    data: Omit<GatewayConfigRow, 'id' | 'created_at' | 'updated_at'>
  }): Promise<GatewayConfigRow>

  update(args: {
    where: Record<string, unknown>
    data:  Partial<Omit<GatewayConfigRow, 'id' | 'tenant_id' | 'created_at'>>
  }): Promise<GatewayConfigRow>

  delete(args: {
    where: Record<string, unknown>
  }): Promise<GatewayConfigRow>
}
