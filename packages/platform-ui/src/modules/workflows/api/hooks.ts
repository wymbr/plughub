/**
 * hooks.ts — leitura de instâncias de workflow (workflow-api, porta 3800)
 *
 * ⚠️ **Este arquivo sobreviveu à MOD-11 de propósito, e o consumidor dele não
 * mora mais aqui.** O módulo `workflows` foi encerrado em 2026-09-08 — as telas
 * (`WorkflowEditorPage`, `WebhooksTab`) saíram e o módulo ABAC de mesmo nome saiu
 * do catálogo, porque cada uma das suas funções já tinha sucessor gateado. O que
 * ficou é a LEITURA, e quem a consome é o `MonitorTab` (`contacts.monitorar`),
 * que é o sucessor do antigo `workflows.visualizar`.
 *
 * O que saiu junto e por quê:
 *   · `triggerWorkflow` — único produtor vivo do proxy `/v1/workflow/trigger`, que
 *     a tabela §7.9.1 do `adr-webhook-endpoint-single-registry` mede como **404
 *     desde a Fase E**; a tela que o chamava endereçava SKILL, contra o invariante
 *     *"o POOL é a unidade endereçável"*;
 *   · o CRUD de webhooks — administrava `workflow.webhooks`, medida em **0 linhas**,
 *     enquanto o registro que resolve endereço é o `ChannelEndpoint`
 *     (`/v1/channel-endpoints`, tela `/config/channels`, campo `config.channels`).
 */
import { useCallback, useEffect, useState } from 'react'
import { apiFetch } from '@/api/apiFetch'

async function safeJson<T>(res: Response): Promise<T> {
  const ct = res.headers.get('content-type') ?? ''
  if (!ct.includes('application/json') && !ct.includes('text/json')) {
    throw new Error(`API indisponível (HTTP ${res.status})`)
  }
  return res.json() as Promise<T>
}

export type WorkflowStatus = 'active' | 'suspended' | 'completed' | 'failed' | 'timed_out' | 'cancelled'
export type SuspendReason  = 'approval' | 'input' | 'webhook' | 'timer'

export interface WorkflowInstance {
  id:                 string
  installation_id:    string
  organization_id:    string
  tenant_id:          string
  flow_id:            string
  session_id?:        string
  origin_session_id?: string
  pool_id?:           string
  status:             WorkflowStatus
  current_step?:    string
  pipeline_state:   Record<string, unknown>
  suspend_reason?:  SuspendReason
  resume_token?:    string
  resume_expires_at?: string
  suspended_at?:    string
  resumed_at?:      string
  completed_at?:    string
  outcome?:         string
  created_at:       string
  metadata:         Record<string, unknown>
}

// ─── useWorkflowInstances ─────────────────────────────────────────────────────

export function useWorkflowInstances(
  tenantId:  string,
  status?:   WorkflowStatus | undefined,
  intervalMs = 10_000,
): { instances: WorkflowInstance[]; loading: boolean; refresh: () => void } {
  const [instances, setInstances] = useState<WorkflowInstance[]>([])
  const [loading,   setLoading]   = useState(false)

  const refresh = useCallback(async () => {
    if (!tenantId) return
    setLoading(true)
    try {
      const params = new URLSearchParams({ tenant_id: tenantId, limit: '200' })
      if (status) params.set('status', status)
      const res = await apiFetch(`/v1/workflow/instances?${params.toString()}`)
      if (res.ok) {
        const data = await safeJson<WorkflowInstance[] | { instances?: WorkflowInstance[] }>(res)
        setInstances(Array.isArray(data) ? data : (data.instances ?? []))
      }
    } catch { /* stale ok */ }
    finally { setLoading(false) }
  }, [tenantId, status])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, intervalMs)
    return () => clearInterval(id)
  }, [refresh, intervalMs])

  return { instances, loading, refresh }
}

// ─── useWorkflowInstance ──────────────────────────────────────────────────────

export function useWorkflowInstance(
  instanceId: string | null,
  intervalMs  = 10_000,
): { instance: WorkflowInstance | null; loading: boolean; refresh: () => void } {
  const [instance, setInstance] = useState<WorkflowInstance | null>(null)
  const [loading,  setLoading]  = useState(false)

  const refresh = useCallback(async () => {
    if (!instanceId) return
    setLoading(true)
    try {
      const res = await apiFetch(`/v1/workflow/instances/${encodeURIComponent(instanceId)}`)
      if (res.ok) setInstance(await safeJson<WorkflowInstance>(res))
    } catch { /* stale ok */ }
    finally { setLoading(false) }
  }, [instanceId])

  useEffect(() => {
    setInstance(null)
    if (!instanceId) return
    refresh()
    const id = setInterval(refresh, intervalMs)
    return () => clearInterval(id)
  }, [refresh, instanceId, intervalMs])

  return { instance, loading, refresh }
}

