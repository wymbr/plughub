/**
 * MonitorTab — visão em tempo real de todos os pools.
 *
 * Scope toggle: Sessões | Processos
 *
 * Sessões — unified view:
 *   - KPI strip: total em atendimento / disponíveis / na fila
 *   - Donut chart: distribuição de "ocupados" por pool
 *   - Tabela: todos os pools ordenados por ocupados (desc), clicável para drill-down
 *   - Drill-down: pool → sessões → segmentos → transcript
 *
 * Processos:
 *   - Lista de WorkflowInstances com filtro de status
 *   - Painel de detalhe à direita
 */
import React, { useState, useMemo, useCallback, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import { apiFetch } from '@/api/apiFetch'
import { Radio, Settings, ClipboardList, X, Clock, AlertTriangle, BarChart2 } from 'lucide-react'
import type { ContactFilters } from '../types'
import { usePoolViews } from '@/modules/service/api/hooks'
import { SessionList }      from '@/modules/service/components/SessionList'
import { SegmentList }      from '@/modules/service/components/SegmentList'
import { SessionTranscript } from '@/modules/service/components/SessionTranscript'
import type { PoolView, ContactSegment, Metrics24h } from '@/modules/service/types'
import { scoreToAccent, formatMs } from '@/modules/service/utils/sentiment'
import {
  useWorkflowInstances, useWorkflowInstance,
} from '@/modules/workflows/api/hooks'
import type { WorkflowInstance, WorkflowStatus } from '@/modules/workflows/api/hooks'

interface Props {
  tenantId: string
  filters:  ContactFilters
}

// ── Brand colour palette for pool slices ──────────────────────────────────
const POOL_COLORS = [
  '#1B4F8A', '#2D9CDB', '#00B4D8', '#059669', '#D97706',
  '#7C3AED', '#DC2626', '#0891B2', '#65A30D', '#9333EA',
  '#0284C7', '#16A34A', '#CA8A04', '#9F1239', '#4338CA',
]

// ── Connection pill ────────────────────────────────────────────────────────
function ConnectionPill({ status }: { status: string }) {
  const { t } = useTranslation('contacts')
  const map: Record<string, { bg: string; text: string; key: string }> = {
    connecting: { bg: '#fef3c7', text: '#92400e', key: 'connecting' },
    connected:  { bg: '#d1fae5', text: '#065f46', key: 'connected'  },
    error:      { bg: '#fee2e2', text: '#991b1b', key: 'error'      },
    closed:     { bg: '#f1f5f9', text: '#475569', key: 'closed'     },
  }
  const c = map[status] ?? map.closed
  return (
    <span className="inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full"
      style={{ backgroundColor: c.bg, color: c.text }}>
      {status === 'connected' && <span className="w-1.5 h-1.5 rounded-full bg-green animate-pulse inline-block" />}
      {t(`monitor.pill.${c.key}`)}
    </span>
  )
}

// ── KPI card ──────────────────────────────────────────────────────────────
function KpiCard({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="bg-white rounded-xl border border-border px-5 py-3 flex flex-col items-center min-w-[110px]">
      <span className="text-3xl font-black tabular-nums leading-none" style={{ color }}>{value}</span>
      <span className="text-xs text-muted mt-1 text-center leading-tight">{label}</span>
    </div>
  )
}

// ── F4b — capacidade deduplicada por tipo de licença ──────────────────────
//
// Publicada pelo Routing Engine em `{t}:capacity:snapshot` sobre instâncias
// DISTINTAS, e repassada pelo /v1/operational/pools. Não existe campo escalar de
// disponibilidade: humano e IA são moedas não-fungíveis.
export interface TenantCapacity {
  by_kind: Record<string, {
    total_capacity: number; used: number; available: number; instances: number
  }>
  computed_at: string
}

const KIND_COLOR: Record<string, string> = {
  human:   '#059669',
  ai:      '#7C3AED',
  unknown: '#D97706',   // config contraditória — visível de propósito
}

/** Um cartão por tipo. Recebe `t` explicitamente: helper fora de componente não pode
 *  chamar `useTranslation` (invariante de i18n do CLAUDE.md). */
function renderCapacityKpis(
  adm: { capacity: TenantCapacity | null; capacity_unavailable: string | null } | null,
  t:   (k: string, o?: Record<string, unknown>) => string,
) {
  if (!adm) return null
  if (!adm.capacity) {
    // Ausência com MOTIVO, e nunca a soma como fallback — a soma é o defeito.
    // `scope_limited`: o rollup é do tenant e não projeta sobre um subconjunto de
    // pools; mostrar o número cheio a um supervisor restrito vazaria capacidade.
    return (
      <div className="bg-white rounded-xl border border-border px-5 py-3 flex flex-col items-center min-w-[110px]">
        <span className="text-3xl font-black tabular-nums leading-none text-muted-light">—</span>
        <span className="text-xs text-muted mt-1 text-center leading-tight">
          {t('monitor.kpi.available')}
          <br />
          <span className="text-[10px] text-muted-light">
            {t(`monitor.kpi.capacityUnavailable.${adm.capacity_unavailable ?? 'no_rollup'}`)}
          </span>
        </span>
      </div>
    )
  }
  return Object.entries(adm.capacity.by_kind)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([kind, k]) => (
      <KpiCard
        key={kind}
        label={`${t('monitor.kpi.available')} · ${t(`monitor.kpi.kind.${kind}`)} (${k.instances})`}
        value={k.available}
        color={KIND_COLOR[kind] ?? '#6b7280'}
      />
    ))
}

