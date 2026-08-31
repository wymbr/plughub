/**
 * PoolsPage — /contacts/pools
 *
 * Real-time operational view of all pools:
 *   - Capacity: available / busy / paused agents
 *   - Queue: depth + estimated wait time
 *   - SLA target
 *   - Drill-down: contacts currently waiting in the pool's queue
 *
 * Data sources:
 *   GET /v1/operational/pools            → Redis snapshots + Pool config from DB
 *   GET /v1/operational/pools/:id/queue  → ZSET queue entries
 *
 * Auto-refresh every 15 s. Snapshot age shown per row.
 *
 * Also exports PoolStatusCard for reuse in dashboard widgets (Task #35).
 */
import React, { useCallback, useEffect, useState } from 'react'
import { ChevronDown, ChevronRight, Server } from 'lucide-react'
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from 'recharts'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import { apiFetch } from '@/api/apiFetch'
import Spinner from '@/components/ui/Spinner'
import { EmptyScopeNotice } from '@/components/ui/EmptyScopeNotice'

// ── Types ──────────────────────────────────────────────────────────────────────

interface PoolOp {
  pool_id:             string
  description:         string | null
  sla_target_ms:       number
  channel_types:       string[]
  pool_status:         string
  agent_kind:          string | null
  op_status:           'available' | 'queued' | 'empty' | 'unknown'
  /** `null` = DESCONHECIDO. O bootstrap omite capacidade em pool com membro que ele
   *  não gerencia (humano logado); antes publicava `0`, afirmando que um pool com
   *  agente pronto não tinha vaga. `capacity_unknown` diz o motivo. */
  available:           number | null
  capacity_unknown:    string | null
  queue_length:        number
  estimated_wait_ms:   number | null
  snapshot_age_ms:     number | null
  snapshot_updated_at: string | null
  has_snapshot:        boolean
  // Item 7a (capacity-governance) — admissão por regime.
  // Fatia 3 (2026-08-02): o regime deixou de ser "reservado × compartilhado" (baldes
  // de sessão carvidos do pote misto `C_ai + C_human`) e passou a ser "licenciado por
  // SESSÃO × não". Só pool de IA debita licença por sessão; humano é licenciado no
  // LOGIN. `reservation` saiu junto com os baldes reservados.
  admission_scope:     'licensed' | 'unlicensed'
  admitted:            number
  // Capacidade compartilhada (fatia 2). `null` = o snapshot não trouxe o campo
  // (linha do bootstrap, `capacity_model: 'bootstrap_placeholder'`) → DESCONHECIDO,
  // nunca 0. `busy_elsewhere` = vagas do mesmo recurso consumidas por pools irmãos:
  // é o que explica `available < total − active_sessions` sem que a linha pareça
  // um bug. Consumo na tela (coluna própria + fim da soma de `available`) = F4.
  active_sessions:     number | null
  busy_elsewhere:      number | null
  total_capacity:      number | null
  capacity_model:      'resource_semaphore' | 'bootstrap_placeholder' | null
  queue_mute:          number
  queue_attended:      number
  queue_tier:          'attended' | 'system' | 'none'
  admissible:          number | null
  admissible_shared:   boolean
}

interface OpSummary {
  // `contracted` é o número de PROVISIONAMENTO (Σ declarada nos deploys ≤ C), não o
  // teto de admissão — este último é `ai.cap`. Ver fatia 3 no TODO.
  contracted:     number | null
  admitted_total: number
  headroom:       number | null
  in_service:     number
  queue_total:    number
  queue_attended: number
  queue_mute:     number
  buffer:   { used: number; limit: number }
  ai:       { cap: number | null; used: number; by_pool: Record<string, number> }
  // F4b — capacidade DEDUPLICADA por tipo de licença (rollup do Routing Engine sobre
  // instâncias DISTINTAS). Sem escalar no topo: humano e IA não se substituem.
  // `null` + motivo (`no_rollup` | `scope_limited`) quando indisponível.
  capacity:             { by_kind: Record<string, {
    total_capacity: number; used: number; available: number; instances: number
  }>; computed_at: string } | null
  capacity_unavailable: string | null
}

interface QueueEntry {
  position:          number
  session_id:        string
  queued_at:         string
  wait_ms:           number
  estimated_wait_ms: number
}

// ── Helpers ────────────────────────────────────────────────────────────────────

