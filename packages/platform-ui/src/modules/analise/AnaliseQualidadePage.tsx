/**
 * AnaliseQualidadePage — /analise/qualidade
 *
 * Evaluation quality analytics: avg score, score distribution, contestation
 * rate and compliance flags — grouped by campaign, form, evaluator or date.
 *
 * Data source: GET /reports/evaluations/summary (analytics-api, proxied via Vite)
 */
import React, { useCallback, useEffect, useState } from 'react'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'

// ── Types ─────────────────────────────────────────────────────────────────────

interface SummaryRow {
  group_key:             string
  total_evaluated:       number
  count_submitted:       number
  count_approved:        number
  count_rejected:        number
  count_contested:       number
  count_locked:          number
  avg_score:             number
  min_score:             number
  max_score:             number
  score_excellent:       number
  score_good:            number
  score_fair:            number
  score_poor:            number
  with_compliance_flags: number
}

interface SummaryResponse {
  data:     SummaryRow[]
  group_by: string
  meta:     { total: number; from_dt: string; to_dt: string }
  error?:   string
}

type GroupBy = 'campaign_id' | 'form_id' | 'evaluator_id' | 'date'

const GROUP_BY_OPTIONS: { value: GroupBy; label: string }[] = [
  { value: 'campaign_id',   label: 'Campanha' },
  { value: 'form_id',       label: 'Formulário' },
  { value: 'evaluator_id',  label: 'Avaliador' },
  { value: 'date',          label: 'Data' },
]

// ── Helpers ───────────────────────────────────────────────────────────────────

function isoToday(): string {
  return new Date().toISOString().slice(0, 10)
}

function iso7DaysAgo(): string {
  const d = new Date()
  d.setDate(d.getDate() - 6)
  return d.toISOString().slice(0, 10)
}

function pct(value: number): string {
  return `${(value * 100).toFixed(1)}%`
}

function scoreColor(avg: number): string {
  if (avg >= 0.9) return '#059669'   // green
  if (avg >= 0.7) return '#1B4F8A'   // primary blue
  if (avg >= 0.5) return '#D97706'   // warning
  return '#DC2626'                    // red
}

// ── Score distribution mini-bar ───────────────────────────────────────────────

function ScoreBar({ row }: { row: SummaryRow }) {
  const total = row.total_evaluated || 1
  const segments = [
    { count: row.score_excellent, color: '#059669', label: 'Excelente' },
    { count: row.score_good,      color: '#2D9CDB', label: 'Bom' },
    { count: row.score_fair,      color: '#D97706', label: 'Regular' },
    { count: row.score_poor,      color: '#DC2626', label: 'Ruim' },
  ]
  return (
    <div className="flex h-4 rounded overflow-hidden w-24 gap-px" title={
      segments.map(s => `${s.label}: ${s.count}`).join(' · ')
    }>
      {segments.map(s => (
        s.count > 0
          ? <div
              key={s.label}
              style={{ width: `${(s.count / total) * 100}%`, background: s.color }}
            />
          : null
      ))}
    </div>
  )
}

// ── KPI card ──────────────────────────────────────────────────────────────────

function KpiCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg px-5 py-3 flex flex-col gap-0.5 min-w-[140px]">
      <span className="text-[11px] text-gray-400 uppercase tracking-wide">{label}</span>
      <span className="text-2xl font-bold text-gray-800 leading-none">{value}</span>
      {sub && <span className="text-[11px] text-gray-400">{sub}</span>}
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function AnaliseQualidadePage() {
  const { tenantId } = useAuth()

  const [fromDt,   setFromDt]   = useState(iso7DaysAgo)
  const [toDt,     setToDt]     = useState(isoToday)
  const [groupBy,  setGroupBy]  = useState<GroupBy>('campaign_id')
  const [data,     setData]     = useState<SummaryRow[]>([])
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState<string | null>(null)
  const [sortKey,  setSortKey]  = useState<keyof SummaryRow>('total_evaluated')
  const [sortAsc,  setSortAsc]  = useState(false)

  const load = useCallback(async () => {
    if (!tenantId) return
    setLoading(true)
    setError(null)
    try {
      const qs = new URLSearchParams({
        tenant_id: tenantId,
        from_dt:   fromDt,
        to_dt:     toDt,
        group_by:  groupBy,
      })
      const res  = await fetch(`/reports/evaluations/summary?${qs}`)
      const body = await res.json() as SummaryResponse
      if (body.error) throw new Error(body.error)
      setData(body.data ?? [])
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setData([])
    } finally {
      setLoading(false)
    }
  }, [tenantId, fromDt, toDt, groupBy])

  useEffect(() => { load() }, [load])

  // ── Derived KPIs ─────────────────────────────────────────────────────────────

  const totalEvaluated  = data.reduce((s, r) => s + r.total_evaluated, 0)
  const totalContested  = data.reduce((s, r) => s + r.count_contested, 0)
  const contestationPct = totalEvaluated > 0 ? totalContested / totalEvaluated : 0

  // weighted avg score
  const weightedScoreNum = data.reduce((s, r) => s + (r.avg_score ?? 0) * r.total_evaluated, 0)
  const avgScore         = totalEvaluated > 0 ? weightedScoreNum / totalEvaluated : null

  // ── Sort ──────────────────────────────────────────────────────────────────────

  function handleSort(key: keyof SummaryRow) {
    if (key === sortKey) setSortAsc(a => !a)
    else { setSortKey(key); setSortAsc(false) }
  }

  const sorted = [...data].sort((a, b) => {
    const va = a[sortKey]
    const vb = b[sortKey]
    const cmp = typeof va === 'number' && typeof vb === 'number'
      ? va - vb
      : String(va).localeCompare(String(vb))
    return sortAsc ? cmp : -cmp
  })

  // ── CSV export ────────────────────────────────────────────────────────────────

  function exportCsv() {
    const cols: (keyof SummaryRow)[] = [
      'group_key', 'total_evaluated', 'avg_score', 'min_score', 'max_score',
      'score_excellent', 'score_good', 'score_fair', 'score_poor',
      'count_contested', 'count_approved', 'count_rejected',
      'count_locked', 'with_compliance_flags',
    ]
    const header = cols.join(',')
    const rows   = data.map(r => cols.map(c => r[c]).join(',')).join('\n')
    const blob   = new Blob([header + '\n' + rows], { type: 'text/csv' })
    const url    = URL.createObjectURL(blob)
    const a      = document.createElement('a')
    a.href = url; a.download = `qualidade_${fromDt}_${toDt}.csv`; a.click()
    URL.revokeObjectURL(url)
  }

  // ── Th helper ─────────────────────────────────────────────────────────────────

  function Th({ label, k, align = 'left' }: { label: string; k: keyof SummaryRow; align?: string }) {
    const active = sortKey === k
    return (
      <th
        onClick={() => handleSort(k)}
        className={`px-3 py-2.5 font-medium text-${align} cursor-pointer select-none whitespace-nowrap hover:text-gray-700 transition-colors ${active ? 'text-primary' : 'text-gray-500'}`}
      >
        {label}{active ? (sortAsc ? ' ↑' : ' ↓') : ''}
      </th>
    )
  }

  if (!tenantId) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400 text-sm">
        Nenhum tenant selecionado.
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full overflow-hidden bg-gray-50">

      {/* ── Filter bar ─────────────────────────────────────────────────────── */}
      <div className="bg-white border-b border-gray-200 px-5 py-2.5 flex items-center gap-3 flex-shrink-0 flex-wrap">
        <span className="font-semibold text-gray-800 text-sm">Análise de Qualidade</span>

        <div className="flex items-center gap-1.5 ml-2">
          <label className="text-xs text-gray-500">De</label>
          <input type="date" value={fromDt} onChange={e => setFromDt(e.target.value)}
            className="text-xs border border-gray-300 rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-primary/40" />
        </div>
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-gray-500">Até</label>
          <input type="date" value={toDt} onChange={e => setToDt(e.target.value)}
            className="text-xs border border-gray-300 rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-primary/40" />
        </div>
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-gray-500">Agrupar por</label>
          <select value={groupBy} onChange={e => setGroupBy(e.target.value as GroupBy)}
            className="text-xs border border-gray-300 rounded px-2 py-1 bg-white focus:outline-none focus:ring-1 focus:ring-primary/40">
            {GROUP_BY_OPTIONS.map(o => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </div>

        <div className="flex-1" />

        {loading
          ? <Spinner />
          : <button onClick={load} className="text-xs text-gray-400 hover:text-gray-600 transition-colors px-2 py-1">↻ Atualizar</button>
        }
        <button onClick={exportCsv} disabled={data.length === 0}
          className="text-xs border border-gray-200 rounded px-2.5 py-1 text-gray-600 hover:bg-gray-50 disabled:opacity-40 transition-colors">
          ↓ CSV
        </button>
      </div>

      {/* ── KPI strip ──────────────────────────────────────────────────────── */}
      <div className="flex gap-3 px-5 py-3 flex-shrink-0 flex-wrap">
        <KpiCard label="Avaliações" value={totalEvaluated.toLocaleString('pt-BR')} />
        <KpiCard
          label="Nota Média"
          value={avgScore !== null ? pct(avgScore) : '—'}
          sub={avgScore !== null && avgScore >= 0.9 ? 'Excelente'
               : avgScore !== null && avgScore >= 0.7 ? 'Bom'
               : avgScore !== null && avgScore >= 0.5 ? 'Regular'
               : avgScore !== null ? 'Ruim' : undefined}
        />
        <KpiCard
          label="Contestações"
          value={pct(contestationPct)}
          sub={`${totalContested} de ${totalEvaluated}`}
        />
      </div>

      {/* ── Error ──────────────────────────────────────────────────────────── */}
      {error && (
        <div className="mx-5 mb-2 px-3 py-2 bg-red-50 border border-red-200 rounded text-xs text-red-600 flex-shrink-0">
          Erro ao carregar dados: {error}
        </div>
      )}

      {/* ── Table ──────────────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-auto px-5 pb-5">
        {sorted.length === 0 && !loading ? (
          <div className="flex flex-col items-center justify-center py-20 text-gray-400 gap-2">
            <span className="text-3xl">✓</span>
            <span className="text-sm">Nenhuma avaliação no período</span>
          </div>
        ) : (
          <table className="w-full text-xs bg-white border border-gray-200 rounded-lg overflow-hidden border-separate border-spacing-0">
            <thead className="sticky top-0 z-10 bg-gray-50 border-b border-gray-200">
              <tr>
                <Th label={GROUP_BY_OPTIONS.find(o => o.value === groupBy)?.label ?? 'Grupo'} k="group_key" />
                <Th label="Avaliações" k="total_evaluated" align="right" />
                <Th label="Nota Média" k="avg_score" align="right" />
                <Th label="Mín / Máx"  k="min_score" align="right" />
                <th className="px-3 py-2.5 text-left text-gray-500 font-medium">Distribuição</th>
                <Th label="Contestadas" k="count_contested" align="right" />
                <Th label="Aprovadas"   k="count_approved"  align="right" />
                <Th label="Rejeitadas"  k="count_rejected"  align="right" />
                <Th label="c/ Flags"    k="with_compliance_flags" align="right" />
              </tr>
            </thead>
            <tbody>
              {sorted.map((row, i) => (
                <tr key={i}
                  className="border-t border-gray-100 hover:bg-gray-50 transition-colors">
                  <td className="px-3 py-2.5 font-mono text-gray-700 max-w-[200px] truncate" title={row.group_key}>
                    {row.group_key || '—'}
                  </td>
                  <td className="px-3 py-2.5 text-right text-gray-700 font-medium">
                    {row.total_evaluated.toLocaleString('pt-BR')}
                  </td>
                  <td className="px-3 py-2.5 text-right font-bold"
                    style={{ color: scoreColor(row.avg_score) }}>
                    {pct(row.avg_score)}
                  </td>
                  <td className="px-3 py-2.5 text-right text-gray-500">
                    {pct(row.min_score)} / {pct(row.max_score)}
                  </td>
                  <td className="px-3 py-2.5">
                    <ScoreBar row={row} />
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    <span className={row.count_contested > 0 ? 'text-warning font-medium' : 'text-gray-400'}>
                      {row.count_contested}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 text-right text-gray-500">{row.count_approved}</td>
                  <td className="px-3 py-2.5 text-right text-gray-500">{row.count_rejected}</td>
                  <td className="px-3 py-2.5 text-right">
                    <span className={row.with_compliance_flags > 0 ? 'text-red-500 font-medium' : 'text-gray-300'}>
                      {row.with_compliance_flags}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
