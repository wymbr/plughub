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
  EvaluationPermission,
  KnowledgeSnippet,
  CampaignReport,
  AgentEvaluationReport,
} from '@/types'

const BASE = '/v1/evaluation'
const KN_BASE = '/v1/knowledge'

function adminHeaders(token?: string): Record<string, string> {
  const h: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) h['X-Admin-Token'] = token
  return h
}

// ── Forms ─────────────────────────────────────────────────────────────────────

export function useForms(tenantId: string) {
  const [forms, setForms] = useState<EvaluationForm[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch(`${BASE}/forms?tenant_id=${tenantId}`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      setForms(await r.json())
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

export async function createForm(tenantId: string, body: Partial<EvaluationForm>, token?: string) {
  const r = await fetch(`${BASE}/forms`, {
    method: 'POST',
    headers: adminHeaders(token),
    body: JSON.stringify({ ...body, tenant_id: tenantId }),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`)
  return r.json() as Promise<EvaluationForm>
}

export async function updateForm(formId: string, body: Partial<EvaluationForm>, token?: string) {
  const r = await fetch(`${BASE}/forms/${formId}`, {
    method: 'PATCH',
    headers: adminHeaders(token),
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`)
  return r.json() as Promise<EvaluationForm>
}

export async function deleteForm(formId: string, token?: string) {
  const r = await fetch(`${BASE}/forms/${formId}`, {
    method: 'DELETE',
    headers: adminHeaders(token),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
}

// ── Campaigns ─────────────────────────────────────────────────────────────────

export function useCampaigns(tenantId: string, pollMs = 0) {
  const [campaigns, setCampaigns] = useState<EvaluationCampaign[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${BASE}/campaigns?tenant_id=${tenantId}`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      setCampaigns(await r.json())
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [tenantId])

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
  const r = await fetch(`${BASE}/campaigns`, {
    method: 'POST',
    headers: adminHeaders(token),
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`)
  return r.json() as Promise<EvaluationCampaign>
}

export async function pauseCampaign(campaignId: string, token?: string) {
  const r = await fetch(`${BASE}/campaigns/${campaignId}/pause`, {
    method: 'POST',
    headers: adminHeaders(token),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return r.json() as Promise<EvaluationCampaign>
}

export async function resumeCampaign(campaignId: string, token?: string) {
  const r = await fetch(`${BASE}/campaigns/${campaignId}/resume`, {
    method: 'POST',
    headers: adminHeaders(token),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return r.json() as Promise<EvaluationCampaign>
}

// ── Instances ─────────────────────────────────────────────────────────────────

export function useInstances(campaignId: string, status?: string, pollMs = 0) {
  const [instances, setInstances] = useState<EvaluationInstance[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const qs = [`campaign_id=${campaignId}`, status ? `status=${status}` : ''].filter(Boolean).join('&')
      const r = await fetch(`${BASE}/instances?${qs}`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      setInstances(await r.json())
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [campaignId, status])

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

export function useResults(tenantId: string, campaignId?: string, evaluatorId?: string, pollMs = 0) {
  const [results, setResults] = useState<EvaluationResult[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const params = new URLSearchParams({ tenant_id: tenantId })
      if (campaignId) params.set('campaign_id', campaignId)
      if (evaluatorId) params.set('evaluator_id', evaluatorId)
      const r = await fetch(`${BASE}/results?${params}`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      setResults(await r.json())
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [tenantId, campaignId, evaluatorId])

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

/** Arc 6 v2 — Fetch result detail with server-side available_actions. */
export async function fetchResultWithActions(
  resultId: string,
  callerUserId: string,
): Promise<EvaluationResultWithActions> {
  const r = await fetch(`${BASE}/results/${resultId}?caller_user_id=${encodeURIComponent(callerUserId)}`)
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`)
  return r.json()
}

// ── Contestations ─────────────────────────────────────────────────────────────

export function useContestations(tenantId: string, resultId?: string) {
  const [contestations, setContestations] = useState<EvaluationContestation[]>([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    try {
      const params = new URLSearchParams({ tenant_id: tenantId })
      if (resultId) params.set('result_id', resultId)
      const r = await fetch(`${BASE}/contestations?${params}`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      setContestations(await r.json())
    } catch { /* silent */ } finally {
      setLoading(false)
    }
  }, [tenantId, resultId])

  useEffect(() => { load() }, [load])
  return { contestations, loading, reload: load }
}

export async function createContestation(
  body: { result_id: string; tenant_id: string; contested_by: string; reason: string },
  token?: string,
) {
  const r = await fetch(`${BASE}/contestations`, {
    method: 'POST',
    headers: adminHeaders(token),
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`)
  return r.json() as Promise<EvaluationContestation>
}

export async function adjudicateContestation(
  contestationId: string,
  body: { status: 'upheld' | 'dismissed'; adjudicator: string; note?: string },
  token?: string,
) {
  const r = await fetch(`${BASE}/contestations/${contestationId}/adjudicate`, {
    method: 'POST',
    headers: adminHeaders(token),
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
    fetch(`${BASE}/reports/campaigns/${campaignId}`)
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
    fetch(`${BASE}/reports/agents?${params}`)
      .then(r => r.ok ? r.json() : [])
      .then(d => setRows(Array.isArray(d) ? d : []))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [tenantId, poolId])

  return { rows, loading }
}

// ── Knowledge Base ─────────────────────────────────────────────────────────────

export async function searchKnowledge(
  tenantId: string,
  query: string,
  namespace?: string,
  topK = 20,
): Promise<KnowledgeSnippet[]> {
  const params = new URLSearchParams({ tenant_id: tenantId, query, top_k: String(topK) })
  if (namespace) params.set('namespace', namespace)
  const r = await fetch(`${KN_BASE}/search?${params}`)
  if (!r.ok) return []
  const data = await r.json()
  return (data.results ?? data) as KnowledgeSnippet[]
}

export async function upsertSnippet(
  body: { tenant_id: string; namespace: string; content: string; source_ref?: string; metadata?: Record<string, unknown> },
  token?: string,
): Promise<KnowledgeSnippet> {
  const r = await fetch(`${KN_BASE}/snippets`, {
    method: 'POST',
    headers: adminHeaders(token),
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`)
  return r.json()
}

export async function deleteSnippet(snippetId: string, token?: string): Promise<void> {
  const r = await fetch(`${KN_BASE}/snippets/${snippetId}`, {
    method: 'DELETE',
    headers: adminHeaders(token),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
}


// ── Permissions (Arc 6 v2 — 2D permission model) ──────────────────────────────

export function usePermissions(tenantId: string, userId?: string) {
  const [permissions, setPermissions] = useState<EvaluationPermission[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams({ tenant_id: tenantId })
      if (userId) params.set('user_id', userId)
      const r = await fetch(`${BASE}/permissions?${params}`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      setPermissions(await r.json())
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [tenantId, userId])

  useEffect(() => { load() }, [load])
  return { permissions, loading, error, reload: load }
}

export async function createPermission(
  body: {
    tenant_id:   string
    user_id:     string
    scope_type:  'pool' | 'campaign' | 'global'
    scope_id?:   string | null
    can_contest: boolean
    can_review:  boolean
    granted_by?: string
  },
  token?: string,
): Promise<EvaluationPermission> {
  const r = await fetch(`${BASE}/permissions`, {
    method:  'POST',
    headers: adminHeaders(token),
    body:    JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`)
  return r.json()
}

export async function updatePermission(
  permId: string,
  body: { can_contest?: boolean; can_review?: boolean },
  token?: string,
): Promise<EvaluationPermission> {
  const r = await fetch(`${BASE}/permissions/${permId}`, {
    method:  'PATCH',
    headers: adminHeaders(token),
    body:    JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`)
  return r.json()
}

export async function deletePermission(permId: string, token?: string): Promise<void> {
  const r = await fetch(`${BASE}/permissions/${permId}`, {
    method:  'DELETE',
    headers: adminHeaders(token),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
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
    fetch(`${ANALYTICS_BASE}/evaluations?${q}`)
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
    fetch(`${ANALYTICS_BASE}/evaluations/summary?${q}`)
      .then(r => r.json())
      .then(d => { setRows(d.data ?? []); setGroupBy(d.group_by ?? 'campaign_id'); setMeta(d.meta ?? meta); setError(null) })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  React.useEffect(() => { fetch_(); if (pollMs > 0) { const t = setInterval(fetch_, pollMs); return () => clearInterval(t) } }, [tenantId, params.campaign_id, params.group_by, pollMs])

  return { rows, group_by: groupBy, meta, loading, error }
}
