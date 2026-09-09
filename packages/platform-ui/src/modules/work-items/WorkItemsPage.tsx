/**
 * WorkItemsPage
 * Rota: /monitor/work-items — pendências de wrap-up AGORA (I5 / ADR § D7b, fatia 1).
 *
 * Monitor = estado agregado ao vivo. Esta tela responde "quem está com wrap-up
 * pendente neste momento" e nada mais; o histórico ("quantos venceram no período")
 * é query sobre `segments` e vive no Analytics (fatia 2, a lente `disposition` de
 * `/analise/sessions`).
 *
 * ⚠️ **PUL-04 (2026-09-09): ela abre em CONSOLIDADO, e o filtro de estado saiu.**
 * Era a última tela do Monitor a abrir em lista crua. O argumento não é simetria
 * com a aba Processos — é AMBIGUIDADE: medida, esta tela está honestamente vazia
 * (0 chaves no ledger; 85 wrap-ups no total, o último em 2026-09-07), mas dizia
 * isso com o mesmo "nenhum item" que a aba Processos exibia com 51 processos DE PÉ
 * (ORQ-10). Um zero em contador AFIRMA; uma lista vazia só sugere — e aqui o zero
 * é o valor esperado, logo é justamente ele que ninguém distingue de falha.
 *
 * E o filtro de estado era um SEGUNDO controle para a mesma partição que o contador
 * já expressava — a família do `pool_id IN (…)` que a F1b fechou. O contador ainda
 * diz o TAMANHO antes do clique, o que o filtro não dizia.
 *
 * ESCOPO: só wrap-up. O ledger que a alimenta é genérico (cobre aprovação e
 * delegate a pool push), mas o relatório da D4 é de trabalho AUTHOR-BOUND —
 * aprovação é pooled e tem transbordo, então ninguém fica preso nela. O corte é
 * pelo sufixo `-int` do pool, garantia por construção da D6.
 *
 * JANELA, NÃO ACUMULADO: o ledger vive `timeout_hours*3600 + 3600` (25 h no
 * wrap-up default). Passado isso a pendência some — sem rastro, se o timeout
 * scanner não tiver passado. A tela diz isso em vez de deixar supor que acumula.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useLocation, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'
import EmptyState from '@/components/ui/EmptyState'
import {
  PendingWorkTask, WorkTaskState, fetchPending, fetchDirectory, expirePending,
  ExpirePendingError, fmtDuration, fmtDateTime,
} from './api'
import { resumeConflictDetails } from '@/lib/resume-conflict'

const POLL_MS = 15_000

/** Estados na ordem em que a tela os apresenta. `all` não é estado — é ausência de recorte. */
const STATES: WorkTaskState[] = ['unclaimed', 'claimed', 'orphaned', 'not_queued', 'unknown']

/**
 * Os que aparecem MESMO EM ZERO.
 *
 * `orphaned` é anomalia de infra (lease vencida sem reaper — PUL-01), não estado de
 * trabalho: como contador permanente ele vira vigilância, e só desaparece da tela
 * quando o problema desaparece do parque. `not_queued` e `unknown` classificam
 * INFRA, não trabalho, e por isso só ocupam espaço quando existem.
 */
const STATES_SEMPRE: WorkTaskState[] = ['unclaimed', 'claimed', 'orphaned']

type GroupAxis = 'agent' | 'pool'

/**
 * O recorte do drill-down. `overdue` NÃO é um `WorkTaskState` — é um fato
 * transversal (o prazo passou), verdadeiro em companhia de qualquer estado.
 * Tratá-lo como estado colapsaria dois eixos num só.
 */
type DrillState = WorkTaskState | 'all' | 'overdue'

/**
 * Os recortes que o endereço aceita. Existe porque a URL é entrada de FORA:
 * sem a conferência, `?state=qualquer` viraria um filtro que não casa nada e a
 * tela diria "nenhuma pendência" — o valor plausível que esta ficha fecha.
 */
const RECORTES_VALIDOS: DrillState[] = ['all', 'overdue', ...STATES]

function casaODrill(i: PendingWorkTask, st: DrillState): boolean {
  if (st === 'all')     return true
  if (st === 'overdue') return i.overdue
  return i.state === st
}

