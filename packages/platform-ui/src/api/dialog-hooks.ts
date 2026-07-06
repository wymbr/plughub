/**
 * dialog-hooks.ts
 * API hooks for the Dialog primitive editor (DialogForm store — dialog-api,
 * port 3760, via the /v1/dialog Vite/nginx proxy). Custom fetch (mirrors
 * evaluation-hooks). Tenant scoping via the X-Tenant-ID header (dialog-api
 * requires it). Writes are open in the demo (admin_token unset).
 *
 * The DialogForm shape mirrors @plughub/schemas/dialog.ts but is defined
 * locally to keep platform-ui decoupled from the schemas package.
 */
import { useCallback, useEffect, useState } from 'react'

const BASE = '/v1/dialog/forms'

export type LocalizedText = string | Record<string, string>

export interface DialogValidation {
  numeric?: boolean
  pattern?: string
  min_length?: number
  max_length?: number
  min?: number
  max?: number
}

export interface DialogCapture {
  metric?: string
  value?: number | string
}

export interface DialogOption {
  id: string
  label: LocalizedText
  value?: string
  capture?: DialogCapture
}

export interface DialogField {
  id: string
  label: LocalizedText
  type: string
  required?: boolean
  masked?: boolean
  validation?: DialogValidation
  capture?: DialogCapture
}

export type DialogVisibility = 'all' | 'agents_only' | string[]

export interface StatementNode {
  id: string
  kind: 'statement'
  text: LocalizedText
  visibility?: DialogVisibility
}

export type DialogInteraction = 'text' | 'button' | 'list' | 'checklist' | 'form'

export interface QuestionNode {
  id: string
  kind: 'question'
  prompt: LocalizedText
  interaction: DialogInteraction
  options?: DialogOption[]
  fields?: DialogField[]
  masked?: boolean
  output_key: string
  capture?: DialogCapture
  validation?: DialogValidation
  retry?: { reprompt: LocalizedText; max_attempts?: number }
  visibility?: DialogVisibility
  timeout_s?: number
}

export type DialogNode = StatementNode | QuestionNode

export interface DialogForm {
  form_id: string
  tenant_id?: string
  name: string
  description?: string
  status?: 'draft' | 'published'
  version?: number
  default_locale: string
  locales: string[]
  nodes: DialogNode[]
  tags?: string[]
  created_at?: string
  updated_at?: string
}

/** Fields the store accepts on create/update (the rest is server-owned). */
export type DialogFormUpsert = Pick<
  DialogForm,
  'form_id' | 'name' | 'description' | 'default_locale' | 'locales' | 'nodes' | 'tags'
>

function headers(tenantId: string): Record<string, string> {
  return { 'Content-Type': 'application/json', 'X-Tenant-ID': tenantId }
}

// ── List (latest version metadata per form) ───────────────────────────────────

export function useDialogForms(tenantId: string) {
  const [forms, setForms]     = useState<DialogForm[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch(BASE, { headers: headers(tenantId) })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json()
      setForms(Array.isArray(d) ? d : (d?.forms ?? []))
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [tenantId])

  useEffect(() => { load() }, [load])

  return { forms, loading, error, reload: load }
}

// ── Get one (full, with nodes) — highest version (draft or published) ─────────

export async function getDialogForm(tenantId: string, formId: string): Promise<DialogForm> {
  const r = await fetch(`${BASE}/${encodeURIComponent(formId)}`, { headers: headers(tenantId) })
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return r.json()
}

// ── Create / update / publish ─────────────────────────────────────────────────

export async function createDialogForm(tenantId: string, body: DialogFormUpsert): Promise<DialogForm> {
  const r = await fetch(BASE, { method: 'POST', headers: headers(tenantId), body: JSON.stringify(body) })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text().catch(() => '')}`)
  return r.json()
}

export async function updateDialogForm(tenantId: string, formId: string, body: DialogFormUpsert): Promise<DialogForm> {
  const r = await fetch(`${BASE}/${encodeURIComponent(formId)}`, {
    method: 'PUT', headers: headers(tenantId), body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text().catch(() => '')}`)
  return r.json()
}

export async function publishDialogForm(tenantId: string, formId: string): Promise<DialogForm> {
  const r = await fetch(`${BASE}/${encodeURIComponent(formId)}/publish`, {
    method: 'POST', headers: headers(tenantId),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text().catch(() => '')}`)
  return r.json()
}
