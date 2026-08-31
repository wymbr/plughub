/**
 * evaluation-hooks.ts
 * API hooks for Arc 6 Evaluation Platform.
 * All requests go to evaluation-api (port 3400) via /v1/evaluation Vite proxy.
 */

import React, { useState, useEffect, useCallback } from 'react'
import type {
  EvaluationForm,
  EvaluationCampaign,
  EvaluationInstance,
  EvaluationResult,
  EvaluationResultWithActions,
  EvaluationContestation,
  KnowledgeSnippet,
  CampaignReport,
  AgentEvaluationReport,
  // Arc 13
  InstanceThreads,
  HumanDimensionDecision,
  HumanReviewResponse,
  DimensionContestationPayload,
  DimensionContestationResponse,
  CurationSamplingRule,
} from '@/types'

import { getAccessToken } from '@/auth/token-store'
import { apiFetch } from '@/api/apiFetch'

const BASE = '/v1/evaluation'
const KN_BASE = '/v1/knowledge'

/** Bearer-JWT headers for grant-first ABAC endpoints (Quality "human config").
 *  Pass the session access token; sent as `Authorization: Bearer <token>`. */
function bearerHeaders(token?: string): Record<string, string> {
  const h: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) h['Authorization'] = `Bearer ${token}`
  return h
}

// ── Forms ─────────────────────────────────────────────────────────────────────

export function useForms(tenantId: string, accessToken?: string) {
  const [forms, setForms] = useState<EvaluationForm[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const headers: Record<string, string> = {}
      if (accessToken) headers['Authorization'] = `Bearer ${accessToken}`
      const r = await fetch(`${BASE}/forms?tenant_id=${tenantId}`, { headers })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json()
      const raw: EvaluationForm[] = Array.isArray(d) ? d : (d?.forms ?? d?.data ?? d?.items ?? [])
      // Defensive: asyncpg may return JSONB columns as strings — parse them
      const normalized = raw.map(f => ({
        ...f,
        dimensions: typeof f.dimensions === 'string'
          ? (() => { try { return JSON.parse(f.dimensions as unknown as string) } catch { return [] } })()
          : (f.dimensions ?? []),
      }))
      setForms(normalized)
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [tenantId, accessToken])

  useEffect(() => { load() }, [load])

  return { forms, loading, error, reload: load }
}

