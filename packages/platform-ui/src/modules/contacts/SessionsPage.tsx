/**
 * SessionsPage — /analise/sessions
 *
 * Three-level drill-down (matches Journeys / Processes pattern):
 *   Level 1: filtered session list
 *   Level 2: segment list for a specific session  (breadcrumb bar)
 *   Level 3: SessionTranscript for a specific segment
 */
import React, { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'
import { ChevronRight } from 'lucide-react'
import { useAuth } from '@/auth/useAuth'
import { apiFetch } from '@/api/apiFetch'
import AnaliseJourneysPage from '@/modules/analise/AnaliseJourneysPage'
import { PoolDomainSelect } from '@/components/ui/PoolDomainSelect'
import { SessionTranscript }   from '@/modules/service/components/SessionTranscript'
import { SegmentList }         from '@/modules/service/components/SegmentList'
import { WorkflowTraceList }   from '@/modules/service/components/WorkflowTraceList'
import { WebhookSegmentDetail } from '@/modules/service/components/WebhookSegmentDetail'
import type { ContactSegment } from '@/modules/service/types'
import type { TraceNode }      from '@/modules/service/components/WorkflowTraceList'
import type { ContactFilters } from './types'
import { DEFAULT_FILTERS } from './types'
import { ListaTab }        from './tabs/ListaTab'
// Mesmo componente que a coluna "Processo" da lista usa — ver o comentário do
// `ProcessCrumb` abaixo para por que isto não é conveniência.
import { ProcessChip, processCounts, hasProcess } from './ProcessChip'
import type { ProcessCounts } from './ProcessChip'

// ── Extended filters ──────────────────────────────────────────────────────────

interface SessionFilters extends ContactFilters {
  sessionStatus: string
}

const DEFAULT_SESSION_FILTERS: SessionFilters = {
  ...DEFAULT_FILTERS,
  sessionStatus: '',
}

// ── Filter bar ─────────────────────────────────────────────────────────────────

const inp = 'text-sm border border-border-strong rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary/30'

function FilterBar({ filters, setFilters }: {
  filters: SessionFilters
  setFilters: React.Dispatch<React.SetStateAction<SessionFilters>>
}) {
  const { t } = useTranslation('contacts')
  const { tenantId, currentUser } = useAuth()
  const [showExtra, setShowExtra] = useState(false)

  function set<K extends keyof SessionFilters>(key: K, value: SessionFilters[K]) {
    setFilters(prev => ({ ...prev, [key]: value }))
  }

  function clearAll() { setFilters(DEFAULT_SESSION_FILTERS) }

  const hasExtra = !!(filters.poolId || filters.entryPoolId || filters.agentId
    || filters.insightCategory || filters.insightTags)

  const hasAny = !!(filters.sessionIdSearch || filters.channel || filters.outcome
    || filters.sessionStatus || filters.direction || hasExtra
    || filters.fromDt !== DEFAULT_FILTERS.fromDt || filters.toDt !== DEFAULT_FILTERS.toDt)

  return (
    <div className="bg-white border-b border-border px-4 py-2.5 flex-shrink-0">
      <div className="flex flex-wrap items-center gap-2">

        <div className="flex items-center gap-1.5 text-xs text-muted">
          <span>{t('filter.from')}</span>
          <input type="date" value={filters.fromDt} onChange={e => set('fromDt', e.target.value)} className={inp} />
          <span>{t('filter.to')}</span>
          <input type="date" value={filters.toDt}   onChange={e => set('toDt',   e.target.value)} className={inp} />
        </div>

        <input type="text" value={filters.sessionIdSearch}
          onChange={e => set('sessionIdSearch', e.target.value)}
          placeholder={t('filter.sessionId')}
          className={`${inp} w-44`} />

        <select value={filters.channel} onChange={e => set('channel', e.target.value)} className={`${inp} bg-white`}>
          <option value="">{t('filter.allChannels')}</option>
          {['webchat','whatsapp','voice','email','sms','instagram','telegram','webrtc','webhook'].map(c => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>

        {/* F4 — o seletor de DIREÇÃO, agora de verdade.
            A F3 removeu o antigo «Inbound / Outbound» porque ele não filtrava nada:
            `sessionType` nunca virou parâmetro, então escolher "Outbound" devolvia a
            lista inteira. O que muda aqui não é o controle — é que existe UM
            predicado: `?direction=` no backend usa a MESMA expressão que preenche a
            coluna, então o que o filtro devolve e o que a linha mostra não podem
            divergir. Sem essa identidade, este seletor voltaria a ser o de antes,
            só que mentindo mais devagar. */}
        <select value={filters.direction ?? ''}
          onChange={e => set('direction', e.target.value as SessionFilters['direction'])}
          className={`${inp} bg-white`}
          title={t('filter.directionHint')}>
          <option value="">{t('filter.allDirections')}</option>
          <option value="inbound">{t('filter.direction.inbound')}</option>
          <option value="outbound">{t('filter.direction.outbound')}</option>
          <option value="internal">{t('filter.direction.internal')}</option>
        </select>

        <select value={filters.sessionStatus} onChange={e => set('sessionStatus', e.target.value)} className={`${inp} bg-white`}>
          <option value="">{t('sessions.allStatuses')}</option>
          <option value="active">{t('sessions.status.active')}</option>
          <option value="suspended">{t('sessions.status.suspended')}</option>
          <option value="closed">{t('sessions.status.closed')}</option>
          <option value="abandoned">{t('sessions.status.abandoned')}</option>
        </select>

        <button onClick={() => setShowExtra(v => !v)}
          className={`text-xs px-3 py-1.5 rounded-lg border transition-colors flex items-center gap-1 ${
            showExtra || hasExtra
              ? 'bg-primary/10 text-primary border-primary/30 font-semibold'
              : 'text-muted border-border-strong hover:border-primary hover:text-primary'
          }`}>
          {showExtra ? '▲' : '▼'} {t('filter.moreFilters')}
          {hasExtra && (
            <span className="ml-1 inline-flex items-center justify-center w-4 h-4 rounded-full bg-primary text-white text-2xs font-bold">
              {[filters.poolId, filters.entryPoolId, filters.agentId, filters.insightCategory, filters.insightTags].filter(Boolean).length}
            </span>
          )}
        </button>

        {hasAny && (
          <button onClick={clearAll}
            className="text-xs text-muted-light hover:text-red px-2 py-1.5 rounded-lg border border-border hover:border-red/30 transition-colors ml-auto">
            {t('filter.clearFilters')}
          </button>
        )}
      </div>

      {showExtra && (
        <div className="flex flex-wrap items-center gap-2 mt-2 pt-2 border-t border-border">
          {/* ── Os DOIS filtros de pool (D12) ────────────────────────────────────
              `entrou por` = a PORTA (`sessions.pool_id`, first-write-wins).
              `atendido por` = qualquer segmento daquele pool.
              **Nenhum dos dois se chama "Pool" na tela.** Se chamassem, o operador
              leria um e receberia o outro — que é exatamente o erro que
              `sessions.pool_id` cometia antes da F1b, um nível abaixo.
              Removidos aqui: ANI/DNIS (permanentemente vazios; não voltam). */}
          {([
            { key: 'entryPoolId',     label: t('filter.entryPool'),     placeholder: 'ex: sac_ia',    width: 'w-36' },
            { key: 'poolId',          label: t('filter.attendedBy'),    placeholder: 'ex: sac_ia',    width: 'w-36' },
            { key: 'agentId',         label: t('filter.agent'),         placeholder: 'participant…',  width: 'w-44' },
            { key: 'insightCategory', label: t('filter.eventCategory'), placeholder: 'categoria…',   width: 'w-40' },
            { key: 'insightTags',     label: t('filter.tags'),          placeholder: 'tag1,tag2',     width: 'w-36' },
          ] as { key: keyof SessionFilters; label: string; placeholder: string; width: string }[]).map(f => (
            <div key={f.key} className="flex items-center gap-1">
              <span className="text-xs text-muted-light whitespace-nowrap">{f.label}:</span>
              {(f.key === 'poolId' || f.key === 'entryPoolId') ? (
                // Segurança Fase E — pool = combo do DOMÍNIO (listPools ∩ accessiblePools),
                // não texto livre. Vazio no combo = todo o domínio; o backend reintersecta.
                <PoolDomainSelect
                  tenantId={tenantId ?? ''}
                  accessiblePools={currentUser?.accessiblePools ?? []}
                  value={filters[f.key] as string}
                  onChange={v => set(f.key, v)}
                  allLabel={t('filter.allPools', { defaultValue: 'Todos os pools do domínio' })}
                  className={`${inp} ${f.width}`} />
              ) : (
                <input type="text" value={filters[f.key] as string}
                  onChange={e => set(f.key, e.target.value)}
                  placeholder={f.placeholder}
                  className={`${inp} ${f.width}`} />
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Selo do processo no breadcrumb do drill ───────────────────────────────────
//
// A volta ao PROCESSO, que não existia. Até 2026-08-25 o único pivô para a visão 2
// era o chip na LISTA — e quem chegava ao drill por deep-link (`?session_id=`, que
// três telas usam) nunca passava pela lista. O sintoma foi relatado como
// *"os contatos do processo não aparecem em lugar nenhum"*: apareciam, a um clique,
// numa tela sem caminho até ela.
//
// Só é renderizado quando há para onde pivotar — a regra é `hasProcess`, do chip, e
// não uma cópia dela aqui: um selo apontando para um processo de um contato só
// afirmaria uma relação que não existe. Desde 2026-08-26 "há para onde pivotar" tem
// DUAS razões (mais de uma sessão, ou membros fora do escopo do usuário), e é por
// morar numa função só que este selo não ficou para trás da lista.
//
// ⚠️ **É o MESMO componente da lista** (`ProcessChip`), não um parecido. Enquanto
// eram dois, divergiram no corte do id (4 × 8 caracteres) — mesmo processo, dois
// códigos, um em cada ponta do clique. A regra do pivô (`hasProcess`) e os números
// vêm de lá pela mesma razão.
function ProcessCrumb({ journeyId, counts, onOpen }: {
  journeyId: string; counts: ProcessCounts | null; onOpen: () => void
}) {
  const { t } = useTranslation('contacts')
  return (
    <>
      <ChevronRight className="w-3.5 h-3.5 text-border-strong" aria-hidden="true" />
      <ProcessChip journeyId={journeyId} counts={counts} t={t} onOpen={onOpen} />
    </>
  )
}

// ── SessionsPage ──────────────────────────────────────────────────────────────

export default function SessionsPage() {
  const { t } = useTranslation('contacts')
  const { tenantId } = useAuth()
  const [searchParams, setSearchParams] = useSearchParams()

  const [filters,            setFilters]            = useState<SessionFilters>(DEFAULT_SESSION_FILTERS)
  // Escopo da listagem (ADR wrapup-detached-pull §7). Aqui em cima pela mesma razão
  // que `filters`: os ramos de drill abaixo dão `return` e desmontam a ListaTab.
  const [listScopeAll,       setListScopeAll]       = useState(false)
  const [detailSessionId,    setDetailSessionId]    = useState<string | null>(null)
  /** Canal da sessão aberta, **amarrado ao id a que pertence**.
   *
   *  Era um valor solto, e isso é um defeito com sintoma silencioso: navegar de uma
   *  sessão A para uma B pelo BOTÃO VOLTAR do navegador troca o id sem passar por
   *  `openSession`, e o canal de A sobrevivia — B renderizava com o renderer errado
   *  (workflow como conversa, ou o contrário). Com o par `{id, ch}`, "não resolvido
   *  para ESTE id" é uma pergunta que se pode fazer, e é ela que dispara o lookup.
   *
   *  `''` continua sendo "resolvido e vazio" — diferente de não resolvido. */
  const [chEntry, setChEntry] = useState<{ id: string; ch: string } | null>(null)
  /** Processo a que a sessão aberta pertence, quando tem mais de um contato. É o
   *  selo `PRC-…` do breadcrumb — a volta ao processo, que não existia. */
  const [sessionJourney, setSessionJourney] = useState<{ id: string; counts: ProcessCounts } | null>(null)
  /** S3 — trilha de ancestrais ao navegar para uma sessão ORIGINADA. Guarda o canal
   *  junto do id: é ele que decide trace × segmentos ao voltar. */
  const [sessionTrail,       setSessionTrail]       = useState<Array<{ id: string; ch: string | null }>>([])
  const [detailSegment,      setDetailSegment]      = useState<ContactSegment | null>(null)
  const [detailWebhookNode,  setDetailWebhookNode]  = useState<TraceNode | null>(null)
  /** F3 (resíduo) — a sessão pedida por `?session_id=` não voltou. Ver o efeito. */
  const [deepLinkMiss,       setDeepLinkMiss]       = useState<string | null>(null)

  // ── F3 (resíduo) — `?session_id=` é ENDEREÇO, não sugestão ────────────────
  //
  // Três telas já linkavam para cá com este parâmetro (`WorkItemsPage`,
  // `SchedulesMonitorPage`, `DeliveriesTab`) e o parâmetro era IGNORADO: o operador
  // clicava em "ver a sessão" e caía na lista inteira, sem nada dizendo que o pedido
  // tinha sido descartado. Silêncio, e do tipo que parece funcionar.
  //
  // A URL passa a ser a fonte ÚNICA do nível de sessão — o mesmo desenho que
  // `?journey=` já usa. Estado local aqui criaria dois endereços para o mesmo lugar,
  // que divergem no reload e no botão "voltar" do navegador.
  const urlSession = searchParams.get('session_id')
  /** Processo no endereço. Presente ⇒ chegamos aqui PELO processo, e é para ele que
   *  o selo do breadcrumb volta (sem precisar redescobri-lo). */
  const urlJourney = searchParams.get('journey')
  useEffect(() => {
    if (urlSession) {
      setDetailSessionId(prev => (prev === urlSession ? prev : urlSession))
    } else {
      setDetailSessionId(null)
      setSessionTrail([])
    }
  }, [urlSession])

  // Clear drill-down state when session changes
  useEffect(() => {
    setDetailSegment(null)
    setDetailWebhookNode(null)
    setDeepLinkMiss(null)
  }, [detailSessionId])

  // ── Resolução da SESSÃO: canal (qual tela) + processo (a volta) ───────────
  //
  // O canal decide o nível 2 (`webhook` → trace de workflow; demais → segmentos);
  // adivinhar renderizaria um workflow como conversa. O processo decide se há selo
  // `PRC-…` no breadcrumb. **Os dois vêm do MESMO lookup** — `journey_id` e
  // `journey_session_count` já viajam na linha desde a F3.3, então o selo não custa
  // requisição nenhuma, e não existe caminho em que um apareça sem o outro.
  //
  // Roda sempre que o id muda, inclusive quando o canal já veio do clique: quem
  // navega pelo BOTÃO VOLTAR não passa por `openSession`, e sem esta releitura o
  // selo ficaria apontando para o processo da sessão anterior.
  //
  // Não achou a sessão: **não cai na lista em silêncio**. Uma lista com filtro vazio
  // é indistinguível de "não existe" e de "existe, mas fora do seu escopo" (ABAC), e
  // essas respostas pedem ações diferentes do operador.
  useEffect(() => {
    if (!tenantId || !detailSessionId) { setSessionJourney(null); return }
    let cancelled = false
    apiFetch(`/reports/sessions?${new URLSearchParams({
      tenant_id: tenantId, session_id: detailSessionId, page_size: '1',
    })}`)
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then(d => {
        if (cancelled) return
        const row = d.data?.[0]
        if (!row) { setDeepLinkMiss(detailSessionId); return }
        // Só completa o canal se ele ainda não foi resolvido PARA ESTE id — o valor
        // que veio do clique é o mesmo, e sobrescrevê-lo causaria um repaint à toa.
        setChEntry(prev => (prev && prev.id === detailSessionId
          ? prev
          : { id: detailSessionId, ch: row.channel ?? '' }))
        const jid    = row.journey_id || row.root_session_id || null
        const counts = processCounts(row)
        // Mesma regra do chip da lista, e o MESMO predicado — não uma reescrita
        // dele: sem processo para onde pivotar, um selo afirmaria uma relação que
        // não existe. O predicado cresceu (marcador de escopo) sem que esta linha
        // precisasse saber — que é o ponto de ele viver em `ProcessChip.tsx`.
        setSessionJourney(jid && counts && hasProcess(counts) ? { id: jid, counts } : null)
      })
      .catch(e => { if (!cancelled) setDeepLinkMiss(String(e)) })
    return () => { cancelled = true }
  }, [tenantId, detailSessionId])

  /** Abre o nível 2 pelo ENDEREÇO, **preservando o processo** quando há um. `ch =
   *  null` ⇒ resolve antes de escolher a tela. Único caminho para abrir uma sessão —
   *  inclusive o clique da lista e o da visão 2. */
  function openSession(sid: string, ch: string | null) {
    setChEntry(ch === null ? null : { id: sid, ch })
    setSearchParams(urlJourney ? { journey: urlJourney, session_id: sid } : { session_id: sid })
  }
  /** Sai do drill. Volta ao PROCESSO quando foi por ele que se entrou; à lista
   *  quando não. O selo `PRC-…` do breadcrumb é o caminho explícito para o processo
   *  no outro caso (deep-link, clique na lista) — um controle, um significado. */
  function closeSession() {
    setSessionTrail([])
    setSearchParams(urlJourney ? { journey: urlJourney } : {})
  }

  /** `null` = canal NÃO RESOLVIDO para o id corrente (ver `chEntry`). */
  const detailSessionCh  = chEntry && chEntry.id === detailSessionId ? chEntry.ch : null
  const isWebhookSession = detailSessionCh === 'webhook'
  /** O processo do breadcrumb: o da URL (viemos dele) ou o resolvido pela sessão. */
  const crumbJourney = urlJourney ?? sessionJourney?.id ?? null

  // ── Nível 2: o PROCESSO (F3.3) ────────────────────────────────────────────
  //
  // `/analise/processos` foi absorvido: o processo passa a ser um nível DESTA rota,
  // alcançado por `?journey=…`. A lista livre de processos deixa de existir por
  // decisão (D2/ADR §D3): processo é **pivô**, e o único caminho até ele é o chip da
  // linha de contato — ou um deep-link que já traz o id. Uma lista de processos
  // reintroduziria "filtrar por pool" no nível errado, devolvendo *journeys que
  // tocaram o pool* onde o operador pediu contatos.
  //
  // O componente é o mesmo (`AnaliseJourneysPage`); a rota velha redireciona
  // preservando a query. Aqui ele só é montado COM `?journey=` — sem o parâmetro
  // caímos na lista de contatos, que é o `onBack` dele (`setSearchParams({})`).
  //
  // ⚠️ **`&& !urlSession`** — a condição mudou ao unificar o nível de sessão
  // (2026-08-25). `?journey=X&session_id=Y` significa *"a sessão Y, vista de dentro
  // do processo X"*, e quem a renderiza é o drill ABAIXO, não a visão 2. Sem esta
  // cláusula o processo engoliria o endereço e voltaríamos a ter dois níveis de
  // sessão — o defeito que a unificação existe para fechar.
  if (urlJourney && !urlSession) {
    return <AnaliseJourneysPage />
  }

  if (!tenantId) {
    return (
      <div className="flex items-center justify-center h-full text-muted-light text-sm">
        {t('noTenant')}
      </div>
    )
  }

  // ── Deep-link por `?session_id=`: resolvendo, ou não encontrado ────────────
  //
  // Estado próprio, e barulhento. Cair na lista aqui daria uma tela plausível — a
  // listagem inteira, sem nada errado à vista — para um pedido que não foi
  // atendido. As duas causas possíveis são nomeadas porque pedem ações diferentes:
  // a sessão pode não existir, ou pode estar fora do escopo de pools do usuário.
  if (detailSessionId && detailSessionCh === null) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 text-sm">
        {deepLinkMiss ? (
          <>
            <span className="text-3xl" aria-hidden="true">🔍</span>
            <span className="text-dark font-medium">{t('sessions.deepLink.notFound')}</span>
            <span className="text-xs text-muted font-mono break-all max-w-lg text-center">{detailSessionId}</span>
            <span className="text-xs text-muted-light max-w-md text-center">{t('sessions.deepLink.notFoundHint')}</span>
            <button onClick={closeSession}
              className="text-xs px-3 py-1.5 rounded-lg border border-border-strong text-muted hover:border-primary hover:text-primary transition-colors">
              {t('sessions.deepLink.backToList')}
            </button>
          </>
        ) : (
          <span className="text-muted-light text-xs">{t('sessions.deepLink.resolving')}</span>
        )}
      </div>
    )
  }

  // ── Level 3a: webhook exec detail ──────────────────────────────────────────

  if (detailSessionId && detailWebhookNode) {
    return (
      <div className="flex flex-col h-full overflow-hidden">
        {/* Breadcrumb */}
        <div className="bg-white border-b border-border px-5 py-2.5 flex items-center gap-2 text-xs flex-shrink-0 sticky top-0 z-10">
          <button
            onClick={closeSession}
            className="text-muted-light hover:text-dark transition-colors font-medium"
          >
            {t('sessions.breadcrumbs.sessions')}
          </button>
          {crumbJourney && (
            <ProcessCrumb journeyId={crumbJourney} counts={sessionJourney?.counts ?? null}
              onOpen={() => setSearchParams({ journey: crumbJourney })} />
          )}
          <ChevronRight className="w-3.5 h-3.5 text-border-strong" aria-hidden="true" />
          <button
            onClick={() => setDetailWebhookNode(null)}
            className="text-muted-light hover:text-dark transition-colors font-medium font-mono"
            title={detailSessionId}
          >
            {'…' + detailSessionId.slice(-14)}
          </button>
          <ChevronRight className="w-3.5 h-3.5 text-border-strong" aria-hidden="true" />
          <span className="text-dark font-medium">{t('trace.execWindow')}</span>
        </div>
        <div className="flex-1 overflow-hidden p-3">
          <WebhookSegmentDetail
            tenantId={tenantId}
            node={detailWebhookNode}
            onBack={() => setDetailWebhookNode(null)}
          />
        </div>
      </div>
    )
  }

  // ── Level 3b: agent segment transcript ──────────────────────────────────────

  if (detailSessionId && detailSegment) {
    // Cross-session navigation (e.g. input_origin from WorkflowTraceList belongs
    // to a different session than detailSessionId). When segment.session_id differs
    // from the current context session, omit the segment filter so SessionTranscript
    // shows all messages instead of filtering by segment time-window.
    const isCrossSession = detailSegment.session_id !== detailSessionId
    return (
      <div className="h-full overflow-hidden">
        <SessionTranscript
          tenantId={tenantId}
          sessionId={detailSegment.session_id}
          segment={isCrossSession ? undefined : detailSegment}
          canJoin={!isCrossSession && detailSegment.ended_at === null}
          onBack={() => setDetailSegment(null)}
        />
      </div>
    )
  }

  // ── Level 2: trace list (webhook) or segment list (regular) ─────────────────

  if (detailSessionId) {
    const shortId = detailSessionId.length > 16
      ? '…' + detailSessionId.slice(-14)
      : detailSessionId

    return (
      <div className="flex flex-col h-full overflow-hidden">
        {/* Breadcrumb — inclui a TRILHA de sessões originadas (S3). Sem ela, abrir a
            filha troca o `sessionId` e o único caminho de volta é a listagem: o
            operador perde o contato de onde veio, que é o contexto da visita. */}
        <div className="bg-white border-b border-border px-5 py-2.5 flex items-center gap-2 text-xs flex-shrink-0 sticky top-0 z-10">
          <button
            onClick={closeSession}
            className="text-muted-light hover:text-dark transition-colors font-medium"
          >
            {t('sessions.breadcrumbs.sessions')}
          </button>
          {/* A volta ao PROCESSO. Fica ANTES da trilha de ancestrais porque é o
              escopo mais largo: processo › contato de origem › … › esta sessão. */}
          {crumbJourney && (
            <ProcessCrumb journeyId={crumbJourney} counts={sessionJourney?.counts ?? null}
              onOpen={() => setSearchParams({ journey: crumbJourney })} />
          )}
          {sessionTrail.map((crumb, i) => (
            <React.Fragment key={crumb.id}>
              <ChevronRight className="w-3.5 h-3.5 text-border-strong" aria-hidden="true" />
              <button
                onClick={() => {
                  // O canal viaja na trilha: sem ele, voltar a um ancestral webhook
                  // o renderizaria como sessão comum (SegmentList em vez do trace).
                  const trail = sessionTrail.slice(0, i)
                  openSession(crumb.id, crumb.ch)
                  setSessionTrail(trail)
                }}
                className="text-muted-light hover:text-dark transition-colors font-mono"
                title={crumb.id}
              >
                {'…' + crumb.id.slice(-14)}
              </button>
            </React.Fragment>
          ))}
          <ChevronRight className="w-3.5 h-3.5 text-border-strong" aria-hidden="true" />
          <span className="text-dark font-medium font-mono" title={detailSessionId}>{shortId}</span>
        </div>

        <div className="flex-1 overflow-hidden">
          {isWebhookSession ? (
            /* Arc 19: webhook sessions use WorkflowTraceList (cross-session trace) */
            <WorkflowTraceList
              tenantId={tenantId}
              sessionId={detailSessionId}
              onSelectAgent={seg => setDetailSegment(seg)}
              onSelectWebhook={node => setDetailWebhookNode(node)}
            />
          ) : (
            /* Regular sessions use standard SegmentList */
            <SegmentList
              tenantId={tenantId}
              sessionId={detailSessionId}
              onSelect={seg => setDetailSegment(seg)}
              onBack={closeSession}
              showBack={false}
              onOpenChild={(sid, ch) => {
                const trail = [...sessionTrail, { id: detailSessionId, ch: detailSessionCh }]
                openSession(sid, ch)
                setSessionTrail(trail)
              }}
            />
          )}
        </div>
      </div>
    )
  }

  // ── Level 1: session list ───────────────────────────────────────────────────

  const contactFilters: ContactFilters = {
    fromDt:          filters.fromDt,
    toDt:            filters.toDt,
    sessionIdSearch: filters.sessionIdSearch,
    channel:         filters.channel,
    outcome:         filters.outcome,
    poolId:          filters.poolId,
    entryPoolId:     filters.entryPoolId,
    agentId:         filters.agentId,
    insightCategory: filters.insightCategory,
    insightTags:     filters.insightTags,
    status:          filters.sessionStatus || undefined,  // Arc 19: pass status filter
    direction:       filters.direction,                   // D8 (F4)
  }

  return (
    <div className="flex flex-col h-full overflow-hidden bg-surface-muted">
      <FilterBar filters={filters} setFilters={setFilters} />

      <div className="flex-1 overflow-hidden">
        <ListaTab
          tenantId={tenantId}
          filters={contactFilters}
          onOpenDetail={(sid, ch) => openSession(sid, ch || null)}
          scopeAll={listScopeAll}
          onScopeAllChange={setListScopeAll}
          // Pivô para o nível 2. Vai pela URL (e não por estado local) de propósito:
          // é o mesmo endereço dos deep-links externos, então há UM caminho para o
          // processo, não dois que podem divergir.
          onOpenJourney={jid => setSearchParams({ journey: jid })}
        />
      </div>
    </div>
  )
}