// ─── useWorkflowInstancesFiltered — Arc 18 B2 analytics drill-down ───────────

export interface InstanceFilters {
  status?:  string   // 'all' | WorkflowStatus
  poolId?:  string
  flowId?:  string
  fromDt?:  string   // ISO date
  toDt?:    string   // ISO date
}

export function useWorkflowInstancesFiltered(
  tenantId:  string,
  filters:   InstanceFilters = {},
): { instances: WorkflowInstance[]; loading: boolean; error: string | null; refresh: () => void } {
  const [instances, setInstances] = useState<WorkflowInstance[]>([])
  const [loading,   setLoading]   = useState(false)
  const [error,     setError]     = useState<string | null>(null)

  const refresh = useCallback(async () => {
    if (!tenantId) return
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({ tenant_id: tenantId, limit: '200' })
      if (filters.status && filters.status !== 'all') params.set('status', filters.status)
      if (filters.poolId)  params.set('pool_id',  filters.poolId)
      if (filters.flowId)  params.set('flow_id',  filters.flowId)
      if (filters.fromDt)  params.set('from_dt',  filters.fromDt)
      if (filters.toDt)    params.set('to_dt',    filters.toDt)
      const url = `/v1/workflow/instances?${params}`
      console.debug('[useWorkflowInstancesFiltered] GET', url)
      const res = await apiFetch(url)
      if (res.ok) {
        const data: WorkflowInstance[] = await safeJson(res)
        setInstances(Array.isArray(data) ? data : [])
      } else {
        const body = await res.text().catch(() => '')
        const msg = `HTTP ${res.status} — ${body.slice(0, 200)}`
        console.error('[useWorkflowInstancesFiltered]', msg)
        setError(msg)
        setInstances([])
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      console.error('[useWorkflowInstancesFiltered] network error:', msg)
      setError(msg)
    }
    finally { setLoading(false) }
  }, [tenantId, filters.status, filters.poolId, filters.flowId, filters.fromDt, filters.toDt])

  useEffect(() => { refresh() }, [refresh])

  return { instances, loading, error, refresh }
}

// ─── useWorkflowInstanceSessions — Arc 18 B2 instance → sessions drill-down ──

export interface InstanceSession {
  session_id:   string
  type:         'origin' | 'collect'
  step_id?:     string
  channel?:     string
  responded_at?: string
}

export function useWorkflowInstanceSessions(
  instanceId: string | null,
): { sessions: InstanceSession[]; loading: boolean } {
  const [sessions, setSessions] = useState<InstanceSession[]>([])
  const [loading,  setLoading]  = useState(false)

  useEffect(() => {
    if (!instanceId) { setSessions([]); return }
    let cancelled = false
    setLoading(true)
    apiFetch(`/v1/workflow/instances/${encodeURIComponent(instanceId)}/sessions`)
      .then(r => r.ok ? safeJson<{ sessions: InstanceSession[] }>(r) : Promise.reject(r.status))
      .then(d => { if (!cancelled) setSessions(d.sessions ?? []) })
      .catch(() => { if (!cancelled) setSessions([]) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [instanceId])

  return { sessions, loading }
}

// ─── cancelWorkflow — REMOVIDA em 2026-08-07 (I5, lacuna 4b) ──────────────────
//
// Chamava `POST /v1/workflow/instances/{id}/cancel`, que é **410 hard** desde o
// Arc 19 Fase D. Quatro telas a usavam, todas com `catch { alert(String(e)) }`:
// o operador confirmava um cancelamento e recebia `Error: HTTP 410`.
//
// Não foi reapontada para `POST /api/force-complete/:sessionId` (o encerramento
// por terceiro que a I5/D4 construiu) porque a medição provou que **não há
// endereço**: `workflow.instances` tem UM escritor (`router.py:794`, o gatilho
// legado por token) e ele grava `session_id: None` HARDCODED (`:799`) — a
// cobertura é 0% **por construção**, não por amostra. O caminho canônico
// (`/v1/workflow/trigger`) virou proxy do channel-gateway e não escreve linha
// alguma aqui. Reapontar teria trocado `HTTP 410` por `HTTP 404`.
//
// Sonda: `infra/test/probe_workflow_cancel_callers.sh`. Detalhe: TODO § "Lacuna 4b".
