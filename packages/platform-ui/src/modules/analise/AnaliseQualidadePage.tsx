/**
 * AnaliseQualidadePage — /analise/qualidade
 *
 * Aba única:
 *   Resumo — relatório de qualidade Oficial × Operacional, agrupável (§17.3).
 *
 * As abas Tendência/Comparação foram aposentadas (consolidação 2026-06-16): a
 * comparação de qualidade — por agente, dimensão, tempo e DEPLOY — vive no bench
 * (Analytics → Agents), incluindo a lente `deploy` (Arc 6 Fase 2). Os endpoints
 * `quality-timeseries`/`quality-comparison` que estas views chamavam nunca
 * existiram no backend.
 *
 * Fonte: GET /reports/evaluations/quality (via useQualityReport).
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import { useQualityReport } from '@/api/evaluation-hooks'
import Spinner from '@/components/ui/Spinner'

// ── Shared helpers ────────────────────────────────────────────────────────────

function isoToday(): string {
  return new Date().toISOString().slice(0, 10)
}
function iso7DaysAgo(): string {
  const d = new Date(); d.setDate(d.getDate() - 6); return d.toISOString().slice(0, 10)
}
function pct(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return `${(value * 100).toFixed(1)}%`
}
function scoreColor(avg: number): string {
  if (avg >= 0.9) return '#059669'
  if (avg >= 0.7) return '#1B4F8A'
  if (avg >= 0.5) return '#D97706'
  return '#DC2626'
}

// ── Shared KPI card ───────────────────────────────────────────────────────────

function KpiCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-white border border-border rounded-lg px-5 py-3 flex flex-col gap-0.5 min-w-[140px]">
      <span className="text-xs text-muted-light uppercase tracking-wide">{label}</span>
      <span className="text-2xl font-bold text-dark leading-none">{value}</span>
      {sub && <span className="text-xs text-muted-light">{sub}</span>}
    </div>
  )
}

// ── Tab bar ───────────────────────────────────────────────────────────────────

type Tab = 'summary'
// Consolidação (2026-06-16) + Arc 6 Fase 2 (P2-C): Trend/Comparison aposentados —
// a comparação de qualidade (por agente/dimensão/tempo/deploy) vive no bench
// (Analytics → Agents). Mantém só o Summary (tabela agregada por campanha).
const TAB_IDS: Tab[] = ['summary']

// ═══════════════════════════════════════════════════════════════════════════════
// TAB: RESUMO
// ═══════════════════════════════════════════════════════════════════════════════

type QGroupBy = 'campaign_id' | 'finalize_reason' | 'segment_id' | 'form_version' | 'evaluated_agent_type' | 'date'
const Q_GROUP_BY_VALUES: QGroupBy[] = ['campaign_id', 'finalize_reason', 'segment_id', 'form_version', 'evaluated_agent_type', 'date']

function QDistBar({ high, mid, low }: { high: number; mid: number; low: number }) {
  const total = high + mid + low || 1
  return (
    <div className="flex h-4 rounded overflow-hidden w-24 gap-px" title={`Alta:${high} · Média:${mid} · Baixa:${low}`}>
      {high > 0 && <div style={{ width: `${(high / total) * 100}%`, background: '#059669' }} />}
      {mid > 0  && <div style={{ width: `${(mid / total) * 100}%`, background: '#D97706' }} />}
      {low > 0  && <div style={{ width: `${(low / total) * 100}%`, background: '#DC2626' }} />}
    </div>
  )
}

/**
 * SummaryView — T11: relatório de qualidade Oficial × Operacional (§17.3).
 * Oficial (default) = só finalizadas (invariante); Operacional = inclui provisório, rotulado.
 * Fonte: GET /reports/evaluations/quality (via useQualityReport).
 */