// ── Apresentação ──────────────────────────────────────────────────────────────

function StatePill({ state }: { state: WorkTaskState }) {
  const { t } = useTranslation('workItems')
  const styles: Record<WorkTaskState, string> = {
    unclaimed:  'bg-warning-light text-warning-text',
    claimed:    'bg-primary/10 text-primary',
    // orphaned é anomalia de infra (lease venceu sem reaper), não estado normal
    // de trabalho — cor de alerta de propósito.
    orphaned:   'bg-red-light text-red-text',
    not_queued: 'bg-surface-alt text-muted',
    unknown:    'bg-surface-alt text-muted',
  }
  return (
    <span
      title={t(`state.${state}Hint`)}
      className={`text-xs px-2 py-0.5 rounded-full font-medium ${styles[state]}`}
    >
      {t(`state.${state}`)}
    </span>
  )
}

function ConfirmModal({ message, confirmLabel, onCancel, onConfirm }: {
  message: string; confirmLabel: string; onCancel: () => void; onConfirm: () => void
}) {
  const { t } = useTranslation('workItems')
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-md">
        <p className="text-sm text-dark mb-4 whitespace-pre-line">{message}</p>
        <div className="flex gap-2 justify-end">
          <button onClick={onCancel} className="px-4 py-2 text-sm text-muted hover:text-dark">
            {t('actions.cancel')}
          </button>
          <button onClick={onConfirm}
            className="px-4 py-2 text-sm text-white rounded-lg bg-red hover:bg-red-text">
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}

function ItemRow({ item, canExpire, onExpire, busy, origem }: {
  item: PendingWorkTask; canExpire: boolean; onExpire: () => void; busy: boolean
  /** Endereço COMPLETO desta tela (com o recorte), para o destino saber voltar. */
  origem: string
}) {
  const { t } = useTranslation('workItems')
  return (
    <div className="grid grid-cols-[1.2fr_auto_auto_auto_auto_auto] gap-3 items-center px-3 py-2 bg-white rounded text-xs border-b border-border last:border-b-0">
      {/* `from` carrega o recorte inteiro: o operador volta para a lista que
          montou, não para o consolidado nem para a lista de outra tela. */}
      <Link
        to={`/analise/sessions?session_id=${encodeURIComponent(item.session_id)}&from=${encodeURIComponent(origem)}`}
        title={item.session_id}
        className="text-primary hover:text-primary-dark font-mono truncate"
      >
        {item.session_id.slice(0, 8)}… ↗
      </Link>
      <StatePill state={item.state} />
      <span className="text-muted whitespace-nowrap" title={t('col.ageHint')}>
        {fmtDuration(item.age_ms)}
      </span>
      <span
        className={`whitespace-nowrap ${item.overdue ? 'text-red-text font-semibold' : 'text-muted'}`}
        title={item.overdue ? t('col.overdueHint') : fmtDateTime(item.deadline)}
      >
        {item.overdue ? `⚠ ${t('col.overdue')}` : fmtDuration(item.time_to_deadline_ms)}
      </span>
      <span className="text-muted-light truncate" title={item.claimed_by ?? ''}>
        {item.claimed_by ?? '—'}
      </span>
      {canExpire ? (
        <button
          onClick={onExpire}
          disabled={busy}
          className="px-2 py-1 text-xs rounded border border-border-strong text-muted hover:text-red-text hover:border-red disabled:opacity-40"
        >
          {busy ? '…' : t('actions.expire')}
        </button>
      ) : <span />}
    </div>
  )
}

/**
 * Rótulo do recorte para a migalha. Recebe `t` por parâmetro: helper fora de
 * componente nunca chama `useTranslation` (invariante i18n nº 4).
 */
function rotuloDoEstado(st: DrillState, t: (k: string) => string): string {
  if (st === 'all')     return t('filterAll')
  if (st === 'overdue') return t('kpi.overdue')
  return t(`state.${st}`)
}

// ── Consolidado ───────────────────────────────────────────────────────────────

