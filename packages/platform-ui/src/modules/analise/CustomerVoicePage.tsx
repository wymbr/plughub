/**
 * CustomerVoicePage — /analise/customer-voice
 *
 * Voz do Cliente (Fatia 1): lente genérica (grão × instrumento) sobre session_signal,
 * com overlay do KPI operacional SLA no mesmo eixo temporal. O board pergunta ao backend
 * o CATÁLOGO de instrumentos (metric → source/rollup/grãos) e monta os seletores. A leitura
 * é descritiva — justapõe a voz percebida (survey) com a medida objetiva (SLA); não conclui
 * causa. Backend: GET /reports/customer-voice + /customer-voice/instruments.
 */
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { apiFetch } from '@/api/apiFetch'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'
import EmptyState from '@/components/ui/EmptyState'

type Grain = 'segment' | 'session' | 'journey'
interface Instrument { source: string; rollup: string; label: string; higher_is_better: boolean; grains: Grain[] }
interface Point { date: string; n: number; value: number | null }
interface CVResp {
  metric: string
  grain: string
  instrument: { label: string; rollup: string; source: string; higher_is_better: boolean }
  series: Point[]
  overlay: { sla?: Point[] }
  summary: { value: number | null; n: number }
}

// ── dual-line overlay chart (each line normalized to its own band) ─────────────
function OverlayChart({ survey, sla, surveyLabel }: {
  survey: Point[]; sla: Point[]; surveyLabel: string
}) {
  const { t } = useTranslation('customerVoice')
  const W = 720, H = 260, padX = 44, padY = 24
  const dates = Array.from(new Set([...survey, ...sla].map(p => p.date))).sort()
  if (dates.length === 0) return null
  const xIdx = new Map(dates.map((d, i) => [d, i]))
  const x = (d: string) => padX + (dates.length === 1 ? (W - 2 * padX) / 2
    : (xIdx.get(d)! / (dates.length - 1)) * (W - 2 * padX))

  function band(pts: Point[]) {
    const vals = pts.filter(p => p.value != null).map(p => p.value as number)
    const lo = Math.min(...vals), hi = Math.max(...vals)
    const span = hi - lo || 1
    return { lo, hi, y: (v: number) => padY + (1 - (v - lo) / span) * (H - 2 * padY) }
  }
  const sBand = band(survey), lBand = sla.length ? band(sla) : null

  const line = (pts: Point[], yf: (v: number) => number, color: string) => {
    const seg = pts.filter(p => p.value != null)
    if (seg.length === 0) return null
    const dPath = seg.map((p, i) => `${i === 0 ? 'M' : 'L'} ${x(p.date).toFixed(1)} ${yf(p.value as number).toFixed(1)}`).join(' ')
    return (
      <g>
        <path d={dPath} fill="none" stroke={color} strokeWidth={2} />
        {seg.map(p => <circle key={p.date} cx={x(p.date)} cy={yf(p.value as number)} r={3} fill={color} />)}
      </g>
    )
  }

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ maxHeight: 300 }}>
      {/* survey band labels (left) */}
      <text x={4} y={padY + 4} className="fill-primary" style={{ fontSize: 10 }}>{sBand.hi.toFixed(0)}</text>
      <text x={4} y={H - padY} className="fill-primary" style={{ fontSize: 10 }}>{sBand.lo.toFixed(0)}</text>
      {/* sla band labels (right) */}
      {lBand && <text x={W - 40} y={padY + 4} className="fill-warning-text" style={{ fontSize: 10 }}>{lBand.hi.toFixed(0)}%</text>}
      {lBand && <text x={W - 40} y={H - padY} className="fill-warning-text" style={{ fontSize: 10 }}>{lBand.lo.toFixed(0)}%</text>}
      {lBand && line(sla, lBand.y, 'var(--color-warning, #D97706)')}
      {line(survey, sBand.y, 'var(--color-primary, #1B4F8A)')}
      {/* x labels: first / last */}
      <text x={padX} y={H - 4} style={{ fontSize: 10 }} className="fill-muted-light">{dates[0]}</text>
      <text x={W - padX} y={H - 4} textAnchor="end" style={{ fontSize: 10 }} className="fill-muted-light">{dates[dates.length - 1]}</text>
      {/* legend */}
      <g>
        <rect x={padX} y={4} width={10} height={3} fill="var(--color-primary, #1B4F8A)" />
        <text x={padX + 14} y={9} style={{ fontSize: 10 }} className="fill-dark">{surveyLabel}</text>
        <rect x={padX + 120} y={4} width={10} height={3} fill="var(--color-warning, #D97706)" />
        <text x={padX + 134} y={9} style={{ fontSize: 10 }} className="fill-dark">{t('sla')}</text>
      </g>
    </svg>
  )
}

