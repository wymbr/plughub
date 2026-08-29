/**
 * AnalisePoolsPage — /analise/pools (Fase 2 — saúde operacional por pool/canal)
 *
 * Sub-abas: Volume · Fila · Capacidade · SLA.
 * Volume lê GET /reports/pools/volume → área de contatos no tempo (empilhada por
 * canal) + donut por canal + tabela por endpoint (DNIS) + demanda reprimida
 * (Fase D queue-attended-model: sessões outage + causa dos segmentos system).
 * Fila/SLA leem GET /reports/pools/queue — derivado dos segments role='queue'
 * (espera = duration_ms; abandono = outcome='abandoned'; handoff = fila→primary).
 * Capacidade lê GET /reports/pools/occupancy — KPIs + tabela por pool + time-series
 * (pico de concorrência vs teto: per-pool = provisionada flashada pelo occupancy
 * sampler; total = configurada no pricing quando disponível, capacity_source).
 */
import React, { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { apiFetch } from '@/api/apiFetch'
import {
  AreaChart, Area, LineChart, Line, ComposedChart, ReferenceLine,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  PieChart, Pie, Cell,
} from 'recharts'
import { useAuth } from '@/auth/useAuth'

type SubTab = 'volume' | 'queue' | 'capacity' | 'sla'

interface VolumeRow   { bucket: string; pool_id: string; channel: string; endpoint: string; contacts: number }
interface ChannelRow  { channel: string; contacts: number }
interface EndpointRow { channel: string; endpoint: string; contacts: number }
interface RejectedCauseRow { pool_id: string; cause: string; contacts: number }
interface RejectedData     { series: unknown[]; by_cause: RejectedCauseRow[]; total: number }
interface VolumeData  {
  series: VolumeRow[]; by_channel: ChannelRow[]; by_endpoint: EndpointRow[]
  totals: { contacts: number; rejected?: number }
  rejected?: RejectedData
}

interface OccPoolRow   { pool_id: string; peak_concurrency: number; capacity: number; headroom: number; utilization: number | null }
interface OccTotal     {
  peak_concurrency: number; capacity: number; headroom: number; utilization: number | null
  provisioned_capacity?: number; capacity_source?: 'pricing' | 'provisioned'
}
interface OccSeriesRow { bucket: string; pool_id: string; peak_concurrency: number; capacity: number; admitted?: number }
interface OccTotalRow  { bucket: string; peak_concurrency: number; capacity: number }
interface AdmSeriesRow { bucket: string; used: number; limit: number }
interface OccData      {
  series: OccSeriesRow[]; by_pool: OccPoolRow[]; total: OccTotal | null; total_series?: OccTotalRow[]
  // Fatia 3 (2026-08-02): `reserved_series`/`shared_series` saíram — mediam os baldes
  // de SESSÃO carvidos do pote misto `C_ai + C_human`. `ai_series` (sessões debitando
  // C_ai vs C_ai) é a série que sobrou, e ela COMEÇA em 2026-08-02: numerador e
  // denominador mudaram, então não é continuação da antiga.
  admission?: { ai_series: AdmSeriesRow[]; buffer_series: AdmSeriesRow[] }
}

// F5: `available_agents` saiu do contrato — produtor, query e gráfico removidos juntos.
// D14-i (2026-08-24): DUAS unidades no mesmo contrato, e elas são nomeadas.
//   `contacts` / `queued` → SESSÕES distintas (significado inalterado — é o que o
//                           operador já lê nesta tela).
//   `waits` (NOVO) / `abandoned` / `handoff` / `avg_wait_ms` / `p95_wait_ms`
//                         → PASSAGENS pela fila. Uma sessão que espera em dois
//                           pools conta 1 contato em cada e 2 esperas no total.
// Antes, o backend colapsava a sessão numa linha e SORTEAVA (`anyIf`) o pool e o
// desfecho quando havia mais de uma espera: 12 de 71 esperas eram invisíveis.
// ⚠️ `max_queue_len` continua sem leitor nesta tela (declarado e nunca renderizado).
interface QSeriesRow  { bucket: string; pool_id: string; avg_wait_ms: number; contacts: number; queued: number; waits: number; abandoned: number; max_queue_len: number }
interface QPoolRow    { pool_id: string; contacts: number; queued: number; waits: number; abandoned: number; handoff: number; abandon_rate: number; avg_wait_ms: number; p95_wait_ms: number; sla_target_ms: number; within_sla: number; sla_eligible: number; sla_attainment: number | null }
interface QueueData   { series: QSeriesRow[]; by_pool: QPoolRow[] }

const CHANNEL_COLORS = ['#1B4F8A', '#2D9CDB', '#00B4D8', '#059669', '#D97706', '#7C3AED', '#DC2626', '#0891B2']

function fmtBucket(b: string): string {
  return b.slice(5, 16).replace('T', ' ')
}

function fmtMs(ms: number): string {
  if (!ms || ms < 1000) return '—'
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}m ${s % 60}s`
}

function colorFor(i: number): string {
  return CHANNEL_COLORS[i % CHANNEL_COLORS.length]
}

// ── Volume sub-tab ─────────────────────────────────────────────────────────────

const VolumeSubTab: React.FC<{ data: VolumeData | null; loading: boolean }> = ({ data, loading }) => {
  const { t } = useTranslation('agentReports')

  if (loading) return (
    <div className="h-48 flex items-center justify-center text-sm text-muted-light animate-pulse">
      {t('pools.volume.loading')}
    </div>
  )
  const series = data?.series ?? []
  if (series.length === 0) return (
    <div className="h-48 flex items-center justify-center text-sm text-muted-light">
      {t('pools.volume.noData')}
    </div>
  )

  const chLabel = (ch: string) => ch || '—'
  const channels = [...new Set(series.map(r => chLabel(r.channel)))].sort()
  const byBucket = new Map<string, Record<string, number | string>>()
  for (const r of series) {
    const row = byBucket.get(r.bucket) ?? { bucket: r.bucket }
    const k = chLabel(r.channel)
    row[k] = ((row[k] as number) ?? 0) + r.contacts
    byBucket.set(r.bucket, row)
  }
  const chartData = [...byBucket.values()].sort((a, b) => String(a.bucket).localeCompare(String(b.bucket)))
  const donut = (data?.by_channel ?? []).map(c => ({ name: chLabel(c.channel), value: c.contacts }))

  return (
    <div className="flex flex-col gap-4">
      {/* Área no tempo */}
      <div className="bg-white rounded-lg border border-border overflow-hidden">
        <div className="px-4 py-2.5 border-b border-border bg-surface-muted">
          <p className="text-xs font-semibold text-muted uppercase tracking-wide">{t('pools.volume.title')}</p>
        </div>
        <div className="p-3">
          <ResponsiveContainer width="100%" height={240}>
            <AreaChart data={chartData} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
              <XAxis dataKey="bucket" tick={{ fontSize: 11 }} tickFormatter={fmtBucket} />
              <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
              <Tooltip labelFormatter={fmtBucket} />
              <Legend iconSize={10} wrapperStyle={{ fontSize: 12 }} />
              {channels.map((ch, i) => (
                <Area key={ch} type="monotone" dataKey={ch} stackId="1"
                      stroke={colorFor(i)} fill={colorFor(i)} fillOpacity={0.5} />
              ))}
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Donut por canal */}
        <div className="bg-white rounded-lg border border-border overflow-hidden">
          <div className="px-4 py-2.5 border-b border-border bg-surface-muted">
            <p className="text-xs font-semibold text-muted uppercase tracking-wide">{t('pools.volume.byChannel')}</p>
          </div>
          <ResponsiveContainer width="100%" height={220}>
            <PieChart>
              <Pie data={donut} dataKey="value" nameKey="name" cx="50%" cy="50%" innerRadius={50} outerRadius={80} paddingAngle={2}>
                {donut.map((_, i) => <Cell key={i} fill={colorFor(i)} />)}
              </Pie>
              <Tooltip />
              <Legend iconSize={10} wrapperStyle={{ fontSize: 12 }} />
            </PieChart>
          </ResponsiveContainer>
        </div>

        {/* Tabela por endpoint */}
        <div className="bg-white rounded-lg border border-border overflow-hidden">
          <div className="px-4 py-2.5 border-b border-border bg-surface-muted">
            <p className="text-xs font-semibold text-muted uppercase tracking-wide">{t('pools.volume.byEndpoint')}</p>
          </div>
          <div className="overflow-auto max-h-[220px]">
            <table className="min-w-full text-xs border-collapse">
              <thead className="bg-surface-muted sticky top-0">
                <tr className="border-b border-border text-2xs text-muted uppercase">
                  <th className="text-left px-3 py-2">{t('pools.volume.cols.channel')}</th>
                  <th className="text-left px-3 py-2">{t('pools.volume.cols.endpoint')}</th>
                  <th className="text-right px-3 py-2">{t('pools.volume.cols.contacts')}</th>
                </tr>
              </thead>
              <tbody>
                {(data?.by_endpoint ?? []).map((r, i) => (
                  <tr key={i} className="border-b border-border hover:bg-surface-muted">
                    <td className="px-3 py-2 text-dark">{r.channel || '—'}</td>
                    <td className="px-3 py-2 text-muted">{r.endpoint || '—'}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-dark">{r.contacts}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* Demanda reprimida (Fase D — queue-attended-model) */}
      <RejectedCard data={data} />
    </div>
  )
}

// ── Demanda reprimida card (Volume) ───────────────────────────────────────────

const RejectedCard: React.FC<{ data: VolumeData | null }> = ({ data }) => {
  const { t } = useTranslation('agentReports')
  const rej   = data?.rejected
  const total = rej?.total ?? 0
  const all   = data?.totals?.contacts ?? 0
  const share = all > 0 ? Math.round((total / all) * 100) : 0
  const causeLabel = (c: string) =>
    ['reservation_full', 'shared_full', 'quota', 'queue_full'].includes(c)
      ? t(`pools.volume.rejected.cause.${c}`) : (c || '—')

  return (
    <div className="bg-white rounded-lg border border-border overflow-hidden">
      <div className="px-4 py-2.5 border-b border-border bg-surface-muted">
        <p className="text-xs font-semibold text-muted uppercase tracking-wide">{t('pools.volume.rejected.title')}</p>
        <p className="text-2xs text-muted-light mt-0.5">{t('pools.volume.rejected.hint')}</p>
      </div>
      {total === 0 ? (
        <div className="px-4 py-6 text-sm text-muted-light">{t('pools.volume.rejected.none')}</div>
      ) : (
        <div className="flex flex-col md:flex-row">
          <div className="flex gap-3 p-3 md:flex-col md:w-48 md:border-r border-border">
            <div className="bg-surface-muted rounded-md p-3 flex-1">
              <div className="text-xs text-muted">{t('pools.volume.rejected.total')}</div>
              <div className="text-xl font-semibold text-red">{total}</div>
            </div>
            <div className="bg-surface-muted rounded-md p-3 flex-1">
              <div className="text-xs text-muted">{t('pools.volume.rejected.share')}</div>
              <div className="text-xl font-semibold text-dark">{share}%</div>
            </div>
          </div>
          <div className="flex-1 overflow-auto max-h-[220px]">
            <table className="min-w-full text-xs border-collapse">
              <thead className="bg-surface-muted sticky top-0">
                <tr className="border-b border-border text-2xs text-muted uppercase">
                  <th className="text-left px-3 py-2">{t('pools.volume.rejected.cols.pool')}</th>
                  <th className="text-left px-3 py-2">{t('pools.volume.rejected.cols.cause')}</th>
                  <th className="text-right px-3 py-2">{t('pools.volume.rejected.cols.contacts')}</th>
                </tr>
              </thead>
              <tbody>
                {(rej?.by_cause ?? []).map((r, i) => (
                  <tr key={i} className="border-b border-border hover:bg-surface-muted">
                    <td className="px-3 py-2 text-dark">{r.pool_id || '—'}</td>
                    <td className="px-3 py-2 text-muted">{causeLabel(r.cause)}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-red">{r.contacts}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Capacity sub-tab ───────────────────────────────────────────────────────────

function utilColor(u: number | null): string {
  if (u === null) return 'var(--color-border-strong, #D1D5DB)'
  if (u >= 0.9) return '#DC2626'
  if (u >= 0.7) return '#D97706'
  return '#059669'
}

const CapacitySubTab: React.FC<{ data: OccData | null; loading: boolean }> = ({ data, loading }) => {
  const { t } = useTranslation('agentReports')
  if (loading) return (
    <div className="h-48 flex items-center justify-center text-sm text-muted-light animate-pulse">{t('pools.volume.loading')}</div>
  )
  const rows = data?.by_pool ?? []
  if (rows.length === 0) return (
    <div className="h-48 flex items-center justify-center text-sm text-muted-light">{t('pools.capacity.noData')}</div>
  )
  const total = data?.total
  const pct = (u: number | null) => u === null ? '—' : `${Math.round(u * 100)}%`

  // Time-series: com filtro de pool a série tem 1 pool (teto = provisionada do
  // pool); sem filtro usa o total do tenant (peak_total instantâneo ≠ soma dos
  // máximos por pool) com teto provisionado por bucket + linha da configurada.
  const poolIds    = [...new Set((data?.series ?? []).map(r => r.pool_id))]
  const singlePool = poolIds.length === 1 ? poolIds[0] : null
  const tsSource   = singlePool ? (data?.series ?? []) : (data?.total_series ?? [])
  const tsData     = tsSource
    .map(r => ({ bucket: r.bucket, peak: r.peak_concurrency, capacity: r.capacity }))
    .sort((a, b) => a.bucket.localeCompare(b.bucket))
  const configured = !singlePool && total?.capacity_source === 'pricing' ? total.capacity : null

  return (
    <div className="flex flex-col gap-4">
      {total && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <div className="bg-surface-muted rounded-md p-3"><div className="text-xs text-muted">{t('pools.capacity.totalPeak')}</div><div className="text-xl font-semibold">{total.peak_concurrency}</div></div>
          <div className="bg-surface-muted rounded-md p-3">
            <div className="text-xs text-muted">{t('pools.capacity.totalCap')}</div>
            <div className="text-xl font-semibold">{total.capacity}</div>
            {total.capacity_source && <div className="text-2xs text-muted-light">{t(`pools.capacity.source.${total.capacity_source}`)}</div>}
          </div>
          <div className="bg-surface-muted rounded-md p-3"><div className="text-xs text-muted">{t('pools.capacity.headroom')}</div><div className="text-xl font-semibold">{total.headroom}</div></div>
          <div className="bg-surface-muted rounded-md p-3"><div className="text-xs text-muted">{t('pools.capacity.utilization')}</div><div className="text-xl font-semibold" style={{ color: utilColor(total.utilization) }}>{pct(total.utilization)}</div></div>
          {/* Item 5 (capacity-governance): alocado como diagnóstico — vermelho
              quando o deploy excede o contratado (teto único da aba = C). */}
          {total.capacity_source === 'pricing' && total.provisioned_capacity !== undefined && (
            <div className="bg-surface-muted rounded-md p-3">
              <div className="text-xs text-muted">{t('pools.capacity.allocated')}</div>
              <div className="text-xl font-semibold"
                   style={total.provisioned_capacity > total.capacity ? { color: '#DC2626' } : undefined}>
                {total.provisioned_capacity}
              </div>
              {total.provisioned_capacity > total.capacity && (
                <div className="text-2xs" style={{ color: '#DC2626' }}>{t('pools.capacity.overAllocated')}</div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Item 7b — Admissão no tempo: histórico do Monitor (licença de IA usada vs
          C_ai; sala de espera gratuita vs teto). O empilhamento reservado×compartilhado
          saiu na fatia 3 junto com os baldes: sobrou UMA área, e o teto agora vem da
          própria série (`limit` = C_ai) em vez do `capacity_source` do pool. */}
      {(() => {
        const adm = data?.admission
        if (!adm || adm.ai_series.length === 0) return null
        const admData = [...adm.ai_series].sort((a, b) => a.bucket.localeCompare(b.bucket))
        // Teto da própria série. Antes vinha de `total.capacity` quando a fonte era
        // `pricing` — o `max_concurrent_sessions` misto. Agora é o denominador que o
        // produtor gravou no mesmo minuto do pico.
        const cLine = admData.length > 0 ? Math.max(...admData.map(r => r.limit)) : null
        const buf = adm.buffer_series
        const bufLimit = buf.length > 0 ? Math.max(...buf.map(b => b.limit)) : null
        return (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="bg-white rounded-lg border border-border overflow-hidden">
              <div className="px-4 py-2.5 border-b border-border bg-surface-muted">
                <p className="text-xs font-semibold text-muted uppercase tracking-wide">{t('pools.capacity.admissionTitle')}</p>
                <p className="text-2xs text-muted-light mt-0.5">{t('pools.capacity.admissionHint')}</p>
              </div>
              <div className="p-3">
                <ResponsiveContainer width="100%" height={200}>
                  <ComposedChart data={admData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
                    <XAxis dataKey="bucket" tick={{ fontSize: 11 }} tickFormatter={fmtBucket} />
                    <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
                    <Tooltip labelFormatter={fmtBucket} />
                    <Legend iconSize={10} wrapperStyle={{ fontSize: 12 }} />
                    <Area type="monotone" dataKey="used" name={t('pools.capacity.aiArea')}
                          stroke="#7C3AED" fill="#7C3AED" fillOpacity={0.4} />
                    {cLine !== null && cLine > 0 && (
                      <ReferenceLine y={cLine} stroke="#DC2626" strokeDasharray="4 4" ifOverflow="extendDomain"
                        label={{ value: t('pools.capacity.aiLine'), fontSize: 11, fill: '#DC2626', position: 'insideTopRight' }} />
                    )}
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            </div>
            <div className="bg-white rounded-lg border border-border overflow-hidden">
              <div className="px-4 py-2.5 border-b border-border bg-surface-muted">
                <p className="text-xs font-semibold text-muted uppercase tracking-wide">{t('pools.capacity.bufferTitle')}</p>
                <p className="text-2xs text-muted-light mt-0.5">{t('pools.capacity.bufferHint')}</p>
              </div>
              <div className="p-3">
                <ResponsiveContainer width="100%" height={200}>
                  <ComposedChart data={buf} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
                    <XAxis dataKey="bucket" tick={{ fontSize: 11 }} tickFormatter={fmtBucket} />
                    <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
                    <Tooltip labelFormatter={fmtBucket} />
                    <Legend iconSize={10} wrapperStyle={{ fontSize: 12 }} />
                    <Area type="monotone" dataKey="used" name={t('pools.capacity.bufferUsed')}
                          stroke="#7C3AED" fill="#7C3AED" fillOpacity={0.4} />
                    {bufLimit !== null && bufLimit > 0 && (
                      <ReferenceLine y={bufLimit} stroke="#DC2626" strokeDasharray="4 4" ifOverflow="extendDomain"
                        label={{ value: t('pools.capacity.bufferLimit'), fontSize: 11, fill: '#DC2626', position: 'insideTopRight' }} />
                    )}
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>
        )
      })()}

      {/* Time-series de capacidade (Arc 19 — "Pools (time-series capacity)") */}
      {tsData.length > 0 && (
        <div className="bg-white rounded-lg border border-border overflow-hidden">
          <div className="px-4 py-2.5 border-b border-border bg-surface-muted">
            <p className="text-xs font-semibold text-muted uppercase tracking-wide">
              {t('pools.capacity.seriesTitle')}{singlePool ? ` — ${singlePool}` : ''}
            </p>
            <p className="text-2xs text-muted-light mt-0.5">{t('pools.capacity.seriesHint')}</p>
          </div>
          <div className="p-3">
            <ResponsiveContainer width="100%" height={220}>
              <ComposedChart data={tsData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
                <XAxis dataKey="bucket" tick={{ fontSize: 11 }} tickFormatter={fmtBucket} />
                <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
                <Tooltip labelFormatter={fmtBucket} />
                <Legend iconSize={10} wrapperStyle={{ fontSize: 12 }} />
                <Area type="monotone" dataKey="peak" name={t('pools.capacity.peakLine')}
                      stroke="#1B4F8A" fill="#1B4F8A" fillOpacity={0.35} />
                {/* Provisionada só quando ela é o teto da visão (per-pool / sem pricing):
                    no total com pricing ela é ordens de grandeza maior (pools IA) e
                    esmagaria o eixo — fica no KPI (provisioned_capacity) e na tabela. */}
                {configured === null && (
                  <Line type="stepAfter" dataKey="capacity" name={t('pools.capacity.provisionedLine')}
                        stroke="#D97706" strokeWidth={2} strokeDasharray="6 3" dot={false} />
                )}
                {configured !== null && (
                  <ReferenceLine y={configured} stroke="#DC2626" strokeDasharray="4 4" ifOverflow="extendDomain"
                    label={{ value: t('pools.capacity.configuredLine'), fontSize: 11, fill: '#DC2626', position: 'insideTopRight' }} />
                )}
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      <div className="bg-white rounded-lg border border-border overflow-hidden">
        <div className="px-4 py-2.5 border-b border-border bg-surface-muted">
          <p className="text-xs font-semibold text-muted uppercase tracking-wide">{t('pools.capacity.title')}</p>
          <p className="text-2xs text-muted-light mt-0.5">{t('pools.capacity.hint')}</p>
        </div>
        <table className="min-w-full text-xs border-collapse">
          <thead className="bg-surface-muted">
            <tr className="border-b border-border text-2xs text-muted uppercase">
              <th className="text-left px-3 py-2">{t('pools.capacity.cols.pool')}</th>
              <th className="text-right px-3 py-2">{t('pools.capacity.cols.peak')}</th>
              <th className="text-right px-3 py-2">{t('pools.capacity.cols.capacity')}</th>
              <th className="text-right px-3 py-2">{t('pools.capacity.cols.headroom')}</th>
              <th className="px-3 py-2 w-[34%]">{t('pools.capacity.cols.util')}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const u = r.utilization ?? (r.capacity > 0 ? r.peak_concurrency / r.capacity : 0)
              return (
                <tr key={i} className="border-b border-border hover:bg-surface-muted">
                  <td className="px-3 py-2 text-dark">{r.pool_id || '—'}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-dark">{r.peak_concurrency}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-muted">{r.capacity}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-dark">{r.headroom}</td>
                  <td className="px-3 py-2">
                    <div className="flex items-center gap-2">
                      <div className="flex-1 h-2.5 rounded bg-surface-muted overflow-hidden">
                        <div className="h-full rounded" style={{ width: `${Math.min(Math.round(u * 100), 100)}%`, backgroundColor: utilColor(r.utilization) }} />
                      </div>
                      <span className="text-2xs tabular-nums text-muted w-9 text-right">{pct(r.utilization)}</span>
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Queue (Fila) sub-tab ─────────────────────────────────────────────────────

const FilaSubTab: React.FC<{
  data: QueueData | null; loading: boolean
  queueTiers: Record<string, 'attended' | 'system' | 'none'>
}> = ({ data, loading, queueTiers }) => {
  const { t } = useTranslation('agentReports')
  if (loading) return (
    <div className="h-48 flex items-center justify-center text-sm text-muted-light animate-pulse">{t('pools.volume.loading')}</div>
  )
  const rows = data?.by_pool ?? []
  if (rows.length === 0) return (
    <div className="h-48 flex items-center justify-center text-sm text-muted-light">{t('pools.queue.noData')}</div>
  )

  // F5 — a linha `available` foi REMOVIDA deste gráfico (§3.1 do desenho de capacidade
  // compartilhada). Não era a mesma grandeza do `available` do snapshot: vinha de
  // `queue_events.available_agents`, alimentado por `SCARD(pool:instances)` — contagem
  // de PERTENCIMENTO, com valor ambíguo (o `1` podia ser filtro de canal, pool
  // `dispatch_mode: pull` ou defeito), 77% de nulos que viravam 0 na leitura, e ainda
  // somada ENTRE POOLS (o defeito C). Não havia o que corrigir, só o que redefinir — e
  // redefinir não backfilla. O produtor e a coluna da query também saíram; deixar o
  // campo sendo escrito sem leitor seria convite a que voltasse ao gráfico.
  // Substituto honesto, se a série for desejada: amostragem por relógio do rollup de
  // tenant (`{t}:capacity:snapshot`), que é deduplicado e separado por tipo de licença.
  const byBucket = new Map<string, { bucket: string; _w: number; _n: number }>()
  for (const r of (data?.series ?? [])) {
    const b = byBucket.get(r.bucket) ?? { bucket: r.bucket, _w: 0, _n: 0 }
    if (r.avg_wait_ms > 0) { b._w += r.avg_wait_ms; b._n += 1 }
    byBucket.set(r.bucket, b)
  }
  const chartData = [...byBucket.values()]
    .sort((a, b) => a.bucket.localeCompare(b.bucket))
    .map(b => ({ bucket: b.bucket, wait_s: b._n ? Math.round(b._w / b._n / 1000) : 0 }))

  return (
    <div className="flex flex-col gap-4">
      <div className="bg-white rounded-lg border border-border overflow-hidden">
        <div className="px-4 py-2.5 border-b border-border bg-surface-muted">
          <p className="text-xs font-semibold text-muted uppercase tracking-wide">{t('pools.queue.title')}</p>
        </div>
        <div className="p-3">
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={chartData} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
              <XAxis dataKey="bucket" tick={{ fontSize: 11 }} tickFormatter={fmtBucket} />
              <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
              <Tooltip labelFormatter={fmtBucket} />
              <Legend iconSize={10} wrapperStyle={{ fontSize: 12 }} />
              <Line type="monotone" dataKey="wait_s" name={t('pools.queue.waitAvg')} stroke="#D97706" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="bg-white rounded-lg border border-border overflow-hidden">
        <div className="px-4 py-2.5 border-b border-border bg-surface-muted">
          <p className="text-xs font-semibold text-muted uppercase tracking-wide">{t('pools.queue.tableTitle')}</p>
          <p className="text-2xs text-muted-light mt-0.5">{t('pools.queue.hint')}</p>
        </div>
        <table className="min-w-full text-xs border-collapse">
          <thead className="bg-surface-muted">
            <tr className="border-b border-border text-2xs text-muted uppercase">
              <th className="text-left px-3 py-2">{t('pools.queue.cols.pool')}</th>
              <th className="text-left px-3 py-2">{t('pools.queue.cols.tier')}</th>
              <th className="text-right px-3 py-2">{t('pools.queue.cols.contacts')}</th>
              <th className="text-right px-3 py-2">{t('pools.queue.cols.queued')}</th>
              <th className="text-right px-3 py-2">{t('pools.queue.cols.waits')}</th>
              <th className="text-right px-3 py-2">{t('pools.queue.cols.handoff')}</th>
              <th className="text-right px-3 py-2">{t('pools.queue.cols.abandoned')}</th>
              <th className="text-right px-3 py-2">{t('pools.queue.cols.abandonRate')}</th>
              <th className="text-right px-3 py-2">{t('pools.queue.cols.avgWait')}</th>
              <th className="text-right px-3 py-2">{t('pools.queue.cols.p95Wait')}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-b border-border hover:bg-surface-muted">
                <td className="px-3 py-2 text-dark">{r.pool_id || '—'}</td>
                {/* Tier da fila (system-queue.md Fase B): atendida = IA licenciada;
                    sistema = gratuita (espera muda, isenta de C). */}
                <td className="px-3 py-2">
                  {(() => {
                    const tier = queueTiers[r.pool_id] ?? 'none'
                    const cls = tier === 'attended'
                      ? 'bg-blue-50 text-blue-700'
                      : tier === 'system' ? 'bg-gray-100 text-gray-600' : 'text-muted-light'
                    return <span className={`text-2xs px-1.5 py-0.5 rounded ${cls}`}>{t(`pools.queue.tier.${tier}`)}</span>
                  })()}
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-dark">{r.contacts}</td>
                <td className="px-3 py-2 text-right tabular-nums text-muted">{r.queued}</td>
                {/* Passagens ≥ sessões em fila: a diferença é a re-espera (transferência,
                    devolução à fila). Onde os dois números divergem, o relatório antigo
                    mostrava só um deles — e sorteava qual. */}
                <td className="px-3 py-2 text-right tabular-nums text-muted">{r.waits ?? 0}</td>
                <td className="px-3 py-2 text-right tabular-nums text-green">{r.handoff ?? 0}</td>
                <td className="px-3 py-2 text-right tabular-nums text-warning-text">{r.abandoned}</td>
                <td className="px-3 py-2 text-right tabular-nums text-muted">{Math.round((r.abandon_rate ?? 0) * 100)}%</td>
                <td className="px-3 py-2 text-right tabular-nums text-dark">{fmtMs(r.avg_wait_ms)}</td>
                <td className="px-3 py-2 text-right tabular-nums text-muted">{fmtMs(r.p95_wait_ms)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── SLA sub-tab ──────────────────────────────────────────────────────────────

const SlaSubTab: React.FC<{ data: QueueData | null; loading: boolean }> = ({ data, loading }) => {
  const { t } = useTranslation('agentReports')
  if (loading) return (
    <div className="h-48 flex items-center justify-center text-sm text-muted-light animate-pulse">{t('pools.volume.loading')}</div>
  )
  const rows = (data?.by_pool ?? []).filter(r => r.sla_eligible > 0)
  if (rows.length === 0) return (
    <div className="h-48 flex items-center justify-center text-sm text-muted-light">{t('pools.sla.noData')}</div>
  )

  return (
    <div className="bg-white rounded-lg border border-border overflow-hidden">
      <div className="px-4 py-2.5 border-b border-border bg-surface-muted">
        <p className="text-xs font-semibold text-muted uppercase tracking-wide">{t('pools.sla.title')}</p>
        <p className="text-2xs text-muted-light mt-0.5">{t('pools.sla.hint')}</p>
      </div>
      <table className="min-w-full text-xs border-collapse">
        <thead className="bg-surface-muted">
          <tr className="border-b border-border text-2xs text-muted uppercase">
            <th className="text-left px-3 py-2">{t('pools.sla.cols.pool')}</th>
            <th className="text-right px-3 py-2">{t('pools.sla.cols.target')}</th>
            <th className="text-right px-3 py-2">{t('pools.sla.cols.within')}</th>
            <th className="px-3 py-2 w-[40%]">{t('pools.sla.cols.attainment')}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            // `sla_attainment` NULL = não medido (nenhuma espera concluída com alvo).
            // O `?? 0` que havia aqui pintava a barra de VERMELHO em 0% — ausência
            // virando ponto legítimo da escala, a mesma família do sentimento
            // (CLAUDE.md § Sentiment Tracking). Hoje o filtro `sla_eligible > 0`
            // acima o tornava inalcançável, mas guarda a montante não é motivo para
            // manter mentira a jusante: com a D14-i mais pools passam a ter
            // `sla_eligible = 0`, e o default estava a uma edição de voltar a mentir.
            const a = r.sla_attainment
            if (a === null || a === undefined) return (
              <tr key={i} className="border-b border-border hover:bg-surface-muted">
                <td className="px-3 py-2 text-dark">{r.pool_id || '—'}</td>
                <td className="px-3 py-2 text-right tabular-nums text-muted">{fmtMs(r.sla_target_ms)}</td>
                <td className="px-3 py-2 text-right tabular-nums text-muted-light">—</td>
                <td className="px-3 py-2 text-2xs text-muted-light">{t('pools.sla.notMeasured')}</td>
              </tr>
            )
            const c = a >= 0.9 ? '#059669' : a >= 0.75 ? '#D97706' : '#DC2626'
            return (
              <tr key={i} className="border-b border-border hover:bg-surface-muted">
                <td className="px-3 py-2 text-dark">{r.pool_id || '—'}</td>
                <td className="px-3 py-2 text-right tabular-nums text-muted">{fmtMs(r.sla_target_ms)}</td>
                <td className="px-3 py-2 text-right tabular-nums text-dark">{r.within_sla}/{r.sla_eligible}</td>
                <td className="px-3 py-2">
                  <div className="flex items-center gap-2">
                    <div className="flex-1 h-2.5 rounded bg-surface-muted overflow-hidden">
                      <div className="h-full rounded" style={{ width: `${Math.round(a * 100)}%`, backgroundColor: c }} />
                    </div>
                    <span className="text-2xs tabular-nums w-9 text-right" style={{ color: c }}>{Math.round(a * 100)}%</span>
                  </div>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── Page ───────────────────────────────────────────────────────────────────────

/**
 * Filtros e lente vindos da SUPERFÍCIE que hospeda estes painéis (F3).
 *
 * `/analise/pools` deixou de ser endereço: as quatro sub-abas viraram lentes da
 * Superfície B (D7 — "endereço morre, componente é re-hospedado"). Quando `host` está
 * presente, esta página não desenha a própria barra de filtro nem a própria faixa de
 * sub-abas: as duas passam a ser da superfície.
 *
 * É o mesmo padrão que a F2 usou para o `WrapupSummaryPage`, e pela mesma razão: DUAS
 * janelas de tempo na mesma tela — a da superfície e a de dentro — e a de dentro vence
 * em silêncio. O usuário mexe na de cima e o painel não muda.
 */
export interface PoolPanelHost {
  fromDt:  string
  toDt:    string
  poolId:  string
  channel: string
  subTab:  SubTab
}

export default function AnalisePoolsPage({ host }: { host?: PoolPanelHost } = {}) {
  const { t }        = useTranslation('agentReports')
  const { tenantId } = useAuth()

  const today   = new Date()
  const weekAgo = new Date(today.getTime() - 7 * 86400000)
  const iso     = (d: Date) => d.toISOString().slice(0, 10)

  const [ownSubTab,  setSubTab]  = useState<SubTab>('volume')
  const [ownFromDt,  setFromDt]  = useState(iso(weekAgo))
  const [ownToDt,    setToDt]    = useState(iso(today))
  const [ownPoolId,  setPoolId]  = useState('')
  const [ownChannel, setChannel] = useState('')

  // O hospedeiro VENCE quando existe. Não é merge: dois donos para o mesmo filtro é
  // exatamente o defeito que o `host` fecha.
  const subTab  = host ? host.subTab  : ownSubTab
  const fromDt  = host ? host.fromDt  : ownFromDt
  const toDt    = host ? host.toDt    : ownToDt
  const poolId  = host ? host.poolId  : ownPoolId
  const channel = host ? host.channel : ownChannel

  const [volume,  setVolume]  = useState<VolumeData | null>(null)
  const [occ,     setOcc]     = useState<OccData | null>(null)
  const [queue,   setQueue]   = useState<QueueData | null>(null)
  const [loading, setLoading] = useState(false)

  // Tier da fila por pool (system-queue.md Fase B): derivado da config do
  // registry (sem mudar o analytics) — queue_config ⇒ atendida (IA licenciada);
  // pool humano sem ⇒ fila de sistema (gratuita); pool IA ⇒ sem fila.
  const [queueTiers, setQueueTiers] = useState<Record<string, 'attended' | 'system' | 'none'>>({})
  // Opções do combo de pool — reaproveita o mesmo GET /v1/pools dos tiers.
  const [poolOptions, setPoolOptions] = useState<string[]>([])
  useEffect(() => {
    if (!tenantId) return
    let cancelled = false
    fetch('/v1/pools', { headers: { 'x-tenant-id': tenantId } })
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then((d: { pools?: Array<{ pool_id: string; queue_config?: unknown; agent_kind?: string }> }) => {
        if (cancelled) return
        const map: Record<string, 'attended' | 'system' | 'none'> = {}
        for (const p of d.pools ?? []) {
          map[p.pool_id] = p.queue_config ? 'attended'
            : p.agent_kind === 'human' ? 'system' : 'none'
        }
        setQueueTiers(map)
        setPoolOptions([...new Set((d.pools ?? []).map(p => p.pool_id).filter(Boolean))].sort())
      })
      .catch(() => { /* coluna mostra '—' */ })
    return () => { cancelled = true }
  }, [tenantId])

  useEffect(() => {
    if (!tenantId || subTab !== 'volume') return
    let cancelled = false
    setLoading(true)
    const p = new URLSearchParams({ tenant_id: tenantId, from_dt: fromDt, to_dt: toDt, bucket: 'day' })
    if (poolId)  p.set('pool_id', poolId)
    if (channel) p.set('channel', channel)
    apiFetch(`/reports/pools/volume?${p}`)
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then((d: { data: VolumeData }) => { if (!cancelled) setVolume(d.data ?? null) })
      .catch(() => { if (!cancelled) setVolume(null) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [tenantId, subTab, fromDt, toDt, poolId, channel])

  useEffect(() => {
    if (!tenantId || subTab !== 'capacity') return
    let cancelled = false
    setLoading(true)
    // Convenção do spec: bucket hour até 48h, senão day (série fica legível).
    const spanDays = (new Date(toDt).getTime() - new Date(fromDt).getTime()) / 86400000
    const p = new URLSearchParams({ tenant_id: tenantId, from_dt: fromDt, to_dt: toDt, bucket: spanDays <= 2 ? 'hour' : 'day' })
    if (poolId) p.set('pool_id', poolId)
    apiFetch(`/reports/pools/occupancy?${p}`)
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then((d: { data: OccData }) => { if (!cancelled) setOcc(d.data ?? null) })
      .catch(() => { if (!cancelled) setOcc(null) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [tenantId, subTab, fromDt, toDt, poolId])

  useEffect(() => {
    if (!tenantId || (subTab !== 'queue' && subTab !== 'sla')) return
    let cancelled = false
    setLoading(true)
    const p = new URLSearchParams({ tenant_id: tenantId, from_dt: fromDt, to_dt: toDt, bucket: 'day' })
    if (poolId) p.set('pool_id', poolId)
    apiFetch(`/reports/pools/queue?${p}`)
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then((d: { data: QueueData }) => { if (!cancelled) setQueue(d.data ?? null) })
      .catch(() => { if (!cancelled) setQueue(null) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [tenantId, subTab, fromDt, toDt, poolId])

  if (!tenantId) return null

  const subtabs: Array<{ id: SubTab; soon?: boolean }> = [
    { id: 'volume' }, { id: 'queue' }, { id: 'capacity' }, { id: 'sla' },
  ]

  // Hospedado: só o conteúdo. Barra de filtro e faixa de sub-abas são da superfície.
  if (host) {
    return (
      <div className="flex-1 overflow-auto p-4">
        {subTab === 'volume'   && <VolumeSubTab data={volume} loading={loading} />}
        {subTab === 'queue'    && <FilaSubTab data={queue} loading={loading} queueTiers={queueTiers} />}
        {subTab === 'capacity' && <CapacitySubTab data={occ} loading={loading} />}
        {subTab === 'sla'      && <SlaSubTab data={queue} loading={loading} />}
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full overflow-hidden bg-surface-muted">
      {/* Filtros */}
      <div className="flex flex-wrap items-center gap-3 px-4 py-3 bg-white border-b border-border">
        <span className="text-xs text-muted">{t('pools.filter.from')}</span>
        <input type="date" value={fromDt} onChange={e => setFromDt(e.target.value)}
          className="text-sm border border-border rounded px-2 py-1" />
        <span className="text-xs text-muted">{t('pools.filter.to')}</span>
        <input type="date" value={toDt} onChange={e => setToDt(e.target.value)}
          className="text-sm border border-border rounded px-2 py-1" />
        <select value={poolId} onChange={e => setPoolId(e.target.value)}
          className="text-sm border border-border rounded px-2 py-1 w-44 bg-white">
          <option value="">{t('pools.filter.allPools')}</option>
          {poolOptions.map(p => <option key={p} value={p}>{p}</option>)}
        </select>
        <select value={channel} onChange={e => setChannel(e.target.value)}
          className="text-sm border border-border rounded px-2 py-1 w-36 bg-white">
          <option value="">{t('pools.filter.allChannels')}</option>
          {['webchat', 'whatsapp', 'voice', 'email', 'sms', 'webhook'].map(c => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        {volume && (
          <span className="ml-auto text-sm text-muted">
            {t('pools.kpi.contacts')}: <strong className="text-dark">{volume.totals.contacts}</strong>
            {(volume.totals.rejected ?? 0) > 0 && (
              <> · {t('pools.kpi.rejected')}: <strong className="text-red">{volume.totals.rejected}</strong></>
            )}
          </span>
        )}
      </div>

      {/* Sub-abas */}
      <div className="flex gap-1 px-4 border-b border-border bg-white">
        {subtabs.map(s => (
          <button key={s.id} onClick={() => !s.soon && setSubTab(s.id)} disabled={s.soon}
            className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
              subTab === s.id ? 'border-primary text-primary'
              : s.soon ? 'border-transparent text-muted-light cursor-not-allowed'
              : 'border-transparent text-muted hover:text-dark'}`}>
            {t(`pools.subtabs.${s.id}`)}{s.soon && <span className="ml-1 text-2xs">· {t('pools.soon')}</span>}
          </button>
        ))}
      </div>

      {/* Conteúdo */}
      <div className="flex-1 overflow-auto p-4">
        {subTab === 'volume'   && <VolumeSubTab data={volume} loading={loading} />}
        {subTab === 'queue'    && <FilaSubTab data={queue} loading={loading} queueTiers={queueTiers} />}
        {subTab === 'capacity' && <CapacitySubTab data={occ} loading={loading} />}
        {subTab === 'sla'      && <SlaSubTab data={queue} loading={loading} />}
      </div>
    </div>
  )
}
