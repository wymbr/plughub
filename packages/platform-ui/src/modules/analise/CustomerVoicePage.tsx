/**
 * CustomerVoicePage — /analise/customer-voice · **Voz do Cliente** (F4 do
 * `adr-relatorios-duas-superficies-e-lentes.md`).
 *
 * A superfície do SINAL. Sobrevive por mérito próprio (D7: unidade de análise distinta —
 * não é contato nem recurso), e na F4 **absorve `/analise/surveys`** como o seu nível de
 * inspeção: o endereço morre, o componente é re-hospedado.
 *
 *   agregado  → série diária do instrumento (roll-up do catálogo) + overlay de SLA
 *   respostas → a lista resposta-a-resposta, com verbatim (gate ABAC de evaluation)
 *
 * Uma barra de filtro para os dois: período · pools · instrumento · grão.
 *
 * ── O que a medição da F4 mudou no desenho ───────────────────────────────────
 *
 * **1. Os dois níveis NÃO leem o mesmo store, e a tela diz isso.** O agregado vem de
 * `session_signal` (ClickHouse); a lista vem de `survey_response` (PostgreSQL, via
 * evaluation-api). Medido em 2026-08-29 neste ambiente: 130 sinais × 48 respostas para
 * `nps`/`segment`. **Não é defeito** — a diferença é `seed_volume_demo.sh`, que escreve
 * 82 linhas `vol_%` direto no ClickHouse; para todo sinal de caminho REAL existe a
 * resposta correspondente (48 = 48, 3 = 3), porque os dois produtores
 * (`survey_record` e `/survey/{token}/submit`) são persist-first por ADR.
 *
 * Mas o operador vê dois números diferentes, então os dois são MOSTRADOS lado a lado,
 * com o que cada um conta. Um drill que dissesse *"estas são as respostas por trás deste
 * ponto"* afirmaria uma identidade que o dado não garante — e afirmar identidade entre
 * duas populações é a forma mais convincente de publicar uma correlação inexistente.
 *
 * **2. O vocabulário vem do CATÁLOGO, não de lista local.** `AnaliseSurveysPage` tinha
 * `METRICS`/`GRAINS` hardcodados e oferecia combinações que o backend não serve — `pmf ×
 * segment` (o catálogo dá só `session|journey` para PMF) e um grão **`workflow`** que não
 * existe nem no catálogo nem no dado (medido: só `segment` e `session`).
 *
 * **3. O grão default sai do catálogo.** Era `'journey'` fixo, e a página abria em "No
 * signals" com 130 sinais na base — o grão `journey` não tem uma linha sequer neste
 * ambiente. Agora é o primeiro grão que o instrumento declara suportar.
 */
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'
import { apiFetch } from '@/api/apiFetch'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'
import EmptyState from '@/components/ui/EmptyState'
import { PoolMultiSelect } from '@/components/ui/PoolMultiSelect'
import { passesAbacRule } from '@/lib/permissions'
import * as registryApi from '@/api/registry'
import AnaliseSurveysPage from './AnaliseSurveysPage'

type Grain = 'segment' | 'session' | 'journey'
type Level = 'aggregate' | 'responses'

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

const inp = 'text-sm border border-border-strong rounded-lg px-3 py-1.5 bg-white focus:outline-none focus:ring-2 focus:ring-primary/30'