// ── Donut chart — pure SVG, multi-arc ─────────────────────────────────────
function PoolDonut({ pools, colorMap, selectedPool, onSelect }: {
  pools:        PoolView[]
  colorMap:     Record<string, string>
  selectedPool: string | null
  onSelect:     (id: string) => void
}) {
  const { t } = useTranslation('contacts')
  const totalBusy = pools.reduce((s, p) => s + p.busy, 0)

  const r = 68, cx = 90, cy = 90
  const circ = 2 * Math.PI * r

  // Build sequential arcs from the sorted list
  let cumOffset = 0
  const arcs = pools.map(pool => {
    const arcLen     = totalBusy > 0 ? (pool.busy / totalBusy) * circ : 0
    const dashOffset = -cumOffset
    cumOffset += arcLen
    return { pool, color: colorMap[pool.pool_id] ?? '#cbd5e1', arcLen, dashOffset }
  })

  return (
    <div className="flex flex-col items-center">
      <svg width="180" height="180" viewBox="0 0 180 180" aria-label={t('monitor.donut.title')}>
        {/* Track ring */}
        <circle cx={cx} cy={cy} r={r} fill="none" stroke="#f1f5f9" strokeWidth="24" />

        {totalBusy === 0 ? (
          <circle cx={cx} cy={cy} r={r} fill="none" stroke="#e2e8f0" strokeWidth="24" />
        ) : (
          arcs.filter(a => a.arcLen > 0).map(arc => (
            <circle
              key={arc.pool.pool_id}
              cx={cx} cy={cy} r={r}
              fill="none"
              stroke={arc.color}
              strokeWidth={selectedPool === arc.pool.pool_id ? 30 : 24}
              strokeDasharray={`${arc.arcLen} ${circ - arc.arcLen}`}
              strokeDashoffset={arc.dashOffset}
              strokeLinecap="butt"
              style={{ cursor: 'pointer', transition: 'stroke-width 0.15s', transformOrigin: `${cx}px ${cy}px`, transform: 'rotate(-90deg)' }}
              onClick={() => onSelect(arc.pool.pool_id)}
            />
          ))
        )}

        {/* Center: total em atendimento */}
        <text x={cx} y={cy - 8} textAnchor="middle" fontSize="26" fontWeight="800" fill="#111827">
          {totalBusy}
        </text>
        <text x={cx} y={cy + 12} textAnchor="middle" fontSize="9" fill="#6b7280" fontWeight="500">
          em atendimento
        </text>
      </svg>

      {/* Legend */}
      <div className="flex flex-col gap-1 mt-1 w-full max-w-[170px]">
        {arcs.map(arc => {
          const pct = totalBusy > 0 ? Math.round((arc.pool.busy / totalBusy) * 100) : 0
          const isSelected = selectedPool === arc.pool.pool_id
          return (
            <button
              key={arc.pool.pool_id}
              onClick={() => onSelect(arc.pool.pool_id)}
              className="flex items-center gap-2 text-left rounded px-1.5 py-0.5 hover:bg-surface-alt transition-colors"
              style={{ fontWeight: isSelected ? 700 : 400 }}
            >
              <span className="w-2.5 h-2.5 rounded-sm flex-shrink-0" style={{ backgroundColor: arc.color }} />
              <span className="text-xs text-dark truncate flex-1 min-w-0">
                {arc.pool.pool_id.replace(/_/g, ' ')}
              </span>
              <span className="text-xs font-mono text-muted-light flex-shrink-0 ml-auto">
                {arc.pool.busy}
                {totalBusy > 0 && <span className="text-muted ml-0.5">({pct}%)</span>}
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

// ── Channel colour palette ────────────────────────────────────────────────
const CHANNEL_COLORS: Record<string, string> = {
  webchat:   '#1B4F8A',
  whatsapp:  '#25D366',
  voice:     '#7C3AED',
  email:     '#D97706',
  sms:       '#0891B2',
  instagram: '#E1306C',
  telegram:  '#26A5E4',
  webrtc:    '#10B981',
  webhook:   '#6366F1',  // Arc 19: webhook channel (indigo)
}

// ── Channel donut — shows contact traffic distribution by channel ─────────
// Data source: Metrics24h.sessions.by_channel — contacts actually handled
// today per channel.  Fallback (when metrics not yet loaded): distributes
// pool busy counts across channel_types.  The donut must reflect traffic,
// not capacity/resource counts.
function ChannelDonut({ metrics, pools }: { metrics: Metrics24h | null; pools: PoolView[] }) {
  const { t } = useTranslation('contacts')

  // Build channel → count map.
  // Primary: Metrics24h.sessions.by_channel (actual contacts today per channel).
  // Fallback: distribute each pool's busy count across its channel_types.
  const channelMap: Record<string, number> = useMemo(() => {
    if (metrics && Object.keys(metrics.sessions.by_channel).length > 0) {
      return metrics.sessions.by_channel
    }
    // Fallback: use live busy count distributed across channel types.
    // When a pool serves a single channel this is exact; multi-channel pools
    // distribute evenly — still better than showing capacity.
    const m: Record<string, number> = {}
    for (const p of pools) {
      const channels = p.channel_types ?? []
      if (channels.length === 0 || p.busy === 0) continue
      const share = p.busy / channels.length
      for (const ch of channels) {
        m[ch] = (m[ch] ?? 0) + share
      }
    }
    // Round to integers for display
    return Object.fromEntries(Object.entries(m).map(([k, v]) => [k, Math.round(v)]))
  }, [metrics, pools])

  const entries = Object.entries(channelMap)
    .map(([ch, count]) => ({ ch, count }))
    .filter(e => e.count > 0)
    .sort((a, b) => b.count - a.count)
  const total = entries.reduce((s, e) => s + e.count, 0)

  const r = 52, cx = 70, cy = 70
  const circ = 2 * Math.PI * r
  let cumOffset = 0
  const arcs = entries.map(e => {
    const arcLen     = total > 0 ? (e.count / total) * circ : 0
    const dashOffset = -cumOffset
    cumOffset += arcLen
    return { ...e, arcLen, dashOffset, color: CHANNEL_COLORS[e.ch] ?? '#cbd5e1' }
  })

  const centerLabel = t('monitor.donutChannel.centerLabel')

  return (
    <div className="flex flex-col items-center w-full">
      <svg width="140" height="140" viewBox="0 0 140 140" aria-label={t('monitor.donutChannel.title')}>
        <circle cx={cx} cy={cy} r={r} fill="none" stroke="#f1f5f9" strokeWidth="20" />
        {total === 0 ? (
          <circle cx={cx} cy={cy} r={r} fill="none" stroke="#e2e8f0" strokeWidth="20" />
        ) : (
          arcs.filter(a => a.arcLen > 0).map(arc => (
            <circle key={arc.ch} cx={cx} cy={cy} r={r}
              fill="none" stroke={arc.color} strokeWidth="20"
              strokeDasharray={`${arc.arcLen} ${circ - arc.arcLen}`}
              strokeDashoffset={arc.dashOffset}
              style={{ transformOrigin: `${cx}px ${cy}px`, transform: 'rotate(-90deg)' }}
            />
          ))
        )}
        <text x={cx} y={cy - 6}  textAnchor="middle" fontSize="20" fontWeight="800" fill="#111827">{total}</text>
        <text x={cx} y={cy + 10} textAnchor="middle" fontSize="8"  fill="#6b7280" fontWeight="500">{centerLabel}</text>
      </svg>
      <div className="flex flex-col gap-0.5 w-full max-w-[160px]">
        {arcs.map(arc => (
          <div key={arc.ch} className="flex items-center gap-1.5 px-1">
            <span className="w-2 h-2 rounded-sm flex-shrink-0" style={{ backgroundColor: arc.color }} />
            <span className="text-xs text-dark truncate flex-1 min-w-0 capitalize">{arc.ch}</span>
            <span className="text-xs font-mono text-muted flex-shrink-0">{arc.count}</span>
          </div>
        ))}
        {total === 0 && (
          <p className="text-xs text-muted text-center mt-1">{t('monitor.donutChannel.noData')}</p>
        )}
      </div>
    </div>
  )
}

// ── Pool table row ─────────────────────────────────────────────────────────
function PoolRow({ pool, color, selected, onDrillDown }: {
  pool:       PoolView
  color:      string
  selected:   boolean
  onDrillDown:() => void
}) {
  // total_instances = CAPACIDADE total do pool (soma de `max_concurrent`), NÃO
  //                   contagem de agentes — o nome é herança do modelo antigo.
  //                   Para pool de IA (max_concurrent=1) os dois coincidem, e é por
  //                   isso que a divergência sobreviveu tanto tempo sem sintoma.
  // available       = total_capacity − ocupação do RECURSO (inclui as vagas que
  //                   pools IRMÃOS consumiram do mesmo agente — capacidade é do
  //                   recurso e não fragmenta por pool). Fatia 2.
  // busy            = sessões servidas NESTE pool (projeção pela tag do ocupante);
  //                   o resto do consumo do recurso está em `busy_elsewhere`.
  // Ocupação        = sessões deste pool / capacidade total.
  const displayTotal = pool.total_instances ?? pool.available
  const displayAvail = pool.available
  const occPct = displayTotal > 0 && pool.busy > 0
    ? Math.round((pool.busy / displayTotal) * 100)
    : 0

  return (
    <tr
      onClick={onDrillDown}
      className="cursor-pointer transition-colors hover:bg-primary/5"
      style={{ background: selected ? '#eff6ff' : undefined }}
    >
      {/* Pool name */}
      <td className="px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="w-2.5 h-2.5 rounded-sm flex-shrink-0" style={{ backgroundColor: color }} />
          <span className="text-sm font-medium text-dark truncate max-w-[200px]">
            {pool.pool_id.replace(/_/g, ' ')}
          </span>
          {pool.channel_types?.includes('webhook') && (
            <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded flex-shrink-0"
              style={{ background: '#6366f120', color: '#6366F1', border: '1px solid #6366f140' }}>
              webhook
            </span>
          )}
        </div>
      </td>
      {/* Busy sessions */}
      <td className="px-4 py-3 text-center">
        <span className="font-bold text-base tabular-nums text-secondary">{pool.busy}</span>
      </td>
      {/* Available = Total - Busy */}
      <td className="px-4 py-3 text-center">
        <span className="font-semibold text-sm tabular-nums text-green-text">{displayAvail}</span>
      </td>
      {/* Total = distinct instances dimensioned to pool (ready_set ∪ busy_set) */}
      <td className="px-4 py-3 text-center">
        <span className="font-semibold text-sm tabular-nums text-muted"
          title="Total de instâncias registradas no pool (prontas + ocupadas)">
          {displayTotal > 0 ? displayTotal : '—'}
        </span>
      </td>
      {/* Occupation % bar */}
      <td className="px-4 py-3">
        <div className="flex items-center gap-2">
          <div className="flex-1 h-1.5 bg-surface-alt rounded-full overflow-hidden min-w-[60px]">
            <div className="h-full rounded-full transition-all duration-500"
              style={{ width: `${occPct}%`, backgroundColor: color }} />
          </div>
          <span className="text-xs text-muted tabular-nums w-9 text-right flex-shrink-0">{occPct}%</span>
        </div>
      </td>
      {/* Queue */}
      <td className="px-4 py-3 text-center">
        <span className={`font-semibold text-sm tabular-nums ${pool.queue_length > 0 ? 'text-warning' : 'text-muted-light'}`}>
          {pool.queue_length}
        </span>
      </td>
      {/* SLA target */}
      <td className="px-4 py-3 text-center text-xs text-muted">
        {pool.sla_target_ms ? formatMs(pool.sla_target_ms) : '—'}
      </td>
      {/* Avg wait (last 1h from ClickHouse) */}
      <td className="px-4 py-3 text-center">
        {pool.avg_wait_ms !== null
          ? (() => {
              // Color: green < 50% of target, yellow 50-100%, red > target
              const ratio = pool.sla_target_ms > 0 ? pool.avg_wait_ms / pool.sla_target_ms : null
              const color = ratio === null ? '#6b7280'
                : ratio < 0.5  ? '#059669'
                : ratio <= 1.0 ? '#d97706'
                : '#dc2626'
              return (
                <span className="text-xs font-semibold tabular-nums" style={{ color }}
                  title={`p90: ${formatMs(pool.p90_wait_ms ?? 0)}`}>
                  {formatMs(pool.avg_wait_ms)}
                </span>
              )
            })()
          : <span className="text-xs text-muted-light">—</span>}
      </td>
      {/* SLA compliance % */}
      <td className="px-4 py-3 text-center">
        {pool.sla_compliance_pct !== null
          ? (() => {
              const pct   = pool.sla_compliance_pct
              const color = pct >= 90 ? '#059669' : pct >= 70 ? '#d97706' : '#dc2626'
              return (
                <span className="text-xs font-bold tabular-nums" style={{ color }}
                  title={`${pool.sla_sessions_count} sessões na última 1h`}>
                  {pct.toFixed(1)}%
                </span>
              )
            })()
          : <span className="text-xs text-muted-light">—</span>}
      </td>
      {/* Drill arrow */}
      <td className="px-3 py-3 text-muted-light text-right text-sm">›</td>
    </tr>
  )
}

// ── Unified pools overview — KPIs + donut + sorted table ──────────────────
function PoolsOverview({ pools, metrics, selectedPool, onPoolClick, isStale, lastUpdated }: {
  pools:        PoolView[]
  metrics:      Metrics24h | null
  selectedPool: string | null
  onPoolClick:  (poolId: string) => void
  isStale:      boolean
  lastUpdated:  number | null
}) {
  const { t } = useTranslation('contacts')
  const { tenantId } = useAuth()

  // `busy` e `queue_length` SÃO aditivos: um ocupante carrega exatamente uma tag de
  // pool, e fila é fato do pool. Somá-los é legítimo.
  const totalBusy      = pools.reduce((s, p) => s + p.busy, 0)
  const totalQueue     = pools.reduce((s, p) => s + p.queue_length, 0)
  // `available` e `total_instances` NÃO são aditivos — foi o defeito **C** (F4b).
  // A capacidade é do RECURSO: um humano de 3 vagas logado em 3 pools aparece —
  // corretamente — com 3 em cada linha, e a soma dizia 9 para 3 vagas. Somar melhor
  // não resolve: a informação de sobreposição não existe nas linhas. Os dois números
  // agora vêm do rollup deduplicado (`summary.capacity`), e POR TIPO DE LICENÇA —
  // humano e IA não se substituem, então um total único responderia "há 356 agentes"
  // para quem perguntou se há atendente humano. Ver
  // docs/product/shared-capacity-pool-as-tag-design.md §3.

  // Item 7a (capacity-governance): tiles de contrato e sala de espera gratuita —
  // mesmos agregados do Monitor/Pools (summary do /v1/operational/pools).
  // Fatia 3 (2026-08-02): o tile de contrato passou a mostrar a LICENÇA DE IA
  // (`ai.cap`), não o `contracted` misto — este último soma licença humana com
  // licença de IA e por isso não é teto de nada.
  const [adm, setAdm] = useState<{
    contracted: number | null; admitted_total: number; headroom: number | null
    ai: { cap: number | null; used: number }
    buffer: { used: number; limit: number }
    capacity: TenantCapacity | null
    capacity_unavailable: string | null
  } | null>(null)
  useEffect(() => {
    if (!tenantId) return
    let cancelled = false
    const load = () => {
      apiFetch('/v1/operational/pools', { headers: { 'x-tenant-id': tenantId } })
        .then(r => r.ok ? r.json() : Promise.reject(r.status))
        .then((d: { summary?: typeof adm }) => { if (!cancelled && d.summary) setAdm(d.summary) })
        .catch(() => { /* tiles ficam ocultos */ })
    }
    load()
    const id = setInterval(load, 15_000)
    return () => { cancelled = true; clearInterval(id) }
  }, [tenantId])

  // Sort by busy descending, then alphabetical
  const sorted = useMemo(() =>
    [...pools].sort((a, b) => b.busy - a.busy || a.pool_id.localeCompare(b.pool_id)),
    [pools]
  )

  // Assign consistent colors in sort order
  const colorMap = useMemo(() => {
    const m: Record<string, string> = {}
    sorted.forEach((p, i) => { m[p.pool_id] = POOL_COLORS[i % POOL_COLORS.length] })
    return m
  }, [sorted])

  // Show loading state only when no data has ever arrived (first connect)
  if (pools.length === 0 && !isStale) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 text-muted">
        <Radio className="w-10 h-10 animate-pulse text-muted-light" aria-hidden="true" />
        <span className="text-sm">{t('monitor.noData')}</span>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full gap-4 p-4 overflow-auto">

      {/* Stale data banner — shown when Redis TTL expired (>125s since last real data) */}
      {isStale && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-yellow-50 border border-yellow-200 text-yellow-800 text-xs">
          <AlertTriangle className="w-4 h-4 flex-shrink-0 text-yellow-600" aria-hidden="true" />
          <span className="font-medium">{t('monitor.stale.label')}</span>
          {lastUpdated && (
            <span className="text-yellow-700">
              {t('monitor.stale.lastSeen', { time: new Date(lastUpdated).toLocaleTimeString() })}
            </span>
          )}
        </div>
      )}

      {/* KPI strip */}
      <div className="flex gap-3 flex-wrap">
        <KpiCard label={t('monitor.kpi.active')}    value={totalBusy}      color="#1B4F8A" />
        {/* F4b — um cartão POR TIPO de licença. Não existe cartão "disponível" único:
            humano e IA não se substituem, e o total somado responderia a pergunta
            errada. Rollup ausente → "—" com o motivo, nunca a soma das linhas. */}
        {renderCapacityKpis(adm, t)}
        <KpiCard label={t('monitor.kpi.queue')}     value={totalQueue}     color="#d97706" />
        {/* Item 7a — contrato e sala de espera gratuita */}
        {adm && adm.ai.cap !== null && (
          <KpiCard
            label={`${t('pools.admission.aiLicense')} · ${t('pools.admission.headroom')} ${adm.headroom ?? '—'}`}
            value={`${adm.admitted_total}/${adm.ai.cap}` as unknown as number}
            color={adm.headroom !== null && adm.headroom <= 0 ? '#DC2626' : '#1B4F8A'} />
        )}
        {adm && (
          <KpiCard
            label={t('pools.admission.freeBuffer')}
            value={`${adm.buffer.used}/${adm.buffer.limit}` as unknown as number}
            color={adm.buffer.used >= adm.buffer.limit ? '#DC2626' : '#7C3AED'} />
        )}
      </div>

      {/* Main content: donuts + table */}
      <div className="flex gap-4 flex-1 min-h-0">

        {/* Left panel: two donuts stacked — overflow-y-auto so both are reachable */}
        <div className="flex-shrink-0 flex flex-col gap-3 w-[210px] overflow-y-auto">

          {/* Pool donut — busy sessions by pool */}
          <div className="bg-white rounded-xl border border-border p-4 flex flex-col items-center">
            <p className="text-xs font-semibold text-muted uppercase tracking-wide mb-3 text-center">
              {t('monitor.donut.title')}
            </p>
            <PoolDonut
              pools={sorted}
              colorMap={colorMap}
              selectedPool={selectedPool}
              onSelect={onPoolClick}
            />
          </div>

          {/* Channel donut — contact traffic distribution by channel (today) */}
          <div className="bg-white rounded-xl border border-border p-4 flex flex-col items-center">
            <p className="text-xs font-semibold text-muted uppercase tracking-wide mb-3 text-center">
              {t('monitor.donutChannel.title')}
            </p>
            <ChannelDonut metrics={metrics} pools={sorted} />
          </div>

        </div>

        {/* Pool table */}
        <div className="flex-1 min-w-0 bg-white rounded-xl border border-border overflow-auto">
          <table className="w-full text-sm">
            <thead className="bg-surface-muted border-b border-border sticky top-0 z-10">
              <tr>
                {([
                  t('monitor.table.pool'),
                  t('monitor.table.busy') + ' ↓',
                  t('monitor.table.available'),
                  t('monitor.table.total'),
                  t('monitor.table.occupancy'),
                  t('monitor.table.queue'),
                  t('monitor.table.slaTarget'),
                  t('monitor.table.avgWait'),
                  t('monitor.table.slaCompliance'),
                  '',
                ]).map((col, i) => (
                  <th key={i}
                    className="text-left text-xs font-semibold text-muted uppercase tracking-wide px-4 py-2.5 whitespace-nowrap">
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {sorted.map(pool => (
                <PoolRow
                  key={pool.pool_id}
                  pool={pool}
                  color={colorMap[pool.pool_id]}
                  selected={selectedPool === pool.pool_id}
                  onDrillDown={() => onPoolClick(pool.pool_id)}
                />
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

// ── Processos (workflow instances) view ────────────────────────────────────

type MonitorScope = 'sessions' | 'processes' | 'events'

const WF_STATUS_COLORS: Record<WorkflowStatus, string> = {
  active:    '#3b82f6',
  suspended: '#eab308',
  completed: '#22c55e',
  failed:    '#ef4444',
  timed_out: '#ef4444',
  cancelled: '#6b7280',
}

function ProcessosView({ tenantId }: { tenantId: string }) {
  const { t, i18n } = useTranslation('contacts')
  const [filterStatus, setFilterStatus] = useState<WorkflowStatus | 'all'>('all')
  const [selectedId,   setSelectedId]   = useState<string | null>(null)

  const statusParam = filterStatus === 'all' ? undefined : filterStatus
  // `refresh` saiu com o botão Cancelar (2026-08-07): esta aba não tinha outro
  // consumidor dele — o polling de 10 s já mantém a lista viva.
  const { instances, loading }          = useWorkflowInstances(tenantId, statusParam, 10_000)
  const { instance: detail }            = useWorkflowInstance(selectedId, 10_000)

  const sorted = [...instances].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  )

  return (
    <div className="flex h-full overflow-hidden bg-[#0a1628] text-slate-200">
      {/* Left: list */}
      <div className="w-72 flex-shrink-0 border-r border-slate-800 flex flex-col overflow-hidden">
        {/* Status filter */}
        <div className="flex flex-wrap gap-1.5 px-3 py-2.5 border-b border-slate-800 flex-shrink-0">
          {(['all', 'active', 'suspended', 'completed', 'failed'] as const).map(s => {
            const active = filterStatus === s
            const color  = s === 'all' ? '#3b82f6' : WF_STATUS_COLORS[s as WorkflowStatus]
            return (
              <button key={s} onClick={() => { setFilterStatus(s); setSelectedId(null) }}
                className="text-xs px-2.5 py-1 rounded-md font-medium transition-all"
                style={{
                  border:  `1px solid ${active ? color : '#334155'}`,
                  background: active ? color + '22' : 'transparent',
                  color:   active ? color : '#64748b',
                  fontWeight: active ? 600 : 400,
                }}>
                {t(`processes.wfStatus.${s}`)}
              </button>
            )
          })}
        </div>

        {/* Instance list */}
        <div className="flex-1 overflow-y-auto">
          {sorted.length === 0 && !loading && (
            <div className="flex flex-col items-center justify-center py-12 text-slate-500 text-sm gap-2">
              <ClipboardList className="w-8 h-8" aria-hidden="true" />
              <span>{t('processes.instances.empty')}</span>
            </div>
          )}
          {sorted.map(inst => {
            const color    = WF_STATUS_COLORS[inst.status]
            const isSelected = inst.id === selectedId
            return (
              <div key={inst.id} onClick={() => setSelectedId(inst.id === selectedId ? null : inst.id)}
                className="px-4 py-3 cursor-pointer transition-colors hover:bg-slate-800/50"
                style={{
                  borderBottom: '1px solid #1e293b',
                  background:   isSelected ? '#1e293b' : 'transparent',
                  borderLeft:   isSelected ? `3px solid ${color}` : '3px solid transparent',
                }}>
                <div className="flex justify-between items-start gap-2">
                  <div className="flex-1 min-w-0">
                    <code className="text-xs font-semibold text-blue-300">{inst.id.slice(0, 8)}…</code>
                    <div className="text-xs text-slate-500 mt-0.5 truncate">{inst.flow_id}</div>
                    {inst.origin_session_id && (
                      <div className="text-xs text-slate-600 mt-0.5 truncate font-mono">
                        {t('processes.instances.sessionLabel')}: …{inst.origin_session_id.slice(-10)}
                      </div>
                    )}
                  </div>
                  <span className="text-xs font-bold px-1.5 py-0.5 rounded flex-shrink-0"
                    style={{ background: color + '33', color }}>
                    {t(`processes.wfStatus.${inst.status}`, { defaultValue: inst.status })}
                  </span>
                </div>
                <div className="text-xs text-slate-500 mt-1.5">
                  {new Date(inst.created_at).toLocaleString(i18n.language)}
                </div>
                {inst.suspend_reason && (
                  <div className="text-xs text-yellow-400 mt-1">
                    {t(`processes.wfSuspend.${inst.suspend_reason}`, { defaultValue: inst.suspend_reason })}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>

      {/* Right: detail or empty */}
      {detail ? (
        <div className="flex-1 flex flex-col overflow-hidden">
          {/* Detail header */}
          <div className="flex justify-between items-start px-5 py-3.5 border-b border-slate-800 flex-shrink-0">
            <div>
              <code className="text-xs text-blue-300">{detail.id}</code>
              <div className="text-xs text-slate-500 mt-0.5">{detail.flow_id}</div>
            </div>
            <button onClick={() => setSelectedId(null)}
              className="text-slate-500 hover:text-slate-200" aria-label="Close">
              <X className="w-4 h-4" />
            </button>
          </div>

          {/* Scrollable detail body */}
          <div className="flex-1 overflow-y-auto p-5 space-y-5">
            {/* Status */}
            <div>
              <div className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">
                {t('processes.instances.detail.status')}
              </div>
              <span className="text-xs font-bold px-2.5 py-1 rounded"
                style={{ background: WF_STATUS_COLORS[detail.status] + '33', color: WF_STATUS_COLORS[detail.status] }}>
                {t(`processes.wfStatus.${detail.status}`, { defaultValue: detail.status })}
              </span>
              {detail.current_step && (
                <div className="mt-2 text-xs text-slate-400">
                  {t('processes.instances.detail.currentStep')}: <code className="text-slate-200">{detail.current_step}</code>
                </div>
              )}
              {detail.outcome && (
                <div className="mt-1 text-xs text-slate-400">
                  {t('processes.instances.detail.outcome')}: <code className="text-slate-200">{detail.outcome}</code>
                </div>
              )}
            </div>

            {/* Timeline */}
            <div>
              <div className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">
                {t('processes.instances.detail.timeline')}
              </div>
              <div className="space-y-1.5">
                {[
                  { dot: '#22c55e', label: t('processes.instances.detail.created'),   ts: detail.created_at },
                  detail.suspended_at ? { dot: '#eab308', label: t('processes.instances.detail.suspended'), ts: detail.suspended_at } : null,
                  detail.resumed_at   ? { dot: '#3b82f6', label: t('processes.instances.detail.resumed'),   ts: detail.resumed_at   } : null,
                  detail.completed_at ? { dot: '#22c55e', label: t('processes.instances.detail.completed'), ts: detail.completed_at } : null,
                ].filter(Boolean).map((entry, i) => (
                  <div key={i} className="flex items-center gap-2 text-xs">
                    <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: entry!.dot }} />
                    <span className="text-slate-400 w-20 flex-shrink-0">{entry!.label}</span>
                    <span className="text-slate-500">{new Date(entry!.ts).toLocaleString(i18n.language)}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Suspend reason */}
            {detail.suspend_reason && (
              <div>
                <div className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">
                  {t('processes.instances.detail.suspendReason')}
                </div>
                <span className="text-xs px-2.5 py-1 rounded border border-yellow-900/60 bg-yellow-900/20 text-yellow-300">
                  {t(`processes.wfSuspend.${detail.suspend_reason}`, { defaultValue: detail.suspend_reason })}
                </span>
              </div>
            )}

            {/* Resume token */}
            {detail.resume_token && (
              <div>
                <div className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">
                  {t('processes.instances.detail.resumeToken')}
                </div>
                <div
                  className="bg-slate-900 border border-slate-700 rounded px-3 py-2 text-xs font-mono text-slate-400 break-all cursor-pointer hover:border-slate-500"
                  onClick={() => void navigator.clipboard.writeText(detail.resume_token!)}
                  title={t('processes.instances.detail.tokenClickHint')}>
                  {detail.resume_token}
                </div>
                {detail.resume_expires_at && (
                  <div className="mt-1 text-xs text-slate-600">
                    {t('processes.instances.detail.expires')}: {new Date(detail.resume_expires_at).toLocaleString(i18n.language)}
                  </div>
                )}
              </div>
            )}

            {/* Origin session */}
            {detail.origin_session_id && (
              <div>
                <div className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">
                  {t('processes.instances.detail.originSession')}
                </div>
                <code className="text-xs text-blue-400 font-mono">{detail.origin_session_id}</code>
              </div>
            )}
          </div>

          {/* Botão "Cancelar" REMOVIDO em 2026-08-07 (I5, lacuna 4b) — ver
              `modules/workflows/api/hooks.ts` para o motivo medido. */}
        </div>
      ) : (
        <div className="flex-1 flex flex-col items-center justify-center text-slate-500">
          <Settings className="w-10 h-10 mb-3 text-slate-600" aria-hidden="true" />
          <div className="text-sm">{t('processes.instances.selectPrompt')}</div>
        </div>
      )}
    </div>
  )
}

// ── Events view — Arc 12 business events ──────────────────────────────────
//
// ⚠️ Esta vista nasceu no Arc 19 falando um contrato que o endpoint NUNCA teve, e
// ficou vazia desde então — quatro divergências, todas mudas (medido 2026-09-05, ao
// procurar onde ver as contagens da árvore de wrap-up):
//
//   1. lia `data.rows`; a resposta é `{data, meta}` ⇒ `rows` = `[]` ⇒ a tabela
//      renderizava o estado "nenhum evento" SEMPRE, inclusive com 9 linhas no ar;
//   2. mandava `period=24h`; o endpoint recebe `from_dt`/`to_dt` ⇒ o parâmetro caía
//      no chão (FastAPI ignora query desconhecida) e a janela real era o default de
//      7 dias, sob um título que promete 24 h;
//   3. mandava `category_regex`; o filtro é `category`, e é **prefixo**, não regex
//      (`startsWith`) ⇒ o operador digitava e nada acontecia;
//   4. lia `row.category`/`row.pool_id`; a linha traz `group_key` e, agrupando por
//      categoria, **não tem pool** — a coluna Pool era estruturalmente vazia.
//
// Prefixo não é um filtro pior que regex aqui: é o recorte que a taxonomia em
// árvore pede (D10 do `adr-dialog-tree-options`) — `pool.skill.motivo.financeiro.`
// alcança o ramo inteiro, e é assim que a agregação por caminho é feita do lado do
// servidor. O rótulo foi corrigido junto; dizer "regex" era a tela mentindo.

/** Espelho do que `/reports/agent-events/summary` devolve por linha. */
interface AgentEventSummaryRow {
  group_key:   string
  count:       number
  total_value: number | null
  avg_value:   number | null
  min_value:   number | null
  max_value:   number | null
  first_seen:  string
  last_seen:   string
}

const EVENTS_WINDOW_H = 24

function EventsView({ tenantId }: { tenantId: string }) {
  const { t } = useTranslation('contacts')
  const [categoryPrefix, setCategoryPrefix] = useState('')
  const [rows,    setRows]    = useState<AgentEventSummaryRow[]>([])
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState<string | null>(null)

  const fetchData = useCallback(async () => {
    if (!tenantId) return
    setLoading(true)
    setError(null)
    try {
      // A janela é DECLARADA, não implícita: o título promete 24 h e o endpoint
      // defaulta para 7 dias. Sem esta linha a tela mostra sete vezes mais período
      // do que diz — divergência que nenhum estado vazio denuncia.
      const from = new Date(Date.now() - EVENTS_WINDOW_H * 3600_000).toISOString()
      const params = new URLSearchParams({
        tenant_id: tenantId,
        from_dt:   from,
        ...(categoryPrefix.trim() ? { category: categoryPrefix.trim() } : {}),
      })
      const res = await apiFetch(`/reports/agent-events/summary?${params}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const body = await res.json()
      // `data` é a chave do contrato. O `Array.isArray` fica como tolerância a um
      // envelope que o endpoint não usa hoje — mas `?? []` sobre a chave ERRADA é
      // como esta tela passou meses vazia, então o fallback nomeia a chave certa.
      setRows(Array.isArray(body) ? body : body.data ?? [])
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [tenantId, categoryPrefix])

  useEffect(() => {
    fetchData()
    const id = setInterval(fetchData, 30_000)
    return () => clearInterval(id)
  }, [fetchData])

  return (
    <div className="flex flex-col h-full overflow-hidden bg-surface-muted">
      {/* Toolbar */}
      <div className="flex items-center gap-3 px-4 py-2.5 flex-shrink-0 border-b border-border bg-white">
        <BarChart2 className="w-4 h-4 text-muted" aria-hidden="true" />
        <span className="text-sm font-semibold text-dark">{t('monitor.events.title')}</span>
        <input
          type="text"
          value={categoryPrefix}
          onChange={e => setCategoryPrefix(e.target.value)}
          placeholder={t('monitor.events.filterPlaceholder')}
          className="ml-auto w-64 text-xs border border-border rounded-md px-3 py-1.5 bg-white text-dark placeholder-muted focus:outline-none focus:ring-1 focus:ring-primary"
        />
        <button onClick={fetchData}
          className="text-xs px-3 py-1.5 rounded-md border border-border bg-white hover:bg-surface-alt text-muted transition-colors">
          {t('monitor.events.refresh')}
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto p-4">
        {error && (
          <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-red-50 border border-red-200 text-red-800 text-xs mb-4">
            <AlertTriangle className="w-4 h-4 flex-shrink-0" aria-hidden="true" />
            <span>{error}</span>
          </div>
        )}

        {loading && rows.length === 0 && (
          <div className="flex items-center justify-center h-32 text-muted text-sm">
            <Clock className="w-5 h-5 animate-pulse mr-2" aria-hidden="true" />
            {t('monitor.events.loading')}
          </div>
        )}

        {!loading && rows.length === 0 && !error && (
          <div className="flex flex-col items-center justify-center h-32 text-muted gap-2 text-sm">
            <BarChart2 className="w-8 h-8 text-muted-light" aria-hidden="true" />
            <span>{t('monitor.events.empty')}</span>
          </div>
        )}

        {rows.length > 0 && (
          <div className="bg-white rounded-xl border border-border overflow-auto">
            <table className="w-full text-sm">
              <thead className="bg-surface-muted border-b border-border sticky top-0 z-10">
                <tr>
                  {/* Sem coluna Pool: agrupando por categoria a linha NÃO traz pool.
                      Uma coluna estruturalmente vazia é pior que coluna nenhuma —
                      ela sugere que o dado existe e não chegou. O pool está no
                      PRIMEIRO segmento da categoria, por construção do Arc 12. */}
                  {[
                    t('monitor.events.col.category'),
                    t('monitor.events.col.count'),
                    t('monitor.events.col.avg'),
                    t('monitor.events.col.lastSeen'),
                  ].map((col, i) => (
                    <th key={i}
                      className="text-left text-xs font-semibold text-muted uppercase tracking-wide px-4 py-2.5 whitespace-nowrap">
                      {col}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {rows.map((row, i) => (
                  <tr key={i} className="hover:bg-primary/5 transition-colors">
                    <td className="px-4 py-2.5">
                      <code className="text-xs text-secondary font-mono">{row.group_key}</code>
                    </td>
                    <td className="px-4 py-2.5 text-center">
                      <span className="font-bold tabular-nums text-dark">{row.count}</span>
                    </td>
                    <td className="px-4 py-2.5 text-center text-xs tabular-nums text-muted">
                      {row.avg_value != null ? row.avg_value.toFixed(2) : '—'}
                    </td>
                    <td className="px-4 py-2.5 text-xs text-muted">
                      {row.last_seen ? new Date(row.last_seen).toLocaleTimeString() : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Main MonitorTab ────────────────────────────────────────────────────────

type DrillLevel = 'pools' | 'sessions' | 'segments' | 'transcript'

export function MonitorTab({ tenantId, filters }: Props) {
  const { t } = useTranslation('contacts')
  const [scope, setScope] = useState<MonitorScope>('sessions')
  const { pools, status, metrics, isStale, lastUpdated } = usePoolViews(tenantId)
  const [drillLevel,      setDrillLevel]      = useState<DrillLevel>('pools')
  const [selectedPool,    setSelectedPool]    = useState<string | null>(null)
  const [selectedSession, setSelectedSession] = useState<string | null>(null)
  const [selectedSegment, setSelectedSegment] = useState<ContactSegment | null>(null)

  // Apply filters (pool_id / channel)
  const filteredPools = useMemo(() => {
    if (!filters.poolId && !filters.channel) return pools
    return pools.filter(p => {
      if (filters.poolId && p.pool_id !== filters.poolId) return false
      if (filters.channel && !p.channel_types?.includes(filters.channel)) return false
      return true
    })
  }, [pools, filters])

  function handlePoolClick(poolId: string) {
    setSelectedPool(poolId)
    setSelectedSession(null)
    setSelectedSegment(null)
    setDrillLevel('sessions')
  }

  function handleSessionSelect(sid: string) {
    setSelectedSession(sid)
    setSelectedSegment(null)
    setDrillLevel('segments')
  }

  function handleSegmentSelect(segment: ContactSegment) {
    setSelectedSegment(segment)
    setDrillLevel('transcript')
  }

  function goBackToPools() {
    setDrillLevel('pools')
    setSelectedPool(null)
    setSelectedSession(null)
    setSelectedSegment(null)
  }

  function goBackToSessions() {
    setDrillLevel('sessions')
    setSelectedSession(null)
    setSelectedSegment(null)
  }

  function goBackToSegments() {
    setDrillLevel('segments')
    setSelectedSegment(null)
  }

  return (
    <div className="flex flex-col h-full overflow-hidden bg-surface-muted">

      {/* ── Toolbar ─────────────────────────────────────────────────── */}
      <div className="flex items-center gap-3 px-4 py-2.5 flex-shrink-0 border-b border-border bg-white flex-wrap">

        {/* Scope toggle */}
        <div className="flex items-center gap-0.5 bg-surface-alt rounded-lg p-0.5 flex-shrink-0">
          {([
            { id: 'sessions'  as MonitorScope, labelKey: 'monitor.scope.sessions',  Icon: Radio      },
            { id: 'processes' as MonitorScope, labelKey: 'monitor.scope.processes', Icon: Settings   },
            { id: 'events'    as MonitorScope, labelKey: 'monitor.scope.events',    Icon: BarChart2  },
          ]).map(({ id, labelKey, Icon: ScopeIcon }) => (
            <button key={id} onClick={() => setScope(id)}
              className="flex items-center gap-1 px-3 py-1 rounded-md text-xs font-medium transition-all"
              style={{
                backgroundColor: scope === id ? '#fff' : 'transparent',
                color:           scope === id ? '#1B4F8A' : '#6b7280',
                boxShadow:       scope === id ? '0 1px 3px rgba(0,0,0,0.12)' : 'none',
              }}>
              <ScopeIcon className="w-3.5 h-3.5" aria-hidden="true" />
              <span>{t(labelKey)}</span>
            </button>
          ))}
        </div>

        {scope === 'sessions' && (
          <>
            <ConnectionPill status={status} />

            {/* Breadcrumb for drill-down */}
            {drillLevel !== 'pools' && (
              <div className="flex items-center gap-1 text-xs ml-2 flex-wrap text-muted">
                <button onClick={goBackToPools} className="hover:underline text-primary">
                  {t('monitor.breadcrumbs.pools')}
                </button>
                <span className="mx-0.5">/</span>
                {drillLevel === 'sessions'
                  ? <span className="font-semibold text-dark">{selectedPool?.replace(/_/g, ' ')}</span>
                  : <button onClick={goBackToSessions} className="hover:underline text-primary">
                      {selectedPool?.replace(/_/g, ' ')}
                    </button>
                }
                {(drillLevel === 'segments' || drillLevel === 'transcript') && selectedSession && (
                  <>
                    <span className="mx-0.5">/</span>
                    {drillLevel === 'segments'
                      ? <span className="font-semibold font-mono text-dark">…{selectedSession.slice(-10)}</span>
                      : <button onClick={goBackToSegments} className="hover:underline font-mono text-primary">
                          …{selectedSession.slice(-10)}
                        </button>
                    }
                  </>
                )}
                {drillLevel === 'transcript' && selectedSegment && (
                  <>
                    <span className="mx-0.5">/</span>
                    <span className="font-semibold text-dark">
                      {selectedSegment.role}
                      {selectedSegment.ended_at === null && (
                        <span className="ml-1 text-green-600">●</span>
                      )}
                    </span>
                  </>
                )}
              </div>
            )}
          </>
        )}
      </div>

      {/* ── Scope content ─────────────────────────────────────────── */}
      {scope === 'processes' && (
        <div className="flex-1 overflow-hidden">
          <ProcessosView tenantId={tenantId} />
        </div>
      )}

      {scope === 'events' && (
        <div className="flex-1 overflow-hidden">
          <EventsView tenantId={tenantId} />
        </div>
      )}

      {scope === 'sessions' && (
        <div className="flex-1 overflow-hidden">
          {drillLevel === 'pools' && (
            <PoolsOverview
              pools={filteredPools}
              metrics={metrics}
              selectedPool={selectedPool}
              onPoolClick={handlePoolClick}
              isStale={isStale}
              lastUpdated={lastUpdated}
            />
          )}

          {drillLevel === 'sessions' && selectedPool && (
            <SessionList
              tenantId={tenantId}
              poolId={selectedPool}
              onSelect={handleSessionSelect}
              onBack={goBackToPools}
            />
          )}

          {drillLevel === 'segments' && selectedSession && (
            <SegmentList
              tenantId={tenantId}
              sessionId={selectedSession}
              onSelect={handleSegmentSelect}
              onBack={goBackToSessions}
            />
          )}

          {drillLevel === 'transcript' && selectedSession && (
            <div style={{ height: '100%', backgroundColor: '#0f172a', overflow: 'hidden' }}>
              <SessionTranscript
                tenantId={tenantId}
                sessionId={selectedSession}
                onBack={goBackToSegments}
                canJoin={selectedSegment?.ended_at === null}
                segment={selectedSegment ?? undefined}
              />
            </div>
          )}
        </div>
      )}
    </div>
  )
}