function fmtMs(ms: number | null): string {
  if (ms === null || ms < 0) return '—'
  if (ms < 1000)  return `${ms}ms`
  const s = Math.round(ms / 1000)
  if (s < 60)    return `${s}s`
  const m = Math.floor(s / 60), rem = s % 60
  return `${m}m${rem > 0 ? `${rem}s` : ''}`
}

type TFn = (key: string, opts?: Record<string, unknown>) => string

function fmtAge(ms: number | null, t: TFn): string {
  if (ms === null) return '—'
  const s = Math.floor(ms / 1000)
  if (s < 5)  return t('pools.age.now')
  if (s < 60) return t('pools.age.seconds', { s })
  const m = Math.floor(s / 60)
  return t('pools.age.minutes', { m })
}

function statusConfig(op: PoolOp['op_status']) {
  switch (op) {
    case 'available': return { dot: '#22c55e', labelKey: 'pools.status.available', bg: '#22c55e18' }
    case 'queued':    return { dot: '#eab308', labelKey: 'pools.status.queued',    bg: '#eab30818' }
    case 'empty':     return { dot: '#ef4444', labelKey: 'pools.status.empty',     bg: '#ef444418' }
    default:          return { dot: '#6b7280', labelKey: 'pools.status.unknown',   bg: '#6b728018' }
  }
}

// ── API ────────────────────────────────────────────────────────────────────────