function SummaryView({ tenantId }: { tenantId: string }) {
  const { t } = useTranslation('contacts')
  const [mode,    setMode]    = useState<'oficial' | 'operacional'>('oficial')
  const [fromDt,  setFromDt]  = useState(iso7DaysAgo)
  const [toDt,    setToDt]    = useState(isoToday)
  const [groupBy, setGroupBy] = useState<QGroupBy>('campaign_id')

  const { rows, finalize_reasons, meta, loading, error } = useQualityReport(
    tenantId, { mode, group_by: groupBy, from_dt: fromDt, to_dt: toDt }, 0,
  )

  const totalN        = rows.reduce((s, r) => s + r.n, 0)
  const weightedScore = rows.reduce((s, r) => s + (r.avg_score ?? 0) * r.n, 0)
  const avgScore      = totalN > 0 ? weightedScore / totalN : null
  const reasonEntries = Object.entries(finalize_reasons || {}).sort((a, b) => b[1] - a[1])
  const sorted        = [...rows].sort((a, b) => b.n - a.n)

  function exportCsv() {
    const cols = ['group_key', 'n', 'finalized_n', 'provisional_n', 'avg_score', 'score_high', 'score_mid', 'score_low'] as const
    const header = cols.join(',')
    const body   = rows.map(r => cols.map(c => (r as unknown as Record<string, unknown>)[c]).join(',')).join('\n')
    const blob   = new Blob([header + '\n' + body], { type: 'text/csv' })
    const url    = URL.createObjectURL(blob)
    const a      = document.createElement('a')
    a.href = url; a.download = `qualidade_${mode}_${fromDt}_${toDt}.csv`; a.click()
    URL.revokeObjectURL(url)
  }

  const groupLabel = (g: string) =>
    t(`quality.groupByOptions.${g}`, { defaultValue: ({
      campaign_id: 'Campanha', finalize_reason: 'Motivo de finalização', segment_id: 'Segmento',
      form_version: 'Versão do form', evaluated_agent_type: 'Tipo de agente', date: 'Data',
    } as Record<string, string>)[g] ?? g })

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Filter bar */}
      <div className="bg-white border-b border-border px-5 py-2.5 flex items-center gap-3 flex-shrink-0 flex-wrap">
        {/* Mode toggle — Oficial × Operacional (nunca blendados, §17.3) */}
        <div className="flex items-center gap-1">
          {([
            { v: 'oficial',     l: t('quality.modes.oficial',     { defaultValue: 'Oficial' }) },
            { v: 'operacional', l: t('quality.modes.operacional', { defaultValue: 'Operacional' }) },
          ] as const).map(o => (
            <button key={o.v} onClick={() => setMode(o.v)}
              className={`text-xs px-3 py-1 rounded border font-medium transition-colors ${
                mode === o.v ? 'bg-primary text-white border-primary'
                             : 'bg-white text-muted border-border-strong hover:bg-surface-muted'}`}>
              {o.l}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1.5"><label className="text-xs text-muted">{t('quality.from')}</label>
          <input type="date" value={fromDt} onChange={e => setFromDt(e.target.value)}
            className="text-xs border border-border-strong rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-primary/40" />
        </div>
        <div className="flex items-center gap-1.5"><label className="text-xs text-muted">{t('quality.to')}</label>
          <input type="date" value={toDt} onChange={e => setToDt(e.target.value)}
            className="text-xs border border-border-strong rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-primary/40" />
        </div>
        <div className="flex items-center gap-1.5"><label className="text-xs text-muted">{t('quality.groupBy')}</label>
          <select value={groupBy} onChange={e => setGroupBy(e.target.value as QGroupBy)}
            className="text-xs border border-border-strong rounded px-2 py-1 bg-white focus:outline-none focus:ring-1 focus:ring-primary/40">
            {Q_GROUP_BY_VALUES.map(v => <option key={v} value={v}>{groupLabel(v)}</option>)}
          </select>
        </div>
        <div className="flex-1" />
        {loading && <Spinner />}
        <button onClick={exportCsv} disabled={rows.length === 0}
          className="text-xs border border-border rounded px-2.5 py-1 text-muted hover:bg-surface-muted disabled:opacity-40">↓ CSV</button>
      </div>

      {/* Mode banner */}
      <div className={`mx-5 mt-3 text-xs rounded px-3 py-2 border ${mode === 'oficial'
        ? 'bg-green-light text-green-text border-green/30'
        : 'bg-warning/10 text-warning-text border-warning/30'}`}>
        {mode === 'oficial'
          ? t('quality.modeBanner.oficial', { defaultValue: '✓ Oficial — somente avaliações finalizadas (invariante de qualidade).' })
          : t('quality.modeBanner.operacional', { defaultValue: '◷ Operacional — inclui avaliações em andamento (provisório), rotuladas. Não é a nota oficial.' })}
      </div>

      {/* KPI strip */}
      <div className="flex gap-3 px-5 py-3 flex-shrink-0 flex-wrap">
        <KpiCard label={t('quality.kpi.finalized', { defaultValue: 'Finalizadas' })} value={String(meta.total_finalized)} />
        {mode === 'operacional' && (
          <KpiCard label={t('quality.kpi.provisional', { defaultValue: 'Provisórias' })} value={String(meta.total_provisional)} />
        )}
        <KpiCard label={t('quality.kpi.avgScore')} value={avgScore !== null ? pct(avgScore) : '—'}
          sub={avgScore !== null ? (avgScore >= 0.9 ? t('quality.scoreLabels.excellent') : avgScore >= 0.7 ? t('quality.scoreLabels.good') : avgScore >= 0.5 ? t('quality.scoreLabels.fair') : t('quality.scoreLabels.poor')) : undefined} />
      </div>

      {/* Finalize-reason chips */}
      {reasonEntries.length > 0 && (
        <div className="px-5 pb-2 flex items-center gap-2 flex-wrap flex-shrink-0">
          <span className="text-xs text-muted-light">{t('quality.finalizeReasons', { defaultValue: 'Motivos de finalização' })}:</span>
          {reasonEntries.map(([reason, n]) => (
            <span key={reason} className="text-xs bg-surface-muted text-muted rounded px-2 py-0.5 border border-border">
              {reason}: <strong className="text-dark">{n}</strong>
            </span>
          ))}
        </div>
      )}

      {error && <div className="mx-5 mb-2 px-3 py-2 bg-red-light border border-red/30 rounded text-xs text-red-text flex-shrink-0">{error}</div>}

      {/* Table */}
      <div className="flex-1 overflow-auto px-5 pb-5">
        {sorted.length === 0 && !loading ? (
          <div className="flex flex-col items-center justify-center py-20 text-muted-light gap-2">
            <span className="text-3xl">✓</span><span className="text-sm">{t('quality.noData')}</span>
          </div>
        ) : (
          <table className="w-full text-xs bg-white border border-border rounded-lg overflow-hidden border-separate border-spacing-0">
            <thead className="sticky top-0 z-10 bg-surface-muted border-b border-border">
              <tr>
                <th className="px-3 py-2.5 text-left text-muted font-medium">{groupLabel(groupBy)}</th>
                <th className="px-3 py-2.5 text-right text-muted font-medium">{t('quality.table.total', { defaultValue: 'Total' })}</th>
                {mode === 'operacional' && (
                  <>
                    <th className="px-3 py-2.5 text-right text-muted font-medium">{t('quality.kpi.finalized', { defaultValue: 'Finalizadas' })}</th>
                    <th className="px-3 py-2.5 text-right text-muted font-medium">{t('quality.kpi.provisional', { defaultValue: 'Provisórias' })}</th>
                  </>
                )}
                <th className="px-3 py-2.5 text-right text-muted font-medium">{t('quality.table.avgScore')}</th>
                <th className="px-3 py-2.5 text-left text-muted font-medium">{t('quality.table.distribution')}</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((row, i) => (
                <tr key={i} className="border-t border-border hover:bg-surface-muted">
                  <td className="px-3 py-2.5 font-mono text-dark max-w-[200px] truncate" title={row.group_key}>{row.group_key || '—'}</td>
                  <td className="px-3 py-2.5 text-right font-medium">{row.n.toLocaleString('pt-BR')}</td>
                  {mode === 'operacional' && (
                    <>
                      <td className="px-3 py-2.5 text-right text-green-text">{row.finalized_n}</td>
                      <td className="px-3 py-2.5 text-right text-warning-text">{row.provisional_n}</td>
                    </>
                  )}
                  <td className="px-3 py-2.5 text-right font-bold" style={{ color: scoreColor(row.avg_score) }}>{pct(row.avg_score)}</td>
                  <td className="px-3 py-2.5"><QDistBar high={row.score_high} mid={row.score_mid} low={row.score_low} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════════════════════
// MAIN PAGE
// ═══════════════════════════════════════════════════════════════════════════════

export default function AnaliseQualidadePage() {
  const { t } = useTranslation('contacts')
  const { tenantId } = useAuth()
  const [activeTab, setActiveTab] = useState<Tab>('summary')

  if (!tenantId) {
    return (
      <div className="flex items-center justify-center h-full text-muted-light text-sm">
        {t('quality.noTenant')}
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full overflow-hidden bg-surface-muted">
      {/* Tab bar */}
      <div className="bg-white border-b border-border px-5 pt-3 flex-shrink-0">
        <div className="flex gap-0">
          {TAB_IDS.map(id => (
            <button
              key={id}
              onClick={() => setActiveTab(id)}
              className={`px-4 py-2 text-xs font-medium border-b-2 transition-colors ${
                activeTab === id
                  ? 'border-primary text-primary'
                  : 'border-transparent text-muted hover:text-dark'
              }`}
            >
              {t(`quality.tabs.${id}`)}
            </button>
          ))}
        </div>
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-hidden min-h-0">
        {activeTab === 'summary' && <SummaryView tenantId={tenantId} />}
        {/* Trend/Comparison removidos — consolidados no bench (Analytics → Agents) */}
      </div>
    </div>
  )
}