function isoToday(): string { return new Date().toISOString().slice(0, 10) }
function iso30DaysAgo(): string {
  const d = new Date(); d.setDate(d.getDate() - 29)
  return d.toISOString().slice(0, 10)
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
  const { session, tenantId, currentUser } = useAuth()
  const [sp, setSp] = useSearchParams()

  const [instruments, setInstruments] = useState<Record<string, Instrument>>({})
  const [metric, setMetric] = useState(sp.get('metric') || 'nps')
  // ⚠️ Sem default de grão AQUI. Era `'journey'` fixo, e a página abria vazia com 130
  // sinais na base. O default passa a sair do catálogo, no efeito abaixo — mas só
  // depois que ele chega, porque antes disso não há o que declarar.
  const [grain, setGrain] = useState<Grain | ''>((sp.get('grain') as Grain) || '')
  const [fromDt, setFromDt] = useState(sp.get('from') || iso30DaysAgo())
  const [toDt, setToDt] = useState(sp.get('to') || isoToday())
  const [poolIds, setPoolIds] = useState<string[]>(sp.getAll('pool'))
  const [level, setLevel] = useState<Level>(sp.get('view') === 'responses' ? 'responses' : 'aggregate')

  const [domainPools, setDomainPools] = useState<string[]>([])
  const [data, setData] = useState<CVResp | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    apiFetch('/reports/customer-voice/instruments')
      .then(r => r.json())
      .then(d => setInstruments(d.instruments ?? {}))
      .catch(() => setInstruments({}))
  }, [])

  useEffect(() => {
    if (!tenantId) return
    registryApi.listPools(tenantId)
      .then(r => {
        const all = r.items.map(p => p.pool_id)
        const dom = currentUser?.accessiblePools ?? []
        setDomainPools(dom.length ? all.filter(p => dom.includes(p)) : all)
      })
      .catch(() => setDomainPools([]))
  }, [tenantId, currentUser])

  const surveyMetrics = useMemo(
    () => Object.keys(instruments).filter(k => instruments[k].source === 'survey'),
    [instruments],
  )
  const grainsForMetric = instruments[metric]?.grains ?? []

  // O grão vem do CATÁLOGO: mantém o escolhido se o instrumento o suporta, senão cai no
  // primeiro que ele declara. É o que impede oferecer `pmf × segment`, combinação que o
  // backend não serve e que a lista hardcodada de `/analise/surveys` oferecia.
  useEffect(() => {
    if (grainsForMetric.length === 0) return
    if (!grain || !grainsForMetric.includes(grain)) setGrain(grainsForMetric[0])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [metric, instruments])

  // Estado → URL. Esta página é dona de TODOS os parâmetros (a lista hospedada não
  // escreve nenhum), então aqui a substituição inteira é segura — ao contrário da
  // Superfície B, onde a mesa também escreve.
  useEffect(() => {
    const next = new URLSearchParams()
    next.set('metric', metric)
    if (grain) next.set('grain', grain)
    next.set('from', fromDt)
    next.set('to', toDt)
    poolIds.forEach(p => next.append('pool', p))
    if (level === 'responses') next.set('view', level)
    setSp(next, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [metric, grain, fromDt, toDt, poolIds, level])

  useEffect(() => {
    if (!tenantId || !grain) return
    setLoading(true)
    const p = new URLSearchParams({ tenant_id: tenantId, grain, metric, from_dt: fromDt, to_dt: toDt })
    poolIds.forEach(x => p.append('pool_id', x))
    apiFetch(`/reports/customer-voice?${p}`)
      .then(r => r.json())
      .then((d: CVResp) => setData(d))
      .catch(() => setData(null))
      .finally(() => setLoading(false))
  }, [tenantId, grain, metric, fromDt, toDt, poolIds])

  if (!session) return <div className="flex items-center justify-center h-full"><p className="text-muted">{t('restricted')}</p></div>

  const hasData = data && data.series.some(p => p.value != null)

  // ── O nível de RESPOSTAS herda o gate que a entrada de menu tinha ────────────
  //
  // `/analise/surveys` era gateado por `evaluation.report` no `Sidebar`; a superfície é
  // gateada por `contacts.visualizar`. Absorver a lista sem trazer o gate junto ALARGARIA
  // quem alcança verbatim (conteúdo LGPD) — e alargamento é o erro que não aparece na
  // tela, porque só mostra dado a MAIS.
  //
  // ⚠️ **Isto é o gate de NAVEGAÇÃO, não a fronteira.** Medido em 2026-08-29: o endpoint
  // `/v1/evaluation/survey/responses` NÃO confere `evaluation.report` — só recorta por
  // pool —, apesar de o docstring dele afirmar *"gate = acesso ao módulo evaluation,
  // postura LGPD"*. Prosa prometendo invariante sem mecanismo. A F4 não conserta isso
  // (é fronteira de outro serviço, com raio próprio); ela devolve exatamente o gate que
  // existia, para não alargar de carona. Dívida contada no `TODO.md`, com a população
  // medida: 4 dos 6 usuários alcançam `nps_ia` sem o grant, dos quais 3 são fixtures de
  // probe e 1 é o admin.
  const podeVerRespostas = passesAbacRule(
    { module: 'evaluation', field: 'report' }, session.moduleConfig, session.role,
  )
  const nivel: Level = podeVerRespostas ? level : 'aggregate'

  return (
    <div className="flex flex-col h-full overflow-hidden bg-surface-muted">

      {/* Barra de filtro — UMA, para os dois níveis. */}
      <div className="bg-white flex-shrink-0 px-5 py-2.5 border-b border-border flex items-center gap-3 flex-wrap">
        <span className="text-xs text-muted">{t('filter.from')}</span>
        <input type="date" value={fromDt} onChange={e => setFromDt(e.target.value)} className={inp} />
        <span className="text-xs text-muted">{t('filter.to')}</span>
        <input type="date" value={toDt} onChange={e => setToDt(e.target.value)} className={inp} />

        <select value={metric} onChange={e => setMetric(e.target.value)} className={inp}>
          {surveyMetrics.map(k => <option key={k} value={k}>{instruments[k].label}</option>)}
        </select>

        <select value={grain} onChange={e => setGrain(e.target.value as Grain)} className={inp}>
          {grainsForMetric.map(g => <option key={g} value={g}>{t(`grains.${g}`)}</option>)}
        </select>

        <PoolMultiSelect
          pools={domainPools}
          value={poolIds}
          onChange={setPoolIds}
          allLabel={t('filter.allPools')}
          placeholder={t('filter.noPools')}
          countLabel={(n) => t('filter.poolCount', { count: n })}
        />

        <div className="flex-1" />

        {/* Nível: agregado ↔ respostas. O toggle só aparece para quem tem o grant —
            um botão desabilitado anunciaria a existência do dado a quem não o alcança. */}
        {podeVerRespostas && (
          <div className="inline-flex rounded-lg border border-border overflow-hidden bg-white">
            {(['aggregate', 'responses'] as const).map(l => (
              <button key={l} onClick={() => setLevel(l)}
                className={`px-4 py-1.5 text-xs font-medium transition-colors ${
                  nivel === l ? 'bg-primary text-white' : 'text-muted hover:text-dark hover:bg-surface-muted'
                }`}>
                {t(`level.${l}`)}
              </button>
            ))}
          </div>
        )}
      </div>

      {nivel === 'aggregate' ? (
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
                    {data.summary.value ?? '—'}
                  </div>
                  <div className="text-xs text-muted">
                    {data.instrument.label} · {t('nSignals', { count: data.summary.n })}
                  </div>
                </div>
                <p className="text-xs text-muted-light max-w-md ml-auto">{t('overlayNote')}</p>
              </div>
              <OverlayChart survey={data.series} sla={data.overlay?.sla ?? []} surveyLabel={data.instrument.label} />
              {/* Os DOIS níveis contam coisas diferentes, e a tela diz qual é qual em vez
                  de deixar o operador descobrir que os números não batem. */}
              <p className="text-2xs text-muted-light border-t border-border pt-2">
                {t('storeNote')}
              </p>
            </div>
          )}
        </div>
      ) : (
        <div className="flex-1 overflow-hidden">
          <AnaliseSurveysPage host={{ fromDt, toDt, poolIds, metric, grain: grain || '' }} />
        </div>
      )}
    </div>
  )
}
