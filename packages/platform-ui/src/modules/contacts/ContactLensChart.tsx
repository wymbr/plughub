/**
 * ContactLensChart — a série da superfície A (F2 do
 * `adr-relatorios-duas-superficies-e-lentes.md`).
 *
 * DIRIGIDO PELA DECLARAÇÃO, NÃO POR `if` DE LENTE
 * ------------------------------------------------
 * O componente não sabe o nome de nenhuma lente. Ele lê `lens.metrics` do contrato
 * (`lens-contract.ts`) e desenha uma linha por métrica, com o formato que a métrica
 * declara. Acrescentar uma lente de série à superfície A é acrescentar uma entrada
 * na declaração — que é a previsão da D5, e a razão de a F1 vir antes desta fase.
 *
 * POR QUE ELE NÃO É O `TimeseriesChart`
 * --------------------------------------
 * Aquele componente busca sozinho, com os PRÓPRIOS seletores de período e um único
 * recorte de pool. Aqui os filtros são da SUPERFÍCIE (doze campos, uma barra só) e o
 * período também. Reusá-lo colocaria duas janelas de tempo na mesma tela — e a
 * segunda venceria em silêncio.
 *
 * `sample` É DESENHADO, NÃO SÓ TRANSPORTADO
 * ------------------------------------------
 * Bucket sem amostra fica com um FURO na linha (`null`), nunca com zero: uma média
 * ausente desenhada como zero é uma queda que não existiu. É a razão de o backend
 * mandar `sample` em toda métrica.
 *
 * AS AUSÊNCIAS DO `meta` SÃO EXIBIDAS
 * ------------------------------------
 * `without_duration`, `without_segments` e `clamped_segments` viram uma linha de
 * rodapé. Medido na instalação em 2026-08-28: a lente de duração exclui **510 de
 * 881** contatos (58%). Sem a linha, o gráfico apresentaria a duração "típica" de
 * 42% da população como se fosse a de todos.
 */
import React, { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis,
  CartesianGrid, Tooltip, Legend,
} from 'recharts'
import { apiFetch } from '@/api/apiFetch'
import Spinner from '@/components/ui/Spinner'
import { formatCount, formatDurationMs } from '@/components/TimeseriesChart/formatters'
import type { ReportLens, MetricFormat } from '@/modules/analise/lens-contract'
import type { ContactFilters } from './types'

// Cores dos tokens do design system (nunca hex inline — ver CLAUDE.md § Frontend).
const SERIES_COLORS = ['#1B4F8A', '#2D9CDB', '#00B4D8', '#059669']

interface Bucket {
  bucket: string
  values: Record<string, number | null>
  sample: number
}

interface SeriesMeta {
  metric:            string
  interval_minutes:  number
  from:              string
  to:                string
  total?:            number
  /** Ausências NOMEADAS — ver o cabeçalho. Ausentes na resposta = não se aplicam. */
  without_duration?:  number
  without_segments?:  number
  clamped_segments?:  number
  /** T3 — token. `series_starts_at` é a data em que o PRODUTOR passou a existir: sem
   *  ela, uma série nova é lida como "não gastamos nada".
   *
   *  ⚠️ Não é o primeiro bucket da série, e a diferença importa: o bucket é do
   *  CONTATO (`opened_at`), e um contato aberto ontem que consumiu hoje aparece
   *  ONTEM. A primeira redação da nota dizia *"a série começa em X"* e se contradizia
   *  na tela — havia ponto antes de X. O que a data significa é *"nada foi
   *  REGISTRADO antes de X"*. */
  without_tokens?:          number
  series_starts_at?:        string
  unattributed_events?:     number
  /** `true` = eventos sem chave ainda estão CHEGANDO (defeito em curso).
   *  `false` com contagem > 0 = história que não se conserta. A contagem sozinha não
   *  distingue os dois, e no dia da época ela acusa defeito onde não há. */
  unattributed_in_flight?:  boolean
}