export async function createForm(tenantId: string, body: Partial<EvaluationForm>, token?: string) {
  const r = await apiFetch(`${BASE}/forms`, {
    method: 'POST',
    headers: bearerHeaders(token),
    body: JSON.stringify({ ...body, tenant_id: tenantId }),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`)
  return r.json() as Promise<EvaluationForm>
}

export async function updateForm(formId: string, body: Partial<EvaluationForm>, token?: string) {
  const r = await apiFetch(`${BASE}/forms/${formId}`, {
    method: 'PATCH',
    headers: bearerHeaders(token),
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`)
  return r.json() as Promise<EvaluationForm>
}

export async function deleteForm(formId: string, token?: string) {
  const r = await apiFetch(`${BASE}/forms/${formId}`, {
    method: 'DELETE',
    headers: bearerHeaders(token),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
}

// ── Rubric templates (T8-C) ─────────────────────────────────────────────────────

export interface RubricTemplate {
  id:            string
  tenant_id:     string
  scope:         'tenant' | 'campaign'
  campaign_id:   string | null
  name:          string
  body:          string
  version:       number
  deploy_status: 'draft' | 'published'
  created_at:    string
  updated_at:    string
}

export interface RubricVersion {
  rubric_id:    string
  version:      number
  name:         string
  body:         string
  published_at: string
  published_by: string
}

export interface RubricPreviewResult {
  composed_prompt:         string
  sections:                Record<string, string>
  rubric_source:           string
  criteria_count:          number
  calibration_notes_count: number
}

export function useRubricTemplates(tenantId: string, campaignId?: string, accessToken?: string) {
  const [templates, setTemplates] = useState<RubricTemplate[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const q = campaignId ? `&campaign_id=${encodeURIComponent(campaignId)}` : ''
      const headers: Record<string, string> = {}
      if (accessToken) headers['Authorization'] = `Bearer ${accessToken}`
      const r = await fetch(`${BASE}/rubric-templates?tenant_id=${tenantId}${q}`, { headers })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json()
      setTemplates(d?.rubric_templates ?? [])
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [tenantId, campaignId, accessToken])

  useEffect(() => { if (tenantId) load() }, [load, tenantId])
  return { templates, loading, error, reload: load }
}

export async function createRubricTemplate(
  tenantId: string,
  body: { scope: 'tenant' | 'campaign'; campaign_id?: string; name?: string; body?: string },
  token?: string,
): Promise<RubricTemplate> {
  const r = await apiFetch(`${BASE}/rubric-templates`, {
    method: 'POST',
    headers: bearerHeaders(token),
    body: JSON.stringify({ ...body, tenant_id: tenantId }),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`)
  return r.json()
}

// ─── R8c — blind-stage curation (re-score sem ver a IA → reveal + diff) ─────────

export interface BlindDimScore { dimension_id: string; score: number }

export interface BlindDimensionDiff {
  dimension_id: string
  ai_score:     number | null
  human_score:  number | null
  diff:         number | null
  disagree:     boolean
}

export interface BlindResult {
  id:                  string
  blind_overall_score: number | null
  blind_by_dimension:  BlindDimScore[]
  ai_overall_score:    number | null
  ai_by_dimension:     BlindDimScore[]
  per_dimension_diffs: BlindDimensionDiff[]
  created_at:          string
}

export interface BlindFormCriterion {
  criterion_id:   string
  label?:         string
  name?:          string
  type?:          string
  weight?:        number
  min_score?:     number
  max_score?:     number
  choice_scores?: Record<string, number>
  na_allowed?:    boolean
  allow_na?:      boolean
  allows_na?:     boolean
}

export interface BlindFormDimension {
  dimension_id: string
  label?:       string
  name?:        string
  weight?:      number
  criteria?:    BlindFormCriterion[]
}

export interface BlindContext {
  review:           CurationReview
  form:             { name?: string; dimensions?: BlindFormDimension[] } | null
  result_id:        string | null
  session_id:       string | null
  instance_id:      string | null
  already_rescored: boolean
  blind_result:     BlindResult | null
}

export interface BlindCriterionResponse {
  criterion_id:   string
  score?:         number
  boolean_value?: boolean
  choice_value?:  string
  text_value?:    string
  na?:            boolean
}

export interface BlindRescoreReveal {
  blind_result:        BlindResult
  severity_min:        number
  ai_overall_score:    number | null
  blind_overall_score: number | null
  per_dimension_diffs: BlindDimensionDiff[]
}

/** GET blind-context — form to re-score + transcript pointers, WITHOUT the AI scores.
 *  ABAC: requires `curar` (read_only) → pass accessToken (Bearer JWT). */
export async function getBlindContext(reviewId: string, tenantId: string, accessToken?: string): Promise<BlindContext> {
  const headers: Record<string, string> = { 'X-Tenant-ID': tenantId }
  if (accessToken) headers['Authorization'] = `Bearer ${accessToken}`
  const r = await fetch(`${BASE}/curations/${reviewId}/blind-context`, { headers })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`)
  return r.json()
}

/** POST blind-rescore — submit the blind responses → reveal AI score + per-dimension diff.
 *  ABAC: requires `curar` (read_write) → pass accessToken (Bearer JWT). */
export async function blindRescore(
  reviewId: string,
  tenantId: string,
  userId: string,
  criterionResponses: BlindCriterionResponse[],
  accessToken?: string,
): Promise<BlindRescoreReveal> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json', 'X-Tenant-ID': tenantId, 'X-User-ID': userId }
  if (accessToken) headers['Authorization'] = `Bearer ${accessToken}`
  const r = await fetch(`${BASE}/curations/${reviewId}/blind-rescore`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ criterion_responses: criterionResponses }),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`)
  return r.json()
}

/** POST blind-resolve — disagreements → CalibrationNote(s) + calibration.events; resolve the review.
 *  ABAC: requires `curar` (read_write) → pass accessToken (Bearer JWT). */
export async function blindResolve(
  reviewId: string,
  tenantId: string,
  userId: string,
  body: { curator_notes?: string; severity?: string; flag_bias?: boolean },
  accessToken?: string,
): Promise<{ review: CurationReview; status: string; disagreements: number; calibration_notes: unknown[] }> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json', 'X-Tenant-ID': tenantId, 'X-User-ID': userId }
  if (accessToken) headers['Authorization'] = `Bearer ${accessToken}`
  const r = await fetch(`${BASE}/curations/${reviewId}/blind-resolve`, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`)
  return r.json()
}

export async function updateRubricTemplate(
  rubricId: string, tenantId: string, body: { name?: string; body?: string },
  token?: string,
): Promise<RubricTemplate> {
  const r = await apiFetch(`${BASE}/rubric-templates/${rubricId}?tenant_id=${tenantId}`, {
    method: 'PUT',
    headers: bearerHeaders(token),
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`)
  return r.json()
}

export async function publishRubricTemplate(rubricId: string, tenantId: string, token?: string): Promise<RubricTemplate> {
  const r = await apiFetch(`${BASE}/rubric-templates/${rubricId}/publish?tenant_id=${tenantId}`, {
    method: 'POST',
    headers: bearerHeaders(token),
    body: JSON.stringify({}),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`)
  return r.json()
}

export function useRubricVersions(rubricId: string | null, tenantId: string) {
  const [versions, setVersions] = useState<RubricVersion[]>([])
  const load = useCallback(async () => {
    if (!rubricId) { setVersions([]); return }
    try {
      const r = await apiFetch(`${BASE}/rubric-templates/${rubricId}/versions?tenant_id=${tenantId}`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json()
      setVersions(d?.versions ?? [])
    } catch { setVersions([]) }
  }, [rubricId, tenantId])
  useEffect(() => { load() }, [load])
  return { versions, reload: load }
}

export async function previewRubric(
  tenantId: string,
  body: { form_id?: string; campaign_id?: string; rubric_body?: string; rubric_id?: string },
): Promise<RubricPreviewResult> {
  const r = await apiFetch(`${BASE}/rubric-templates/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...body, tenant_id: tenantId }),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`)
  return r.json()
}

// ── T9-B — critérios do resultado + versão fixada do form ───────────────────────

export function useResultCriteria(resultId: string | null, tenantId: string) {
  const [criteria, setCriteria] = useState<import('@/types').CriterionResponseRow[]>([])
  const [loading, setLoading] = useState(false)
  const load = useCallback(async () => {
    if (!resultId) { setCriteria([]); return }
    setLoading(true)
    try {
      const r = await apiFetch(`${BASE}/results/${resultId}/criteria?tenant_id=${tenantId}`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json()
      setCriteria(d?.criterion_responses ?? [])
    } catch { setCriteria([]) } finally { setLoading(false) }
  }, [resultId, tenantId])
  useEffect(() => { load() }, [load])
  return { criteria, loading, reload: load }
}

/** Snapshot da versão fixada do form (T6b) — base do render tipado por critério. */
export function useFormVersion(formId: string | null, version: number | null | undefined, tenantId: string) {
  const [form, setForm] = useState<EvaluationForm | null>(null)
  const load = useCallback(async () => {
    if (!formId) { setForm(null); return }
    try {
      const v = version ?? 1
      const r = await apiFetch(`${BASE}/forms/${formId}/versions/${v}?tenant_id=${tenantId}`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      setForm(await r.json())
    } catch { setForm(null) }
  }, [formId, version, tenantId])
  useEffect(() => { load() }, [load])
  return { form }
}

// ── Campaign summaries (T9-A2 — nível 1) ────────────────────────────────────────

export interface CampaignSummary {
  instance_status: Record<string, number>
  result_state:    Record<string, number>
  finalize_reason: Record<string, number>
  evaluated:       Record<string, number>
  avg_process_ms:  number | null
  sla_overdue:     number
  total_results:   number
}

export function useCampaignSummaries(tenantId: string, pollMs = 0) {
  const [summaries, setSummaries] = useState<Record<string, CampaignSummary>>({})
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    try {
      const r = await apiFetch(`${BASE}/reports/campaign-summary?tenant_id=${tenantId}`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json()
      setSummaries(d?.summaries ?? {})
    } catch { /* keep last */ } finally { setLoading(false) }
  }, [tenantId])

  useEffect(() => {
    if (!tenantId) return
    load()
    if (pollMs > 0) { const id = setInterval(load, pollMs); return () => clearInterval(id) }
  }, [load, tenantId, pollMs])

  return { summaries, loading, reload: load }
}

// ── Campaigns ─────────────────────────────────────────────────────────────────

export function useCampaigns(tenantId: string, pollMs = 0, accessToken?: string) {
  const [campaigns, setCampaigns] = useState<EvaluationCampaign[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const headers: Record<string, string> = {}
      if (accessToken) headers['Authorization'] = `Bearer ${accessToken}`
      const r = await fetch(`${BASE}/campaigns?tenant_id=${tenantId}`, { headers })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json()
      setCampaigns(Array.isArray(d) ? d : (d?.campaigns ?? d?.data ?? d?.items ?? []))
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [tenantId, accessToken])

  useEffect(() => {
    load()
    if (pollMs > 0) {
      const id = setInterval(load, pollMs)
      return () => clearInterval(id)
    }
  }, [load, pollMs])

  return { campaigns, loading, error, reload: load }
}

export async function createCampaign(body: Partial<EvaluationCampaign>, token?: string) {
  const r = await apiFetch(`${BASE}/campaigns`, {
    method: 'POST',
    headers: bearerHeaders(token),
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`)
  return r.json() as Promise<EvaluationCampaign>
}

export async function pauseCampaign(campaignId: string, token?: string) {
  const r = await apiFetch(`${BASE}/campaigns/${campaignId}/pause`, {
    method: 'POST',
    headers: bearerHeaders(token),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return r.json() as Promise<EvaluationCampaign>
}

export async function updateCampaign(campaignId: string, tenantId: string, body: Partial<EvaluationCampaign>, token?: string) {
  const r = await apiFetch(`${BASE}/campaigns/${campaignId}?tenant_id=${encodeURIComponent(tenantId)}`, {
    method: 'PUT',
    headers: bearerHeaders(token),
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`)
  return r.json() as Promise<EvaluationCampaign>
}

export async function deleteCampaign(campaignId: string, tenantId: string, token?: string) {
  const r = await apiFetch(`${BASE}/campaigns/${campaignId}?tenant_id=${encodeURIComponent(tenantId)}`, {
    method: 'DELETE',
    headers: bearerHeaders(token),
  })
  if (!r.ok && r.status !== 204) throw new Error(`HTTP ${r.status}: ${await r.text()}`)
}

// S2.Q1 — avaliador fake: gera avaliações sintéticas para validar o módulo em volume.
export async function seedSyntheticEvaluations(
  tenantId: string, campaignId: string, count: number, token?: string,
): Promise<{ results_created: number; nps_signals_emitted: number; requested: number }> {
  // G-PROBE fase 2 — endpoint de ops agora gateado (serviço OU Bearer+ABAC formularios:rw);
  // a UI usa o Bearer do operador (token = session.accessToken), sem segredo no frontend.
  const r = await apiFetch(`${BASE}/admin/seed-synthetic`, {
    method: 'POST',
    headers: bearerHeaders(token),
    body: JSON.stringify({ tenant_id: tenantId, campaign_id: campaignId, count }),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`)
  return r.json()
}

// S2.Q1 — limpa a massa sintética: Postgres (evaluation-api) + ClickHouse (analytics-api).
export async function flushSyntheticEvaluations(
  tenantId: string, token?: string,
): Promise<{ pg: unknown; ch: unknown }> {
  const q = `?tenant_id=${encodeURIComponent(tenantId)}`
  // G-PROBE fase 2 — flush da evaluation-api gateado (serviço OU Bearer+ABAC); a UI usa Bearer.
  const pgRes = await apiFetch(`${BASE}/admin/flush-synthetic${q}`, { method: 'POST', headers: bearerHeaders(token) })
  if (!pgRes.ok) throw new Error(`evaluation flush HTTP ${pgRes.status}: ${await pgRes.text()}`)
  const pg = await pgRes.json()
  // analytics-api é alcançado pelo proxy "/reports" → 3500 (open-access no demo; Bearer ignorado)
  const chRes = await apiFetch(`/reports/admin/flush-synthetic${q}`, { method: 'POST', headers: bearerHeaders(token) })
  const ch = chRes.ok ? await chRes.json() : { error: `HTTP ${chRes.status}` }
  return { pg, ch }
}

// S2.2 — dispara a avaliação real das instances scheduled da campanha (Rodar agora).
export async function dispatchCampaign(
  campaignId: string, tenantId: string, token?: string,
): Promise<{ campaign_id: string; dispatched: number; evaluator_pool: string }> {
  // G-PROBE fase 2 — dispatch gateado (serviço OU Bearer+ABAC formularios:rw); a UI usa Bearer.
  const r = await apiFetch(
    `${BASE}/campaigns/${campaignId}/dispatch?tenant_id=${encodeURIComponent(tenantId)}`,
    { method: 'POST', headers: bearerHeaders(token) },
  )
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`)
  return r.json()
}

export async function resumeCampaign(campaignId: string, token?: string) {
  const r = await apiFetch(`${BASE}/campaigns/${campaignId}/resume`, {
    method: 'POST',
    headers: bearerHeaders(token),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return r.json() as Promise<EvaluationCampaign>
}

// ── Instances ─────────────────────────────────────────────────────────────────

export function useInstances(campaignId: string, status?: string, pollMs = 0, accessToken?: string) {
  const [instances, setInstances] = useState<EvaluationInstance[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const qs = [`campaign_id=${campaignId}`, status ? `status=${status}` : ''].filter(Boolean).join('&')
      const headers: Record<string, string> = {}
      if (accessToken) headers['Authorization'] = `Bearer ${accessToken}`
      const r = await fetch(`${BASE}/instances?${qs}`, { headers })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json()
      setInstances(Array.isArray(d) ? d : (d?.instances ?? d?.data ?? d?.items ?? []))
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [campaignId, status, accessToken])

  useEffect(() => {
    load()
    if (pollMs > 0) {
      const id = setInterval(load, pollMs)
      return () => clearInterval(id)
    }
  }, [load, pollMs])

  return { instances, loading, error, reload: load }
}

// ── Results ───────────────────────────────────────────────────────────────────

export interface ResultFilters {
  campaignId?: string
  sessionId?: string
  evalStatus?: string
  actionRequired?: 'review' | 'contestation' | 'any'
  poolId?: string
  evaluatorId?: string
  locked?: boolean
  limit?: number
  offset?: number
}

export function useResults(
  tenantId: string,
  filters: ResultFilters = {},
  pollMs = 0,
  accessToken?: string,
) {
  const [results, setResults] = useState<EvaluationResultWithActions[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const filtersKey = JSON.stringify(filters)

  const load = useCallback(async () => {
    try {
      const params = new URLSearchParams({ tenant_id: tenantId })
      if (filters.campaignId)    params.set('campaign_id', filters.campaignId)
      if (filters.sessionId)     params.set('session_id', filters.sessionId)
      if (filters.evalStatus)    params.set('eval_status', filters.evalStatus)
      if (filters.actionRequired) params.set('action_required', filters.actionRequired)
      if (filters.poolId)        params.set('pool_id', filters.poolId)
      if (filters.evaluatorId)   params.set('evaluator_id', filters.evaluatorId)
      if (filters.locked != null) params.set('locked', String(filters.locked))
      if (filters.limit)         params.set('limit', String(filters.limit))
      if (filters.offset)        params.set('offset', String(filters.offset))

      const headers: Record<string, string> = {}
      if (accessToken) headers['Authorization'] = `Bearer ${accessToken}`

      const r = await fetch(`${BASE}/results?${params}`, { headers })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json()
      setResults(Array.isArray(d) ? d : (d?.results ?? d?.data ?? d?.items ?? []))
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenantId, filtersKey, accessToken])

  useEffect(() => {
    load()
    if (pollMs > 0) {
      const id = setInterval(load, pollMs)
      return () => clearInterval(id)
    }
  }, [load, pollMs])

  return { results, loading, error, reload: load }
}

/** Arc 6 v2 — JWT-gated review: decision + anti-replay round. */
export async function reviewResult(
  resultId: string,
  body: { decision: 'approved' | 'rejected'; round: number; review_note?: string },
  jwtToken: string,
) {
  const r = await fetch(`${BASE}/results/${resultId}/review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${jwtToken}` },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`)
  return r.json() as Promise<EvaluationResult>
}

/** Arc 6 v2 — Fetch result detail with server-side available_actions.
 *  Pass accessToken (Bearer JWT) to get personalised button state via ABAC. */
export async function fetchResultWithActions(
  resultId: string,
  accessToken?: string,
): Promise<EvaluationResultWithActions> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (accessToken) headers['Authorization'] = `Bearer ${accessToken}`
  const r = await fetch(`${BASE}/results/${resultId}`, { headers })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`)
  return r.json()
}

/** T9-C3 — single result (with server-side available_actions) as a hook (dedicated page). */
export function useResult(
  resultId: string | null,
  tenantId: string,
  accessToken?: string,
  pollMs = 0,
) {
  const [result, setResult] = useState<EvaluationResultWithActions | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!resultId) { setResult(null); setLoading(false); return }
    try {
      const headers: Record<string, string> = {}
      if (accessToken) headers['Authorization'] = `Bearer ${accessToken}`
      const r = await fetch(`${BASE}/results/${resultId}?tenant_id=${encodeURIComponent(tenantId)}`, { headers })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      setResult(await r.json()); setError(null)
    } catch (e) { setError(String(e)) } finally { setLoading(false) }
  }, [resultId, tenantId, accessToken])

  useEffect(() => {
    load()
    if (pollMs > 0) { const id = setInterval(load, pollMs); return () => clearInterval(id) }
  }, [load, pollMs])

  return { result, loading, error, reload: load }
}

// ── T9-C — transcript (nível 3) ─────────────────────────────────────────────────

export interface TranscriptMessage {
  stream_entry_id: string
  event_type:      string
  author_id:       string | null
  author_role:     string | null
  visibility?:     string
  content:         string          // mascarado por construção (D3)
  created_at:      string
}

export interface ResultTranscript {
  result_id:  string
  session_id: string
  segment_id: string | null
  scope:      string               // "segment" | "contact"
  window?:    { start: string; end: string } | null
  masked:     boolean
  messages:   TranscriptMessage[]
}

/** T9-C3 — transcript mascarado do result, delegado pelo evaluation-api ao analytics-api.
 *  scope: "segment" (janela do segmento avaliado) | "contact" (sessão inteira). */
export function useResultTranscript(
  resultId: string | null,
  tenantId: string,
  scope: 'segment' | 'contact',
  accessToken?: string,
) {
  const [data, setData] = useState<ResultTranscript | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!resultId) { setData(null); setLoading(false); return }
    setLoading(true)
    try {
      const headers: Record<string, string> = {}
      if (accessToken) headers['Authorization'] = `Bearer ${accessToken}`
      const r = await fetch(
        `${BASE}/results/${resultId}/transcript?tenant_id=${encodeURIComponent(tenantId)}&scope=${scope}`,
        { headers },
      )
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      setData(await r.json()); setError(null)
    } catch (e) { setError(String(e)) } finally { setLoading(false) }
  }, [resultId, tenantId, scope, accessToken])

  useEffect(() => { load() }, [load])

  return { data, loading, error, reload: load }
}

// ── Contestations ─────────────────────────────────────────────────────────────

export function useContestations(tenantId: string, resultId?: string, accessToken?: string) {
  const [contestations, setContestations] = useState<EvaluationContestation[]>([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    try {
      const params = new URLSearchParams({ tenant_id: tenantId })
      if (resultId) params.set('result_id', resultId)
      const headers: Record<string, string> = {}
      if (accessToken) headers['Authorization'] = `Bearer ${accessToken}`
      const r = await fetch(`${BASE}/contestations?${params}`, { headers })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json()
      setContestations(Array.isArray(d) ? d : (d?.contestations ?? d?.data ?? d?.items ?? []))
    } catch { /* silent */ } finally {
      setLoading(false)
    }
  }, [tenantId, resultId, accessToken])

  useEffect(() => { load() }, [load])
  return { contestations, loading, reload: load }
}

export async function createContestation(
  body: {
    result_id:    string
    instance_id:  string
    session_id:   string
    tenant_id:    string
    contested_by: string
    reason:       string
    round:        number   // anti-replay: must match result.current_round
  },
  jwtToken?: string,
) {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (jwtToken) headers['Authorization'] = `Bearer ${jwtToken}`
  const r = await fetch(`${BASE}/contestations`, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`)
  return r.json() as Promise<EvaluationContestation>
}

export async function adjudicateContestation(
  contestationId: string,
  body: { decision: 'accepted' | 'rejected'; adjudicator: string; adjudication_notes?: string },
  token?: string,
) {
  // G-PROBE platform-wide: adjudicação (Arc6 legado) usa o Bearer do operador (token =
  // session.accessToken) em vez de X-Admin-Token.
  const r = await apiFetch(`${BASE}/contestations/${contestationId}/adjudicate`, {
    method: 'POST',
    headers: bearerHeaders(token),
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`)
  return r.json() as Promise<EvaluationContestation>
}

// ── Reports ───────────────────────────────────────────────────────────────────

export function useCampaignReport(campaignId: string | null) {
  const [report, setReport] = useState<CampaignReport | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!campaignId) return
    setLoading(true)
    apiFetch(`${BASE}/reports/campaigns/${campaignId}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => setReport(d))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [campaignId])

  return { report, loading }
}

export function useAgentReport(tenantId: string, poolId?: string) {
  const [rows, setRows] = useState<AgentEvaluationReport[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const params = new URLSearchParams({ tenant_id: tenantId })
    if (poolId) params.set('pool_id', poolId)
    apiFetch(`${BASE}/reports/agents?${params}`)
      .then(r => r.ok ? r.json() : {})
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      .then((d: any) => setRows(Array.isArray(d) ? d : (d?.agents ?? d?.data ?? d?.items ?? [])))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [tenantId, poolId])

  return { rows, loading }
}

// ── Knowledge Base ─────────────────────────────────────────────────────────────

// G-PROBE platform-wide: o surface /v1/knowledge (mcp-server-knowledge) é gateado em
// Bearer+ABAC evaluation.gerir_rubrica. Anexa o Bearer do operador (do token-store);
// a busca por tenant_id é GET gateado por leitura.
function knHeaders(json = true): Record<string, string> {
  const h: Record<string, string> = {}
  if (json) h['Content-Type'] = 'application/json'
  const t = getAccessToken()
  if (t) h['Authorization'] = `Bearer ${t}`
  return h
}

export async function searchKnowledge(
  tenantId: string,
  query: string,
  namespace?: string,
  topK = 20,
): Promise<KnowledgeSnippet[]> {
  const params = new URLSearchParams({ tenant_id: tenantId, query, top_k: String(topK) })
  if (namespace) params.set('namespace', namespace)
  const r = await apiFetch(`${KN_BASE}/search?${params}`, { headers: knHeaders(false) })
  if (!r.ok) return []
  const data = await r.json()
  return (data.results ?? data) as KnowledgeSnippet[]
}

export async function upsertSnippet(
  body: { tenant_id: string; namespace: string; content: string; source_ref?: string; metadata?: Record<string, unknown> },
): Promise<KnowledgeSnippet> {
  const r = await apiFetch(`${KN_BASE}/snippets`, {
    method: 'POST',
    headers: knHeaders(),
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`)
  return r.json()
}

export async function deleteSnippet(snippetId: string): Promise<void> {
  const r = await apiFetch(`${KN_BASE}/snippets/${snippetId}`, {
    method: 'DELETE',
    headers: knHeaders(false),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
}


// ── Arc 13 — ContestationThreads & Human Review ───────────────────────────────

/**
 * Fetch all contestation threads for a workflow instance.
 * Calls GET /v1/evaluation/instances/{id}/threads — authentication via Bearer JWT.
 */
export async function fetchContestationThreads(
  instanceId: string,
  accessToken?: string,
): Promise<InstanceThreads> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (accessToken) headers['Authorization'] = `Bearer ${accessToken}`
  const r = await fetch(`${BASE}/instances/${instanceId}/threads`, { headers })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`)
  const d = await r.json()
  // Guard de shape: cada thread DEVE ter `entries[]` + estado/score (a UI faz .length/.map).
  // Normaliza p/ tolerar respostas parciais/legadas e nunca crashar o render.
  const threads = (Array.isArray(d.threads) ? d.threads : []).map((t: any) => ({
    dimension_id:    t.dimension_id,
    dimension_label: t.dimension_label ?? t.dimension_id,
    current_state:   t.current_state ?? 'neutral',
    original_score:  t.original_score ?? 0,
    current_score:   t.current_score ?? t.original_score ?? 0,
    entries:         Array.isArray(t.entries) ? t.entries : [],
  }))
  return {
    instance_id:   d.instance_id  ?? instanceId,
    result_id:     d.result_id    ?? null,
    current_round: d.current_round ?? 0,
    threads,
  }
}

/**
 * React hook that polls contestation threads for a given instance.
 * Returns null data until the first load completes.
 */
export function useContestationThreads(
  instanceId: string | null,
  accessToken?: string,
  pollMs = 0,
) {
  const [data, setData] = useState<InstanceThreads | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!instanceId) return
    setLoading(true)
    try {
      const d = await fetchContestationThreads(instanceId, accessToken)
      setData(d)
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [instanceId, accessToken])

  useEffect(() => {
    load()
    if (pollMs > 0) {
      const id = setInterval(load, pollMs)
      return () => clearInterval(id)
    }
  }, [load, pollMs])

  return { data, loading, error, reload: load }
}

/**
 * Human reviewer submits upheld/revised decisions per contested dimension.
 * Calls POST /v1/evaluation/instances/{id}/review — same endpoint as MCP evaluation_review_submit.
 */
export async function submitHumanReview(
  instanceId: string,
  body: { dimension_decisions: HumanDimensionDecision[]; reviewer_id?: string },
  jwtToken: string,
): Promise<HumanReviewResponse> {
  const r = await fetch(`${BASE}/instances/${instanceId}/review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${jwtToken}` },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`)
  return r.json()
}

/**
 * Human agent (evaluated) submits dimension-level contestation.
 * Calls POST /v1/evaluation/instances/{id}/contest — requires Bearer JWT with evaluation.contestar.
 */
export async function submitDimensionContestation(
  instanceId: string,
  body: DimensionContestationPayload,
  jwtToken: string,
): Promise<DimensionContestationResponse> {
  const r = await fetch(`${BASE}/instances/${instanceId}/contest`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${jwtToken}` },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`)
  return r.json()
}

// ── Arc 13 — CurationSamplingRules ───────────────────────────────────────────

/** Fetch curation sampling rules for a campaign. */
export function useCurationSamplingRules(campaignId: string | null, accessToken?: string) {
  const [rules, setRules] = useState<CurationSamplingRule[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!campaignId) return
    setLoading(true)
    try {
      const headers: Record<string, string> = {}
      if (accessToken) headers['Authorization'] = `Bearer ${accessToken}`
      const r = await fetch(`${BASE}/campaigns/${campaignId}/sampling-rules`, { headers })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json()
      setRules(Array.isArray(d) ? d : (d?.rules ?? d?.data ?? []))
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [campaignId, accessToken])

  useEffect(() => { load() }, [load])
  return { rules, loading, error, reload: load }
}

/** Save (full replace) curation sampling rules for a campaign.
 *  G-PROBE fase 2 — token = session.accessToken (Bearer), gate via ABAC formularios:rw. */
export async function saveCurationSamplingRules(
  campaignId: string,
  rules: Omit<CurationSamplingRule, 'campaign_id' | 'rule_id'>[],
  token?: string,
): Promise<CurationSamplingRule[]> {
  const r = await apiFetch(`${BASE}/campaigns/${campaignId}/sampling-rules`, {
    method: 'PUT',
    headers: bearerHeaders(token),
    body: JSON.stringify({ rules: rules.map(rule => ({ ...rule, campaign_id: campaignId })) }),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`)
  const d = await r.json()
  return Array.isArray(d) ? d : (d?.rules ?? d?.data ?? [])
}

// ── Analytics-API backed hooks (Arc 6 — /reports/evaluations*) ─────────────

const ANALYTICS_BASE = import.meta.env.VITE_ANALYTICS_URL ?? '/reports'

interface EvaluationResultRow {
  result_id:       string
  instance_id:     string
  session_id:      string
  tenant_id:       string
  evaluator_id:    string
  form_id:         string
  campaign_id:     string | null
  overall_score:   number
  eval_status:     string
  locked:          number
  compliance_flags: string[]
  timestamp:       string
}

interface EvaluationSummaryRow {
  group_key:            string
  total_evaluated:      number
  count_submitted:      number
  count_approved:       number
  count_rejected:       number
  count_contested:      number
  count_locked:         number
  count_locked_flag:    number
  avg_score:            number
  min_score:            number
  max_score:            number
  score_excellent:      number
  score_good:           number
  score_fair:           number
  score_poor:           number
  with_compliance_flags: number
}

interface EvaluationsAnalyticsResult {
  rows:    EvaluationResultRow[]
  meta:    { total: number; from_dt: string; to_dt: string }
  loading: boolean
  error:   string | null
}

interface EvaluationsSummaryResult {
  rows:     EvaluationSummaryRow[]
  group_by: string
  meta:     { total: number; from_dt: string; to_dt: string }
  loading:  boolean
  error:    string | null
}

/** Fetches individual evaluation results from analytics-api ClickHouse. */
export function useEvaluationsAnalytics(
  tenantId: string,
  params: { campaign_id?: string; form_id?: string; evaluator_id?: string; eval_status?: string; from_dt?: string; to_dt?: string } = {},
  pollMs = 0,
): EvaluationsAnalyticsResult {
  const [rows, setRows] = useState<EvaluationResultRow[]>([])
  const [meta, setMeta] = useState({ total: 0, from_dt: '', to_dt: '' })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetch_ = () => {
    const q = new URLSearchParams({ tenant_id: tenantId, page_size: '500' })
    if (params.campaign_id)  q.set('campaign_id',  params.campaign_id)
    if (params.form_id)      q.set('form_id',      params.form_id)
    if (params.evaluator_id) q.set('evaluator_id', params.evaluator_id)
    if (params.eval_status)  q.set('eval_status',  params.eval_status)
    if (params.from_dt)      q.set('from_dt',      params.from_dt)
    if (params.to_dt)        q.set('to_dt',        params.to_dt)
    setLoading(true)
    apiFetch(`${ANALYTICS_BASE}/evaluations?${q}`)
      .then(r => r.json())
      .then(d => { setRows(d.data ?? []); setMeta(d.meta ?? meta); setError(null) })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  React.useEffect(() => { fetch_(); if (pollMs > 0) { const t = setInterval(fetch_, pollMs); return () => clearInterval(t) } }, [tenantId, params.campaign_id, params.eval_status, pollMs])

  return { rows, meta, loading, error }
}

/** Fetches aggregated evaluation summary grouped by a dimension. */
export function useEvaluationsSummary(
  tenantId: string,
  params: { campaign_id?: string; form_id?: string; group_by?: string; from_dt?: string; to_dt?: string } = {},
  pollMs = 0,
): EvaluationsSummaryResult {
  const [rows, setRows] = useState<EvaluationSummaryRow[]>([])
  const [groupBy, setGroupBy] = useState<string>('campaign_id')
  const [meta, setMeta] = useState({ total: 0, from_dt: '', to_dt: '' })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetch_ = () => {
    const q = new URLSearchParams({ tenant_id: tenantId, group_by: params.group_by ?? 'campaign_id' })
    if (params.campaign_id) q.set('campaign_id', params.campaign_id)
    if (params.form_id)     q.set('form_id',     params.form_id)
    if (params.from_dt)     q.set('from_dt',     params.from_dt)
    if (params.to_dt)       q.set('to_dt',       params.to_dt)
    setLoading(true)
    apiFetch(`${ANALYTICS_BASE}/evaluations/summary?${q}`)
      .then(r => r.json())
      .then(d => { setRows(d.data ?? []); setGroupBy(d.group_by ?? 'campaign_id'); setMeta(d.meta ?? meta); setError(null) })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  React.useEffect(() => { fetch_(); if (pollMs > 0) { const t = setInterval(fetch_, pollMs); return () => clearInterval(t) } }, [tenantId, params.campaign_id, params.group_by, pollMs])

  return { rows, group_by: groupBy, meta, loading, error }
}

// ── T11 — Quality report Oficial × Operacional (/reports/evaluations/quality) ──

export interface QualityRow {
  group_key:     string
  n:             number
  finalized_n:   number
  provisional_n: number
  avg_score:     number
  score_high:    number
  score_mid:     number
  score_low:     number
}

export interface QualityReportResult {
  rows:             QualityRow[]
  mode:             string
  group_by:         string
  finalize_reasons: Record<string, number>
  meta:             { total: number; total_finalized: number; total_provisional: number; from_dt: string; to_dt: string }
  loading:          boolean
  error:            string | null
}

export function useQualityReport(
  tenantId: string,
  params: { mode?: 'oficial' | 'operacional'; group_by?: string; campaign_id?: string;
            finalize_reason?: string; from_dt?: string; to_dt?: string } = {},
  pollMs = 0,
): QualityReportResult {
  const [rows, setRows] = useState<QualityRow[]>([])
  const [mode, setMode] = useState<string>('oficial')
  const [groupBy, setGroupBy] = useState<string>('campaign_id')
  const [reasons, setReasons] = useState<Record<string, number>>({})
  const [meta, setMeta] = useState({ total: 0, total_finalized: 0, total_provisional: 0, from_dt: '', to_dt: '' })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetch_ = () => {
    const q = new URLSearchParams({
      tenant_id: tenantId,
      mode:      params.mode ?? 'oficial',
      group_by:  params.group_by ?? 'campaign_id',
    })
    if (params.campaign_id)     q.set('campaign_id',     params.campaign_id)
    if (params.finalize_reason) q.set('finalize_reason', params.finalize_reason)
    if (params.from_dt)         q.set('from_dt',         params.from_dt)
    if (params.to_dt)           q.set('to_dt',           params.to_dt)
    setLoading(true)
    apiFetch(`${ANALYTICS_BASE}/evaluations/quality?${q}`)
      .then(r => r.json())
      .then(d => {
        setRows(d.data ?? []); setMode(d.mode ?? 'oficial'); setGroupBy(d.group_by ?? 'campaign_id')
        setReasons(d.finalize_reasons ?? {}); setMeta(d.meta ?? meta); setError(d.error ? String(d.error) : null)
      })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  React.useEffect(() => { fetch_(); if (pollMs > 0) { const t = setInterval(fetch_, pollMs); return () => clearInterval(t) } },
    [tenantId, params.mode, params.group_by, params.campaign_id, params.finalize_reason, params.from_dt, params.to_dt, pollMs])

  return { rows, mode, group_by: groupBy, finalize_reasons: reasons, meta, loading, error }
}

// ── Calibration Dashboard (Arc 13 Fase G) ─────────────────────────────────────

export interface CalibrationPoint {
  period:            string
  skill_version:     string
  evaluator_id:      string
  total:             number
  approved:          number
  recalibrated:      number
  bias_flagged:      number
  calibration_score: number | null
  // R8b — divergência (0–1) e flag de recalibração recomendada (sinal, não auto-mutação)
  divergence?:                 number | null
  recalibration_recommended?:  boolean
}

export interface CalibrationSummary {
  total:             number
  approved:          number
  recalibrated:      number
  bias_flagged:      number
  calibration_score: number | null
  recalibration_recommended_count?: number
}

export interface CalibrationResult {
  data:    CalibrationPoint[]
  summary: CalibrationSummary
  meta:    Record<string, unknown>
  loading: boolean
  error:   string | null
  reload:  () => void
}

export function useEvaluatorCalibration(
  tenantId: string,
  params: {
    campaign_id?:   string
    evaluator_id?:  string
    skill_version?: string
    from_dt?:       string
    to_dt?:         string
    granularity?:   'day' | 'week'
  } = {},
  pollMs = 0,
): CalibrationResult {
  const [data,    setData]    = useState<CalibrationPoint[]>([])
  const [summary, setSummary] = useState<CalibrationSummary>({
    total: 0, approved: 0, recalibrated: 0, bias_flagged: 0, calibration_score: null,
  })
  const [meta,    setMeta]    = useState<Record<string, unknown>>({})
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState<string | null>(null)

  const load = useCallback(() => {
    const q = new URLSearchParams({ tenant_id: tenantId })
    if (params.campaign_id)   q.set('campaign_id',   params.campaign_id)
    if (params.evaluator_id)  q.set('evaluator_id',  params.evaluator_id)
    if (params.skill_version) q.set('skill_version', params.skill_version)
    if (params.from_dt)       q.set('from_dt',       params.from_dt)
    if (params.to_dt)         q.set('to_dt',         params.to_dt)
    if (params.granularity)   q.set('granularity',   params.granularity)
    setLoading(true)
    apiFetch(`${ANALYTICS_BASE}/evaluator-calibration?${q}`)
      .then(r => r.json())
      .then(d => {
        setData(d.data ?? [])
        setSummary(d.summary ?? { total: 0, approved: 0, recalibrated: 0, bias_flagged: 0, calibration_score: null })
        setMeta(d.meta ?? {})
        setError(null)
      })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }, [tenantId, params.campaign_id, params.evaluator_id, params.skill_version, params.from_dt, params.to_dt, params.granularity])

  useEffect(() => {
    load()
    if (pollMs > 0) { const t = setInterval(load, pollMs); return () => clearInterval(t) }
  }, [load, pollMs])

  return { data, summary, meta, loading, error, reload: load }
}


// ── Curation Queue (Arc 13 Fase H) ─────────────────────────────────────────────

export interface CurationReview {
  id:                     string
  tenant_id:              string
  evaluation_instance_id: string
  campaign_id:            string | null
  trigger:                string
  curator_id:             string | null
  status:                 'pending' | 'approved' | 'recalibrated' | 'bias_flagged'
  curator_notes:          string | null
  calibration_note_id:    string | null
  calibration_signal:     {
    severity:      string
    dimension_id:  string
    criterion_id?: string
    observation:   string
    evaluator_id:  string
    skill_version: string
  } | null
  // R8c — blind-stage curation
  mode?:          'standard' | 'blind'
  deadline_at?:   string | null
  expired_at?:    string | null
  skill_version?: string | null
  created_at:  string
  resolved_at: string | null
}

export interface CurationResolvePayload {
  status:                 'approved' | 'recalibrated' | 'bias_flagged'
  curator_notes?:         string
  calibration_note_text?: string
  dimension_id?:          string
  criterion_id?:          string
  evaluator_id?:          string
  skill_version?:         string
  severity?:              string
}

/**
 * Hook: curation review queue.
 * Calls GET /v1/evaluation/curations with optional status + campaign filters.
 */
export function useCurationQueue(
  tenantId: string,
  opts: { status?: string; campaign_id?: string; limit?: number } = {},
  pollMs = 0,
  accessToken?: string,
) {
  const [reviews, setReviews] = useState<CurationReview[]>([])
  const [total,   setTotal]   = useState(0)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const q = new URLSearchParams()
      if (opts.status)      q.set('status',      opts.status)
      if (opts.campaign_id) q.set('campaign_id', opts.campaign_id)
      if (opts.limit)       q.set('limit',       String(opts.limit))
      const headers: Record<string, string> = { 'X-Tenant-ID': tenantId }
      if (accessToken) headers['Authorization'] = `Bearer ${accessToken}`
      const r = await fetch(`${BASE}/curations?${q}`, { headers })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json()
      setReviews(d.reviews ?? [])
      setTotal(d.count ?? 0)
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [tenantId, opts.status, opts.campaign_id, opts.limit, accessToken])

  useEffect(() => {
    load()
    if (pollMs > 0) { const t = setInterval(load, pollMs); return () => clearInterval(t) }
  }, [load, pollMs])

  return { reviews, total, loading, error, reload: load }
}

/**
 * Curator resolves a CurationReview.
 * Calls POST /v1/evaluation/curations/{id}/resolve
 */
export async function resolveCuration(
  reviewId: string,
  tenantId: string,
  userId: string,
  body: CurationResolvePayload,
  accessToken?: string,
): Promise<{ review: CurationReview; calibration_note: unknown; kb_published: boolean }> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    'X-Tenant-ID':  tenantId,
    'X-User-ID':    userId,
  }
  if (accessToken) headers['Authorization'] = `Bearer ${accessToken}`
  const r = await fetch(`${BASE}/curations/${reviewId}/resolve`, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`)
  return r.json()
}
