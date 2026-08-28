/**
 * WrapupSummaryPage — a lente de **disposição** da superfície A.
 *
 * ⚠️ **Não é mais uma rota.** `/analise/wrapup` foi absorvido na F2 do
 * `adr-relatorios-duas-superficies-e-lentes.md` (D7: "endereço morre, componente é
 * re-hospedado"), e o endereço antigo redireciona para
 * `/analise/sessions?lens=disposition`. A regra que o matou é a de sobrevivência:
 * a unidade de análise aqui é o CONTATO, logo isto é lente, não endereço.
 *
 * O seletor de período PRÓPRIO saiu junto. Ele agora recebe `fromDt`/`toDt` da barra
 * de filtro da superfície — duas janelas de tempo na mesma tela seriam dois recortes
 * concorrentes, e a de dentro venceria em silêncio.
 *
 * O que ele NÃO honra, e a tela diz: o resto da barra. O agregado é sobre pools
 * INTERNOS (`-int`), onde o filtro de pool do operador não se aplica. É por isso que
 * a lente se declara `honors: 'period_only'` no contrato — ver `lens-contract.ts`.
 *
 * HISTÓRICO das pendências de wrap-up (I5 / ADR § D7b, fatia 2).
 *
 * Contraparte retrospectiva do Monitor › Pendências. A divisão não é arbitrária:
 *   · Monitor  = quem está devendo AGORA (ledger Redis, janela de ~25 h)
 *   · Analytics = como terminaram no período (segments, permanente)
 * A superfície viva não pode responder "quantos venceram este mês" porque o ledger
 * expira com o prazo do delegate; esta não pode responder "quem está devendo agora"
 * porque o segmento só existe depois de fechado.
 *
 * Fonte: GET /reports/wrapup-summary (analytics-api) — agregado sobre `segments`
 * com o trio de close_reason, escopado a pools `-int`. Nenhum dado novo foi
 * produzido para esta tela: a D7b previu que ela era só a lente.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import { apiFetch } from '@/api/apiFetch'
import Spinner from '@/components/ui/Spinner'
import EmptyState from '@/components/ui/EmptyState'

type GroupAxis = 'agent' | 'pool'

interface Row {
  group_key:         string
  pool_id:           string
  user_id:           string
  total:             number
  submitted:         number
  expired:           number
  supervisor_closed: number
  avg_fill_ms:       number | null
  last_seen:         string | null
}

interface Totals {
  total?:             number
  submitted?:         number
  expired?:           number
  supervisor_closed?: number
  /** (expired + supervisor_closed) / total — a "% sem disposição" da D4. */
  unfilled_rate?:     number | null
}

function fmtDuration(ms: number | null): string {
  if (ms == null || !Number.isFinite(ms)) return '—'
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  const r = s % 60
  return r ? `${m}min ${r}s` : `${m}min`
}

function fmtPct(v: number | null | undefined): string {
  if (v == null) return '—'
  return `${(v * 100).toFixed(1)}%`
}

function KpiCard({ label, value, hint, tone }: {
  label: string; value: string; hint?: string; tone?: 'warn' | 'plain'
}) {
  return (
    <div className="bg-white border border-border rounded-xl px-4 py-3 min-w-[140px]">
      <div className="text-2xs uppercase tracking-wide text-muted-light">{label}</div>
      <div className={`text-xl font-semibold mt-0.5 ${tone === 'warn' ? 'text-red-text' : 'text-dark'}`}>
        {value}
      </div>
      {hint && <div className="text-2xs text-muted-light mt-0.5">{hint}</div>}
    </div>
  )
}

interface Props {
  /** Intervalo da barra de filtro da superfície A. Obrigatórios: sem eles o
   *  componente teria de inventar um período, e um período inventado ao lado de um
   *  escolhido é o defeito que esta absorção existe para fechar. */
  fromDt: string
  toDt:   string
}