interface BreakdownRow {
  pool_id:           string
  account_config_id: string
  model_id:          string
  model_profile:     string
  source:            string
  tokens_in:         number
  tokens_out:        number
  sessions:          number
  events:            number
}

function fmt(value: number, format: MetricFormat): string {
  if (format === 'time')  return formatDurationMs(value)
  if (format === 'pct')   return `${(value * 100).toFixed(1)}%`
  if (format === 'score') return value.toFixed(2)
  return formatCount(value)
}

/**
 * Bucket em minutos a partir da janela. Dia inteiro acima de 3 dias, hora abaixo.
 *
 * É derivado, e não um seletor: um controle de granularidade ao lado de um seletor
 * de período dá ao operador duas formas de dizer a mesma coisa, e elas discordam.
 */
function intervalFor(fromDt: string, toDt: string): number {
  const ms = new Date(toDt).getTime() - new Date(fromDt).getTime()
  if (!Number.isFinite(ms) || ms <= 0) return 1440
  const days = ms / 86_400_000
  if (days <= 2)  return 60
  if (days <= 60) return 1440
  return 10080
}

/**
 * O bucket vem do backend como `2026-08-21T00:00:00` — **sem `Z`, e em UTC**.
 * `new Date(...)` sobre essa string parseia como hora LOCAL, então num fuso a oeste o
 * rótulo cai no dia anterior ao bucket que o ClickHouse agrupou. O eixo passaria a
 * nomear dias que não são os que foram contados.
 */
