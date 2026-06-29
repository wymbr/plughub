/**
 * dashboard-hooks.ts
 * Hooks and helpers for dashboard template management via Config API.
 *
 * Templates are stored as JSON in the Config API namespace "dashboards"
 * under key "template:{uuid}".  The list is derived by fetching all keys
 * in the namespace and filtering for the "template:" prefix.
 *
 * Personal layout overrides are stored as "layout:{tenant_id}:{user_id}"
 * to avoid clobbering the shared template.
 */
import { useEffect, useState } from 'react'
import type { DashboardCard, DashboardTemplate, NewDashboardCard } from '@/types'
import { getAccessToken } from '@/auth/token-store'

type AnyDashboardCard = DashboardCard | NewDashboardCard

// ─── Config API helpers ───────────────────────────────────────────────────────

const CONFIG_BASE = '/config'    // proxied to config-api (port 3600)

// G-PROBE platform-wide: config-api gateia as mutações em Bearer+ABAC (dashboards →
// namespace `dashboards` → default `config.plataforma`). Manda o Bearer do operador
// (do token-store); o param `adminToken` ficou vestigial (ignorado). GETs abertos.
function cfgHeaders(json = false): Record<string, string> {
  const h: Record<string, string> = {}
  if (json) h['Content-Type'] = 'application/json'
  const t = getAccessToken()
  if (t) h['Authorization'] = `Bearer ${t}`
  return h
}

async function configGet(namespace: string, key: string, _adminToken?: string, tenantId?: string): Promise<unknown> {
  const params = tenantId ? `?tenant_id=${encodeURIComponent(tenantId)}` : ''
  const res = await fetch(`${CONFIG_BASE}/${namespace}/${key}${params}`, { headers: cfgHeaders() })
  if (res.status === 404) return null
  if (!res.ok) throw new Error(`Config GET ${namespace}/${key}: HTTP ${res.status}`)
  const json = await res.json()
  // Config API may return { value, ... } or raw value depending on version
  return json?.value !== undefined ? json.value : json
}

async function configPut(
  namespace: string,
  key: string,
  value: unknown,
  _adminToken: string,
  tenantId?: string,
): Promise<void> {
  const body: Record<string, unknown> = { value }
  if (tenantId) body.tenant_id = tenantId
  const res = await fetch(`${CONFIG_BASE}/${namespace}/${key}`, {
    method: 'PUT',
    headers: cfgHeaders(true),
    body: JSON.stringify(body),
  })
  if (res.status === 401 || res.status === 403) throw new Error(`Config PUT ${namespace}/${key}: HTTP ${res.status} — sem permissão (requer config.plataforma; faça login como admin).`)
  if (!res.ok) throw new Error(`Config PUT ${namespace}/${key}: HTTP ${res.status}`)
}

async function configDelete(namespace: string, key: string, _adminToken: string, tenantId?: string): Promise<void> {
  const params = tenantId ? `?tenant_id=${encodeURIComponent(tenantId)}` : ''
  const res = await fetch(`${CONFIG_BASE}/${namespace}/${key}${params}`, {
    method: 'DELETE',
    headers: cfgHeaders(),
  })
  if (!res.ok && res.status !== 404) throw new Error(`Config DELETE: HTTP ${res.status}`)
}

async function configListNamespace(namespace: string, _adminToken?: string, tenantId?: string): Promise<Record<string, unknown>> {
  const params = tenantId ? `?tenant_id=${encodeURIComponent(tenantId)}` : ''
  const res = await fetch(`${CONFIG_BASE}/${namespace}${params}`, { headers: cfgHeaders() })
  if (!res.ok) throw new Error(`Config list ${namespace}: HTTP ${res.status}`)
  const json = await res.json()
  // Config API returns { tenant_id, namespace, entries: {...} } — unwrap entries
  if (json && typeof json === 'object' && 'entries' in json && typeof json.entries === 'object') {
    return json.entries as Record<string, unknown>
  }
  // Fallback: response is already a flat key→value map (older config-api versions)
  return json
}

// ─── Template CRUD ────────────────────────────────────────────────────────────

/** Fetch all templates for the tenant */
export async function listTemplates(tenantId: string, adminToken?: string): Promise<DashboardTemplate[]> {
  const all = await configListNamespace('dashboards', adminToken, tenantId)
  const templates: DashboardTemplate[] = []
  for (const [key, raw] of Object.entries(all)) {
    if (!key.startsWith('template:')) continue
    const parsed = typeof raw === 'object' && raw !== null && 'value' in raw
      ? (raw as { value: unknown }).value
      : raw
    if (parsed && typeof parsed === 'object') {
      const t = parsed as DashboardTemplate
      if (t.tenant_id === tenantId || !t.tenant_id) templates.push(t)
    }
  }
  return templates.sort((a, b) => a.name.localeCompare(b.name))
}

/** Fetch a single template by ID */
export async function getTemplate(templateId: string, adminToken?: string, tenantId?: string): Promise<DashboardTemplate | null> {
  const raw = await configGet('dashboards', `template:${templateId}`, adminToken, tenantId)
  if (!raw) return null
  return raw as DashboardTemplate
}

/** Save (create or update) a template */
export async function saveTemplate(
  template: DashboardTemplate,
  adminToken: string,
): Promise<void> {
  const key = `template:${template.template_id}`
  await configPut('dashboards', key, template, adminToken, template.tenant_id)
}

