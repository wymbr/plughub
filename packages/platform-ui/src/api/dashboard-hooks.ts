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
import type { DashboardCard, DashboardTemplate } from '@/types'

// ─── Config API helpers ───────────────────────────────────────────────────────

const CONFIG_BASE = '/config'    // proxied to config-api (port 3600)

async function configGet(namespace: string, key: string, adminToken?: string): Promise<unknown> {
  const headers: Record<string, string> = {}
  if (adminToken) headers['X-Admin-Token'] = adminToken
  const res = await fetch(`${CONFIG_BASE}/${namespace}/${key}`, { headers })
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
  adminToken: string,
  tenantId?: string,
): Promise<void> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    'X-Admin-Token': adminToken,
  }
  const body: Record<string, unknown> = { value }
  if (tenantId) body.tenant_id = tenantId
  const res = await fetch(`${CONFIG_BASE}/${namespace}/${key}`, {
    method: 'PUT',
    headers,
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`Config PUT ${namespace}/${key}: HTTP ${res.status}`)
}

async function configDelete(namespace: string, key: string, adminToken: string): Promise<void> {
  const res = await fetch(`${CONFIG_BASE}/${namespace}/${key}`, {
    method: 'DELETE',
    headers: { 'X-Admin-Token': adminToken },
  })
  if (!res.ok && res.status !== 404) throw new Error(`Config DELETE: HTTP ${res.status}`)
}

async function configListNamespace(namespace: string, adminToken?: string): Promise<Record<string, unknown>> {
  const headers: Record<string, string> = {}
  if (adminToken) headers['X-Admin-Token'] = adminToken
  const res = await fetch(`${CONFIG_BASE}/${namespace}`, { headers })
  if (!res.ok) throw new Error(`Config list ${namespace}: HTTP ${res.status}`)
  return res.json()
}

// ─── Template CRUD ────────────────────────────────────────────────────────────

/** Fetch all templates for the tenant */
export async function listTemplates(tenantId: string, adminToken: string): Promise<DashboardTemplate[]> {
  const all = await configListNamespace('dashboards', adminToken)
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
export async function getTemplate(templateId: string, adminToken?: string): Promise<DashboardTemplate | null> {
  const raw = await configGet('dashboards', `template:${templateId}`, adminToken)
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
export async function deleteTemplate(templateId: string, adminToken: string): Promise<void> {
  await configDelete('dashboards', `template:${templateId}`, adminToken)
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
  cards: DashboardCard[],
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
): Promise<DashboardCard[] | null> {
  try {
    if (adminToken) {
      const raw = await configGet('dashboards', layoutKey(tenantId, userId), adminToken)
      if (Array.isArray(raw)) return raw as DashboardCard[]
    }
    // Fallback to localStorage
    const stored = localStorage.getItem(`plughub_layout_${tenantId}_${userId}`)
    return stored ? JSON.parse(stored) : null
  } catch {
    return null
  }
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
    if (!tenantId || !adminToken) {
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
export function useTemplate(templateId: string | null, adminToken?: string): TemplateState {
  const [template, setTemplate] = useState<DashboardTemplate | null>(null)
  const [loading, setLoading]   = useState(!!templateId)
  const [error, setError]       = useState<string | null>(null)

  useEffect(() => {
    if (!templateId) { setTemplate(null); setLoading(false); return }
    let cancelled = false
    setLoading(true)
    getTemplate(templateId, adminToken)
      .then(t => { if (!cancelled) { setTemplate(t); setLoading(false) } })
      .catch(e => { if (!cancelled) { setError(String(e)); setLoading(false) } })
    return () => { cancelled = true }
  }, [templateId, adminToken])

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