async function fetchPools(tenantId: string): Promise<{ items: PoolOp[]; summary: OpSummary | null }> {
  const res = await apiFetch('/v1/operational/pools', {
    headers: { 'x-tenant-id': tenantId },
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const body = await res.json()
  return { items: body.items ?? [], summary: body.summary ?? null }
}

async function fetchQueue(tenantId: string, poolId: string): Promise<QueueEntry[]> {
  const res = await apiFetch(`/v1/operational/pools/${poolId}/queue`, {
    headers: { 'x-tenant-id': tenantId },
  })
  if (!res.ok) return []
  const body = await res.json()
  return body.queue ?? []
}

// ── Item 7a — donuts "total e como está sendo consumido" ──────────────────────

const DONUT_COLORS = ['#1B4F8A', '#2D9CDB', '#00B4D8', '#059669', '#D97706', '#7C3AED', '#DC2626', '#0891B2', '#EAB308', '#64748B']
const FREE_COLOR   = '#E5E7EB'

function ConsumptionDonut({
  title, subtitle, slices, free, freeLabel, height = 150,
}: {
  title: string; subtitle?: string
  slices: Array<{ name: string; value: number }>
  free: number | null; freeLabel: string; height?: number
}) {
  const data = [
    ...slices.filter(s => s.value > 0),
    ...(free !== null && free > 0 ? [{ name: freeLabel, value: free, _free: true }] : []),
  ]
  const total = data.reduce((s, d) => s + d.value, 0)
  return (
    <div className="rounded-xl bg-white border border-border p-3 flex-1 min-w-[220px]">
      <div className="text-xs font-semibold text-dark">{title}</div>
      {subtitle && <div className="text-2xs text-muted">{subtitle}</div>}
      {total === 0 ? (
        <div className="flex items-center justify-center text-2xs text-muted-light" style={{ height }}>—</div>
      ) : (
        <ResponsiveContainer width="100%" height={height}>
          <PieChart>
            <Pie data={data} dataKey="value" nameKey="name" cx="50%" cy="50%"
                 innerRadius={36} outerRadius={56} paddingAngle={1}>
              {data.map((d, i) => (
                <Cell key={i} fill={(d as { _free?: boolean })._free ? FREE_COLOR : DONUT_COLORS[i % DONUT_COLORS.length]} />
              ))}
            </Pie>
            <Tooltip />
          </PieChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}

// `MiniDonut` REMOVIDO (fatia 3, 2026-08-02). Desenhava uma rosquinha por RESERVA de
// pool (`session_reservation`); os baldes reservados eram fatia de SESSÃO do pote misto
// `C_ai + C_human`, zero pools os usavam, e saíram inteiros. Sem eles o componente ficou
// sem chamador — e componente sem chamador que desenha um modelo abandonado é
// exatamente o que faz o modelo voltar.

// ── PoolStatusCard — reusable for dashboard (Task #35) ─────────────────────────

export function PoolStatusCard({ pool, compact = false }: { pool: PoolOp; compact?: boolean }) {
  const { t } = useTranslation('contacts')
  const st = statusConfig(pool.op_status)
  return (
    <div
      className="rounded-xl bg-white border p-4 flex flex-col gap-3"
      style={{ borderColor: '#e2e8f0', borderTop: `3px solid ${st.dot}` }}>

      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-sm font-bold text-dark truncate">{pool.pool_id}</div>
          {pool.description && !compact && (
            <div className="text-xs text-muted mt-0.5 truncate">{pool.description}</div>
          )}
        </div>
        <span className="text-xs font-semibold px-2 py-0.5 rounded-full whitespace-nowrap flex-shrink-0"
          style={{ background: st.bg, color: st.dot }}>
          {t(st.labelKey)}
        </span>
      </div>

      {/* Metrics grid */}
      <div className="grid grid-cols-3 gap-2">
        <Metric label={t('pools.card.available')} value={pool.available ?? '—'} color="#22c55e" />
        <Metric label={t('pools.card.inSession')} value={pool.available === null || !pool.has_snapshot ? '—' : String(pool.queue_length > 0 ? '~' : pool.available)} color="#3b82f6" />
        <Metric label={t('pools.card.queue')}     value={pool.queue_length}   color={pool.queue_length > 0 ? '#eab308' : '#6b7280'} />
      </div>

      {/* Wait + SLA */}
      {!compact && (
        <div className="flex gap-4 text-xs text-muted">
          <span>{t('pools.card.estWait')}: <span className="text-dark">{fmtMs(pool.estimated_wait_ms)}</span></span>
          <span>SLA: <span className="text-dark">{fmtMs(pool.sla_target_ms)}</span></span>
        </div>
      )}

      {/* Channels */}
      {!compact && pool.channel_types.length > 0 && (
        <div className="flex gap-1 flex-wrap">
          {pool.channel_types.map(ch => (
            <span key={ch} className="text-2xs px-1.5 py-0.5 rounded bg-surface-alt text-muted">{ch}</span>
          ))}
        </div>
      )}
    </div>
  )
}

function Metric({ label, value, color }: { label: string; value: number | string; color: string }) {
  return (
    <div className="flex flex-col items-center rounded-lg py-2 bg-surface-alt">
      <span className="text-lg font-bold" style={{ color }}>{value}</span>
      <span className="text-2xs text-muted mt-0.5 text-center leading-tight">{label}</span>
    </div>
  )
}

// ── QueueDrilldown ─────────────────────────────────────────────────────────────

function QueueDrilldown({ tenantId, poolId, slaMs }: { tenantId: string; poolId: string; slaMs: number }) {
  const { t } = useTranslation('contacts')
  const [queue,   setQueue]   = useState<QueueEntry[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    fetchQueue(tenantId, poolId)
      .then(setQueue)
      .finally(() => setLoading(false))
    const id = setInterval(() => {
      fetchQueue(tenantId, poolId).then(setQueue)
    }, 15_000)
    return () => clearInterval(id)
  }, [tenantId, poolId])

  if (loading) {
    return (
      <div className="flex items-center gap-2 py-4 px-6 text-xs text-muted-light">
        <Spinner /> {t('pools.queue.loading')}
      </div>
    )
  }

  if (queue.length === 0) {
    return (
      <div className="py-4 px-6 text-xs text-muted-light italic">
        {t('pools.queue.empty')}
      </div>
    )
  }

  return (
    <div className="px-6 py-3">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-muted border-b border-border">
            <th className="text-left py-1.5 pr-4 font-medium w-12">{t('pools.queue.columns.position')}</th>
            <th className="text-left py-1.5 pr-4 font-medium">{t('pools.queue.columns.session')}</th>
            <th className="text-left py-1.5 pr-4 font-medium">{t('pools.queue.columns.waiting')}</th>
            <th className="text-left py-1.5 font-medium">{t('pools.queue.columns.estWait')}</th>
          </tr>
        </thead>
        <tbody>
          {queue.map(entry => {
            const overSla = entry.wait_ms > slaMs
            return (
              <tr key={entry.session_id} className="border-b border-border">
                <td className="py-2 pr-4 text-muted font-mono">{entry.position}</td>
                <td className="py-2 pr-4 font-mono text-secondary">{entry.session_id}</td>
                <td className="py-2 pr-4" style={{ color: overSla ? '#DC2626' : '#1e293b' }}>
                  {fmtMs(entry.wait_ms)}
                  {overSla && <span className="ml-1.5 text-2xs bg-red-light text-red-text px-1 rounded">{t('pools.slaExceeded')}</span>}
                </td>
                <td className="py-2 text-muted">{fmtMs(entry.estimated_wait_ms)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── PoolsPage ──────────────────────────────────────────────────────────────────

export default function PoolsPage() {
  const { tenantId, session } = useAuth()
  const { t, i18n } = useTranslation('contacts')

  const [pools,        setPools]        = useState<PoolOp[]>([])
  const [summary,      setSummary]      = useState<OpSummary | null>(null)
  const [loading,      setLoading]      = useState(false)
  const [error,        setError]        = useState<string | null>(null)
  const [lastRefresh,  setLastRefresh]  = useState<Date | null>(null)
  const [expandedPool, setExpandedPool] = useState<string | null>(null)
  const [filterStatus, setFilterStatus] = useState<string>('')
  const [filterPool,   setFilterPool]   = useState<string>('')

  const load = useCallback(async () => {
    if (!tenantId) return
    setLoading(true); setError(null)
    try {
      const data = await fetchPools(tenantId)
      setPools(data.items)
      setSummary(data.summary)
      setLastRefresh(new Date())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [tenantId])

  useEffect(() => {
    load()
    const id = setInterval(load, 15_000)
    return () => clearInterval(id)
  }, [load])

  if (!tenantId) {
    return (
      <div className="flex items-center justify-center h-full text-muted-light text-sm">
        {t('noTenant')}
      </div>
    )
  }

  // Filter
  const filtered = pools.filter(p => {
    if (filterStatus && p.op_status !== filterStatus) return false
    if (filterPool   && p.pool_id !== filterPool)     return false
    return true
  })

  // Summary counts.
  // `queue_length` é aditivo (fila é fato do pool); `available` NÃO é — capacidade é do
  // RECURSO, e um humano de 3 vagas em 3 pools somava 9 para 3 vagas (defeito C, F4b).
  // A disponibilidade vem do rollup deduplicado, por TIPO de licença.
  const totalQueued    = pools.reduce((s, p) => s + p.queue_length, 0)
  const poolsWithQueue = pools.filter(p => p.queue_length > 0).length

  return (
    <div className="flex flex-col h-full overflow-hidden bg-surface-muted">

      {/* Header bar */}
      <div className="flex-shrink-0 px-5 pt-4 pb-3 bg-white border-b border-border">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-3">
            {lastRefresh && (
              <span className="text-xs text-muted-light">
                {t('pools.updatedAt', { time: lastRefresh.toLocaleTimeString(i18n.language) })}
              </span>
            )}
            {loading
              ? <Spinner />
              : <button onClick={load} className="text-xs text-muted-light hover:text-dark transition-colors px-2 py-1">{t('pools.refresh')}</button>
            }
          </div>
        </div>

        {/* Item 7a — tiles do pipeline de contatos (contrato → atendimento → filas) */}
        <div className="flex gap-3 mb-3 flex-wrap">
          {summary && (
            <>
              {/* Fatia 3: o denominador é `ai.cap` (C_ai), não mais o `contracted`
                  misto. O par antigo (`admitted_total/contracted`) respondia "quantas
                  sessões cabem no contrato" somando licença humana com licença de IA —
                  a mesma falácia de aditividade que a F4 recusou na capacidade. */}
              <SummaryPill
                label={`${t('pools.admission.aiLicense')}${summary.headroom !== null ? ` · ${t('pools.admission.headroom')} ${summary.headroom}` : ''}`}
                value={summary.ai.cap !== null ? `${summary.admitted_total}/${summary.ai.cap}` : summary.admitted_total}
                color={summary.headroom !== null && summary.headroom <= 0 ? '#DC2626' : '#1B4F8A'} />
              <SummaryPill label={t('pools.admission.inService')} value={summary.in_service} color="#059669" />
              <SummaryPill
                label={`${t('pools.admission.inQueue')} (${summary.queue_attended} ${t('pools.admission.att')} / ${summary.queue_mute} ${t('pools.admission.mute')})`}
                value={summary.queue_total}
                color={summary.queue_total > 0 ? '#eab308' : '#6b7280'} />
              <SummaryPill
                label={t('pools.admission.freeBuffer')}
                value={`${summary.buffer.used}/${summary.buffer.limit}`}
                color={summary.buffer.used >= summary.buffer.limit ? '#DC2626' : '#7C3AED'} />
            </>
          )}
          {/* F4b — uma pill por TIPO de licença, do rollup deduplicado. Sem rollup,
              "—" com o motivo: jamais voltar a somar `available` entre pools. */}
          {summary?.capacity
            ? Object.entries(summary.capacity.by_kind)
                .sort(([a], [b]) => a.localeCompare(b))
                .map(([kind, k]) => (
                  <SummaryPill
                    key={kind}
                    label={`${t('pools.summary.available')} · ${t(`pools.summary.kind.${kind}`)}`}
                    value={k.available}
                    color={kind === 'human' ? '#059669' : kind === 'ai' ? '#7C3AED' : '#D97706'} />
                ))
            : (
              <SummaryPill
                label={`${t('pools.summary.available')} · ${t(`pools.summary.capacityUnavailable.${summary?.capacity_unavailable ?? 'no_rollup'}`)}`}
                value={'—'}
                color="#6b7280" />
            )}
          <SummaryPill label={t('pools.summary.queued')}    value={totalQueued}    color={totalQueued > 0 ? '#eab308' : '#6b7280'} />
          <SummaryPill label={t('pools.summary.withQueue')} value={poolsWithQueue} color={poolsWithQueue > 0 ? '#f97316' : '#6b7280'} />
          <SummaryPill label={t('pools.summary.total')}     value={pools.length}   color="#1B4F8A" />
        </div>

        {/* Item 7a — donuts: total e como está sendo consumido */}
        {summary && (
          <div className="flex gap-3 mb-3 flex-wrap items-stretch">
            {/* Fatia 3: o donut do pote compartilhado virou o donut da LICENÇA DE IA
                (único balde com teto), e o de reservas saiu — não havia mais fatias
                por pool a desenhar depois que os baldes reservados foram removidos. */}
            <ConsumptionDonut
              title={t('pools.admission.aiDonut')}
              subtitle={summary.ai.cap !== null
                ? `${summary.ai.used}/${summary.ai.cap}` : `${summary.ai.used}`}
              slices={Object.entries(summary.ai.by_pool).map(([name, value]) => ({ name, value }))}
              free={summary.ai.cap !== null ? Math.max(0, summary.ai.cap - summary.ai.used) : null}
              freeLabel={t('pools.admission.free')} />
            <ConsumptionDonut
              title={t('pools.admission.bufferDonut')}
              subtitle={`${summary.buffer.used}/${summary.buffer.limit}`}
              slices={pools.filter(p => p.queue_mute > 0).map(p => ({ name: p.pool_id, value: p.queue_mute }))}
              free={Math.max(0, summary.buffer.limit - summary.buffer.used)}
              freeLabel={t('pools.admission.free')} />
          </div>
        )}

        {/* Filters */}
        <div className="flex items-center gap-3">
          <select
            value={filterPool}
            onChange={e => { setFilterPool(e.target.value); setExpandedPool(null) }}
            className="text-xs bg-white border border-border-strong rounded px-2.5 py-1.5 text-dark focus:outline-none focus:ring-2 focus:ring-primary/30 min-w-[180px]"
          >
            <option value="">{t('monitor.allPools')}</option>
            {pools.map(p => (
              <option key={p.pool_id} value={p.pool_id}>{p.pool_id}</option>
            ))}
          </select>
          <select
            value={filterStatus}
            onChange={e => setFilterStatus(e.target.value)}
            className="text-xs bg-white border border-border-strong rounded px-2.5 py-1.5 text-dark focus:outline-none focus:ring-2 focus:ring-primary/30"
          >
            <option value="">{t('pools.allStatuses')}</option>
            <option value="available">{t('pools.status.available')}</option>
            <option value="queued">{t('pools.status.queued')}</option>
            <option value="empty">{t('pools.status.empty')}</option>
          </select>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="mx-5 mt-3 px-3 py-2 bg-red-light border border-red/30 rounded text-xs text-red-text flex-shrink-0">
          {t('pools.loadError')}: {error}
        </div>
      )}

      {/* Table */}
      <div className="flex-1 overflow-y-auto">
        {filtered.length === 0 && !loading && (
          <div className="flex flex-col items-center justify-center py-16 text-muted-light gap-2">
            {/* AUT-10: "sem escopo" e "sem pool no tenant" produzem a MESMA tela vazia,
                e só a primeira tem conserto do lado de quem olha. Separá-las é o ponto:
                o operador sem escopo precisa saber que falta atribuição, não concluir
                que a operação está parada. */}
            {session?.accessiblePools.length === 0 ? (
              <EmptyScopeNotice />
            ) : (
              <>
                <Server className="w-10 h-10" aria-hidden="true" />
                <span className="text-sm">{t('pools.noData')}</span>
              </>
            )}
          </div>
        )}

        <table className="w-full text-sm border-collapse">
          <thead className="sticky top-0 z-10 bg-white">
            <tr className="text-muted text-xs border-b border-border">
              <th className="text-left px-5 py-3 font-medium">{t('pools.columns.pool')}</th>
              <th className="text-center px-3 py-3 font-medium w-28">{t('pools.columns.status')}</th>
              <th className="text-center px-3 py-3 font-medium w-24">{t('pools.admission.cols.inService')}</th>
              <th className="text-center px-3 py-3 font-medium w-28" title={t('pools.admission.cols.queueHint')}>{t('pools.admission.cols.queue')}</th>
              <th className="text-center px-3 py-3 font-medium w-28" title={t('pools.admission.cols.availHint')}>{t('pools.admission.cols.avail')}</th>
              <th className="text-center px-3 py-3 font-medium w-28">{t('pools.columns.estWait')}</th>
              <th className="text-center px-3 py-3 font-medium w-20">{t('pools.columns.sla')}</th>
              <th className="text-left px-3 py-3 font-medium">{t('pools.columns.channels')}</th>
              <th className="text-right px-5 py-3 font-medium w-28">{t('pools.columns.snapshot')}</th>
            </tr>
          </thead>
          <tbody>
            {/* Fatia 3: as seções passaram a separar quem debita licença POR SESSÃO
                (IA) de quem é licenciado no LOGIN (humano) — antes separavam dois
                baldes do mesmo pote misto. */}
            {[
              { key: 'licensed', label: t('pools.admission.sectionLicensed'),
                rows: filtered.filter(p => p.admission_scope === 'licensed') },
              { key: 'unlicensed', label: t('pools.admission.sectionUnlicensed'),
                rows: filtered.filter(p => p.admission_scope !== 'licensed') },
            ].filter(s => s.rows.length > 0).map(section => (
            <React.Fragment key={section.key}>
            <tr className="bg-surface-muted border-b border-border">
              <td colSpan={9} className="px-5 py-1.5 text-2xs font-semibold text-muted uppercase tracking-wider">
                {section.label}
              </td>
            </tr>
            {section.rows.map(pool => {
              const st      = statusConfig(pool.op_status)
              const expanded = expandedPool === pool.pool_id
              return (
                <React.Fragment key={pool.pool_id}>
                  <tr
                    className="border-b border-border cursor-pointer transition-colors"
                    style={{ background: expanded ? '#EBF2FA' : 'transparent' }}
                    onMouseEnter={e => { if (!expanded) (e.currentTarget as HTMLElement).style.background = '#F8FAFC' }}
                    onMouseLeave={e => { if (!expanded) (e.currentTarget as HTMLElement).style.background = 'transparent' }}
                    onClick={() => setExpandedPool(expanded ? null : pool.pool_id)}
                  >
                    {/* Pool name + description */}
                    <td className="px-5 py-3">
                      <div className="flex items-center gap-2">
                        {expanded
                          ? <ChevronDown  className="w-3.5 h-3.5 text-primary" aria-hidden="true" />
                          : <ChevronRight className="w-3.5 h-3.5 text-muted-light" aria-hidden="true" />
                        }
                        <div>
                          <div className="font-semibold text-dark text-xs">{pool.pool_id}</div>
                          {pool.description && (
                            <div className="text-xs text-muted truncate max-w-xs">{pool.description}</div>
                          )}
                        </div>
                      </div>
                    </td>

                    {/* Status */}
                    <td className="px-3 py-3 text-center">
                      <span className="text-xs font-semibold px-2 py-0.5 rounded-full"
                        style={{ background: st.bg, color: st.dot }}>
                        {t(st.labelKey)}
                      </span>
                    </td>

                    {/* Em atendimento (item 7a) */}
                    <td className="px-3 py-3 text-center">
                      <span
                        className="text-sm font-bold"
                        style={{ color: (pool.active_sessions ?? 0) > 0 ? '#059669' : '#6b7280' }}
                        title={pool.active_sessions === null
                          ? 'sem snapshot do routing-engine — ocupação por pool desconhecida'
                          : undefined}
                      >
                        {pool.active_sessions ?? '—'}
                      </span>
                    </td>

                    {/* Fila atendida/muda (item 7a) */}
                    <td className="px-3 py-3 text-center">
                      {pool.queue_length > 0
                        ? <span className="text-sm font-bold text-warning tabular-nums">
                            {pool.queue_attended}<span className="text-2xs text-muted"> at</span>
                            {' / '}{pool.queue_mute}<span className="text-2xs text-muted"> mu</span>
                          </span>
                        : <span className="text-xs text-muted-light">0</span>
                      }
                    </td>

                    {/* Disponível: físico / admissível (item 7a — dois números) */}
                    <td className="px-3 py-3 text-center">
                      {pool.has_snapshot
                        ? <span className="text-sm tabular-nums">
                            {/* `null` = desconhecido (bootstrap omitiu por membro que
                                não gerencia). "—" com o motivo no title; renderizar 0
                                aqui era a mentira que o backend parou de contar. */}
                            <span className="font-bold"
                                  title={pool.available === null && pool.capacity_unknown
                                    ? t(`pools.capacityUnknown.${pool.capacity_unknown}`) : undefined}
                                  style={{ color: pool.available === null ? '#6b7280'
                                    : pool.available > 0 ? '#22c55e' : '#6b7280' }}>
                              {pool.available === null ? '—' : pool.available}
                            </span>
                            <span className="text-muted-light"> / </span>
                            <span className="font-bold" style={{
                              color: pool.admissible === null ? '#6b7280'
                                : pool.admissible > 0 ? '#1B4F8A' : '#DC2626' }}>
                              {pool.admissible === null ? '∞' : pool.admissible}{pool.admissible_shared ? '⊕' : ''}
                            </span>
                          </span>
                        : <span className="text-xs text-warning" title="Snapshot Redis expirado — aguardando próximo evento de roteamento">{t('pools.noSnapshot')}</span>
                      }
                    </td>

                    {/* Estimated wait */}
                    <td className="px-3 py-3 text-center text-xs text-muted">
                      {fmtMs(pool.estimated_wait_ms)}
                    </td>

                    {/* SLA */}
                    <td className="px-3 py-3 text-center text-xs text-muted">
                      {fmtMs(pool.sla_target_ms)}
                    </td>

                    {/* Channels */}
                    <td className="px-3 py-3">
                      <div className="flex gap-1 flex-wrap">
                        {pool.channel_types.map(ch => (
                          <span key={ch} className="text-2xs px-1.5 py-0.5 rounded bg-surface-alt text-muted">{ch}</span>
                        ))}
                      </div>
                    </td>

                    {/* Snapshot age */}
                    <td className="px-5 py-3 text-right">
                      <span className={`text-xs ${
                        pool.snapshot_age_ms === null          ? 'text-muted-light'
                        : pool.snapshot_age_ms > 90_000        ? 'text-red'
                        : pool.snapshot_age_ms > 30_000        ? 'text-warning'
                        : 'text-muted'
                      }`}>
                        {fmtAge(pool.snapshot_age_ms, t)}
                      </span>
                    </td>
                  </tr>

                  {/* Queue drill-down row */}
                  {expanded && (
                    <tr key={`${pool.pool_id}-queue`} className="border-b border-border">
                      <td colSpan={9} className="p-0 bg-surface-muted">
                        <div className="pl-8 border-l-2 border-primary/30 ml-5 my-1">
                          <div className="px-4 pt-2 pb-1 text-xs font-semibold text-muted uppercase tracking-wider">
                            {t('pools.queueOf', { pool: pool.pool_id })}
                          </div>
                          <QueueDrilldown
                            tenantId={tenantId}
                            poolId={pool.pool_id}
                            slaMs={pool.sla_target_ms}
                          />
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              )
            })}
            </React.Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── SummaryPill ────────────────────────────────────────────────────────────────

function SummaryPill({ label, value, color }: { label: string; value: number | string; color: string }) {
  return (
    <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-border bg-white">
      <span className="text-base font-bold" style={{ color }}>{value}</span>
      <span className="text-xs text-muted">{label}</span>
    </div>
  )
}