function bucketDate(iso: string): Date {
  return new Date(/[Zz]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`)
}

function bucketLabel(iso: string, intervalMin: number): string {
  const d = bucketDate(iso)
  return intervalMin >= 1440
    ? d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' })
    : d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', timeZone: 'UTC' })
}

/**
 * Completa o eixo com os buckets que a resposta NÃO trouxe.
 *
 * O backend só devolve bucket com linha — o `GROUP BY` não inventa dia vazio. Sem
 * este preenchimento o eixo fica CATEGÓRICO e some com o intervalo: medido em
 * 2026-08-28, 21 e 24 de agosto ficavam adjacentes como se fossem dias seguidos, e
 * os dois dias sem contato desapareciam do gráfico em vez de aparecerem como queda.
 *
 * O bucket ausente entra com `sample: 0` e valor `null` — que o `connectNulls={false}`
 * desenha como BURACO. Entrar com zero seria pior que sumir: afirmaria uma medição
 * de valor zero onde não houve medição nenhuma.
 */
function fillAxis(rows: { bucket: string }[], from: string, to: string, intervalMin: number) {
  const stepMs = intervalMin * 60_000
  const known = new Map(rows.map(r => [bucketDate(r.bucket).getTime(), r]))
  const start = bucketDate(from).getTime()
  const end   = bucketDate(to).getTime()
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return rows
  // Teto de segurança: janela larga com bucket curto geraria milhares de pontos, e um
  // eixo ilegível é outra forma de não responder. Acima do teto, devolve o que veio.
  if ((end - start) / stepMs > 400) return rows

  const first = Math.floor(start / stepMs) * stepMs
  const out: { bucket: string }[] = []
  for (let t = first; t <= end; t += stepMs) {
    out.push(known.get(t) ?? { bucket: new Date(t).toISOString().replace(/\.\d+Z$/, ''), sample: 0 } as { bucket: string })
  }
  return out
}

/** Só os filtros que a série aceita — os demais são de listagem (paginação, escopo). */
function seriesParams(tenantId: string, f: ContactFilters, metric: string): URLSearchParams {
  const p = new URLSearchParams({
    tenant_id: tenantId,
    metric,
    interval: String(intervalFor(f.fromDt, f.toDt)),
    from_dt: f.fromDt,
    to_dt: f.toDt,
  })
  const opt: [string, string | undefined][] = [
    ['channel', f.channel], ['outcome', f.outcome],
    ['pool_id', f.poolId], ['entry_pool_id', f.entryPoolId],
    ['direction', f.direction], ['status', f.status],
    ['agent_id', f.agentId],
    ['insight_category', f.insightCategory], ['insight_tags', f.insightTags],
    ['session_id', f.sessionIdSearch],
  ]
  for (const [k, v] of opt) if (v) p.set(k, v)
  return p
}

/**
 * A tabela que a SÉRIE não substitui (T3).
 *
 * A série diz *quanto* e *quando*. Esta diz **quem** gastou, **de qual conta** e
 * **com qual modelo** — as três dimensões que a revisão pediu nominalmente. Nenhuma
 * é derivável das outras, e por isso ela não é um `breakdown_by` do gráfico: são
 * linhas, não buckets.
 *
 * `model_profile` × `model_id` fica lado a lado de propósito: é o par que diagnostica
 * fallback (*"pedi `balanced` e veio outro"*). Só um dos dois não diz nada.
 *
 * Pool vazio aparece como aviso, e não como travessão: é sintoma de `segment_id` não
 * propagado, o mesmo defeito que o `unattributed_events` conta do outro lado.
 */
function TokenBreakdown({ tenantId, filters }: { tenantId: string; filters: ContactFilters }) {
  const { t } = useTranslation('contacts')
  const [rows, setRows] = useState<BreakdownRow[]>([])
  const [noPool, setNoPool] = useState(0)
  const [state, setState] = useState<'loading' | 'ok' | 'error'>('loading')

  const qs = useMemo(() => {
    const p = seriesParams(tenantId, filters, 'tokens')
    p.delete('metric'); p.delete('interval')
    return p.toString()
  }, [tenantId, filters])

  useEffect(() => {
    let cancelled = false
    setState('loading')
    apiFetch(`/reports/contacts/tokens/breakdown?${qs}`)
      .then(r => r.json())
      .then(d => {
        if (cancelled) return
        if (d.error) { setState('error'); return }
        setRows(d.data ?? [])
        setNoPool(d.meta?.rows_without_pool ?? 0)
        setState('ok')
      })
      .catch(() => { if (!cancelled) setState('error') })
    return () => { cancelled = true }
  }, [qs])

  if (state === 'loading') return null
  if (state === 'error') {
    return <p className="text-xs text-red-text px-1">{t('lens.errorBackend')}</p>
  }
  // Sem linha ≠ zero consumo: a tabela some e a nota da época, que está acima, é a
  // explicação. Uma tabela vazia com cabeçalho sugeriria que se mediu e não se achou.
  if (rows.length === 0) return null

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-left text-muted border-b border-border">
            <th className="py-1.5 pr-3 font-medium">{t('lens.breakdown.pool')}</th>
            <th className="py-1.5 pr-3 font-medium">{t('lens.breakdown.account')}</th>
            <th className="py-1.5 pr-3 font-medium">{t('lens.breakdown.model')}</th>
            <th className="py-1.5 pr-3 font-medium">{t('lens.breakdown.profile')}</th>
            <th className="py-1.5 pr-3 font-medium">{t('lens.breakdown.source')}</th>
            <th className="py-1.5 pr-3 font-medium text-right">{t('lens.metric.tokens_in')}</th>
            <th className="py-1.5 pr-3 font-medium text-right">{t('lens.metric.tokens_out')}</th>
            <th className="py-1.5 font-medium text-right">{t('lens.breakdown.contacts')}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-b border-border-light last:border-0">
              <td className="py-1.5 pr-3 font-mono">
                {r.pool_id || <span className="text-warning">{t('lens.breakdown.noPool')}</span>}
              </td>
              <td className="py-1.5 pr-3 font-mono">{r.account_config_id || '—'}</td>
              <td className="py-1.5 pr-3 font-mono">{r.model_id || '—'}</td>
              <td className="py-1.5 pr-3">{r.model_profile || '—'}</td>
              <td className="py-1.5 pr-3">{r.source || '—'}</td>
              <td className="py-1.5 pr-3 text-right tabular-nums">{formatCount(r.tokens_in)}</td>
              <td className="py-1.5 pr-3 text-right tabular-nums">{formatCount(r.tokens_out)}</td>
              <td className="py-1.5 text-right tabular-nums">{formatCount(r.sessions)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {noPool > 0 && (
        <p className="text-xs text-warning pt-1.5">
          {t('lens.breakdown.noPoolNote', { count: noPool })}
        </p>
      )}
    </div>
  )
}

export function ContactLensChart({ tenantId, filters, lens }: {
  tenantId: string
  filters:  ContactFilters
  lens:     ReportLens
}) {
  const { t } = useTranslation('contacts')
  const [buckets, setBuckets] = useState<Bucket[]>([])
  const [meta,    setMeta]    = useState<SeriesMeta | null>(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState('')

  const qs = useMemo(
    () => seriesParams(tenantId, filters, lens.id).toString(),
    [tenantId, filters, lens.id],
  )

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    apiFetch(`/reports/contacts/series?${qs}`)
      .then(r => r.json())
      .then(d => {
        if (cancelled) return
        // `error` do backend é DEGRADAÇÃO, não série vazia. Sem esta distinção a tela
        // diria "nenhum contato no período" para uma query que falhou — um zero que
        // parece resultado, que é o modo de falha mais caro desta superfície.
        if (d.error) { setError(d.error); setBuckets([]); setMeta(null); return }
        setBuckets(d.buckets ?? [])
        setMeta(d.meta ?? null)
        setError('')
      })
      .catch(() => { if (!cancelled) setError('fetch_failed') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [qs])

  const rows = useMemo(() => {
    const iv = meta?.interval_minutes ?? 1440
    const filled = meta
      ? fillAxis(buckets as { bucket: string }[], meta.from, meta.to, iv)
      : (buckets as { bucket: string }[])
    return (filled as Bucket[]).map(b => ({
      bucket: b.bucket,
      label:  bucketLabel(b.bucket, iv),
      sample: b.sample ?? 0,
      // Bucket sem amostra vira FURO (`null`), nunca zero — ver o cabeçalho.
      ...Object.fromEntries(lens.metrics.map(m => [
        m.key, (b.sample ?? 0) > 0 ? (b.values?.[m.key] ?? null) : null,
      ])),
    }))
  }, [buckets, meta, lens.metrics])

  if (loading) return <div className="flex items-center justify-center h-full"><Spinner /></div>

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-2 text-sm">
        <span className="text-3xl" aria-hidden="true">⚠️</span>
        <span className="text-dark font-medium">{t('lens.errorBackend')}</span>
        <span className="text-xs text-muted-light font-mono">{error}</span>
      </div>
    )
  }

  // Vazio ≠ zero: nenhum bucket com amostra é AUSÊNCIA DE DADO, e é dito assim.
  //
  // ⚠️ Na lente de token o estado vazio tem uma SEGUNDA causa, e ela precisa de texto
  // próprio: a série começa na data em que o produtor passou a existir, e **não há
  // backfill possível** (o `metadata` era descartado no ingest; o `segment_id` nunca
  // viajou). Sem dizer isso, uma série nova é lida como "não gastamos nada" — que é a
  // leitura errada e a confortável.
  if (!rows.some(r => r.sample > 0)) {
    const epoch = meta?.series_starts_at
    return (
      <div className="flex flex-col items-center justify-center h-full gap-2 text-sm">
        <span className="text-3xl" aria-hidden="true">📭</span>
        <span className="text-dark font-medium">
          {epoch ? t('lens.noTokenSample') : t('lens.noSample')}
        </span>
        <span className="text-xs text-muted-light max-w-md text-center">
          {epoch ? t('lens.epochHint', { date: epoch }) : t('lens.noSampleHint')}
        </span>
        {!!meta?.without_tokens && (
          <span className="text-xs text-muted-light">
            {t('lens.note.withoutTokens', { count: meta.without_tokens })}
          </span>
        )}
      </div>
    )
  }

  const notes: string[] = []
  if (meta?.without_duration)  notes.push(t('lens.note.withoutDuration', { count: meta.without_duration }))
  if (meta?.without_segments)  notes.push(t('lens.note.withoutSegments', { count: meta.without_segments }))
  if (meta?.clamped_segments)  notes.push(t('lens.note.clampedSegments', { count: meta.clamped_segments }))
  if (meta?.without_tokens)    notes.push(t('lens.note.withoutTokens', { count: meta.without_tokens }))
  if (meta?.series_starts_at)  notes.push(t('lens.note.seriesStartsAt', { date: meta.series_starts_at }))
  // Evento sem chave de atribuição: só é DEFEITO se ainda estiver chegando. A
  // contagem sozinha acusa história como defeito no dia da época — ver o backend.
  if (meta?.unattributed_events) {
    notes.push(meta.unattributed_in_flight
      ? t('lens.note.unattributedInFlight', { count: meta.unattributed_events })
      : t('lens.note.unattributedHistory',  { count: meta.unattributed_events }))
  }

  return (
    <div className="flex flex-col h-full p-4 gap-2">
      <div className="flex items-baseline gap-3 flex-shrink-0">
        <h2 className="text-sm font-semibold text-dark">{t(`lens.${lens.id}.title`)}</h2>
        {meta?.total != null && (
          <span className="text-xs text-muted">{t('lens.sampleTotal', { count: meta.total })}</span>
        )}
      </div>

      <div className="flex-1 min-h-0">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 8, right: 16, bottom: 4, left: 4 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
            <XAxis dataKey="label" tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }}
              tickFormatter={v => fmt(Number(v), lens.metrics[0]?.format ?? 'count')} />
            <Tooltip
              formatter={(v: unknown, name: string) => {
                const m = lens.metrics.find(x => x.key === name)
                return [fmt(Number(v), m?.format ?? 'count'), t(`lens.metric.${name}`)]
              }}
              labelFormatter={(l: string, payload) => {
                const n = payload?.[0]?.payload?.sample
                return n == null ? l : `${l} · ${t('lens.sampleTotal', { count: n })}`
              }}
            />
            <Legend formatter={(name: string) => t(`lens.metric.${name}`)} />
            {lens.metrics.map((m, i) => (
              <Line key={m.key} type="linear" dataKey={m.key}
                stroke={SERIES_COLORS[i % SERIES_COLORS.length]}
                strokeWidth={2}
                // `r: 3`, não 2: com `connectNulls={false}`, um bucket cercado de
                // buracos não tem segmento de reta — o ponto é a ÚNICA marca dele.
                // Medido: com `r: 2` o dia 21/08 (isolado por dois dias sem contato)
                // não aparecia. Medição existente e invisível é ausência funcional.
                dot={{ r: 3 }}
                // Animação DESLIGADA, e não por gosto. O recharts anima a linha por
                // `stroke-dasharray` e **não desenha os pontos enquanto anima** —
                // medido: com ela ligada, a série ficava sem nenhum `<circle>` no
                // DOM e o bucket isolado (21/08, 47 contatos) não aparecia em tela
                // alguma. Um dado medido que não desenha é ausência funcional, e
                // aqui a causa era decoração.
                isAnimationActive={false}
                // `connectNulls={false}`: o furo do bucket sem amostra tem de
                // APARECER. Conectar por cima desenharia uma reta entre duas
                // medições distantes como se houvesse dado no meio.
                connectNulls={false} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>

      {notes.length > 0 && (
        <p className="text-xs text-warning flex-shrink-0">{notes.join(' · ')}</p>
      )}

      {/* A metade da pergunta que o gráfico não responde — só na lente de token. */}
      {lens.id === 'tokens' && (
        <div className="flex-shrink-0 max-h-52 overflow-y-auto border-t border-border pt-2">
          <TokenBreakdown tenantId={tenantId} filters={filters} />
        </div>
      )}
    </div>
  )
}

export default ContactLensChart
