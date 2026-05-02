/**
 * platform.ts
 * Installation and Organization context for PlugHub Platform.
 *
 * An Installation is a physical deployment (one docker-compose stack).
 * An Organization groups tenants within an installation — tenants in the
 * same organization can share platform-level resources (calendars, holiday sets).
 *
 * Both IDs are configured statically per deployment via environment variables
 * or the config-api namespace "platform". They are NOT managed via CRUD API.
 *
 * Environment variables:
 *   PLUGHUB_INSTALLATION_ID   e.g. "install-prod-br"
 *   PLUGHUB_ORGANIZATION_ID   e.g. "org-acme-group"
 */

import { z } from "zod"

// ── Installation context ──────────────────────────────────────────────────────

export const InstallationContextSchema = z.object({
  installation_id: z.string().min(1),
  organization_id: z.string().min(1),
})
export type InstallationContext = z.infer<typeof InstallationContextSchema>

// ── Resource scope ────────────────────────────────────────────────────────────
// Determines who can see and use a shared resource (calendar, holiday set, etc.)

export const ResourceScopeSchema = z.enum([
  "installation",  // visible to all organizations in this installation
  "organization",  // visible to all tenants in this organization
  "tenant",        // visible only to the owning tenant
])
export type ResourceScope = z.infer<typeof ResourceScopeSchema>

// ── Platform config (stored in config-api namespace "platform") ───────────────

export const PlatformConfigSchema = z.object({
  installation_id: z.string().min(1),
  organization_id: z.string().min(1),
  // Default timezone for the installation — can be overridden per tenant/calendar
  default_timezone: z.string().default("America/Sao_Paulo"),
})
export type PlatformConfig = z.infer<typeof PlatformConfigSchema>
