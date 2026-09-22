/**
 * RoutingQualityLens — a lente de **qualidade do roteamento** da superfície A (ORQ-16).
 *
 * Fonte: `GET /reports/navigation/routing` (ORQ-14). Nenhum dado novo foi produzido
 * para esta tela — ela é a superfície de um sinal que até aqui só existia por `curl`
 * com token de serviço, e por isso não entrava em decisão de produto.
 *
 * ── O número é PROXY, e a tela tem de dizer isso ────────────────────────────
 *
 * Ninguém carimba *"o destino estava errado"*. O que se observa é a consequência: o
 * destino não concluiu e OUTRO pool atendeu em seguida. Há falso positivo legítimo (o
 * destino certo que descobre, atendendo, que o caso é de outra área) e falso negativo
 * (o destino errado que resolve assim mesmo). A coluna se chama **re-roteados**, nunca
 * "errados", e o rótulo de proxy fica no cabeçalho — não num tooltip que ninguém abre.
 * O `meta.sinal` do backend vai no `title` como PROVENIÊNCIA: quem lê a tela e quem lê
 * a API veem a mesma declaração, e se o backend parar de declarar, o `title` fica vazio
 * em vez de a tela afirmar sozinha.
 *
 * ── Três honestidades que a tela deve ao dado ───────────────────────────────
 *
 *   1. **Taxa ausente ≠ zero.** Sem ninguém atendido, o backend devolve `null`, e aqui
 *      isso é `—`. Renderizar `0%` diria "esta folha nunca erra" sobre uma folha que
 *      ninguém chegou a atender.
 *   2. **Amostra pequena é marcada, não escondida.** Uma folha com 1 contato e 1
 *      re-roteio dá 100% e encabeçaria qualquer ordenação por taxa — foi assim que a
 *      primeira leitura deste relatório mandou investigar a árvore errada. Abaixo de
 *      `AMOSTRA_MINIMA` a taxa vem apagada e com selo de `n`; a linha continua na
 *      tabela, porque omiti-la esconderia o único sinal de uma folha nova.
 *   3. **A barra não julga.** Largura proporcional à taxa, cor neutra: verde/vermelho
 *      seria veredicto, e veredicto é exatamente o que um proxy não autoriza.
 *
 * ── `proximos` é a coluna acionável ─────────────────────────────────────────
 *
 * Um destino que termina sempre no mesmo outro pool é uma **folha que falta na árvore**
 * — foi esse o defeito da ORQ-11. É por ela que se olha primeiro, e não pela taxa.
 *
 * ── O que a lente honra da barra, e por quê ─────────────────────────────────
 *
 * Só o período (`honors: 'period_only'`). O filtro de pool da barra significa *quem
 * ATENDEU*; aqui o pool é o ORQUESTRADOR, isto é, quem ROTEOU — duas perguntas
 * diferentes (D10 do `adr-journey-session-segment-model`). Aplicar um com a semântica
 * do outro seria a declaração mentindo, e o orquestrador já é a primeira coluna.
 */

import React, { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle } from 'lucide-react'
import { useAuth } from '@/auth/useAuth'
import { apiFetch } from '@/api/apiFetch'
import Spinner from '@/components/ui/Spinner'
import EmptyState from '@/components/ui/EmptyState'

/** Espelho de uma linha de `GET /reports/navigation/routing`. */
interface RoutingRow {
  orquestrador:    string
  destino:         string
  contatos:        number
  atendidos:       number
  re_roteados:     number
  /** `null` = NÃO MEDIDA (ninguém atendido), nunca 0. */
  taxa_re_roteio:  number | null
  sem_destino:     number
  sem_cadeia:      number
  com_renavegacao: number
  proximos:        string[]
}

interface RoutingMeta {
  contatos?:        number
  atendidos?:       number
  re_roteados?:     number
  taxa_re_roteio?:  number | null
  sem_destino?:     number
  sem_cadeia?:      number
  com_renavegacao?: number
  /** A declaração do backend de que o número é proxy. Exibida como proveniência. */
  sinal?:           string
}

interface Props { fromDt?: string; toDt?: string }

/**
 * Abaixo disto a taxa é apagada e recebe selo de amostra.
 *
 * Não é um corte de exibição (a linha fica) nem um limiar estatístico — é o menor
 * número em que a taxa ainda não é uma leitura de um contato só. O `min_sample=30` da
 * lente de deploy (Arc 6 Fase 2) é de outra ordem de grandeza porque lá o eixo é nota
 * média; aqui a pergunta é "houve re-roteio nesta folha", e esconder até 30 apagaria
 * quase toda a tabela de um tenant novo.
 */
const AMOSTRA_MINIMA = 5

const pct = (v: number) => `${(v * 100).toFixed(0)}%`

