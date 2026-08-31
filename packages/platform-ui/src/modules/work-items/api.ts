/**
 * modules/work-items/api.ts
 * Cliente da superfície VIVA de pendências de wrap-up (I5 / ADR § D7b, fatia 1).
 *
 * Backend: mcp-server-plughub (BFF) — `GET /api/work_queue/pending` lê o ledger
 * `{t}:work_task:*` no Redis e classifica cruzando ZSET × lease × dispatch_mode.
 * O encerramento pelo supervisor reusa `POST /api/work_queue/expire/:sessionId`,
 * que já existia desde o núcleo A+B e até aqui não tinha nenhum chamador na UI.
 *
 * HISTÓRICO NÃO MORA AQUI. "Quantos venceram no período" é query sobre `segments`
 * (close_reason ∈ task_submitted | acw_expired | acw_supervisor_closed) e vive no
 * Analytics — fatia 2, outra fonte, outro ciclo.
 */

import { parseResumeConflict, type ResumeConflict } from '@/lib/resume-conflict'
import { apiFetch } from '@/api/apiFetch'

/**
 * Estado derivado do cruzamento ledger × ZSET × lease × registro de posse.
 * Ver lib/work-queue.ts.
 */
export type WorkTaskState =
  | 'unclaimed'    // na fila, ninguém pegou
  | 'claimed'      // reivindicada, formulário não submetido
  // Fase A (D6) estreitou `orphaned`: item cuja lease de 180 s venceu mas que tem
  // dono no registro durável agora aparece como `claimed`. Sobrou aqui o caso
  // literal — fora da fila, sem lease E sem registro. Não é mais ruído esperado.
  | 'orphaned'     // pool pull, fora da fila e SEM dono em nenhuma das duas fontes
  | 'not_queued'   // pool push — não é item de fila
  | 'unknown'      // sem pool_config no cache — infra ausente, nada presumido

export interface PendingWorkTask {
  session_id:          string
  queue_session_id:    string
  pool_id:             string
  assigned_to:         string | null
  step_id:             string | null
  state:               WorkTaskState
  dispatch_mode:       string | null
  created_at:          string | null
  deadline:            string | null
  age_ms:              number | null
  time_to_deadline_ms: number | null
  overdue:             boolean
  claimed_by:          string | null
  claimed_at:          string | null
  /** Fonte que nomeou o dono: 'lease' (fresca) | 'record' (durável, Fase A). */
  claimed_via:         'lease' | 'record' | null
}

export interface PendingResponse {
  items:        PendingWorkTask[]
  total:        number
  scanned:      number
  /** Bateu o teto do SCAN — a lista é PARCIAL. A tela precisa dizer isso. */
  truncated:    boolean
  generated_at: string
}

/** Usuário do auth-api, só o necessário para exibir nome no lugar do user_id. */
export interface DirectoryUser {
  id:    string
  name?: string
  email?: string
}

export async function fetchPending(params: {
  assignedTo?: string
  poolId?:     string
  state?:      WorkTaskState | 'all'
} = {}): Promise<PendingResponse> {
  const q = new URLSearchParams()
  if (params.assignedTo)                      q.set('assigned_to', params.assignedTo)
  if (params.poolId)                          q.set('pool_id',     params.poolId)
  if (params.state && params.state !== 'all') q.set('state',       params.state)
  const qs = q.toString()
  // no-store: pendência é estado tempo-real; o ETag do Express faria o browser
  // devolver 304 com a lista velha logo após um encerramento (mesmo motivo do
  // PullInboxPanel).
  const res = await apiFetch(`/api/work_queue/pending${qs ? `?${qs}` : ''}`, { cache: 'no-store' })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json() as Promise<PendingResponse>
}

/**
 * Falha do encerramento, com o MOTIVO já lido (F2).
 *
 * Antes esta função lançava `Error('HTTP 409: ' + corpo cru)` e a tela dava
 * `alert(String(e))` — o operador via o JSON aninhado duas vezes. O corpo sempre
 * teve a resposta; faltava alguém lê-lo.
 */
export class ExpirePendingError extends Error {
  readonly status:   number
  /** Não-nulo quando o 409 é o conflito nomeado da Fase F. */
  readonly conflict: ResumeConflict | null

  constructor(status: number, conflict: ResumeConflict | null, raw: string) {
    super(`HTTP ${status}${raw ? `: ${raw}` : ''}`)
    this.name     = 'ExpirePendingError'
    this.status   = status
    this.conflict = conflict
  }
}

/**
 * Encerra a pendência SEM disposição (D5): o supervisor não finge ser o autor.
 * O Bearer vai adiante de propósito — o resume é autorado e auditado como dele.
 *
 * O 409 da Fase F chega aqui embrulhado DUAS vezes (`detail.detail`): uma pelo
 * FastAPI do gateway, outra pelo mcp-server, que repassa o corpo inteiro sob
 * `expire_failed`. O parser desce sozinho — ver lib/resume-conflict.ts.
 */
export async function expirePending(sessionId: string, accessToken: string): Promise<void> {
  const res = await fetch(`/api/work_queue/expire/${encodeURIComponent(sessionId)}`, {
    method:  'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
    },
    body: JSON.stringify({}),
  })
  if (!res.ok) {
    const raw = await res.text().catch(() => '')
    let parsed: unknown = null
    try { parsed = raw ? JSON.parse(raw) : null } catch { /* não-JSON: sobra o status */ }
    throw new ExpirePendingError(res.status, parseResumeConflict(parsed), raw)
  }
}

/**
 * Diretório de usuários para trocar user_id por nome.
 *
 * DEGRADAÇÃO CONHECIDA, E NÃO SILENCIOSA: `/auth/users` exige ABAC
 * `config.usuarios` (strict, sem bypass de admin) — grant que o supervisor desta
 * tela pode não ter. Devolve `null` nesse caso, e a tela EXIBE o motivo em vez de
 * mostrar UUID sem explicação. A alternativa Redis (`{t}:instance:human-{uid}`)
 * foi descartada: aquela chave é heartbeat de 30 s e some no logout — falharia
 * justamente na linha mais interessante, a pendência de quem já saiu.
 */
export async function fetchDirectory(
  tenantId: string, accessToken: string,
): Promise<Map<string, string> | null> {
  if (!tenantId || !accessToken) return null
  try {
    const res = await fetch(`/auth/users?tenant_id=${encodeURIComponent(tenantId)}`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    })
    if (!res.ok) return null
    const data = await res.json() as DirectoryUser[] | { users?: DirectoryUser[] }
    const arr  = Array.isArray(data) ? data : (data.users ?? [])
    const map  = new Map<string, string>()
    for (const u of arr) if (u.id) map.set(u.id, u.name || u.email || u.id)
    return map
  } catch {
    return null
  }
}

// ── Formatação ────────────────────────────────────────────────────────────────

export function fmtDuration(ms: number | null): string {
  if (ms == null) return '—'
  const abs = Math.abs(ms)
  const s = Math.floor(abs / 1000)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  if (h > 0) return `${h}h${String(m).padStart(2, '0')}`
  if (m > 0) return `${m}min`
  return `${s}s`
}

export function fmtDateTime(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString()
}
