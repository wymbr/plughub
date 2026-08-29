/**
 * ResourcesPage — /analise/resources · **Superfície B · Recursos** (F3 do
 * `adr-relatorios-duas-superficies-e-lentes.md`).
 *
 * A superfície da OFERTA, par da Superfície A (`/analise/sessions`, a demanda).
 * Uma barra de filtro, um seletor de modo, uma faixa de lentes.
 *
 * ── D6: a mesa é MODO, não página ────────────────────────────────────────────
 * Comparar difere de evoluir em UMA dimensão — uma série por entidade selecionada ×
 * uma série para a população filtrada. Filtro, lente e bucket são os mesmos. Por isso
 * `/analise/agents` deixa de ser endereço: ele vira o modo **comparar** daqui.
 *
 *   evoluir  → os quatro painéis por pool (volume · fila · capacidade · SLA), que
 *              eram as sub-abas de `/analise/pools`
 *   comparar → a mesa (`AgentsBenchPage`), com a lista de entidades e as dez lentes
 *              servidas por `/reports/agents/compare`
 *
 * ── Quem é dono de quê na URL ────────────────────────────────────────────────
 * Esta página é dona de `from`, `to`, `pool`, `channel`, `mode` e `lens` do modo
 * evoluir. A mesa é dona de `lens` (o dela), `sel`, `view` e `deploy`. A partição é
 * explícita porque o efeito de sincronia da mesa substituía a query inteira —
 * hospedada sem essa mudança, ela apagaria os filtros desta barra a cada render, e o
 * sintoma (a barra voltando ao default sozinha) não se liga ao componente de dentro.
 *
 * ⚠️ **A partição só vale se os NOMES forem disjuntos**, e a primeira versão disto
 * falhou nisso: os dois escreviam `mode` — aqui `evolve|compare`, na mesa
 * `daily|epoch` do toggle de deploy. Trocar de lente no modo comparar apagava o
 * `mode=compare`, e o reload caía no modo evoluir. Medido no browser, não deduzido: a
 * URL saía `?from=…&to=…&lens=sessions_aht`, sem o `mode`. O parâmetro da mesa passou
 * a ser `deploy`, e o redirect de `/analise/agents` renomeia o legado.
 *
 * ⚠️ **`lens` é compartilhado entre os dois modos, e isso é deliberado**: são
 * vocabulários disjuntos (`pool_*` aqui, as dez da mesa lá), e cada lado ignora o que
 * não reconhece caindo no próprio default. Um segundo parâmetro por modo daria dois
 * endereços para a mesma tela, que é o defeito que a lente na URL já fechou na
 * Superfície A.
 */
