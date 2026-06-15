/**
 * PoolsPage.tsx — CRUD for routing pools
 *
 * Form opens as a right-side Drawer (replaces Modal).
 * routing_weights section:
 *   Fixos     — per-competency-skill weight (0-9) loaded from /config/competency_skills
 *   Dinâmicos — dynamic queue scoring factors (0-9 integer scale)
 */
import React, { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import * as registryApi from '@/api/registry'
import {
  Pool,
  RoutingWeights,
  ROUTING_WEIGHTS_DEFAULTS,
  RoutingWeightsDinamicos,
  PoolHooks,
  PoolHookEntry,
  PoolHookSide,
} from '@/types'
import Button from '@/components/ui/Button'
import Table from '@/components/ui/Table'
import Input from '@/components/ui/Input'
import Select from '@/components/ui/Select'
import Badge from '@/components/ui/Badge'
import EmptyState from '@/components/ui/EmptyState'
import Spinner from '@/components/ui/Spinner'

// ── types ──────────────────────────────────────────────────────────────────────

interface CalendarOption { id: string; name: string }
interface CompetencySkill { key: string; domain: number }

interface TimeInterval { open: string; close: string }
interface ExceptionEntry {
  date:           string               // "YYYY-MM-DD"
  label:          string
  override_slots: TimeInterval[] | null // null = closed all day
}

// ── PoolExceptionsEditor ──────────────────────────────────────────────────────
// Compact inline editor for pool-level exception overrides.

function PoolExceptionsEditor({
  exceptions, onChange,
}: { exceptions: ExceptionEntry[]; onChange: (e: ExceptionEntry[]) => void }) {
  const { t } = useTranslation('configRecursos')
  const [newDate,  setNewDate]  = useState('')
  const [newLabel, setNewLabel] = useState('')
  const [closed,   setClosed]   = useState(true)
  const [newSlots, setNewSlots] = useState<TimeInterval[]>([{ open: '08:00', close: '18:00' }])

  const add = () => {
    if (!newDate) return
    onChange([...exceptions, {
      date:           newDate,
      label:          newLabel.trim(),
      override_slots: closed ? null : [...newSlots],
    }])
    setNewDate(''); setNewLabel(''); setClosed(true)
    setNewSlots([{ open: '08:00', close: '18:00' }])
  }

  const remove = (i: number) => onChange(exceptions.filter((_, idx) => idx !== i))

  const updateSlot = (idx: number, field: 'open' | 'close', val: string) =>
    setNewSlots(prev => prev.map((s, i) => i === idx ? { ...s, [field]: val } : s))

  return (
    <div className="space-y-2">
      {/* Existing list */}
      <div className="max-h-36 overflow-y-auto space-y-1">
        {exceptions.length === 0 ? (
          <p className="text-xs text-muted-light italic">{t('pools.exceptions.none')}</p>
        ) : exceptions.map((exc, i) => (
          <div key={i} className="flex items-start gap-2 px-2 py-1.5 bg-contested-light border border-contested/20 rounded text-sm">
            <span className="font-mono text-xs text-muted w-24 flex-shrink-0 pt-0.5">{exc.date}</span>
            <div className="flex-1 min-w-0">
              {exc.label && <p className="text-xs text-dark truncate mb-0.5">{exc.label}</p>}
              {exc.override_slots === null ? (
                <span className="text-xs text-red-text font-medium">{t('pools.exceptions.closedAllDay')}</span>
              ) : (
                <span className="text-xs text-contested-text">
                  {exc.override_slots.map(s => `${s.open}–${s.close}`).join(', ')}
                </span>
              )}
            </div>
            <button type="button" onClick={() => remove(i)}
              className="text-red hover:text-red-text text-xs flex-shrink-0 pt-0.5">✕</button>
          </div>
        ))}
      </div>

      {/* Add form */}
      <div className="border border-dashed border-contested/30 rounded-lg p-3 space-y-2 bg-contested-light/40">
        <div className="flex gap-2">
          <input type="date" value={newDate} onChange={e => setNewDate(e.target.value)}
            className="text-sm border border-border-strong rounded px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-contested/40 bg-white" />
          <input type="text" placeholder={t('pools.exceptions.descPlaceholder')} value={newLabel}
            onChange={e => setNewLabel(e.target.value)} onKeyDown={e => e.key === 'Enter' && add()}
            className="flex-1 text-sm border border-border-strong rounded px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-contested/40 bg-white" />
        </div>
        <div className="flex items-center gap-4">
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="radio" checked={closed} onChange={() => setClosed(true)}
              className="text-red focus:ring-red/40" />
            <span className="text-xs text-dark">{t('pools.exceptions.closedAllDay')}</span>
          </label>
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="radio" checked={!closed} onChange={() => setClosed(false)}
              className="text-contested focus:ring-contested/40" />
            <span className="text-xs text-dark">{t('pools.exceptions.specialHours')}</span>
          </label>
        </div>
        {!closed && (
          <div className="space-y-1 pl-1">
            {newSlots.map((sl, idx) => (
              <div key={idx} className="flex items-center gap-2">
                <input type="time" value={sl.open} onChange={e => updateSlot(idx, 'open', e.target.value)}
                  className="text-sm border border-border-strong rounded px-2 py-1 bg-white focus:outline-none focus:ring-1 focus:ring-contested/40" />
                <span className="text-xs text-muted-light">{t('pools.exceptions.to')}</span>
                <input type="time" value={sl.close} onChange={e => updateSlot(idx, 'close', e.target.value)}
                  className="text-sm border border-border-strong rounded px-2 py-1 bg-white focus:outline-none focus:ring-1 focus:ring-contested/40" />
                {newSlots.length > 1 && (
                  <button type="button" onClick={() => setNewSlots(prev => prev.filter((_, i) => i !== idx))}
                    className="text-muted-light hover:text-red text-sm leading-none">×</button>
                )}
              </div>
            ))}
            {newSlots.length < 4 && (
              <button type="button"
                onClick={() => setNewSlots(prev => [...prev, { open: prev[prev.length-1]?.close ?? '18:00', close: '23:00' }])}
                className="text-xs text-contested hover:text-contested-text mt-0.5">{t('pools.exceptions.addInterval')}</button>
            )}
          </div>
        )}
        <button type="button" onClick={add} disabled={!newDate}
          className="w-full px-3 py-1.5 text-xs bg-contested text-white rounded-lg hover:bg-contested-text disabled:opacity-50 transition-colors">
          {t('pools.exceptions.add')}
        </button>
      </div>
    </div>
  )
}

// ── Hooks editor ───────────────────────────────────────────────────────────────
// Editor for one hook slot (a list of { pool, side, nps_on_disconnect } entries).
// The stored reference is a pool_id (stable across skill-flow version changes);
// the option label surfaces the pool's currently deployed skill as a visual hint.

const EMPTY_HOOKS: PoolHooks = { on_human_start: [], on_human_end: [], on_contact_end: [], post_human: [] }

interface PoolOption { value: string; label: string }

function HookListEditor({
  entries, onChange, poolOptions, defaultSide = 'agent',
}: {
  entries:     PoolHookEntry[]
  onChange:    (e: PoolHookEntry[]) => void
  poolOptions: PoolOption[]
  /** G7 Fase 3b-ii: novas entries de on_contact_end nascem side=customer (NPS). */
  defaultSide?: PoolHookSide
}) {
  const { t } = useTranslation('configRecursos')

  const add = () =>
    onChange([...entries, { pool: '', side: defaultSide, nps_on_disconnect: 'timeout' }])

  const remove = (i: number) => onChange(entries.filter((_, idx) => idx !== i))

  const update = (i: number, patch: Partial<PoolHookEntry>) =>
    onChange(entries.map((e, idx) => idx === i ? { ...e, ...patch } : e))

  return (
    <div className="space-y-2">
      {entries.length === 0 ? (
        <p className="text-xs text-muted-light italic">{t('pools.hooks.none')}</p>
      ) : entries.map((entry, i) => (
        <div key={i} className="flex flex-col gap-1.5 px-2 py-2 bg-surface-muted border border-border rounded">
          <div className="flex items-center gap-2">
            <select
              value={entry.pool}
              onChange={e => update(i, { pool: e.target.value })}
              className="flex-1 min-w-0 text-sm border border-border-strong rounded px-2 py-1.5 bg-white focus:outline-none focus:ring-1 focus:ring-primary/40"
            >
              <option value="">{t('pools.hooks.selectPool')}</option>
              {poolOptions.map(o => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
            <select
              value={entry.side}
              onChange={e => update(i, { side: e.target.value as PoolHookSide })}
              className="w-28 text-sm border border-border-strong rounded px-2 py-1.5 bg-white focus:outline-none focus:ring-1 focus:ring-primary/40"
            >
              <option value="agent">{t('pools.hooks.sideAgent')}</option>
              <option value="customer">{t('pools.hooks.sideCustomer')}</option>
            </select>
            <button type="button" onClick={() => remove(i)}
              className="text-red hover:text-red-text text-sm flex-shrink-0">✕</button>
          </div>
          {entry.side === 'customer' && (
            <div className="flex items-center gap-2 pl-1">
              <span className="text-2xs text-muted-light">{t('pools.hooks.npsOnDisconnect')}:</span>
              <select
                value={entry.nps_on_disconnect}
                onChange={e => update(i, { nps_on_disconnect: e.target.value as PoolHookEntry['nps_on_disconnect'] })}
                className="text-xs border border-border-strong rounded px-2 py-1 bg-white focus:outline-none focus:ring-1 focus:ring-primary/40"
              >
                <option value="timeout">{t('pools.hooks.npsTimeout')}</option>
                <option value="skip">{t('pools.hooks.npsSkip')}</option>
              </select>
            </div>
          )}
        </div>
      ))}
      <button type="button" onClick={add}
        className="text-xs text-secondary hover:text-primary transition-colors">
        {t('pools.hooks.add')}
      </button>
    </div>
  )
}

// ── Transfer destinations editor (escalation_pools: string[]) ───────────────────
// A list of pool_ids the human agent can escalate/transfer the contact to.

function PoolListEditor({
  values, onChange, poolOptions, addLabel, noneLabel, selectLabel,
}: {
  values:      string[]
  onChange:    (v: string[]) => void
  poolOptions: PoolOption[]
  addLabel:    string
  noneLabel:   string
  selectLabel: string
}) {
  const add    = () => onChange([...values, ''])
  const remove = (i: number) => onChange(values.filter((_, idx) => idx !== i))
  const update = (i: number, val: string) => onChange(values.map((v, idx) => idx === i ? val : v))

  return (
    <div className="space-y-2">
      {values.length === 0 ? (
        <p className="text-xs text-muted-light italic">{noneLabel}</p>
      ) : values.map((val, i) => (
        <div key={i} className="flex items-center gap-2">
          <select
            value={val}
            onChange={e => update(i, e.target.value)}
            className="flex-1 min-w-0 text-sm border border-border-strong rounded px-2 py-1.5 bg-white focus:outline-none focus:ring-1 focus:ring-primary/40"
          >
            <option value="">{selectLabel}</option>
            {poolOptions.map(o => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
          <button type="button" onClick={() => remove(i)}
            className="text-red hover:text-red-text text-sm flex-shrink-0">✕</button>
        </div>
      ))}
      <button type="button" onClick={add}
        className="text-xs text-secondary hover:text-primary transition-colors">
        {addLabel}
      </button>
    </div>
  )
}

// ── @mention specialists editor (mentionable_pools: alias → pool) ───────────────

interface MentionEntry { alias: string; pool: string }

function MentionListEditor({
  entries, onChange, poolOptions,
}: {
  entries:     MentionEntry[]
  onChange:    (e: MentionEntry[]) => void
  poolOptions: PoolOption[]
}) {
  const { t } = useTranslation('configRecursos')

  const add    = () => onChange([...entries, { alias: '', pool: '' }])
  const remove = (i: number) => onChange(entries.filter((_, idx) => idx !== i))
  const update = (i: number, patch: Partial<MentionEntry>) =>
    onChange(entries.map((e, idx) => idx === i ? { ...e, ...patch } : e))

  // When a pool is picked and the alias is still empty, suggest the pool_id as alias.
  const pickPool = (i: number, pool: string) =>
    onChange(entries.map((e, idx) =>
      idx === i ? { ...e, pool, alias: e.alias.trim() ? e.alias : pool } : e))

  return (
    <div className="space-y-2">
      {entries.length === 0 ? (
        <p className="text-xs text-muted-light italic">{t('pools.mention.none')}</p>
      ) : entries.map((entry, i) => (
        <div key={i} className="flex items-center gap-2">
          <div className="flex items-center gap-1 w-32 shrink-0">
            <span className="text-sm text-muted-light">@</span>
            <input
              type="text"
              value={entry.alias}
              placeholder={t('pools.mention.aliasPlaceholder')}
              onChange={e => update(i, { alias: e.target.value })}
              className="w-full min-w-0 text-sm border border-border-strong rounded px-2 py-1.5 bg-white focus:outline-none focus:ring-1 focus:ring-primary/40"
            />
          </div>
          <select
            value={entry.pool}
            onChange={e => pickPool(i, e.target.value)}
            className="flex-1 min-w-0 text-sm border border-border-strong rounded px-2 py-1.5 bg-white focus:outline-none focus:ring-1 focus:ring-primary/40"
          >
            <option value="">{t('pools.mention.selectPool')}</option>
            {poolOptions.map(o => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
          <button type="button" onClick={() => remove(i)}
            className="text-red hover:text-red-text text-sm flex-shrink-0">✕</button>
        </div>
      ))}
      <button type="button" onClick={add}
        className="text-xs text-secondary hover:text-primary transition-colors">
        {t('pools.mention.add')}
      </button>
    </div>
  )
}

// ── Drawer ────────────────────────────────────────────────────────────────────

function Drawer({
  isOpen,
  onClose,
  title,
  children,
  footer,
}: {
  isOpen:    boolean
  onClose:   () => void
  title:     string
  children:  React.ReactNode
  footer?:   React.ReactNode
}) {
  // close on Escape
  useEffect(() => {
    if (!isOpen) return
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [isOpen, onClose])

  return (
    <>
      {/* Backdrop */}
      <div
        className={`fixed inset-0 bg-black/30 z-40 transition-opacity duration-200 ${
          isOpen ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none'
        }`}
        onClick={onClose}
      />
      {/* Panel */}
      <div
        className={`fixed inset-y-0 right-0 w-[540px] bg-white shadow-2xl z-50 flex flex-col
          transform transition-transform duration-200 ease-in-out
          ${isOpen ? 'translate-x-0' : 'translate-x-full'}`}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-border shrink-0">
          <span className="font-semibold text-dark text-base">{title}</span>
          <button
            onClick={onClose}
            className="text-muted-light hover:text-dark text-xl leading-none w-7 h-7 flex items-center justify-center rounded hover:bg-surface-alt transition-colors"
            aria-label="Fechar"
          >
            ✕
          </button>
        </div>
        {/* Scrollable body */}
        <div className="flex-1 overflow-y-auto px-5 py-5">
          {children}
        </div>
        {/* Footer */}
        {footer && (
          <div className="flex justify-end gap-2 px-5 py-3.5 border-t border-border shrink-0 bg-surface-muted">
            {footer}
          </div>
        )}
      </div>
    </>
  )
}

// ── WeightSlider ──────────────────────────────────────────────────────────────

function WeightSlider({
  label,
  hint,
  value,
  onChange,
}: {
  label:    string
  hint:     string
  value:    number
  onChange: (v: number) => void
}) {
  const pct = Math.round((value / 9) * 100)
  return (
    <div className="flex items-center gap-3">
      <div className="w-32 shrink-0">
        <p className="text-xs font-medium text-dark leading-tight">{label}</p>
        <p className="text-2xs text-muted-light leading-tight">{hint}</p>
      </div>
      <input
        type="range"
        min={0}
        max={9}
        step={1}
        value={value}
        onChange={e => onChange(Number(e.target.value))}
        className="flex-1 accent-primary"
      />
      <span className="w-12 text-right text-xs font-mono font-bold text-dark shrink-0">
        {value} <span className="text-muted-light font-normal">/{pct}%</span>
      </span>
    </div>
  )
}

// ── PoolsPage ─────────────────────────────────────────────────────────────────

const CHANNEL_OPTIONS = [
  { value: 'webchat',   label: 'WebChat'   },
  { value: 'whatsapp',  label: 'WhatsApp'  },
  { value: 'voice',     label: 'Voice'     },
  { value: 'email',     label: 'Email'     },
  { value: 'sms',       label: 'SMS'       },
  { value: 'instagram', label: 'Instagram' },
  { value: 'telegram',  label: 'Telegram'  },
  { value: 'webrtc',    label: 'WebRTC'    },
]

const PoolsPage: React.FC = () => {
  const { session } = useAuth()
  const { t } = useTranslation('configRecursos')
  const { t: tCommon } = useTranslation('common')

  const DINAMICOS_META: Array<{
    key: keyof RoutingWeightsDinamicos
    label: string
    hint: string
    default: number
  }> = [
    { key: 'sla',      label: t('pools.routingDynamic.sla.label'),      hint: t('pools.routingDynamic.sla.hint'),      default: 9 },
    { key: 'wait',     label: t('pools.routingDynamic.wait.label'),     hint: t('pools.routingDynamic.wait.hint'),     default: 7 },
    { key: 'tier',     label: t('pools.routingDynamic.tier.label'),     hint: t('pools.routingDynamic.tier.hint'),     default: 5 },
    { key: 'churn',    label: t('pools.routingDynamic.churn.label'),    hint: t('pools.routingDynamic.churn.hint'),    default: 8 },
    { key: 'business', label: t('pools.routingDynamic.business.label'), hint: t('pools.routingDynamic.business.hint'), default: 3 },
  ]

  const [pools,     setPools]     = useState<Pool[]>([])
  const [skillFlows, setSkillFlows] = useState<Array<{ skill_id: string; name: string }>>([])
  const [calendars, setCalendars] = useState<CalendarOption[]>([])
  const [competencySkills, setCompetencySkills] = useState<CompetencySkill[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [isOpen,    setIsOpen]    = useState(false)
  const [editingPool, setEditingPool] = useState<Pool | null>(null)
  const [isSaving, setIsSaving]   = useState(false)
  const [error,    setError]      = useState('')

  // Pool-level calendar exceptions (Level 2: association exceptions override calendar exceptions)
  const [calExceptions, setCalExceptions] = useState<ExceptionEntry[]>([])

  const [formData, setFormData] = useState({
    pool_id:           '',
    description:       '',
    // Type & capacity (capacity-governance): agent_kind governs queue⇒human and
    // the AI/human capacity gates; '' = inferred by the registry backfill.
    agent_kind:        '' as '' | 'human' | 'ai',
    dispatch_mode:     'push' as 'push' | 'pull',
    session_reservation:         null as number | null,
    channel_types:     [] as string[],
    sla_target_ms:     30000,
    max_reply_time_ms:           null as number | null,
    calendar_id:                 '',
    context_visibility_ns:       '' as string,   // comma-separated operator_namespaces
    routing_weights:             { ...ROUTING_WEIGHTS_DEFAULTS } as RoutingWeights,
    // Queue treatment (queue-attended-model, skill-first): flow de fila + teto
    queue_skill_id:              '',
    queue_max_wait_s:            null as number | null,
    // Lifecycle hooks (wrap-up / NPS / post) — pool references per slot
    hooks:                       { ...EMPTY_HOOKS } as PoolHooks,
    // Transfer/escalate destinations (supervisor_config.escalation_pools)
    escalation_pools:            [] as string[],
    // @mention specialists (mentionable_pools) as an editable alias→pool list
    mention_pools:               [] as MentionEntry[],
  })

  // ── data loading ─────────────────────────────────────────────────────────────

  const loadCalendars = useCallback(async () => {
    if (!session) return
    try {
      const params = new URLSearchParams({
        organization_id: session.tenantId,
        tenant_id:       session.tenantId,
      })
      const res = await fetch(`/v1/calendars?${params}`)
      if (res.ok) {
        const data = await res.json() as Array<{ id: string; name: string }>
        setCalendars((data ?? []).map(c => ({ id: c.id, name: c.name })))
      }
    } catch { /* stale ok */ }
  }, [session])

  /** Fetch the calendar-api association for this pool and return its exceptions. */
  const loadPoolAssociation = useCallback(async (poolId: string): Promise<ExceptionEntry[]> => {
    if (!session) return []
    try {
      const params = new URLSearchParams({
        tenant_id:   session.tenantId,
        entity_type: 'pool',
        entity_id:   poolId,
      })
      const res = await fetch(`/v1/associations?${params}`)
      if (!res.ok) return []
      const assocs = await res.json() as Array<{ exceptions?: ExceptionEntry[] }>
      // Return exceptions from the first (primary) association
      return assocs[0]?.exceptions ?? []
    } catch { return [] }
  }, [session])

  const loadCompetencySkills = useCallback(async () => {
    if (!session) return
    try {
      const res = await fetch(
        `/config/competency_skills?tenant_id=${encodeURIComponent(session.tenantId)}`
      )
      if (res.ok) {
        const data = await res.json() as { entries?: Record<string, unknown> }
        const raw = data.entries ?? {}
        const skills: CompetencySkill[] = Object.entries(raw).map(([k, v]) => {
          let domain = 5
          if (typeof v === 'number') domain = Math.min(9, Math.max(0, Math.round(v)))
          else if (typeof v === 'object' && v !== null) {
            const d = (v as Record<string, unknown>).domain
            if (typeof d === 'number') domain = Math.min(9, Math.max(0, Math.round(d)))
          }
          return { key: k, domain }
        })
        setCompetencySkills(skills.sort((a, b) => a.key.localeCompare(b.key)))
      }
    } catch { /* stale ok */ }
  }, [session])

  const loadPools = useCallback(async () => {
    if (!session) return
    setIsLoading(true)
    try {
      const result = await registryApi.listPools(session.tenantId)
      setPools(result.items || [])
    } catch {
      setError('Failed to load pools')
    } finally {
      setIsLoading(false)
    }
  }, [session])

  // Skill-flow catalog for the queue-treatment dropdown (skill-first).
  const loadSkillFlows = useCallback(async () => {
    if (!session) return
    try {
      const result = await registryApi.listSkills(session.tenantId)
      setSkillFlows(
        (result.items || []).map(s => ({ skill_id: s.skill_id, name: s.name || s.skill_id }))
      )
    } catch { /* stale ok */ }
  }, [session])

  useEffect(() => {
    void loadPools()
    void loadCalendars()
    void loadCompetencySkills()
    void loadSkillFlows()
  }, [loadPools, loadCalendars, loadCompetencySkills, loadSkillFlows])

  // ── form helpers ──────────────────────────────────────────────────────────────

  function buildDefaultWeights(pool?: Pool): RoutingWeights {
    const base = { ...ROUTING_WEIGHTS_DEFAULTS }
    if (!pool) return base
    // If pool has existing routing_weights, use those
    if (pool.routing_weights) return pool.routing_weights
    // Otherwise migrate routing_expression (floats) to 0-9 dinamicos
    if (pool.routing_expression) {
      const re = pool.routing_expression
      return {
        fixos: base.fixos,
        dinamicos: {
          sla:      Math.round(re.weight_sla      * 9),
          wait:     Math.round(re.weight_wait     * 9),
          tier:     Math.round(re.weight_tier     * 9),
          churn:    Math.round(re.weight_churn    * 9),
          business: Math.round(re.weight_business * 9),
        },
      }
    }
    return base
  }

  const handleOpenCreate = () => {
    setEditingPool(null)
    setFormData({
      pool_id: '', description: '', agent_kind: '', dispatch_mode: 'push', session_reservation: null,
      channel_types: [], sla_target_ms: 30000,
      max_reply_time_ms: null, calendar_id: '', context_visibility_ns: '',
      routing_weights: { ...ROUTING_WEIGHTS_DEFAULTS },
      queue_skill_id: '', queue_max_wait_s: null,
      hooks: { ...EMPTY_HOOKS },
      escalation_pools: [], mention_pools: [],
    })
    setCalExceptions([])
    setError('')
    setIsOpen(true)
  }

  const handleOpenEdit = (pool: Pool) => {
    setEditingPool(pool)
    setFormData({
      pool_id:         pool.pool_id,
      description:     pool.description || '',
      agent_kind:        pool.agent_kind ?? '',
      dispatch_mode:     (pool.dispatch_mode as 'push' | 'pull') ?? 'push',
      session_reservation:         pool.session_reservation ?? null,
      channel_types:     pool.channel_types,
      sla_target_ms:     pool.sla_target_ms,
      max_reply_time_ms:           pool.max_reply_time_ms ?? null,
      calendar_id:                 pool.calendar_id || '',
      context_visibility_ns:       (pool.context_visibility?.operator_namespaces ?? []).join(', '),
      routing_weights:             buildDefaultWeights(pool),
      queue_skill_id:              pool.queue_config?.skill_id ?? '',
      queue_max_wait_s:            pool.queue_config?.max_wait_s ?? null,
      hooks: {
        on_human_start: pool.hooks?.on_human_start ?? [],
        on_human_end:   pool.hooks?.on_human_end   ?? [],
        on_contact_end: pool.hooks?.on_contact_end ?? [],
        post_human:     pool.hooks?.post_human     ?? [],
      },
      escalation_pools: pool.supervisor_config?.escalation_pools ?? [],
      mention_pools: Object.entries(pool.mentionable_pools ?? {}).map(
        ([alias, p]) => ({ alias, pool: p }),
      ),
    })
    setCalExceptions([])  // will be loaded async below
    setError('')
    setIsOpen(true)
    // Load pool-level exceptions from calendar-api association
    void loadPoolAssociation(pool.pool_id).then(exc => setCalExceptions(exc))
  }

  const handleClose = () => { setIsOpen(false); setEditingPool(null) }

  const handleChannelToggle = (ch: string) =>
    setFormData(prev => ({
      ...prev,
      channel_types: prev.channel_types.includes(ch)
        ? prev.channel_types.filter(c => c !== ch)
        : [...prev.channel_types, ch],
    }))

  const setFixoWeight = (skillKey: string, val: number) =>
    setFormData(prev => ({
      ...prev,
      routing_weights: {
        ...prev.routing_weights,
        fixos: { ...prev.routing_weights.fixos, [skillKey]: val },
      },
    }))

  const setDinamicoWeight = (key: keyof RoutingWeightsDinamicos, val: number) =>
    setFormData(prev => ({
      ...prev,
      routing_weights: {
        ...prev.routing_weights,
        dinamicos: { ...prev.routing_weights.dinamicos, [key]: val },
      },
    }))

  const handleSubmit = async () => {
    if (!session || !formData.pool_id.trim()) {
      setError(t('pools.fields.poolId') + ' ' + tCommon('isRequired'))
      return
    }
    setIsSaving(true); setError('')
    try {
      const rw = formData.routing_weights
      // Derive routing_skills from fixos (keys with weight > 0)
      const routing_skills = Object.entries(rw.fixos)
        .filter(([, w]) => w > 0)
        .map(([k]) => k)

      const poolId = editingPool ? editingPool.pool_id : formData.pool_id

      // Hooks: drop entries with no pool selected; send empty slots to clear when
      // the pool previously had hooks (otherwise omit to leave untouched).
      const cleanHooks: PoolHooks = {
        on_human_start: formData.hooks.on_human_start.filter(h => h.pool),
        on_human_end:   formData.hooks.on_human_end.filter(h => h.pool),
        on_contact_end: formData.hooks.on_contact_end.filter(h => h.pool),
        post_human:     formData.hooks.post_human.filter(h => h.pool),
      }
      const hasHooks =
        cleanHooks.on_human_start.length > 0 ||
        cleanHooks.on_human_end.length > 0 ||
        cleanHooks.on_contact_end.length > 0 ||
        cleanHooks.post_human.length > 0
      const hadHooks = !!(editingPool?.hooks && (
        (editingPool.hooks.on_human_start?.length ?? 0) > 0 ||
        (editingPool.hooks.on_human_end?.length ?? 0) > 0 ||
        (editingPool.hooks.on_contact_end?.length ?? 0) > 0 ||
        (editingPool.hooks.post_human?.length ?? 0) > 0
      ))

      // Transfer destinations → supervisor_config.escalation_pools. Merge-safe:
      // preserve the rest of supervisor_config (enabled, intent_capability_map, …).
      const cleanEscalation = formData.escalation_pools.filter(Boolean)
      const hadSupervisor   = !!editingPool?.supervisor_config
      const mergedSupervisor = {
        ...(editingPool?.supervisor_config ?? {}),
        enabled: editingPool?.supervisor_config?.enabled ?? false,
        escalation_pools: cleanEscalation,
      }

      // @mention specialists → mentionable_pools (alias → pool). Drop incomplete rows.
      const cleanMentions: Record<string, string> = {}
      for (const m of formData.mention_pools) {
        const alias = m.alias.trim()
        if (alias && m.pool) cleanMentions[alias] = m.pool
      }
      const hadMentions = !!(editingPool?.mentionable_pools &&
        Object.keys(editingPool.mentionable_pools).length > 0)

      const payload = {
        description:       formData.description,
        channel_types:     formData.channel_types,
        sla_target_ms:     formData.sla_target_ms,
        // Type & capacity. agent_kind: send only when explicitly set ('' leaves the
        // registry backfill to infer). session_reservation: number to set, null to clear.
        ...(formData.agent_kind ? { agent_kind: formData.agent_kind } : {}),
        dispatch_mode:     formData.dispatch_mode,
        ...(formData.session_reservation !== null
          ? { session_reservation: formData.session_reservation }
          : (editingPool?.session_reservation != null ? { session_reservation: null } : {})),
        ...(formData.max_reply_time_ms !== null && { max_reply_time_ms: formData.max_reply_time_ms }),
        // calendar_id: null limpa no registry (undefined era removido pelo
        // JSON.stringify e o valor antigo persistia).
        ...(formData.calendar_id
          ? { calendar_id: formData.calendar_id }
          : (editingPool?.calendar_id ? { calendar_id: null } : {})),
        ...(formData.context_visibility_ns.trim()
          ? { context_visibility: { operator_namespaces: formData.context_visibility_ns.split(',').map(s => s.trim()).filter(Boolean) } }
          : {}),
        ...(routing_skills.length ? { routing_skills } : {}),
        routing_weights: rw,
        // Queue treatment (queue-attended-model, skill-first). Desmarcar a
        // skill num pool que tinha fila envia null → registry limpa (DbNull).
        ...(formData.queue_skill_id.trim() ? {
          queue_config: {
            skill_id: formData.queue_skill_id.trim(),
            ...(formData.queue_max_wait_s !== null ? { max_wait_s: formData.queue_max_wait_s } : {}),
          },
        } : (editingPool?.queue_config ? { queue_config: null } : {})),
        // Lifecycle hooks: send when present; send empty slots to clear; else omit.
        ...(hasHooks ? { hooks: cleanHooks }
          : (hadHooks ? { hooks: { ...EMPTY_HOOKS } } : {})),
        // Transfer destinations nested in supervisor_config (merge-safe). Send when
        // present or to clear the list while preserving the rest of the object.
        ...((cleanEscalation.length > 0 || hadSupervisor) ? { supervisor_config: mergedSupervisor } : {}),
        // @mention specialists: send when present; send {} to clear; else omit.
        ...(Object.keys(cleanMentions).length > 0 ? { mentionable_pools: cleanMentions }
          : (hadMentions ? { mentionable_pools: {} } : {})),
      }
      if (editingPool) {
        await registryApi.updatePool(editingPool.pool_id, payload, session.tenantId)
      } else {
        await registryApi.createPool({ pool_id: formData.pool_id, ...payload }, session.tenantId)
      }

      // ── Sync calendar association in calendar-api ─────────────────────────
      // The actual engine uses calendar-api associations — keep them in sync.
      if (formData.calendar_id) {
        await fetch('/v1/associations/upsert', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            tenant_id:   session.tenantId,
            entity_type: 'pool',
            entity_id:   poolId,
            calendar_id: formData.calendar_id,
            operator:    'UNION',
            priority:    1,
            exceptions:  calExceptions,
          }),
        })
      } else {
        // No calendar selected — remove any existing associations for this pool
        await fetch(
          `/v1/associations/entity?tenant_id=${encodeURIComponent(session.tenantId)}&entity_type=pool&entity_id=${encodeURIComponent(poolId)}`,
          { method: 'DELETE' },
        )
      }

      await loadPools()
      handleClose()
    } catch (e) {
      // Surface the registry's error (e.g. 422 Σ session_reservation > contracted C).
      setError(e instanceof Error && e.message ? e.message : tCommon('failedToSave'))
    } finally {
      setIsSaving(false)
    }
  }

  // ── table columns ─────────────────────────────────────────────────────────────

  const columns = [
    { key: 'pool_id', label: t('pools.fields.poolId') },
    {
      key: 'agent_kind',
      label: t('pools.fields.type'),
      render: (kind?: string) => kind === 'ai'
        ? <Badge variant="default" className="text-xs bg-secondary/10 text-secondary border border-secondary/30">{t('pools.typeCapacity.kindAi')}</Badge>
        : kind === 'human'
          ? <Badge variant="default" className="text-xs">{t('pools.typeCapacity.kindHuman')}</Badge>
          : <span className="text-xs text-gray">—</span>,
    },
    {
      key: 'channel_types',
      label: t('pools.fields.channelTypes'),
      render: (channels: string[]) => (
        <div className="flex gap-1 flex-wrap">
          {channels.map(ch => (
            <Badge key={ch} variant="default" className="text-xs">{ch}</Badge>
          ))}
        </div>
      ),
    },
    { key: 'sla_target_ms', label: t('pools.fields.slaTargetMs') },
    {
      key: 'calendar_id',
      label: t('pools.fields.calendar'),
      render: (id?: string) => id
        ? <span className="text-xs font-mono text-secondary">{calendars.find(c => c.id === id)?.name ?? id}</span>
        : <span className="text-xs text-gray">—</span>,
    },
    {
      key: 'routing_weights',
      label: t('pools.fields.priority'),
      render: (rw?: RoutingWeights, row?: Pool) => {
        const weights = rw ?? (row ? buildDefaultWeights(row) : null)
        if (!weights) return <span className="text-xs text-gray">{t('pools.fields.default')}</span>
        const skills = Object.entries(weights.fixos).filter(([, w]) => w > 0)
        return (
          <div className="flex flex-col gap-0.5">
            <span className="text-2xs font-mono text-gray" title={
              `SLA:${weights.dinamicos.sla} Wait:${weights.dinamicos.wait} ` +
              `Tier:${weights.dinamicos.tier} Churn:${weights.dinamicos.churn} ` +
              `Biz:${weights.dinamicos.business}`
            }>
              SLA·{weights.dinamicos.sla} Churn·{weights.dinamicos.churn}
            </span>
            {skills.length > 0 && (
              <div className="flex gap-1 flex-wrap">
                {skills.map(([k, w]) => (
                  <span key={k} className="text-2xs px-1 py-0 rounded bg-green-light text-green-text border border-green/30 font-mono">
                    {k}·{w}
                  </span>
                ))}
              </div>
            )}
          </div>
        )
      },
    },
    {
      key: 'status',
      label: t('pools.fields.status'),
      render: (status: string) => <Badge variant="active">{status}</Badge>,
    },
  ]

  const calendarOptions = [
    { value: '', label: t('pools.fields.noneOption') },
    ...calendars.map(c => ({ value: c.id, label: c.name })),
  ]

  // Pool options for hook/transfer/mention combos. The stored value is the
  // pool_id (stable across skill versions); the label surfaces the pool's
  // currently deployed skill as a hint. Excludes the pool being edited.
  const poolComboOptions: PoolOption[] = pools
    .filter(p => p.pool_id !== editingPool?.pool_id)
    .map(p => ({
      value: p.pool_id,
      label: p.deployed_skill_id
        ? `${p.pool_id} — ${p.deployed_skill_id}`
        : p.pool_id,
    }))

  const setHookSlot = (slot: keyof PoolHooks, entries: PoolHookEntry[]) =>
    setFormData(prev => ({ ...prev, hooks: { ...prev.hooks, [slot]: entries } }))

  // ── render ────────────────────────────────────────────────────────────────────

  return (
    <div>
      <div className="mb-6 flex justify-end">
        <Button variant="primary" onClick={handleOpenCreate}>
          + {t('pools.createPool')}
        </Button>
      </div>

      {isLoading ? (
        <div className="flex justify-center py-8"><Spinner /></div>
      ) : pools.length === 0 ? (
        <EmptyState
          title={t('pools.empty')}
          action={<Button onClick={handleOpenCreate}>{t('pools.createFirst')}</Button>}
        />
      ) : (
        <Table columns={columns} data={pools} keyField="pool_id" onRowClick={handleOpenEdit} />
      )}

      {/* ── Drawer form ──────────────────────────────────────────────────────── */}
      <Drawer
        isOpen={isOpen}
        onClose={handleClose}
        title={editingPool
          ? `Pool: ${editingPool.pool_id}`
          : t('pools.createPool')
        }
        footer={
          <>
            <Button variant="ghost" onClick={handleClose}>{tCommon('cancel')}</Button>
            <Button variant="primary" onClick={handleSubmit} disabled={isSaving}>
              {isSaving ? tCommon('saving') : tCommon('save')}
            </Button>
          </>
        }
      >
        <div className="space-y-5">
          {error && (
            <div className="bg-red-light border border-red/30 text-red-text px-3 py-2 rounded text-sm">
              {error}
            </div>
          )}

          {/* ── Basic info ───────────────────────────────────────────────────── */}
          <Input
            label={t('pools.fields.poolId')}
            value={formData.pool_id}
            onChange={e => setFormData({ ...formData, pool_id: e.target.value })}
            disabled={!!editingPool}
            required
          />

          <Input
            label={t('pools.fields.description')}
            value={formData.description}
            onChange={e => setFormData({ ...formData, description: e.target.value })}
            placeholder={tCommon('optionalDescription')}
          />

          {/* ── Channel types ─────────────────────────────────────────────────── */}
          <div>
            <label className="text-sm font-semibold text-dark mb-2 block">
              {t('pools.fields.channelTypes')}
            </label>
            <div className="grid grid-cols-2 gap-y-2">
              {CHANNEL_OPTIONS.map(ch => (
                <label key={ch.value} className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={formData.channel_types.includes(ch.value)}
                    onChange={() => handleChannelToggle(ch.value)}
                    className="w-4 h-4 rounded accent-primary"
                  />
                  <span className="text-sm text-dark">{ch.label}</span>
                </label>
              ))}
            </div>
          </div>

          {/* ── SLA ───────────────────────────────────────────────────────────── */}
          <div className="flex gap-3">
            <div className="flex-1">
              <Input
                label={t('pools.fields.slaTargetMs')}
                type="number"
                value={formData.sla_target_ms}
                onChange={e => setFormData({ ...formData, sla_target_ms: parseInt(e.target.value) })}
              />
              <p className="text-xs text-muted-light mt-0.5">{t('pools.fields.slaHint')}</p>
            </div>
            <div className="flex-1">
              <Input
                label={t('pools.fields.maxReplyLabel')}
                type="number"
                placeholder={t('pools.fields.noLimit')}
                value={formData.max_reply_time_ms ?? ''}
                onChange={e => {
                  const v = e.target.value
                  setFormData({ ...formData, max_reply_time_ms: v === '' ? null : parseInt(v) })
                }}
              />
              <p className="text-xs text-muted-light mt-0.5">{t('pools.fields.maxReplyHint')}</p>
            </div>
          </div>

          {/* ── Type & capacity (agent_kind + session_reservation) ───────────── */}
          <div>
            <p className="text-sm font-semibold text-dark mb-2">{t('pools.typeCapacity.label')}</p>
            <div className="flex gap-3">
              <div className="flex-1">
                <Select
                  label={t('pools.typeCapacity.agentKind')}
                  value={formData.agent_kind}
                  onChange={e => setFormData({ ...formData, agent_kind: e.target.value as '' | 'human' | 'ai' })}
                  options={[
                    { value: '',      label: t('pools.typeCapacity.kindInferred') },
                    { value: 'human', label: t('pools.typeCapacity.kindHuman') },
                    { value: 'ai',    label: t('pools.typeCapacity.kindAi') },
                  ]}
                />
              </div>
              <div className="w-40">
                <Input
                  label={t('pools.typeCapacity.sessionReservation')}
                  type="number"
                  placeholder={t('pools.typeCapacity.reservationPlaceholder')}
                  value={formData.session_reservation ?? ''}
                  onChange={e => {
                    const v = e.target.value
                    setFormData({ ...formData, session_reservation: v === '' ? null : parseInt(v) })
                  }}
                />
              </div>
            </div>
            <p className="text-xs text-muted-light mt-0.5">{t('pools.typeCapacity.agentKindHint')}</p>
            <p className="text-xs text-muted-light mt-0.5">{t('pools.typeCapacity.sessionReservationHint')}</p>
            {formData.agent_kind === 'ai' && formData.queue_skill_id.trim() && (
              <div className="mt-2 bg-warning-light border border-warning/30 text-warning-text px-3 py-1.5 rounded text-xs">
                {t('pools.typeCapacity.queueNeedsHuman')}
              </div>
            )}
          </div>

          {/* ── Dispatch mode (Frente 1 — pull) ──────────────────────────────── */}
          <div>
            <Select
              label={t('pools.dispatch.label')}
              value={formData.dispatch_mode}
              onChange={e => setFormData({ ...formData, dispatch_mode: e.target.value as 'push' | 'pull' })}
              options={[
                { value: 'push', label: t('pools.dispatch.push') },
                { value: 'pull', label: t('pools.dispatch.pull') },
              ]}
            />
            <p className="text-xs text-muted-light mt-0.5">{t('pools.dispatch.hint')}</p>
          </div>

          {/* ── Queue treatment (queue-attended-model, skill-first) ──────────── */}
          <div>
            <p className="text-sm font-semibold text-dark">{t('pools.queueCfg.label')}</p>
            <p className="text-xs text-gray mt-0.5 mb-2">{t('pools.queueCfg.hint')}</p>
            <div className="flex gap-3">
              <div className="flex-1">
                <Select
                  label={t('pools.queueCfg.skillFlow')}
                  value={formData.queue_skill_id}
                  onChange={e => setFormData({ ...formData, queue_skill_id: e.target.value })}
                  options={[
                    { value: '', label: t('pools.queueCfg.tenantDefault') },
                    ...skillFlows.map(s => ({ value: s.skill_id, label: `${s.skill_id} — ${s.name}` })),
                  ]}
                />
              </div>
              <div className="w-36">
                <Input
                  label={t('pools.queueCfg.maxWaitS')}
                  type="number"
                  placeholder={t('pools.queueCfg.platformDefault')}
                  value={formData.queue_max_wait_s ?? ''}
                  onChange={e => {
                    const v = e.target.value
                    setFormData({ ...formData, queue_max_wait_s: v === '' ? null : parseInt(v) })
                  }}
                />
              </div>
            </div>
          </div>

          {/* ── Lifecycle hooks (wrap-up / NPS / post) ───────────────────────── */}
          <div className="border-t border-border pt-4">
            <div className="mb-2">
              <p className="text-sm font-semibold text-dark">{t('pools.hooks.label')}</p>
              <p className="text-xs text-gray mt-0.5">{t('pools.hooks.hint')}</p>
            </div>
            <div className="space-y-3">
              <div>
                <p className="text-xs font-medium text-dark">{t('pools.hooks.onHumanStart')}</p>
                <p className="text-2xs text-muted-light mb-1">{t('pools.hooks.onHumanStartHint')}</p>
                <HookListEditor
                  entries={formData.hooks.on_human_start}
                  onChange={e => setHookSlot('on_human_start', e)}
                  poolOptions={poolComboOptions}
                />
              </div>
              <div>
                <p className="text-xs font-medium text-dark">{t('pools.hooks.onHumanEnd')}</p>
                <p className="text-2xs text-muted-light mb-1">{t('pools.hooks.onHumanEndHint')}</p>
                <HookListEditor
                  entries={formData.hooks.on_human_end}
                  onChange={e => setHookSlot('on_human_end', e)}
                  poolOptions={poolComboOptions}
                />
              </div>
              <div>
                <p className="text-xs font-medium text-dark">{t('pools.hooks.onContactEnd')}</p>
                <p className="text-2xs text-muted-light mb-1">{t('pools.hooks.onContactEndHint')}</p>
                <HookListEditor
                  entries={formData.hooks.on_contact_end}
                  onChange={e => setHookSlot('on_contact_end', e)}
                  poolOptions={poolComboOptions}
                  defaultSide="customer"
                />
              </div>
              <div>
                <p className="text-xs font-medium text-dark">{t('pools.hooks.postHuman')}</p>
                <p className="text-2xs text-muted-light mb-1">{t('pools.hooks.postHumanHint')}</p>
                <HookListEditor
                  entries={formData.hooks.post_human}
                  onChange={e => setHookSlot('post_human', e)}
                  poolOptions={poolComboOptions}
                />
              </div>
            </div>
          </div>

          {/* ── Transfer / escalate destinations (supervisor_config.escalation_pools) ── */}
          <div className="border-t border-border pt-4">
            <div className="mb-2">
              <p className="text-sm font-semibold text-dark">{t('pools.transfer.label')}</p>
              <p className="text-xs text-gray mt-0.5">{t('pools.transfer.hint')}</p>
            </div>
            <PoolListEditor
              values={formData.escalation_pools}
              onChange={v => setFormData(prev => ({ ...prev, escalation_pools: v }))}
              poolOptions={poolComboOptions}
              addLabel={t('pools.transfer.add')}
              noneLabel={t('pools.transfer.none')}
              selectLabel={t('pools.transfer.selectPool')}
            />
          </div>

          {/* ── @mention specialists (mentionable_pools: alias → pool) ───────────── */}
          <div>
            <div className="mb-2">
              <p className="text-sm font-semibold text-dark">{t('pools.mention.label')}</p>
              <p className="text-xs text-gray mt-0.5">{t('pools.mention.hint')}</p>
            </div>
            <MentionListEditor
              entries={formData.mention_pools}
              onChange={e => setFormData(prev => ({ ...prev, mention_pools: e }))}
              poolOptions={poolComboOptions}
            />
          </div>

          {/* ── Calendar ──────────────────────────────────────────────────────── */}
          <div>
            <Select
              label={t('pools.fields.calendar')}
              value={formData.calendar_id}
              onChange={e => setFormData({ ...formData, calendar_id: e.target.value })}
              options={calendarOptions}
            />
            {calendars.length === 0 && (
              <p className="text-xs text-gray mt-1">
                {t('pools.fields.noCalendars')}
              </p>
            )}
          </div>

          {/* ── Pool-level exceptions (Level 2) ──────────────────────────────── */}
          {formData.calendar_id && (
            <div>
              <div className="mb-2">
                <p className="text-sm font-semibold text-dark">{t('pools.exceptions.label')}</p>
                <p className="text-xs text-gray mt-0.5">{t('pools.exceptions.hint')}</p>
              </div>
              <PoolExceptionsEditor exceptions={calExceptions} onChange={setCalExceptions} />
            </div>
          )}

          {/* ── Context visibility — operator namespace filter ─────────────────── */}
          <div>
            <div className="mb-1">
              <p className="text-sm font-semibold text-dark">{t('pools.contextVisibility.label')}</p>
              <p className="text-xs text-gray mt-0.5">{t('pools.contextVisibility.hint')}</p>
            </div>
            <input
              type="text"
              placeholder="service, journey, session"
              value={formData.context_visibility_ns}
              onChange={e => setFormData({ ...formData, context_visibility_ns: e.target.value })}
              className="w-full border border-border rounded px-3 py-1.5 text-sm text-dark focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>

          {/* ── Routing weights — Fixos ───────────────────────────────────────── */}
          <div>
            <div className="mb-2">
              <p className="text-sm font-semibold text-dark">{t('pools.routingFixed.label')}</p>
              <p className="text-xs text-gray mt-0.5">{t('pools.routingFixed.hint')}</p>
            </div>

            {competencySkills.length === 0 ? (
              <p className="text-xs text-gray italic py-2">{t('pools.routingFixed.empty')}</p>
            ) : (
              <div className="flex flex-col gap-2.5 border border-border rounded p-3 bg-surface-muted">
                {competencySkills.map(skill => (
                  <WeightSlider
                    key={skill.key}
                    label={skill.key}
                    hint={t('pools.fields.domainDefault', { value: skill.domain })}
                    value={formData.routing_weights.fixos[skill.key] ?? 0}
                    onChange={v => setFixoWeight(skill.key, v)}
                  />
                ))}
              </div>
            )}
          </div>

          {/* ── Routing weights — Dinâmicos ───────────────────────────────────── */}
          <div>
            <div className="mb-2">
              <p className="text-sm font-semibold text-dark">{t('pools.routingDynamic.label')}</p>
              <p className="text-xs text-gray mt-0.5">{t('pools.routingDynamic.hint')}</p>
            </div>
            <div className="flex flex-col gap-2.5 border border-border rounded p-3 bg-surface-muted">
              {DINAMICOS_META.map(({ key, label, hint }) => (
                <WeightSlider
                  key={key}
                  label={label}
                  hint={hint}
                  value={formData.routing_weights.dinamicos[key]}
                  onChange={v => setDinamicoWeight(key, v)}
                />
              ))}
            </div>
            <button
              type="button"
              onClick={() =>
                setFormData(prev => ({
                  ...prev,
                  routing_weights: {
                    ...prev.routing_weights,
                    dinamicos: { ...ROUTING_WEIGHTS_DEFAULTS.dinamicos },
                  },
                }))
              }
              className="mt-1.5 text-xs text-secondary hover:text-primary transition-colors"
            >
              {t('pools.routingDynamic.reset')}
            </button>
          </div>
        </div>
      </Drawer>
    </div>
  )
}

export default PoolsPage