export default function CustomerVoicePage() {
  const { t } = useTranslation('customerVoice')
  const { session, tenantId } = useAuth()
  const [instruments, setInstruments] = useState<Record<string, Instrument>>({})
  const [metric, setMetric] = useState('nps')
  const [grain, setGrain] = useState<Grain>('journey')
  const [data, setData] = useState<CVResp | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    fetch('/reports/customer-voice/instruments')
      .then(r => r.json())
      .then(d => setInstruments(d.instruments ?? {}))
      .catch(() => setInstruments({}))
  }, [])

  const surveyMetrics = useMemo(
    () => Object.keys(instruments).filter(k => instruments[k].source === 'survey'),
    [instruments],
  )
  const grainsForMetric = instruments[metric]?.grains ?? ['segment', 'session', 'journey']

  // keep grain valid for the chosen instrument
  useEffect(() => {
    if (!grainsForMetric.includes(grain)) setGrain(grainsForMetric[0] as Grain)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [metric, instruments])

  useEffect(() => {
    if (!tenantId) return
    setLoading(true)
    const p = new URLSearchParams({ tenant_id: tenantId, grain, metric })
    apiFetch(`/reports/customer-voice?${p}`)
      .then(r => r.json())
      .then((d: CVResp) => setData(d))
      .catch(() => setData(null))
      .finally(() => setLoading(false))
  }, [tenantId, grain, metric])

  if (!session) return <div className="flex items-center justify-center h-full"><p className="text-muted">{t('restricted')}</p></div>

  const hasData = data && data.series.some(p => p.value != null)

  return (
    <div className="flex flex-col h-full bg-surface-muted">
      <div className="bg-white flex-shrink-0 px-6 pt-4 pb-3 border-b border-border">
        <h1 className="text-lg font-semibold text-dark">{t('title')}</h1>
        <p className="text-sm text-muted mt-0.5">{t('info')}</p>
        <div className="flex items-center gap-3 mt-3 flex-wrap">
          <div>
            <label className="block text-xs font-medium text-dark mb-1">{t('instrument')}</label>
            <select value={metric} onChange={e => setMetric(e.target.value)}
              className="text-sm border border-border-strong rounded-lg px-3 py-1.5 bg-white">
              {surveyMetrics.map(k => <option key={k} value={k}>{instruments[k].label}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-dark mb-1">{t('grain')}</label>
            <select value={grain} onChange={e => setGrain(e.target.value as Grain)}
              className="text-sm border border-border-strong rounded-lg px-3 py-1.5 bg-white">
              {grainsForMetric.map(g => <option key={g} value={g}>{t(`grains.${g}`)}</option>)}
            </select>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {loading && <div className="flex justify-center py-8"><Spinner /></div>}
        {!loading && !hasData && (
          <EmptyState icon="🗣️" title={t('empty.title')} description={t('empty.desc')} />
        )}
        {!loading && hasData && data && (
          <div className="bg-white border border-border rounded-xl p-4 space-y-3">
            <div className="flex items-center gap-4 flex-wrap">
              <div>
                <div className="text-2xl font-semibold text-primary">
                  {data.summary.value ?? '—'}{data.instrument.rollup === 'avg' ? '' : ''}
                </div>
                <div className="text-xs text-muted">{data.instrument.label} · {t('nResponses', { count: data.summary.n })}</div>
              </div>
              <p className="text-xs text-muted-light max-w-md ml-auto">{t('overlayNote')}</p>
            </div>
            <OverlayChart survey={data.series} sla={data.overlay?.sla ?? []} surveyLabel={data.instrument.label} />
          </div>
        )}
      </div>
    </div>
  )
}
