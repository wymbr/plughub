/**
 * evaluation-hooks.ts
 * API hooks for Arc 6 Evaluation Platform.
 * All requests go to evaluation-api (port 3400) via /v1/evaluation Vite proxy.
 */

import { useState, useEffect, useCallback } from 'react'
import type {
  EvaluationForm,
  EvaluationCampaign,
  EvaluationInstance,
  EvaluationResult,
  EvaluationContestation,
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

export async function reviewResult(
  resultId: string,
  body: { eval_status: string; review_note?: string },
  token?: string,
) {
  const r = await fetch(`${BASE}/results/${resultId}/review`, {
    method: 'POST',
    headers: adminHeaders(token),
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`)
  return r.json() as Promise<EvaluationResult>
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