export default function WrapupSummaryPage({ fromDt, toDt }: Props) {
  const { t } = useTranslation('workItems')
  const { session, tenantId, perms } = useAuth()

  const [rows,    setRows]    = useState<Row[]>([])
  const [totals,  setTotals]  = useState<Totals>({})
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState('')
  const [axis,    setAxis]    = useState<GroupAxis>('agent')

  const canView = perms.can('contacts', 'visualizar')

  const load = useCallback(async () => {
    if (!tenantId) return
    setLoading(true)
    const p = new URLSearchParams({
      tenant_id: tenantId,
      group_by:  axis,
      from_dt:   fromDt,
      to_dt:     toDt,
    })
    try {
      const res = await apiFetch(`/reports/wrapup-summary?${p}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json() as { data?: Row[]; totals?: Totals; error?: string }
      // `error: data_unavailable` é degradação do backend — NÃO é lista vazia. Sem
      // esta distinção a tela mostraria "nenhum wrap-up no período" para uma query
      // que falhou, que é o modo de falha mais caro: um zero que parece resultado.
      if (data.error) { setError(t('history.errorBackend')); setRows([]); setTotals({}); return }
      setRows(data.data ?? [])
      setTotals(data.totals ?? {})
      setError('')
    } catch (e) {
      setError(t('errors.loadFailed'))
      console.error(e)
    } finally {
      setLoading(false)
    }
  }, [tenantId, axis, fromDt, toDt, t])

  useEffect(() => { if (canView) void load(); else setLoading(false) }, [canView, load])

  const maxTotal = useMemo(
    () => rows.reduce((m, r) => Math.max(m, Number(r.total) || 0), 0),
    [rows],
  )

  if (!session || !canView) {
    return (
      <div className="flex items-center justify-center h-full">
        <p className="text-muted">{t('restricted')}</p>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full bg-surface-muted">
      <div className="bg-white flex-shrink-0 px-6 pt-4 pb-3 border-b border-border">
        <h1 className="text-lg font-semibold text-dark">{t('history.title')}</h1>
        <p className="text-sm text-muted mt-0.5">{t('history.info')}</p>

        <div className="flex items-center gap-3 mt-3 flex-wrap">
          <div className="flex gap-1 rounded-lg border border-border-strong overflow-hidden">
            {(['agent', 'pool'] as GroupAxis[]).map(a => (
              <button key={a} type="button" onClick={() => setAxis(a)}
                className={`px-3 py-1.5 text-xs transition-colors ${axis === a
                  ? 'bg-primary text-white' : 'bg-white text-muted hover:text-dark'}`}>
                {t(`axis.${a}`)}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {loading && <div className="flex justify-center py-8"><Spinner /></div>}
        {error && <p className="text-sm text-red-text">{error}</p>}

        {!loading && !error && (
          <div className="flex gap-3 flex-wrap">
            <KpiCard label={t('history.kpi.total')}     value={String(totals.total ?? 0)} />
            <KpiCard label={t('history.kpi.submitted')} value={String(totals.submitted ?? 0)} />
            <KpiCard label={t('history.kpi.expired')}   value={String(totals.expired ?? 0)} />
            <KpiCard label={t('history.kpi.supervisorClosed')} value={String(totals.supervisor_closed ?? 0)} />
            <KpiCard
              label={t('history.kpi.unfilled')}
              value={fmtPct(totals.unfilled_rate)}
              hint={t('history.kpi.unfilledHint')}
              tone={(totals.unfilled_rate ?? 0) > 0 ? 'warn' : 'plain'}
            />
          </div>
        )}

        {!loading && !error && rows.length === 0 && (
          <EmptyState icon="📋" title={t('history.empty')} description={t('history.emptyHint')} />
        )}

        {/*
            TABELA, não duas grids.

            Era um `<div grid-cols-[1.4fr_auto×5]>` para o cabeçalho e OUTRO, igual, por
            linha de dado. Grids irmãs não compartilham trilha: `auto` dimensiona pelo
            conteúdo DAQUELA grid, e o conteúdo é de naturezas diferentes — palavra
            ("Submetidos") no cabeçalho, um dígito ("9") no dado. Some o `1.4fr`
            absorvendo sobras diferentes em cada uma e o resultado é o da tela: títulos
            espalhados, números espremidos à direita, nenhum sob o seu título.

            Largura fixa em px consertaria a foto e não a causa — o primeiro rótulo
            traduzido mais longo reabre o defeito. A tabela alinha por construção, que é
            o que a tela pede, e ainda é o padrão dominante do platform-ui.
            ⚠️ Mesmo par de grids vive em `work-items/WorkItemsPage.tsx` (:88/:352) e
            `schedules/SchedulesMonitorPage.tsx` (:101/:108) — mesmo defeito, ainda de pé.
        */}
        {!loading && !error && rows.length > 0 && (
          <div className="bg-white border border-border rounded-xl overflow-hidden">
            <table className="w-full table-auto border-collapse">
              <thead>
                <tr className="text-2xs uppercase tracking-wide text-muted-light border-b border-border">
                  <th scope="col" className="text-left font-normal px-4 py-2 w-full">{t(`axis.${axis}`)}</th>
                  <th scope="col" className="text-right font-normal px-3 py-2 whitespace-nowrap">{t('history.col.total')}</th>
                  <th scope="col" className="text-right font-normal px-3 py-2 whitespace-nowrap">{t('history.col.submitted')}</th>
                  <th scope="col" className="text-right font-normal px-3 py-2 whitespace-nowrap">{t('history.col.expired')}</th>
                  <th scope="col" className="text-right font-normal px-3 py-2 whitespace-nowrap">{t('history.col.supervisorClosed')}</th>
                  <th scope="col" className="text-right font-normal px-3 py-2 whitespace-nowrap">{t('history.col.avgFill')}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(r => {
                  const unfilled = (Number(r.expired) || 0) + (Number(r.supervisor_closed) || 0)
                  return (
                    <tr key={r.group_key || '—'}
                      className="text-xs border-b border-border last:border-b-0">
                      {/* `w-full` no cabeçalho + `max-w-0` aqui: a coluna do nome absorve a
                          sobra e o `truncate` tem de quem truncar (numa <td> sem largura
                          resolvida o texto empurra a tabela em vez de cortar). */}
                      <td className="px-4 py-2 align-middle max-w-0">
                        <div className="text-dark font-medium truncate" title={r.group_key}>
                          {r.group_key || t('history.unknownAgent')}
                        </div>
                        {/* Barra proporcional: comparar agentes sem ler número a número. */}
                        <div className="h-1 bg-surface-alt rounded mt-1 overflow-hidden">
                          <div className="h-full bg-primary"
                            style={{ width: maxTotal ? `${(Number(r.total) / maxTotal) * 100}%` : '0%' }} />
                        </div>
                      </td>
                      <td className="px-3 py-2 text-right align-middle text-dark tabular-nums">{r.total}</td>
                      <td className="px-3 py-2 text-right align-middle text-muted tabular-nums">{r.submitted}</td>
                      <td className={`px-3 py-2 text-right align-middle tabular-nums ${Number(r.expired) > 0 ? 'text-red-text font-medium' : 'text-muted'}`}>
                        {r.expired}
                      </td>
                      <td className={`px-3 py-2 text-right align-middle tabular-nums ${Number(r.supervisor_closed) > 0 ? 'text-warning-text font-medium' : 'text-muted'}`}>
                        {r.supervisor_closed}
                      </td>
                      <td className="px-3 py-2 text-right align-middle text-muted tabular-nums whitespace-nowrap"
                        title={t('history.col.avgFillHint')}>
                        {fmtDuration(r.avg_fill_ms)}
                        {unfilled > 0 && <span className="sr-only"> ({unfilled})</span>}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}

        {!loading && !error && rows.length > 0 && (
          <p className="text-2xs text-muted-light">{t('history.avgNote')}</p>
        )}
      </div>
    </div>
  )
}
