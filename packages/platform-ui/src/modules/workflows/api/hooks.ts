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

/**
 * Um processo EM ANDAMENTO.
 *
 * ⚠️ ORQ-10 (2026-09-09): isto deixou de ser a `WorkflowInstance` da workflow-api.
 * Aquela entidade morreu no Arc 19 — processo passou a ser **sessão de canal
 * webhook** — e a tabela `workflow.instances` ficou em ZERO linhas, com o endpoint
 * respondendo `[]` e HTTP 200. A tela dizia "No processes found" com 51 processos
 * suspensos vivos, e o vazio bem-formado não acendia nada.
 *
 * ⚠️ **`resume_token` SAIU do contrato, e é decisão.** A tela antiga o exibia com um
 * botão de copiar; quem tem o token retoma o processo pela porta externa sem passar
 * por portão nenhum. O que a tela precisa saber é *se existe endereço de retomada* —
 * `has_resume_token` — e não qual é. Mesma postura da APR-10: pedir a ação não é
 * receber a credencial.
 */
export interface WorkflowInstance {
  /** = session_id. `id` fica como alias porque a tela inteira já o usa por este nome. */
  id:                 string
  session_id:         string
  tenant_id?:         string
  pool_id?:           string
  channel?:           string
  origin_session_id?: string
  root_session_id?:   string
  spawn_reason?:      string
  status:             WorkflowStatus
  /** O step em que o processo parou — vem de `session_transitions` (D4/RET-12). */
  current_step?:      string
  suspend_reason?:    SuspendReason
  has_resume_token?:  boolean
  resume_expires_at?: string
  suspended_at?:      string
  outcome?:           string
  created_at:         string
}

/** A linha da analytics vira o processo que a tela conhece. */
function paraProcesso(linha: Record<string, unknown>): WorkflowInstance {
  const sid = String(linha['session_id'] ?? '')
  return {
    id:                sid,
    session_id:        sid,
    tenant_id:         linha['tenant_id']         as string | undefined,
    pool_id:           linha['pool_id']           as string | undefined,
    channel:           linha['channel']           as string | undefined,
    origin_session_id: (linha['origin_session_id'] as string | null) ?? undefined,
    root_session_id:   (linha['root_session_id']   as string | null) ?? undefined,
    spawn_reason:      (linha['spawn_reason']      as string | null) ?? undefined,
    status:            (linha['status'] as WorkflowStatus) ?? 'active',
    current_step:      (linha['step_id']           as string | null) ?? undefined,
    suspend_reason:    (linha['suspend_reason']    as SuspendReason | null) ?? undefined,
    has_resume_token:  Boolean(linha['has_resume_token']),
    resume_expires_at: (linha['resume_expires_at'] as string | null) ?? undefined,
    suspended_at:      (linha['suspended_at']      as string | null) ?? undefined,
    outcome:           (linha['outcome']           as string | null) ?? undefined,
    created_at:        String(linha['opened_at'] ?? ''),
  }
}


export interface ProcessosPorPool {
  pool_id:      string
  em_execucao:  number
  suspensos:    number
  sem_endereco: number
  vencendo_24h: number
  mais_antigo:  string
}

export interface ProcessosResumo {
  totais:   { em_execucao: number; suspensos: number; sem_endereco: number; vencendo_24h: number }
  por_pool: ProcessosPorPool[]
}

/**
 * O consolidado dos processos DE PÉ — números, não lista (ORQ-10).
 *
 * ⚠️ Agregado no BACKEND. Contar as linhas da lista daria o menor entre a verdade e
 * o teto de 200 — e pareceria certo, porque 200 é um número plausível.
 *
 * ⚠️ Sem janela de tempo: das 51 suspensas medidas em 2026-09-09, 49 abriram há mais
 * de 24 h. Processo parado há uma semana é presente, não passado — mesma natureza
 * dos mostradores da aba Sessões (`busy`/`available`/`queue`), que são estado AGORA.
 */
export function useProcessosResumo(tenantId: string, pollMs = 15_000) {
  const [resumo,  setResumo]  = useState<ProcessosResumo | null>(null)
  const [loading, setLoading] = useState(true)
  const [erro,    setErro]    = useState<string | null>(null)

  const buscar = useCallback(async () => {
    if (!tenantId) return
    try {
      const res = await apiFetch(`/sessions/processes/summary?tenant_id=${encodeURIComponent(tenantId)}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setResumo(await res.json() as ProcessosResumo)
      setErro(null)
    } catch (e) {
      // Nunca zerar em silêncio: consolidado mostrando 0 por falha é indistinguível
      // de "não há processo" — a leitura errada que esta ficha inteira existe para
      // fechar. O erro sobe para a tela dizer que não sabe.
      setErro(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [tenantId])

  useEffect(() => {
    void buscar()
    if (!pollMs) return
    const id = setInterval(() => void buscar(), pollMs)
    return () => clearInterval(id)
  }, [buscar, pollMs])

  return { resumo, loading, erro, recarregar: buscar }
}


// ⚠️ ORQ-10 (2026-09-09): `useWorkflowInstance` e `useWorkflowInstanceSessions`
// foram REMOVIDOS. Os dois chamavam `/v1/workflow/instances/{id}[/sessions]` —
// a tabela que o Arc 19 esvaziou ao transformar processo em sessão webhook —, e
// nenhum tinha consumidor fora deste arquivo (medido). O detalhe do processo sai
// da PRÓPRIA lista, que já traz o parque de cada um; manter os hooks vivos seria
// deixar a chamada morta à mão de quem os importasse, e ela responde `[]` com
// HTTP 200 — vazio bem-formado, que não acende nada.

// ─── useWorkflowInstances ─────────────────────────────────────────────────────

export function useWorkflowInstances(
  tenantId:  string,
  status?:   WorkflowStatus | undefined,
  intervalMs = 10_000,
  /** Drill-down: só os processos deste pool (o clique no consolidado). */
  poolId?:   string,
): { instances: WorkflowInstance[]; loading: boolean; refresh: () => void } {
  const [instances, setInstances] = useState<WorkflowInstance[]>([])
  const [loading,   setLoading]   = useState(false)

  const refresh = useCallback(async () => {
    if (!tenantId) return
    setLoading(true)
    try {
      // ⚠️ ORQ-10: `/sessions/processes` é pergunta de ESTADO (o que está de pé
      // AGORA), sem janela de tempo — e é por isso que ela não é `/reports/sessions`,
      // que tem janela default de 7 dias. Medido: das 51 suspensas, 49 abriram há
      // MAIS de 24 h e 6 há mais de 7 dias; a janela esconderia justamente as que
      // precisam de ação. Monitor pergunta estado; Analytics pergunta período.
      const params = new URLSearchParams({ tenant_id: tenantId, limit: '200' })
      if (poolId) params.set('pool_id', poolId)
      if (status) params.set('status', status)
      const res = await apiFetch(`/sessions/processes?${params.toString()}`)
      if (res.ok) {
        const data = await safeJson<WorkflowInstance[] | { instances?: WorkflowInstance[] }>(res)
        setInstances(Array.isArray(data) ? data : (data.instances ?? []))
      }
    } catch { /* stale ok */ }
    finally { setLoading(false) }
  }, [tenantId, status, poolId])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, intervalMs)
    return () => clearInterval(id)
  }, [refresh, intervalMs])

  return { instances, loading, refresh }
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
      const url = `/sessions/processes?${params}`
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