import React, { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'
import { useAuth } from '@/auth/useAuth'
import AnalisePoolsPage from './AnalisePoolsPage'
import AgentsBenchPage from './AgentsBenchPage'
import {
  RESOURCE_PANEL_LENSES, isResourcePanelLens,
  type ResourcePanelLensId,
} from './lens-contract'

type Mode = 'evolve' | 'compare'

const inp = 'text-sm border border-border-strong rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary/30'

/** Lente da faixa → sub-aba do painel re-hospedado. */
const PANEL_SUBTAB: Record<ResourcePanelLensId, 'volume' | 'queue' | 'capacity' | 'sla'> = {
  pool_volume:    'volume',
  pool_queue:     'queue',
  pool_occupancy: 'capacity',
  pool_sla:       'sla',
}

function iso(d: Date): string { return d.toISOString().slice(0, 10) }

export default function ResourcesPage() {
  const { t } = useTranslation('agentReports')
  const { tenantId } = useAuth()
  const [sp, setSp] = useSearchParams()

  const hoje    = new Date()
  const semana  = new Date(hoje.getTime() - 7 * 86400000)

  const [fromDt,  setFromDt]  = useState(sp.get('from')    || iso(semana))
  const [toDt,    setToDt]    = useState(sp.get('to')      || iso(hoje))
  const [poolId,  setPoolId]  = useState(sp.get('pool')    || '')
  const [channel, setChannel] = useState(sp.get('channel') || '')
  const [mode,    setMode]    = useState<Mode>(sp.get('mode') === 'compare' ? 'compare' : 'evolve')
  const [lens,    setLens]    = useState<ResourcePanelLensId>(
    isResourcePanelLens(sp.get('lens') ?? '') ? (sp.get('lens') as ResourcePanelLensId) : 'pool_volume')

  const [poolOptions, setPoolOptions] = useState<string[]>([])

  useEffect(() => {
    if (!tenantId) return
    let cancelado = false
    // ⚠️ O corpo é `{ pools: [...] }`, NÃO um array. A primeira versão desta página
    // fazia `Array.isArray(rows) ? rows : []` e o combo ficava permanentemente vazio —
    // e o modo de falha é o desta casa: a URL levava `?pool=sac_ia`, o painel filtrava
    // certo, e só o SELETOR mentia, mostrando "todos os pools". Sem `.catch` engolindo
    // nada e sem erro no console: um seletor vazio parece "não há pool".
    fetch('/v1/pools', { headers: { 'x-tenant-id': tenantId } })
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then((d: { pools?: Array<{ pool_id: string }> }) => {
        if (cancelado) return
        setPoolOptions([...new Set((d.pools ?? []).map(p => p.pool_id).filter(Boolean))].sort())
      })
      .catch(() => { /* combo sem opções degrada para "todos"; a barra segue usável */ })
    return () => { cancelado = true }
  }, [tenantId])

  // Estado → URL. Só os parâmetros DESTA página; os da mesa são preservados por ela
  // (ver `BenchHost`), e é essa partição que permite os dois escreverem no mesmo
  // endereço sem se apagarem.
  useEffect(() => {
    const next = new URLSearchParams(sp)
    next.set('from', fromDt)
    next.set('to', toDt)
    if (poolId)  next.set('pool', poolId);       else next.delete('pool')
    if (channel) next.set('channel', channel);   else next.delete('channel')
    if (mode === 'compare') next.set('mode', mode); else next.delete('mode')
    // A lente do modo evoluir. No modo comparar quem escreve `lens` é a mesa — daí a
    // guarda: escrever os dois faria o último render vencer, e a faixa piscaria.
    if (mode === 'evolve') {
      if (lens !== 'pool_volume') next.set('lens', lens); else next.delete('lens')
    }
    setSp(next, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fromDt, toDt, poolId, channel, mode, lens])

  if (!tenantId) return null

  return (
    <div className="flex flex-col h-full overflow-hidden bg-surface-muted">

      {/* Barra de filtro — UMA, da superfície. */}
      <div className="bg-white border-b border-border px-4 py-2.5 flex-shrink-0 flex items-center gap-3 flex-wrap">
        <span className="text-xs text-muted">{t('resources.filter.from')}</span>
        <input type="date" value={fromDt} onChange={e => setFromDt(e.target.value)} className={inp} />
        <span className="text-xs text-muted">{t('resources.filter.to')}</span>
        <input type="date" value={toDt} onChange={e => setToDt(e.target.value)} className={inp} />
        <select value={poolId} onChange={e => setPoolId(e.target.value)} className={`${inp} w-44 bg-white`}>
          <option value="">{t('resources.filter.allPools')}</option>
          {poolOptions.map(p => <option key={p} value={p}>{p}</option>)}
        </select>
        {/* O canal só filtra os painéis por pool: a mesa compara ENTIDADES, e o
            `/reports/agents/compare` não recebe canal. Escondê-lo no modo comparar é
            o mesmo princípio do `honors` do contrato — a barra não oferece controle
            que aquele modo não aplica. */}
        {mode === 'evolve' && (
          <select value={channel} onChange={e => setChannel(e.target.value)} className={`${inp} w-36 bg-white`}>
            <option value="">{t('resources.filter.allChannels')}</option>
            {['webchat', 'whatsapp', 'voice', 'email', 'sms', 'webhook'].map(c => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        )}

        <div className="flex-1" />

        {/* Modo (D6) — evoluir × comparar. */}
        <div className="inline-flex rounded-lg border border-border overflow-hidden bg-white">
          {(['evolve', 'compare'] as const).map(m => (
            <button key={m} onClick={() => setMode(m)}
              className={`px-4 py-1.5 text-xs font-medium transition-colors ${
                mode === m ? 'bg-primary text-white' : 'text-muted hover:text-dark hover:bg-surface-muted'
              }`}>
              {t(`resources.mode.${m}`)}
            </button>
          ))}
        </div>
      </div>

      {mode === 'evolve' ? (
        <>
          {/* Faixa de lentes do modo evoluir. */}
          <div className="bg-white border-b border-border px-4 py-2 flex-shrink-0 flex items-center gap-2 flex-wrap">
            <span className="text-xs text-muted mr-1">{t('resources.lens.label')}</span>
            {RESOURCE_PANEL_LENSES.map(l => (
              <button key={l.id} onClick={() => setLens(l.id)}
                className={`px-3 py-1.5 text-xs font-medium rounded-lg border transition-colors ${
                  lens === l.id
                    ? 'border-primary bg-primary text-white'
                    : 'border-border text-muted hover:text-dark hover:border-border-strong'
                }`}>
                {t(`resources.lens.${l.id}`)}
              </button>
            ))}
          </div>
          <div className="flex-1 overflow-hidden flex flex-col">
            <AnalisePoolsPage host={{
              fromDt, toDt, poolId, channel, subTab: PANEL_SUBTAB[lens],
            }} />
          </div>
        </>
      ) : (
        <div className="flex-1 overflow-hidden">
          <AgentsBenchPage host={{ fromDt, toDt, poolId }} />
        </div>
      )}
    </div>
  )
}