export default function RoutingQualityLens({ fromDt, toDt }: Props) {
  const { t } = useTranslation('contacts')
  const { tenantId } = useAuth()
  const [rows,    setRows]    = useState<RoutingRow[]>([])
  const [meta,    setMeta]    = useState<RoutingMeta>({})
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState<string | null>(null)

  useEffect(() => {
    if (!tenantId) return
    let vivo = true
    setLoading(true); setError(null)
    const p = new URLSearchParams({ tenant_id: tenantId })
    if (fromDt) p.set('from_dt', fromDt)
    if (toDt)   p.set('to_dt',   toDt)
    apiFetch(`/reports/navigation/routing?${p}`)
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then(body => {
        if (!vivo) return
        setRows((body?.data ?? []) as RoutingRow[])
        setMeta((body?.meta ?? {}) as RoutingMeta)
      })
      .catch(e => { if (vivo) { setError(String(e)); setRows([]); setMeta({}) } })
      .finally(() => { if (vivo) setLoading(false) })
    return () => { vivo = false }
  }, [tenantId, fromDt, toDt])

  if (loading) return <div className="p-6"><Spinner /></div>

  if (error) {
    return (
      <div className="p-4">
        <div className="flex items-center gap-2 text-sm text-red-text bg-red-light border border-red rounded-lg px-3 py-2">
          <AlertTriangle className="w-4 h-4 flex-shrink-0" />
          <span>{t('lens.routing.error')}</span>
          <span className="text-xs text-muted">{error}</span>
        </div>
      </div>
    )
  }

  if (rows.length === 0) {
    return (
      <div className="p-6">
        <EmptyState title={t('lens.routing.empty')} description={t('lens.routing.emptyHint')} />
      </div>
    )
  }

  const th = 'text-left text-xs font-medium text-muted px-3 py-2 whitespace-nowrap'
  const td = 'text-sm px-3 py-2 align-top'

  return (
    <div className="h-full overflow-auto p-4 flex flex-col gap-3">
      {/* Cabeçalho: o total e o RÓTULO do número, lado a lado. O rótulo não é
          decoração — é o que impede que um proxy seja lido como veredicto. */}
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <span className="text-sm text-dark">
          {t('lens.routing.summary', {
            contacts: meta.contatos ?? 0,
            attended: meta.atendidos ?? 0,
            rerouted: meta.re_roteados ?? 0,
          })}
        </span>
        <span className="text-sm font-semibold text-dark">
          {meta.taxa_re_roteio == null
            ? <span className="text-muted" title={t('lens.routing.notMeasuredHint')}>{t('lens.routing.notMeasured')}</span>
            : pct(meta.taxa_re_roteio)}
        </span>
        <span
          className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-surface-muted text-muted"
          title={meta.sinal || undefined}
        >
          {t('lens.routing.proxy')}
        </span>
      </div>

      <p className="text-xs text-muted max-w-3xl">{t('lens.routing.proxyHint')}</p>

      {/* Defeito de DADO, não de roteamento: sessão com evento de navegação e sem
          cadeia de segmentos. Tem contador para não sumir calado. */}
      {(meta.sem_cadeia ?? 0) > 0 && (
        <div className="flex items-center gap-2 text-xs text-warning">
          <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0" />
          <span>{t('lens.routing.dataDefect', { count: meta.sem_cadeia ?? 0 })}</span>
        </div>
      )}

      <table className="min-w-full bg-white border border-border rounded-lg overflow-hidden">
        <thead className="bg-surface-muted border-b border-border">
          <tr>
            <th className={th}>{t('lens.routing.col.orchestrator')}</th>
            <th className={th}>{t('lens.routing.col.destination')}</th>
            <th className={`${th} text-right`}>{t('lens.routing.col.contacts')}</th>
            <th className={`${th} text-right`} title={t('lens.routing.hint.noDestination')}>
              {t('lens.routing.col.noDestination')}
            </th>
            <th className={`${th} text-right`}>{t('lens.routing.col.attended')}</th>
            <th className={`${th} text-right`}>{t('lens.routing.col.rerouted')}</th>
            <th className={th} title={t('lens.routing.hint.rate')}>{t('lens.routing.col.rate')}</th>
            <th className={th} title={t('lens.routing.hint.next')}>{t('lens.routing.col.next')}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(r => {
            const poucos = r.atendidos > 0 && r.atendidos < AMOSTRA_MINIMA
            return (
              <tr key={`${r.orquestrador}::${r.destino}`} className="border-b border-border last:border-0">
                <td className={`${td} text-muted whitespace-nowrap`}>{r.orquestrador}</td>
                <td className={`${td} font-medium text-dark whitespace-nowrap`}>{r.destino}</td>
                <td className={`${td} text-right tabular-nums`}>{r.contatos}</td>
                <td className={`${td} text-right tabular-nums ${r.sem_destino > 0 ? 'text-warning' : 'text-muted'}`}>
                  {r.sem_destino}
                </td>
                <td className={`${td} text-right tabular-nums`}>{r.atendidos}</td>
                <td className={`${td} text-right tabular-nums`}>{r.re_roteados}</td>
                <td className={td}>
                  {r.taxa_re_roteio == null ? (
                    <span className="text-muted" title={t('lens.routing.notMeasuredHint')}>
                      {t('lens.routing.notMeasured')}
                    </span>
                  ) : (
                    <div className="flex items-center gap-2">
                      {/* Barra NEUTRA: proporção, nunca julgamento. */}
                      <div className="w-16 h-1.5 rounded bg-surface-muted overflow-hidden flex-shrink-0">
                        <div
                          className="h-full bg-secondary"
                          style={{ width: `${Math.min(100, r.taxa_re_roteio * 100)}%` }}
                        />
                      </div>
                      <span className={`tabular-nums ${poucos ? 'text-muted' : 'text-dark'}`}>
                        {pct(r.taxa_re_roteio)}
                      </span>
                      {poucos && (
                        <span
                          className="text-[10px] px-1 py-0.5 rounded bg-surface-muted text-muted"
                          title={t('lens.routing.hint.smallSample', { min: AMOSTRA_MINIMA })}
                        >
                          {t('lens.routing.smallSample', { count: r.atendidos })}
                        </span>
                      )}
                    </div>
                  )}
                </td>
                <td className={`${td} text-muted`}>
                  {r.proximos.length === 0
                    ? <span className="text-border-strong">—</span>
                    : r.proximos.join(', ')}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