/**
 * A entrada da tela: NÚMEROS, e cada número é a porta da sua lista.
 *
 * ⚠️ Agregado no CLIENTE, e é decisão medida — não desleixo. A ORQ-10 exigiu
 * agregação no BACKEND para a aba Processos porque lá o teto de 200 linhas era
 * MUDO: contar a lista daria o menor entre a verdade e o teto, e pareceria certo.
 * Aqui o `fetchPending` DECLARA a truncagem (`truncated`/`scanned`), então a
 * mesma regra sobrevive por outro mecanismo: quando a varredura bate no teto, os
 * contadores se dizem PARCIAIS em vez de passar por total.
 *
 * ⚠️ O eixo primário é o AGENTE. A pendência é author-bound — é o que a separa da
 * aprovação, que é pooled e transborda —, então uma tabela só por pool perderia a
 * única pergunta que esta tela responde: *quem* está devendo. O seletor
 * `agente | pool` é DIMENSÃO, não filtro, e por isso sobreviveu ao corte.
 */
function ConsolidadoDePendencias({ items, axis, truncated, displayName, onDrill }: {
  items:       PendingWorkTask[]
  axis:        GroupAxis
  truncated:   boolean
  displayName: (userId: string | null) => string
  onDrill:     (state: DrillState, key: string | null) => void
}) {
  const { t } = useTranslation('workItems')

  const porEstado = useMemo(() => {
    const c = Object.fromEntries(STATES.map(s => [s, 0])) as Record<WorkTaskState, number>
    for (const i of items) c[i.state] = (c[i.state] ?? 0) + 1
    return c
  }, [items])

  const vencidas = useMemo(() => items.filter(i => i.overdue).length, [items])

  const cartoes: { key: DrillState; label: string; value: number; alerta: boolean }[] = [
    { key: 'all', label: t('kpi.total'), value: items.length, alerta: false },
    ...STATES
      .filter(st => STATES_SEMPRE.includes(st) || porEstado[st] > 0)
      .map(st => ({
        key: st as DrillState,
        label: t(`state.${st}`),
        value: porEstado[st],
        alerta: st === 'orphaned' && porEstado[st] > 0,
      })),
    { key: 'overdue', label: t('kpi.overdue'), value: vencidas, alerta: vencidas > 0 },
  ]

  /** Uma linha por valor do eixo. Item sem dono NÃO some — vira linha própria. */
  const linhas = useMemo(() => {
    const m = new Map<string, PendingWorkTask[]>()
    for (const i of items) {
      const key = axis === 'pool' ? i.pool_id : (i.assigned_to ?? '')
      const arr = m.get(key)
      if (arr) arr.push(i); else m.set(key, [i])
    }
    return [...m.entries()]
      .map(([key, rows]) => ({
        key,
        total:    rows.length,
        porEstado: Object.fromEntries(
          STATES.map(st => [st, rows.filter(r => r.state === st).length]),
        ) as Record<WorkTaskState, number>,
        vencidas:  rows.filter(r => r.overdue).length,
        // `null` em `age_ms` é ausência, não zero: a linha mais antiga só existe
        // se alguém soube dizer a idade de pelo menos um item.
        maisAntiga: rows.reduce<number | null>(
          (max, r) => (r.age_ms == null ? max : Math.max(max ?? 0, r.age_ms)), null),
      }))
      .sort((a, b) => b.total - a.total)
  }, [items, axis])

  /** Só as colunas que algum grupo exerce — mais `STATES_SEMPRE`. */
  const colunas = STATES.filter(
    st => STATES_SEMPRE.includes(st) || linhas.some(l => l.porEstado[st] > 0))

  return (
    <div className="p-4 space-y-4">
      {truncated && (
        <p className="text-xs text-warning-text bg-warning-light rounded px-2 py-1">
          {t('summary.partial')}
        </p>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        {cartoes.map(c => (
          <button
            key={c.key}
            type="button"
            onClick={() => onDrill(c.key, null)}
            className="text-left bg-white border border-border rounded-xl px-4 py-3 hover:border-primary/60 hover:shadow-sm transition-colors"
          >
            <div className={`text-2xl font-bold ${c.alerta ? 'text-red-text' : 'text-dark'}`}>
              {c.value}
            </div>
            <div className="text-xs text-muted mt-0.5">{c.label}</div>
          </button>
        ))}
      </div>

      <div className="bg-white border border-border rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-surface-alt/40 text-2xs uppercase tracking-wide text-muted-light">
              <tr>
                <th className="text-left px-4 py-2.5">{t(`col.${axis}`)}</th>
                <th className="text-right px-3 py-2.5">{t('col.total')}</th>
                {colunas.map(st => (
                  <th key={st} className="text-right px-3 py-2.5">{t(`state.${st}`)}</th>
                ))}
                <th className="text-right px-3 py-2.5">{t('kpi.overdue')}</th>
                <th className="text-right px-4 py-2.5" title={t('col.oldestHint')}>
                  {t('col.oldest')}
                </th>
              </tr>
            </thead>
            <tbody>
              {linhas.length === 0 && (
                <tr><td colSpan={colunas.length + 4} className="px-4 py-6 text-center text-muted-light">
                  {t('emptyHint')}
                </td></tr>
              )}
              {linhas.map(l => (
                <tr key={l.key || '__unassigned__'} className="border-t border-border hover:bg-surface-alt/40">
                  <td className="px-4 py-2.5">
                    <button type="button" onClick={() => onDrill('all', l.key)}
                            className="text-primary hover:underline font-medium">
                      {axis === 'pool' ? (l.key || '—') : displayName(l.key || null)}
                    </button>
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    <button type="button" onClick={() => onDrill('all', l.key)}
                            className="text-dark hover:underline">{l.total}</button>
                  </td>
                  {colunas.map(st => (
                    <td key={st} className="px-3 py-2.5 text-right">
                      {l.porEstado[st] > 0 ? (
                        <button type="button" onClick={() => onDrill(st, l.key)}
                                className={`hover:underline ${st === 'orphaned' ? 'text-red-text font-semibold' : 'text-muted'}`}>
                          {l.porEstado[st]}
                        </button>
                      ) : <span className="text-muted-light">0</span>}
                    </td>
                  ))}
                  <td className="px-3 py-2.5 text-right">
                    {l.vencidas > 0 ? (
                      <button type="button" onClick={() => onDrill('overdue', l.key)}
                              className="text-red-text font-semibold hover:underline">{l.vencidas}</button>
                    ) : <span className="text-muted-light">0</span>}
                  </td>
                  <td className="px-4 py-2.5 text-right text-muted-light text-xs">
                    {fmtDuration(l.maisAntiga)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <p className="text-xs text-muted-light">{t('summary.hint')}</p>
    </div>
  )
}


// ── Raiz ──────────────────────────────────────────────────────────────────────

export default function WorkItemsPage() {
  const { t } = useTranslation('workItems')
  const { session, tenantId, perms, getAccessToken } = useAuth()

  const [items,     setItems]     = useState<PendingWorkTask[]>([])
  const [meta,      setMeta]      = useState<{ scanned: number; truncated: boolean; at: string } | null>(null)
  const [loading,   setLoading]   = useState(true)
  const [error,     setError]     = useState('')
  const [directory, setDirectory] = useState<Map<string, string> | null>(null)
  const [dirDenied, setDirDenied] = useState(false)
  const [search,    setSearch]    = useState('')

  /**
   * PUL-04 — o recorte mora na URL, e isso é conserto de um defeito MEDIDO.
   *
   * `drill === null` é o CONSOLIDADO; sob drill, o recorte tem dois eixos que se
   * compõem — o estado (o cartão) e o valor do eixo (a linha) —, e a migalha
   * precisa conseguir dizer os dois, porque foi clicando um e depois o outro que
   * o operador chegou ali.
   *
   * ⚠️ **Por que não é `useState`:** a linha da lista leva para o detalhe da
   * sessão, que é OUTRA rota (`/analise/sessions`). Estado de componente morre na
   * navegação, então o voltar do browser devolvia a tela no consolidado e o
   * operador perdia o recorte que tinha montado — achado do dono na conferência
   * de 2026-09-09. No endereço, o voltar o reconstrói e o link vira partilhável.
   *
   * ⚠️ **`key` ausente ≠ `key` vazia.** String vazia é o grupo *sem dono*, que é
   * linha legítima; `has('key')` distingue as duas, `get('key') || null` as
   * colapsaria — o mesmo `if not x` × `is None` da § Postura, aqui na leitura de
   * um parâmetro que a fonte produz vazio de propósito.
   */
  const [params, setParams] = useSearchParams()
  const location = useLocation()

  const axis: GroupAxis = params.get('axis') === 'pool' ? 'pool' : 'agent'

  const drill = useMemo(() => {
    const st = params.get('state')
    // Endereço é entrada de FORA: um `state` digitado à mão que não seja recorte
    // conhecido não pode virar filtro que não casa nada e parecer "sem pendência".
    if (!st || !RECORTES_VALIDOS.includes(st as DrillState)) return null
    return { state: st as DrillState, key: params.has('key') ? params.get('key')! : null }
  }, [params])

  const irPara = useCallback((proximo: {
    axis?: GroupAxis; state?: DrillState | null; key?: string | null
  }) => {
    const p = new URLSearchParams(params)
    if (proximo.axis !== undefined) p.set('axis', proximo.axis)
    if (proximo.state !== undefined) {
      if (proximo.state === null) { p.delete('state'); p.delete('key') }
      else p.set('state', proximo.state)
    }
    if (proximo.key !== undefined) {
      if (proximo.key === null) p.delete('key')
      else p.set('key', proximo.key)
    }
    setSearch('')
    setParams(p)
  }, [params, setParams])
  const [confirm,   setConfirm]   = useState<PendingWorkTask | null>(null)
  const [busy,      setBusy]      = useState<string | null>(null)
  /** Resultado da ÚLTIMA tentativa de encerrar — separado do erro de carga. */
  const [actionError, setActionError] = useState<string | null>(null)

  // MOD-05: a fila de trabalho e onde o agente RECLAMA trabalho — atender, nao observar.
  const canView   = perms.can('agent_assist', 'atender')
  // A LEITURA é governada pelo ABAC da tela; a AÇÃO é mais estreita. Esconder o botão
  // de quem não pode usá-lo evita oferecer uma ação que só falharia no servidor.
  //
  // AUT-38 (2026-09-08): era uma lista de PAPÉIS espelhando a allowlist do endpoint.
  // O endpoint passou a exigir `agent_assist.supervisionar`, então a tela pergunta o
  // MESMO fato — duas casas para a mesma decisão só têm um valor: o da que ninguém
  // confere. E a lista de papéis carregava o defeito do servidor: `roles` é array, e
  // quem gateia por papel acaba dependendo da ordem dele.
  const canExpire = perms.can('agent_assist', 'supervisionar', 'read_write')

  const load = useCallback(async () => {
    try {
      const r = await fetchPending()
      setItems(r.items ?? [])
      setMeta({ scanned: r.scanned, truncated: r.truncated, at: r.generated_at })
      setError('')
    } catch (e) {
      setError(t('errors.loadFailed'))
      console.error(e)
    } finally {
      setLoading(false)
    }
  }, [t])

  useEffect(() => {
    if (!canView) { setLoading(false); return }
    void load()
    const id = setInterval(() => { void load() }, POLL_MS)
    return () => clearInterval(id)
  }, [canView, load])

  // Diretório: uma vez. Falha => nomes indisponíveis, e a tela DIZ o porquê.
  useEffect(() => {
    if (!canView) return
    let alive = true
    void (async () => {
      const token = await getAccessToken()
      const map   = await fetchDirectory(tenantId, token ?? '')
      if (!alive) return
      setDirectory(map)
      setDirDenied(map === null)
    })()
    return () => { alive = false }
  }, [canView, tenantId, getAccessToken])

  const displayName = useCallback((userId: string | null): string => {
    if (!userId) return t('group.unassigned')
    return directory?.get(userId) ?? userId
  }, [directory, t])

  /** Só existe sob drill: fora dele a tela não mostra linha nenhuma. */
  const filtered = useMemo(() => {
    if (!drill) return []
    const q = search.trim().toLowerCase()
    return items.filter(i =>
      casaODrill(i, drill.state) &&
      (drill.key === null ||
        (axis === 'pool' ? i.pool_id : (i.assigned_to ?? '')) === drill.key) &&
      (!q ||
        i.pool_id.toLowerCase().includes(q) ||
        i.session_id.toLowerCase().includes(q) ||
        displayName(i.assigned_to).toLowerCase().includes(q))
    )
  }, [items, drill, axis, search, displayName])

  /** Agrupa pelo eixo escolhido. Item sem dono NÃO some — vira grupo próprio. */
  const groups = useMemo(() => {
    const m = new Map<string, PendingWorkTask[]>()
    for (const i of filtered) {
      const key = axis === 'pool' ? i.pool_id : (i.assigned_to ?? '')
      const arr = m.get(key)
      if (arr) arr.push(i); else m.set(key, [i])
    }
    return [...m.entries()].sort((a, b) => b[1].length - a[1].length)
  }, [filtered, axis])

  /**
   * F2 — a recusa da Fase F é LIDA, não despejada. O 409 nomeado (`in_flight` /
   * `terminal`) vira frase; qualquer outra falha vira a genérica com o status.
   *
   * A sentença é desta tela porque o que se perde é diferente do lado do agente:
   * aqui NADA foi alterado (o supervisor não tinha trabalho em curso), enquanto
   * lá as respostas digitadas não foram salvas. Os FATOS (quem/por quê/quando)
   * são compartilhados e vêm do helper.
   */
  const runExpire = async (item: PendingWorkTask) => {
    setBusy(item.session_id); setActionError(null)
    try {
      const token = await getAccessToken()
      await expirePending(item.session_id, token ?? '')
      await load()
    } catch (e) {
      if (e instanceof ExpirePendingError && e.conflict) {
        const head = e.conflict.state === 'terminal'
          ? t('errors.conflictTerminal')
          : t('errors.conflictInFlight')
        const facts = resumeConflictDetails(e.conflict, t)
        setActionError(facts ? `${head} ${facts}` : head)
        // A recusa é informação sobre a lista: no ramo `terminal` o item já não
        // existe. Recarregar evita oferecer de novo um botão que só falharia.
        await load()
      } else {
        const status = e instanceof ExpirePendingError ? e.status : 0
        setActionError(t('errors.expireFailed', { status }))
        console.error(e)
      }
    } finally {
      setBusy(null); setConfirm(null)
    }
  }

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
        <h1 className="text-lg font-semibold text-dark">{t('title')}</h1>
        <p className="text-sm text-muted mt-0.5">{t('info')}</p>
        <p className="text-xs text-muted-light mt-1">{t('windowNote')}</p>

        {/* Sob drill a truncagem morde a LISTA; no consolidado ela morde os
            CONTADORES, e lá a frase é outra (`summary.partial`). */}
        {meta?.truncated && drill && (
          <p className="mt-2 text-xs text-warning-text bg-warning-light rounded px-2 py-1">
            {t('truncated', { scanned: meta.scanned })}
          </p>
        )}
        {dirDenied && (
          <p className="mt-2 text-xs text-muted bg-surface-alt rounded px-2 py-1">
            {t('directoryUnavailable')}
          </p>
        )}
        {actionError && (
          <div
            role="alert"
            className="mt-2 flex items-start gap-2 text-xs text-red-text bg-red-light border border-red/30 rounded px-2 py-1"
          >
            <span className="flex-1">{actionError}</span>
            <button
              type="button"
              onClick={() => setActionError(null)}
              aria-label={t('actions.cancel')}
              className="text-red-text/70 hover:text-red-text leading-none"
            >
              ×
            </button>
          </div>
        )}

        {/*
          PUL-04 — o seletor de EIXO fica (é dimensão: por agente × por pool), e a
          busca só aparece sob drill. Os chips de estado saíram: o cartão do
          consolidado já é aquele recorte, e ainda diz o tamanho antes do clique.
        */}
        <div className="flex items-center gap-3 mt-3 flex-wrap">
          {drill && (
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder={t('searchPlaceholder')}
              className="flex-1 min-w-[200px] text-sm border border-border-strong rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary/40"
            />
          )}
          <div className="flex gap-1 rounded-lg border border-border-strong overflow-hidden">
            {(['agent', 'pool'] as GroupAxis[]).map(a => (
              <button key={a} type="button"
                onClick={() => irPara({ axis: a, state: null, key: null })}
                className={`px-3 py-1.5 text-xs transition-colors ${axis === a
                  ? 'bg-primary text-white'
                  : 'bg-white text-muted hover:text-dark'}`}>
                {t(`axis.${a}`)}
              </button>
            ))}
          </div>
        </div>

        {drill && (
          <div className="flex items-center gap-2 mt-3 text-sm">
            <button type="button"
              onClick={() => irPara({ state: null, key: null })}
              className="text-primary hover:underline">
              ← {t('summary.back')}
            </button>
            <span className="text-muted-light">/</span>
            <span className="text-muted">{rotuloDoEstado(drill.state, t)}</span>
            {drill.key !== null && (
              <>
                <span className="text-muted-light">/</span>
                <span className="font-medium text-dark">
                  {axis === 'pool' ? (drill.key || '—') : displayName(drill.key || null)}
                </span>
              </>
            )}
          </div>
        )}
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {loading && <div className="flex justify-center py-8"><Spinner /></div>}
        {error && <p className="text-sm text-red-text">{error}</p>}
        {/*
          ⚠️ O vazio NÃO passa pelo `EmptyState` genérico quando a carga falhou: o
          `error` acima já ocupa esse lugar. Zero por falha e zero por ausência têm
          de ficar distinguíveis — foi a confusão entre os dois que a ORQ-10 mediu
          na aba irmã, com 51 processos escondidos atrás de um "nenhum item".
          Fora do drill, o consolidado responde inclusive com todos os contadores
          em zero, que é uma AFIRMAÇÃO.
        */}
        {!loading && !error && !drill && (
          <ConsolidadoDePendencias
            items={items}
            axis={axis}
            truncated={meta?.truncated ?? false}
            displayName={displayName}
            onDrill={(state, key) => irPara({ state, key })}
          />
        )}
        {!loading && drill && filtered.length === 0 && (
          <p className="text-sm text-muted-light italic text-center py-6">{t('emptyFiltered')}</p>
        )}

        {!loading && groups.map(([key, rows]) => (
          <div key={key || '__unassigned__'} className="bg-white border border-border rounded-xl overflow-hidden">
            <div className="flex items-center gap-2 px-4 py-2.5 border-b border-border bg-surface-alt/40">
              <span className="text-sm font-semibold text-dark">
                {axis === 'pool' ? key : displayName(key || null)}
              </span>
              <span className="text-xs bg-surface-alt text-muted px-2 py-0.5 rounded-full">
                {t('group.count', { count: rows.length })}
              </span>
              {rows.some(r => r.overdue) && (
                <span className="text-xs text-red-text">
                  ⚠ {t('group.overdue', { count: rows.filter(r => r.overdue).length })}
                </span>
              )}
              {axis === 'agent' && !key && (
                <span className="text-xs text-muted-light italic">{t('group.unassignedHint')}</span>
              )}
            </div>
            <div className="grid grid-cols-[1.2fr_auto_auto_auto_auto_auto] gap-3 px-3 py-1.5 text-2xs uppercase tracking-wide text-muted-light">
              <span>{t('col.session')}</span>
              <span>{t('col.state')}</span>
              <span>{t('col.age')}</span>
              <span>{t('col.deadline')}</span>
              <span>{t('col.holder')}</span>
              <span />
            </div>
            {rows.map(item => (
              <ItemRow
                key={item.session_id}
                item={item}
                origem={`${location.pathname}${location.search}`}
                canExpire={canExpire}
                busy={busy === item.session_id}
                onExpire={() => setConfirm(item)}
              />
            ))}
          </div>
        ))}
      </div>

      {confirm && (
        <ConfirmModal
          message={t('confirmExpire', {
            session: confirm.session_id.slice(0, 8),
            agent:   displayName(confirm.assigned_to),
          })}
          confirmLabel={t('actions.expire')}
          onCancel={() => setConfirm(null)}
          onConfirm={() => void runExpire(confirm)}
        />
      )}
    </div>
  )
}
