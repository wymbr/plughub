/**
 * CalibrationDashboard.tsx
 * /evaluation/calibration — Calibration Score do avaliador AI por skill version × tempo (Arc 13 Fase G)
 *
 * Exibe:
 *  - KPI strip: total curados, aprovados %, recalibrados %, viés %.
 *  - LineChart (Recharts): calibration_score por skill_version ao longo do tempo.
 *  - Filtros: campanha, evaluator_id, granularidade (dia/semana).
 */

import React, { useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
} from 'recharts'
import { useEvaluatorCalibration } from '@/api/evaluation-hooks'
import { useCampaigns } from '@/api/evaluation-hooks'
import { useAuth } from '@/auth/useAuth'
import type { CalibrationPoint } from '@/api/evaluation-hooks'

// ── Palette: one colour per skill_version (cycles through) ────────────────────
const COLORS = ['#1B4F8A', '#2D9CDB', '#00B4D8', '#059669', '#D97706', '#DC2626', '#7C3AED']

function versionColor(version: string, allVersions: string[]): string {
  const idx = allVersions.indexOf(version)
  return COLORS[idx % COLORS.length]
}

// ── KPI Card ──────────────────────────────────────────────────────────────────

function KpiCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-white border border-border rounded-lg p-4 flex flex-col gap-1">
      <span className="text-xs text-muted">{label}</span>
      <span className="text-2xl font-bold text-dark">{value}</span>
      {sub && <span className="text-xs text-muted-light">{sub}</span>}
    </div>
  )
}

// ── Pivot time-series rows for Recharts ──────────────────────────────────────
// Input: flat rows [{period, skill_version, calibration_score}]
// Output: [{period, v1: 92, v2: 87, …}] pivoted by skill_version

function pivotData(
  points: CalibrationPoint[],
  versions: string[],
): Record<string, string | number | null>[] {
  const byPeriod: Record<string, Record<string, string | number | null>> = {}
  for (const p of points) {
    if (!byPeriod[p.period]) byPeriod[p.period] = { period: p.period }
    byPeriod[p.period][p.skill_version] = p.calibration_score
  }
  // Fill missing versions with null
  for (const row of Object.values(byPeriod)) {
    for (const v of versions) {
      if (!(v in row)) row[v] = null
    }
  }
  return Object.values(byPeriod).sort((a, b) =>
    String(a.period).localeCompare(String(b.period))
  )
}

// ── CalibrationDashboard ──────────────────────────────────────────────────────