/** Delete a template */
export async function deleteTemplate(templateId: string, adminToken: string, tenantId?: string): Promise<void> {
  await configDelete('dashboards', `template:${templateId}`, adminToken, tenantId)
}

// ─── Personal layout override ─────────────────────────────────────────────────

/** Key for a user's personal layout override */
function layoutKey(tenantId: string, userId: string): string {
  return `layout:${tenantId}:${userId}`
}

/** Save the user's personal card positions (layout override) */
export async function savePersonalLayout(
  tenantId: string,
  userId: string,
  cards: AnyDashboardCard[],
  adminToken?: string,
): Promise<void> {
  if (!adminToken) {
    // Fallback: store in localStorage (read-only tenants without admin token)
    localStorage.setItem(`plughub_layout_${tenantId}_${userId}`, JSON.stringify(cards))
    return
  }
  await configPut('dashboards', layoutKey(tenantId, userId), cards, adminToken, tenantId)
}

/** Load the user's personal card positions */
export async function loadPersonalLayout(
  tenantId: string,
  userId: string,
  adminToken?: string,
): Promise<AnyDashboardCard[] | null> {
  try {
    if (adminToken) {
      const raw = await configGet('dashboards', layoutKey(tenantId, userId), adminToken, tenantId)
      if (Array.isArray(raw)) return raw as DashboardCard[]
    }
    // Fallback to localStorage
    const stored = localStorage.getItem(`plughub_layout_${tenantId}_${userId}`)
    return stored ? JSON.parse(stored) : null
  } catch {
    return null
  }
}

// ─── Role catalog (F3: allowlist + starter per role) ──────────────────────────
//
// Per-role config stored under "role_catalog:{role}":
//   { allowed: string[] /* catalog entry ids */, starter_template_id: string | null }
// `allowed` constrains what a user may add (F4 picker) and reconciles existing cards;
// `starter_template_id` is the default dashboard a role lands on (before personal layout).

export interface RoleCatalog {
  allowed:             string[]
  starter_template_id: string | null
}

function roleCatalogKey(role: string): string {
  return `role_catalog:${role}`
}

export async function loadRoleCatalog(tenantId: string, role: string): Promise<RoleCatalog | null> {
  try {
    const raw = await configGet('dashboards', roleCatalogKey(role), undefined, tenantId)
    if (!raw || typeof raw !== 'object') return null
    const r = raw as Partial<RoleCatalog>
    return {
      allowed:             Array.isArray(r.allowed) ? r.allowed.filter(x => typeof x === 'string') : [],
      starter_template_id: typeof r.starter_template_id === 'string' ? r.starter_template_id : null,
    }
  } catch {
    return null
  }
}

export async function saveRoleCatalog(
  tenantId: string,
  role: string,
  catalog: RoleCatalog,
  adminToken?: string,
): Promise<void> {
  await configPut('dashboards', roleCatalogKey(role), catalog, adminToken ?? '', tenantId)
}

// ─── React hooks ──────────────────────────────────────────────────────────────

interface TemplateListState {
  templates: DashboardTemplate[]
  loading:   boolean
  error:     string | null
  reload:    () => void
}

/** Hook: list all templates for the tenant */
export function useTemplates(tenantId: string, adminToken: string): TemplateListState {
  const [templates, setTemplates] = useState<DashboardTemplate[]>([])
  const [loading, setLoading]     = useState(true)
  const [error, setError]         = useState<string | null>(null)
  const [rev, setRev]             = useState(0)

  useEffect(() => {
    if (!tenantId) {
      setLoading(false)
      return
    }
    let cancelled = false
    setLoading(true)
    listTemplates(tenantId, adminToken)
      .then(ts => { if (!cancelled) { setTemplates(ts); setLoading(false) } })
      .catch(e  => { if (!cancelled) { setError(String(e)); setLoading(false) } })
    return () => { cancelled = true }
  }, [tenantId, adminToken, rev])

  return { templates, loading, error, reload: () => setRev(r => r + 1) }
}

interface TemplateState {
  template: DashboardTemplate | null
  loading:  boolean
  error:    string | null
}

/** Hook: load a single template by ID */
export function useTemplate(templateId: string | null, adminToken?: string, tenantId?: string): TemplateState {
  const [template, setTemplate] = useState<DashboardTemplate | null>(null)
  const [loading, setLoading]   = useState(!!templateId)
  const [error, setError]       = useState<string | null>(null)

  useEffect(() => {
    if (!templateId) { setTemplate(null); setLoading(false); return }
    let cancelled = false
    setLoading(true)
    getTemplate(templateId, adminToken, tenantId)
      .then(t => { if (!cancelled) { setTemplate(t); setLoading(false) } })
      .catch(e => { if (!cancelled) { setError(String(e)); setLoading(false) } })
    return () => { cancelled = true }
  }, [templateId, adminToken, tenantId])

  return { template, loading, error }
}

/** Hook: resolve the user's default template ID from module_config */
export function useDefaultTemplateId(
  moduleConfig: Record<string, Record<string, unknown>> | undefined,
): string | null {
  try {
    const dashEntry = moduleConfig?.dashboard?.default_template_id
    if (!dashEntry) return null
    // module_config field may be a ModuleFieldConfig or a plain string
    if (typeof dashEntry === 'string') return dashEntry
    if (typeof dashEntry === 'object' && dashEntry !== null && 'value' in dashEntry) {
      return (dashEntry as { value: unknown }).value as string
    }
    return null
  } catch {
    return null
  }
}
