/**
 * WorkItemsPage
 * Rota: /monitor/work-items — pendências de wrap-up AGORA (I5 / ADR § D7b, fatia 1).
 *
 * Monitor = estado agregado ao vivo. Esta tela responde "quem está com wrap-up
 * pendente neste momento" e nada mais; o histórico ("quantos venceram no período")
 * é query sobre `segments` e vive no Analytics (fatia 2).
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
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'
import EmptyState from '@/components/ui/EmptyState'
import {
  PendingWorkTask, WorkTaskState, fetchPending, fetchDirectory, expirePending,
  fmtDuration, fmtDateTime,
} from './api'

const POLL_MS = 15_000

const STATE_FILTERS: (WorkTaskState | 'all')[] =
  ['all', 'unclaimed', 'claimed', 'orphaned', 'not_queued', 'unknown']

type GroupAxis = 'agent' | 'pool'

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

function ItemRow({ item, canExpire, onExpire, busy }: {
  item: PendingWorkTask; canExpire: boolean; onExpire: () => void; busy: boolean
}) {
  const { t } = useTranslation('workItems')
  return (
    <div className="grid grid-cols-[1.2fr_auto_auto_auto_auto_auto] gap-3 items-center px-3 py-2 bg-white rounded text-xs border-b border-border last:border-b-0">
      <Link
        to={`/analise/sessions?session_id=${item.session_id}`}
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

// ── Raiz ──────────────────────────────────────────────────────────────────────

export default function WorkItemsPage() {
  const { t } = useTranslation('workItems')
  const { session, tenantId, perms, currentUser, getAccessToken } = useAuth()

  const [items,     setItems]     = useState<PendingWorkTask[]>([])
  const [meta,      setMeta]      = useState<{ scanned: number; truncated: boolean; at: string } | null>(null)
  const [loading,   setLoading]   = useState(true)
  const [error,     setError]     = useState('')
  const [directory, setDirectory] = useState<Map<string, string> | null>(null)
  const [dirDenied, setDirDenied] = useState(false)
  const [axis,      setAxis]      = useState<GroupAxis>('agent')
  const [stateFilter, setStateFilter] = useState<WorkTaskState | 'all'>('all')
  const [search,    setSearch]    = useState('')
  const [confirm,   setConfirm]   = useState<PendingWorkTask | null>(null)
  const [busy,      setBusy]      = useState<string | null>(null)

  const canView   = perms.can('contacts', 'operacao')
  // A LEITURA é governada pelo ABAC da tela; a AÇÃO é mais estreita (o endpoint
  // exige supervisor|admin). Esconder o botão de quem não pode usá-lo evita
  // oferecer uma ação que só falharia no servidor.
  const canExpire = (currentUser?.roles ?? []).some(r => r === 'supervisor' || r === 'admin')

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

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: items.length }
    for (const i of items) c[i.state] = (c[i.state] ?? 0) + 1
    return c
  }, [items])

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    return items.filter(i =>
      (stateFilter === 'all' || i.state === stateFilter) &&
      (!q ||
        i.pool_id.toLowerCase().includes(q) ||
        i.session_id.toLowerCase().includes(q) ||
        displayName(i.assigned_to).toLowerCase().includes(q))
    )
  }, [items, stateFilter, search, displayName])

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

  const runExpire = async (item: PendingWorkTask) => {
    setBusy(item.session_id)
    try {
      const token = await getAccessToken()
      await expirePending(item.session_id, token ?? '')
      await load()
    } catch (e) {
      alert(String(e))
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

        {meta?.truncated && (
          <p className="mt-2 text-xs text-warning-text bg-warning-light rounded px-2 py-1">
            {t('truncated', { scanned: meta.scanned })}
          </p>
        )}
        {dirDenied && (
          <p className="mt-2 text-xs text-muted bg-surface-alt rounded px-2 py-1">
            {t('directoryUnavailable')}
          </p>
        )}

        <div className="flex items-center gap-3 mt-3 flex-wrap">
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder={t('searchPlaceholder')}
            className="flex-1 min-w-[200px] text-sm border border-border-strong rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary/40"
          />
          <div className="flex gap-1 rounded-lg border border-border-strong overflow-hidden">
            {(['agent', 'pool'] as GroupAxis[]).map(a => (
              <button key={a} type="button" onClick={() => setAxis(a)}
                className={`px-3 py-1.5 text-xs transition-colors ${axis === a
                  ? 'bg-primary text-white'
                  : 'bg-white text-muted hover:text-dark'}`}>
                {t(`axis.${a}`)}
              </button>
            ))}
          </div>
          <div className="flex gap-1 flex-wrap">
            {STATE_FILTERS.map(s => (
              <button key={s} type="button" onClick={() => setStateFilter(s)}
                className={`px-2.5 py-1 text-xs rounded-full border transition-colors ${stateFilter === s
                  ? 'bg-primary text-white border-primary'
                  : 'bg-white text-muted border-border-strong hover:text-dark'}`}>
                {s === 'all' ? t('filterAll') : t(`state.${s}`)}
                {counts[s] != null && <span className="ml-1 opacity-70">({counts[s]})</span>}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {loading && <div className="flex justify-center py-8"><Spinner /></div>}
        {error && <p className="text-sm text-red-text">{error}</p>}
        {!loading && items.length === 0 && !error && (
          <EmptyState icon="✅" title={t('empty')} description={t('emptyHint')} />
        )}
        {!loading && items.length > 0 && filtered.length === 0 && (
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
