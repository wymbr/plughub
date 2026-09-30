/**
 * RulesPage
 * Route: /config/rules — regras de escalação do rules-engine (RUL-03).
 *
 * Grant-first: `config.rules` — `read_only` vê, `read_write` cria, edita, apaga e muda o
 * status. Editar e apagar só em draft/disabled (decisão do dono, 2026-09-30): regra que
 * age ou mede não muda por baixo; o caminho é levá-la a disabled e percorrer o ciclo de
 * novo. As transições oferecidas vêm do servidor (`/lifecycle`), não de cópia local.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'
import EmptyState from '@/components/ui/EmptyState'
import {
  Condition, Lifecycle, Logic, Operator, Parameter, Rule, RuleBody, RuleStatus,
  fetchPoolIds, makeRulesApi,
} from './api'

const PARAMETERS: Parameter[] = ['sentiment_score', 'intent_confidence', 'turn_count', 'elapsed_ms', 'flag']
const NUMERIC_OPERATORS: Operator[] = ['lt', 'lte', 'gt', 'gte', 'eq', 'neq']
const FLAGS = ['human_requested', 'sensitive_topic', 'policy_limit_hit', 'handoff_requested']
const RULE_ID_RE = /^[a-z0-9_]{3,64}$/

const inputCls = 'w-full text-sm border border-border-strong rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-primary/40 bg-white'

function StatusPill({ status }: { status: RuleStatus }) {
  const { t } = useTranslation('rules')
  const styles: Record<RuleStatus, string> = {
    draft:    'bg-surface-alt text-muted',
    dry_run:  'bg-primary-light text-primary',
    shadow:   'bg-warning-light text-warning-text',
    active:   'bg-green/10 text-green',
    disabled: 'bg-red-light text-red-text',
  }
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${styles[status]}`}>
      {t(`status.${status}`)}
    </span>
  )
}

function describeCondition(c: Condition, t: TFunction): string {
  if (c.parameter === 'flag') return t('cond.flagIs', { flag: c.flag_name ?? '?' })
  const win = c.window_turns ? ` ${t('cond.window', { n: c.window_turns })}` : ''
  return `${t(`param.${c.parameter}`)}${win} ${t(`op.${c.operator}`)} ${c.value}`
}

function emptyCondition(): Condition {
  return { parameter: 'sentiment_score', operator: 'lt', value: -0.5 }
}

export default function RulesPage() {
  const { t } = useTranslation('rules')
  const { session, tenantId, perms } = useAuth()
  const api = useMemo(() => makeRulesApi(tenantId), [tenantId])
  const canRead = perms.can('config', 'rules')
  const canWrite = perms.can('config', 'rules', 'read_write')

  const [rules, setRules] = useState<Rule[]>([])
  const [lifecycle, setLifecycle] = useState<Lifecycle | null>(null)
  const [pools, setPools] = useState<string[]>([])
  const [poolsError, setPoolsError] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [editing, setEditing] = useState<Rule | 'new' | null>(null)
  const [delTarget, setDelTarget] = useState<Rule | null>(null)
  const [busy, setBusy] = useState('')

  const load = useCallback(async () => {
    setLoading(true); setError('')
    try {
      const [lc, list] = await Promise.all([api.lifecycle(), api.list()])
      setLifecycle(lc); setRules(list)
    } catch (e) {
      setError(t('errors.load', { detail: (e as Error).message }))
    } finally { setLoading(false) }
  }, [api, t])

  useEffect(() => {
    if (!session || !canRead) return
    load()
    fetchPoolIds(tenantId).then(setPools).catch(e => setPoolsError((e as Error).message))
  }, [session, canRead, load, tenantId])

  const editable = (r: Rule) => !!lifecycle?.editable_statuses.includes(r.status)

  async function transition(r: Rule, to: RuleStatus) {
    setBusy(r.rule_id); setError('')
    try { await api.setStatus(r.rule_id, to); await load() }
    catch (e) { setError(t('errors.status', { id: r.rule_id, detail: (e as Error).message })) }
    finally { setBusy('') }
  }

  async function confirmDelete() {
    if (!delTarget) return
    const id = delTarget.rule_id
    setDelTarget(null); setBusy(id); setError('')
    try { await api.remove(id); await load() }
    catch (e) { setError(t('errors.delete', { id, detail: (e as Error).message })) }
    finally { setBusy('') }
  }

  if (!session || !canRead) {
    return (
      <div className="flex items-center justify-center h-full">
        <p className="text-muted">{t('restricted')}</p>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full bg-surface-muted">
      <div className="bg-white flex-shrink-0 px-6 pt-4 pb-3 border-b border-border">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h1 className="text-lg font-semibold text-dark">{t('title')}</h1>
            <p className="text-sm text-muted mt-0.5">{t('info')}</p>
          </div>
          {canWrite && (
            <button onClick={() => setEditing('new')}
              className="px-4 py-2 text-sm bg-primary text-white rounded-lg hover:bg-primary-dark transition-colors flex-shrink-0">
              + {t('new')}
            </button>
          )}
        </div>
        {!canWrite && <p className="text-xs text-muted-light mt-2">{t('readOnly')}</p>}
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {loading && <div className="flex justify-center py-8"><Spinner /></div>}
        {error && <p className="text-sm text-red-text whitespace-pre-wrap">{error}</p>}

        {!loading && !error && rules.length === 0 && (
          <EmptyState icon="⚖️" title={t('empty.title')} description={t('empty.desc')} />
        )}

        {!loading && rules.map(r => (
          <div key={r.rule_id} className="px-4 py-3 bg-white border border-border rounded-xl">
            <div className="flex items-start gap-3">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <p className="text-sm font-semibold text-dark">{r.name}</p>
                  <span className="text-xs text-muted-light font-mono">{r.rule_id}</span>
                  <StatusPill status={r.status} />
                </div>
                <div className="flex items-center gap-2 mt-1.5 flex-wrap text-xs">
                  <span className="bg-surface-alt text-muted px-2 py-0.5 rounded-full">
                    🎯 {r.target_pool ?? t('card.noPool')}
                  </span>
                  <span className="bg-surface-alt text-muted px-2 py-0.5 rounded-full">
                    {t('card.priority', { n: r.priority })}
                  </span>
                  {r.customer_notice && (
                    <span className="bg-primary-light text-primary px-2 py-0.5 rounded-full">💬 {t('card.notice')}</span>
                  )}
                </div>
                <p className="text-xs text-muted mt-1.5">
                  {r.conditions.map(c => describeCondition(c, t)).join(` ${t(`logic.${r.logic}`)} `)}
                </p>
              </div>
              {canWrite && (
                <div className="flex gap-1 flex-shrink-0">
                  <button disabled={!editable(r) || !!busy} onClick={() => setEditing(r)}
                    title={editable(r) ? '' : t('card.lockedHint')}
                    className="px-3 py-1.5 text-xs text-primary hover:bg-primary-light rounded-lg disabled:opacity-40 disabled:hover:bg-transparent">
                    {t('actions.edit')}
                  </button>
                  <button disabled={!editable(r) || !!busy} onClick={() => setDelTarget(r)}
                    title={editable(r) ? '' : t('card.lockedHint')}
                    className="px-3 py-1.5 text-xs text-red hover:bg-red-light rounded-lg disabled:opacity-40 disabled:hover:bg-transparent">
                    {t('actions.delete')}
                  </button>
                </div>
              )}
            </div>
            {canWrite && lifecycle && (lifecycle.transitions[r.status] ?? []).length > 0 && (
              <div className="flex items-center gap-2 mt-2 pt-2 border-t border-border flex-wrap">
                <span className="text-xs text-muted-light">{t('card.moveTo')}</span>
                {lifecycle.transitions[r.status].map(to => (
                  <button key={to} disabled={!!busy} onClick={() => transition(r, to)}
                    className="px-2.5 py-1 text-xs border border-border rounded-lg text-dark hover:border-primary/40 disabled:opacity-40">
                    {t(`status.${to}`)}
                  </button>
                ))}
                {busy === r.rule_id && <Spinner />}
              </div>
            )}
          </div>
        ))}
      </div>

      {editing && (
        <RuleForm
          rule={editing === 'new' ? null : editing}
          pools={pools}
          poolsError={poolsError}
          taken={rules.map(r => r.rule_id)}
          onClose={() => setEditing(null)}
          onSave={async (id, body) => {
            if (editing === 'new') await api.create(id, body)
            else await api.update(editing.rule_id, body)
            setEditing(null)
            await load()
          }}
        />
      )}

      {delTarget && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-sm">
            <p className="text-sm text-dark mb-4">{t('confirm.delete', { name: delTarget.name })}</p>
            <div className="flex gap-2 justify-end">
              <button onClick={() => setDelTarget(null)} className="px-4 py-2 text-sm text-muted hover:text-dark">{t('actions.cancel')}</button>
              <button onClick={confirmDelete} className="px-4 py-2 text-sm bg-red text-white rounded-lg hover:bg-red-text">{t('actions.delete')}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Formulário ───────────────────────────────────────────────────────────────

function RuleForm({ rule, pools, poolsError, taken, onClose, onSave }: {
  rule: Rule | null
  pools: string[]
  poolsError: string
  taken: string[]
  onClose: () => void
  onSave: (id: string, body: RuleBody) => Promise<void>
}) {
  const { t } = useTranslation('rules')
  const [id, setId] = useState(rule?.rule_id ?? '')
  const [name, setName] = useState(rule?.name ?? '')
  const [logic, setLogic] = useState<Logic>(rule?.logic ?? 'AND')
  const [pool, setPool] = useState(rule?.target_pool ?? '')
  const [priority, setPriority] = useState(rule?.priority ?? 1)
  const [notice, setNotice] = useState(rule?.customer_notice ?? '')
  const [conds, setConds] = useState<Condition[]>(rule?.conditions ?? [emptyCondition()])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const idError = rule ? '' :
    !RULE_ID_RE.test(id) ? t('form.idInvalid') :
    taken.includes(id) ? t('form.idTaken') : ''

  function patch(i: number, c: Partial<Condition>) {
    setConds(cs => cs.map((x, k) => (k === i ? { ...x, ...c } : x)))
  }

  function setParameter(i: number, p: Parameter) {
    if (p === 'flag') patch(i, { parameter: p, operator: 'eq', flag_name: FLAGS[0], value: FLAGS[0], window_turns: null })
    else patch(i, { parameter: p, operator: 'lt', flag_name: null, value: 0,
                    window_turns: p === 'sentiment_score' ? conds[i].window_turns ?? null : null })
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (idError) return
    setSaving(true); setError('')
    try {
      await onSave(id, {
        name: name.trim(),
        conditions: conds.map(c => c.parameter === 'flag'
          ? { parameter: 'flag', operator: 'eq', flag_name: c.flag_name, value: c.flag_name ?? '' }
          : { parameter: c.parameter, operator: c.operator, value: Number(c.value),
              window_turns: c.window_turns || null }),
        logic,
        target_pool: pool || null,
        priority,
        customer_notice: notice.trim() || null,
      })
    } catch (err) {
      setError((err as Error).message)
    } finally { setSaving(false) }
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl max-h-[90vh] flex flex-col">
        <div className="flex items-center justify-between px-6 py-4 border-b border-border">
          <h2 className="text-base font-semibold text-dark">
            {rule ? `${t('actions.edit')} — ${rule.name}` : t('new')}
          </h2>
          <button onClick={onClose} className="text-muted-light hover:text-muted text-xl leading-none">×</button>
        </div>
        <form onSubmit={submit} className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <Field label={t('form.id')} hint={rule ? t('form.idFixed') : t('form.idHint')}>
              <input required value={id} disabled={!!rule} onChange={e => setId(e.target.value.trim())}
                className={`${inputCls} font-mono disabled:bg-surface-alt`} />
              {idError && id && <p className="text-xs text-red-text mt-1">{idError}</p>}
            </Field>
            <Field label={t('form.name')}>
              <input required value={name} onChange={e => setName(e.target.value)} className={inputCls} />
            </Field>
          </div>

          <Field label={t('form.conditions')} hint={t('form.conditionsHint')}>
            <div className="space-y-2">
              {conds.map((c, i) => (
                <div key={i} className="flex gap-2 items-center flex-wrap">
                  <select value={c.parameter} onChange={e => setParameter(i, e.target.value as Parameter)}
                    className={`${inputCls} w-44`}>
                    {PARAMETERS.map(p => <option key={p} value={p}>{t(`param.${p}`)}</option>)}
                  </select>
                  {c.parameter === 'flag' ? (
                    <select value={c.flag_name ?? ''} onChange={e => patch(i, { flag_name: e.target.value, value: e.target.value })}
                      className={`${inputCls} flex-1`}>
                      {FLAGS.map(f => <option key={f} value={f}>{t(`flag.${f}`)}</option>)}
                    </select>
                  ) : (
                    <>
                      <select value={c.operator} onChange={e => patch(i, { operator: e.target.value as Operator })}
                        className={`${inputCls} w-28`}>
                        {NUMERIC_OPERATORS.map(o => <option key={o} value={o}>{t(`op.${o}`)}</option>)}
                      </select>
                      <input type="number" step="any" required value={String(c.value)}
                        onChange={e => patch(i, { value: e.target.value })} className={`${inputCls} w-28`} />
                      {c.parameter === 'sentiment_score' && (
                        <input type="number" min={1} placeholder={t('form.window')} value={c.window_turns ?? ''}
                          onChange={e => patch(i, { window_turns: e.target.value ? Number(e.target.value) : null })}
                          className={`${inputCls} w-32`} title={t('form.windowHint')} />
                      )}
                    </>
                  )}
                  <button type="button" disabled={conds.length === 1}
                    onClick={() => setConds(cs => cs.filter((_, k) => k !== i))}
                    className="text-muted-light hover:text-red text-sm disabled:opacity-30">×</button>
                </div>
              ))}
              <div className="flex items-center gap-3">
                <button type="button" onClick={() => setConds(cs => [...cs, emptyCondition()])}
                  className="text-xs text-primary hover:underline">+ {t('form.addCondition')}</button>
                {conds.length > 1 && (
                  <select value={logic} onChange={e => setLogic(e.target.value as Logic)} className={`${inputCls} w-56`}>
                    <option value="AND">{t('form.logicAnd')}</option>
                    <option value="OR">{t('form.logicOr')}</option>
                  </select>
                )}
              </div>
            </div>
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <Field label={t('form.targetPool')} hint={poolsError ? t('form.poolsError', { detail: poolsError }) : t('form.targetPoolHint')}>
              <select value={pool} onChange={e => setPool(e.target.value)} className={inputCls}>
                <option value="">{t('form.noPool')}</option>
                {pool && !pools.includes(pool) && <option value={pool}>{pool}</option>}
                {pools.map(p => <option key={p} value={p}>{p}</option>)}
              </select>
            </Field>
            <Field label={t('form.priority')} hint={t('form.priorityHint')}>
              <input type="number" min={1} max={10} required value={priority}
                onChange={e => setPriority(Number(e.target.value))} className={inputCls} />
            </Field>
          </div>

          <Field label={t('form.notice')} hint={t('form.noticeHint', { n: 500 - notice.length })}>
            <textarea value={notice} maxLength={500} rows={3} onChange={e => setNotice(e.target.value)} className={inputCls} />
          </Field>

          {error && <p className="text-sm text-red-text whitespace-pre-wrap">{error}</p>}

          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={onClose} className="px-4 py-2 text-sm text-muted hover:text-dark">{t('actions.cancel')}</button>
            <button type="submit" disabled={saving || !!idError}
              className="px-4 py-2 text-sm bg-primary text-white rounded-lg hover:bg-primary-dark disabled:opacity-50">
              {saving ? t('actions.saving') : t('actions.save')}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs font-medium text-dark mb-1">{label}</label>
      {children}
      {hint && <p className="text-xs text-muted-light mt-1">{hint}</p>}
    </div>
  )
}