export default function CalibrationDashboard() {
  const { t } = useTranslation('evaluation')
  const { tenantId: TENANT } = useAuth()
  const { campaigns } = useCampaigns(TENANT)

  const [campaignId,  setCampaignId]  = useState<string | undefined>(undefined)
  const [evaluatorId, setEvaluatorId] = useState<string | undefined>(undefined)
  const [granularity, setGranularity] = useState<'day' | 'week'>('day')

  const { data, summary, meta, loading, error, reload } = useEvaluatorCalibration(
    TENANT,
    {
      campaign_id:  campaignId,
      evaluator_id: evaluatorId || undefined,
      granularity,
    },
  )

  // Unique skill versions in the data, sorted
  const versions = Array.from(new Set(data.map(p => p.skill_version))).sort()
  const chartData = pivotData(data, versions)

  const approvedPct   = summary.total > 0 ? ((summary.approved   / summary.total) * 100).toFixed(1) : '—'
  const recalibPct    = summary.total > 0 ? ((summary.recalibrated / summary.total) * 100).toFixed(1) : '—'
  const biasPct       = summary.total > 0 ? ((summary.bias_flagged  / summary.total) * 100).toFixed(1) : '—'
  const scoreDisplay  = summary.calibration_score != null ? `${summary.calibration_score.toFixed(1)}%` : '—'

  const fromLabel = typeof meta.from_dt === 'string' ? meta.from_dt.slice(0, 10) : ''
  const toLabel   = typeof meta.to_dt   === 'string' ? meta.to_dt.slice(0, 10)   : ''

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-dark">{t('calibration.title')}</h1>
          {fromLabel && toLabel && (
            <p className="text-xs text-muted mt-0.5">{fromLabel} → {toLabel}</p>
          )}
        </div>
        <button
          onClick={reload}
          className="text-sm text-primary hover:underline"
        >
          {t('calibration.refresh')}
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-4 items-end">
        <div>
          <label className="block text-xs text-muted mb-1">{t('calibration.campaignFilter')}</label>
          <select
            className="border border-border-strong rounded px-2 py-1.5 text-sm min-w-[200px]"
            value={campaignId ?? ''}
            onChange={e => setCampaignId(e.target.value || undefined)}
          >
            <option value="">{t('calibration.allCampaigns')}</option>
            {campaigns.map(c => (
              <option key={c.campaign_id} value={c.campaign_id}>{c.name}</option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-xs text-muted mb-1">{t('calibration.evaluatorFilter')}</label>
          <input
            type="text"
            placeholder={t('calibration.evaluatorPlaceholder')}
            className="border border-border-strong rounded px-2 py-1.5 text-sm w-52"
            value={evaluatorId ?? ''}
            onChange={e => setEvaluatorId(e.target.value || undefined)}
          />
        </div>

        <div>
          <label className="block text-xs text-muted mb-1">{t('calibration.granularityFilter')}</label>
          <select
            className="border border-border-strong rounded px-2 py-1.5 text-sm"
            value={granularity}
            onChange={e => setGranularity(e.target.value as 'day' | 'week')}
          >
            <option value="day">{t('calibration.granDay')}</option>
            <option value="week">{t('calibration.granWeek')}</option>
          </select>
        </div>
      </div>

      {/* KPI strip */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <KpiCard
          label={t('calibration.kpi.calibrationScore')}
          value={scoreDisplay}
          sub={t('calibration.kpi.approvedSub', { approved: summary.approved, total: summary.total })}
        />
        <KpiCard
          label={t('calibration.kpi.approved')}
          value={approvedPct !== '—' ? `${approvedPct}%` : '—'}
          sub={String(summary.approved)}
        />
        <KpiCard
          label={t('calibration.kpi.recalibrated')}
          value={recalibPct !== '—' ? `${recalibPct}%` : '—'}
          sub={String(summary.recalibrated)}
        />
        <KpiCard
          label={t('calibration.kpi.biasDetected')}
          value={biasPct !== '—' ? `${biasPct}%` : '—'}
          sub={String(summary.bias_flagged)}
        />
      </div>

      {/* Chart */}
      <div className="bg-white border border-border rounded-lg p-4">
        <h2 className="text-sm font-medium text-dark mb-4">
          {t('calibration.chart.title')}
        </h2>

        {loading && (
          <div className="flex items-center justify-center h-48 text-muted-light text-sm">
            {t('calibration.chart.loading')}
          </div>
        )}

        {error && (
          <div className="flex items-center justify-center h-48 text-red text-sm">
            {error}
          </div>
        )}

        {!loading && !error && chartData.length === 0 && (
          <div className="flex items-center justify-center h-48 text-muted-light text-sm">
            {t('calibration.chart.empty')}
          </div>
        )}

        {!loading && !error && chartData.length > 0 && (
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={chartData} margin={{ top: 4, right: 24, left: 0, bottom: 4 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis dataKey="period" tick={{ fontSize: 11 }} />
              <YAxis
                domain={[0, 100]}
                tickFormatter={(v: number) => `${v}%`}
                tick={{ fontSize: 11 }}
                width={44}
              />
              <Tooltip
                formatter={(value: unknown, name: string) => [
                  typeof value === 'number' ? `${value.toFixed(1)}%` : '—',
                  name,
                ]}
              />
              <Legend />
              {/* 90% threshold reference line */}
              <ReferenceLine
                y={90}
                stroke="#059669"
                strokeDasharray="4 4"
                label={{ value: '90%', position: 'insideTopRight', fontSize: 10, fill: '#059669' }}
              />
              {versions.map(v => (
                <Line
                  key={v}
                  type="monotone"
                  dataKey={v}
                  name={v}
                  stroke={versionColor(v, versions)}
                  strokeWidth={2}
                  dot={{ r: 3 }}
                  connectNulls={false}
                  isAnimationActive={false}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        )}

        <p className="text-xs text-muted-light mt-2">
          {t('calibration.chart.footnote')}
        </p>
      </div>

      {/* Raw table */}
      {!loading && chartData.length > 0 && (
        <div className="bg-white border border-border rounded-lg overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-xs text-muted">
                <th className="text-left px-4 py-2">{t('calibration.table.period')}</th>
                <th className="text-left px-4 py-2">{t('calibration.table.version')}</th>
                <th className="text-right px-4 py-2">{t('calibration.table.total')}</th>
                <th className="text-right px-4 py-2">{t('calibration.table.approved')}</th>
                <th className="text-right px-4 py-2">{t('calibration.table.recalibrated')}</th>
                <th className="text-right px-4 py-2">{t('calibration.table.bias')}</th>
                <th className="text-right px-4 py-2">{t('calibration.table.score')}</th>
                <th className="text-center px-4 py-2">{t('calibration.table.recommendation')}</th>
              </tr>
            </thead>
            <tbody>
              {data.map((row, i) => {
                const score = row.calibration_score
                const scoreColor = score == null ? 'text-muted-light'
                  : score >= 90 ? 'text-green-text font-semibold'
                  : score >= 75 ? 'text-warning-text font-semibold'
                  : 'text-red-text font-semibold'
                return (
                  <tr key={i} className="border-b border-border hover:bg-surface-muted">
                    <td className="px-4 py-2 text-muted">{row.period}</td>
                    <td className="px-4 py-2">
                      <span
                        className="inline-block px-2 py-0.5 rounded text-xs text-white"
                        style={{ background: versionColor(row.skill_version, versions) }}
                      >
                        {row.skill_version || '—'}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-right text-dark">{row.total}</td>
                    <td className="px-4 py-2 text-right text-green-text">{row.approved}</td>
                    <td className="px-4 py-2 text-right text-warning-text">{row.recalibrated}</td>
                    <td className="px-4 py-2 text-right text-red-text">{row.bias_flagged}</td>
                    <td className={`px-4 py-2 text-right ${scoreColor}`}>
                      {score != null ? `${score.toFixed(1)}%` : '—'}
                    </td>
                    <td className="px-4 py-2 text-center">
                      {row.recalibration_recommended ? (
                        <span
                          className="inline-block px-2 py-0.5 rounded text-xs font-semibold bg-red-bg text-red-text"
                          title={row.divergence != null
                            ? `${t('calibration.table.divergence')}: ${(row.divergence * 100).toFixed(1)}%`
                            : undefined}
                        >
                          {t('calibration.table.recommendBadge')}
                        </span>
                      ) : (
                        <span className="text-xs text-muted-light">—</span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
