import { apiFetch } from '@/api/apiFetch'
/**
 * rules/api.ts — cliente da API de regras de escalação (RUL-03).
 *
 * Backend: rules-engine (porta 3201), alcançado pela borda em `/rules-api/*` (prefixo
 * removido no nginx e no Vite). Toda rota exige Bearer com `config.rules` (AUT-65), e o
 * tenant é o do TOKEN — o `tenant_id` da query só precisa concordar com ele.
 *
 * A máquina de estados e o que se edita vêm do servidor (`GET /lifecycle`), nunca de uma
 * cópia aqui: a tela mostra a regra que o rules-engine aplica.
 */

export type RuleStatus = 'draft' | 'dry_run' | 'shadow' | 'active' | 'disabled'
export type Parameter = 'sentiment_score' | 'intent_confidence' | 'turn_count' | 'elapsed_ms' | 'flag'
export type Operator = 'lt' | 'lte' | 'gt' | 'gte' | 'eq' | 'neq' | 'contains'
export type Logic = 'AND' | 'OR'

export interface Condition {
  parameter: Parameter
  operator: Operator
  value: number | string
  window_turns?: number | null
  flag_name?: string | null
}

export interface Rule {
  rule_id: string
  tenant_id: string
  name: string
  status: RuleStatus
  conditions: Condition[]
  logic: Logic
  target_pool: string | null
  priority: number
  customer_notice: string | null
  created_at: string
  updated_at: string
}

export interface RuleBody {
  name: string
  conditions: Condition[]
  logic: Logic
  target_pool: string | null
  priority: number
  customer_notice: string | null
}

export interface Lifecycle {
  transitions: Record<RuleStatus, RuleStatus[]>
  editable_statuses: RuleStatus[]
}

/** Erro com o status HTTP e o `detail` do servidor — a tela mostra o motivo, não um código. */
export class RulesApiError extends Error {
  constructor(public status: number, message: string) { super(message) }
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await apiFetch(`/rules-api${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init.headers ?? {}) },
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    let detail = text || String(res.status)
    try {
      const d = JSON.parse(text)?.detail
      if (typeof d === 'string') detail = d
      else if (d) detail = JSON.stringify(d)
    } catch { /* corpo não-JSON: fica o texto cru */ }
    throw new RulesApiError(res.status, detail)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export function makeRulesApi(tenantId: string) {
  const q = `tenant_id=${encodeURIComponent(tenantId)}`
  return {
    lifecycle: () => call<Lifecycle>(`/lifecycle?${q}`),
    list: () => call<Rule[]>(`/rules?${q}`),
    create: (rule_id: string, body: RuleBody) =>
      call<Rule>('/rules', { method: 'POST', body: JSON.stringify({ ...body, rule_id, tenant_id: tenantId }) }),
    update: (id: string, body: RuleBody) =>
      call<Rule>(`/rules/${encodeURIComponent(id)}?${q}`, { method: 'PUT', body: JSON.stringify(body) }),
    remove: (id: string) =>
      call<void>(`/rules/${encodeURIComponent(id)}?${q}`, { method: 'DELETE' }),
    setStatus: (id: string, status: RuleStatus) =>
      call<Rule>(`/rules/${encodeURIComponent(id)}/status?${q}`, { method: 'PATCH', body: JSON.stringify({ status }) }),
  }
}

/** Pools do tenant (agent-registry) — destino da escalação. */
export async function fetchPoolIds(tenantId: string): Promise<string[]> {
  const res = await apiFetch('/v1/pools', { headers: { 'x-tenant-id': tenantId } })
  if (!res.ok) throw new Error(`pools: ${res.status}`)
  const data = await res.json()
  return ((data?.pools ?? []) as Array<{ pool_id: string }>).map(p => p.pool_id).sort()
}
