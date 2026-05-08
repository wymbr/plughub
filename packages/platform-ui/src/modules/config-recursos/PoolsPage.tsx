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
          <p className="text-xs text-gray-400 italic">Nenhuma exceção configurada para este pool.</p>
        ) : exceptions.map((exc, i) => (
          <div key={i} className="flex items-start gap-2 px-2 py-1.5 bg-orange-50 border border-orange-100 rounded text-sm">
            <span className="font-mono text-xs text-gray-600 w-24 flex-shrink-0 pt-0.5">{exc.date}</span>
            <div className="flex-1 min-w-0">
              {exc.label && <p className="text-xs text-gray-700 truncate mb-0.5">{exc.label}</p>}
              {exc.override_slots === null ? (
                <span className="text-xs text-red-600 font-medium">Fechado o dia todo</span>
              ) : (
                <span className="text-xs text-orange-700">
                  {exc.override_slots.map(s => `${s.open}–${s.close}`).join(', ')}
                </span>
              )}
            </div>
            <button type="button" onClick={() => remove(i)}
              className="text-red-400 hover:text-red-600 text-xs flex-shrink-0 pt-0.5">✕</button>
          </div>
        ))}
      </div>

      {/* Add form */}
      <div className="border border-dashed border-orange-200 rounded-lg p-3 space-y-2 bg-orange-50/40">
        <div className="flex gap-2">
          <input type="date" value={newDate} onChange={e => setNewDate(e.target.value)}
            className="text-sm border border-gray-300 rounded px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-orange-400 bg-white" />
          <input type="text" placeholder="Descrição (ex: Manutenção)" value={newLabel}
            onChange={e => setNewLabel(e.target.value)} onKeyDown={e => e.key === 'Enter' && add()}
            className="flex-1 text-sm border border-gray-300 rounded px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-orange-400 bg-white" />
        </div>
        <div className="flex items-center gap-4">
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="radio" checked={closed} onChange={() => setClosed(true)}
              className="text-red-500 focus:ring-red-400" />
            <span className="text-xs text-gray-700">Fechado o dia todo</span>
          </label>
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="radio" checked={!closed} onChange={() => setClosed(false)}
              className="text-orange-500 focus:ring-orange-400" />
            <span className="text-xs text-gray-700">Horário especial</span>
          </label>
        </div>
        {!closed && (
          <div className="space-y-1 pl-1">
            {newSlots.map((sl, idx) => (
              <div key={idx} className="flex items-center gap-2">
                <input type="time" value={sl.open} onChange={e => updateSlot(idx, 'open', e.target.value)}
                  className="text-sm border border-gray-300 rounded px-2 py-1 bg-white focus:outline-none focus:ring-1 focus:ring-orange-400" />
                <span className="text-xs text-gray-400">até</span>
                <input type="time" value={sl.close} onChange={e => updateSlot(idx, 'close', e.target.value)}
                  className="text-sm border border-gray-300 rounded px-2 py-1 bg-white focus:outline-none focus:ring-1 focus:ring-orange-400" />
                {newSlots.length > 1 && (
                  <button type="button" onClick={() => setNewSlots(prev => prev.filter((_, i) => i !== idx))}
                    className="text-gray-400 hover:text-red-500 text-sm leading-none">×</button>
                )}
              </div>
            ))}
            {newSlots.length < 4 && (
              <button type="button"
                onClick={() => setNewSlots(prev => [...prev, { open: prev[prev.length-1]?.close ?? '18:00', close: '23:00' }])}
                className="text-xs text-orange-600 hover:text-orange-800 mt-0.5">+ intervalo</button>
            )}
          </div>
        )}
        <button type="button" onClick={add} disabled={!newDate}
          className="w-full px-3 py-1.5 text-xs bg-orange-500 text-white rounded-lg hover:bg-orange-600 disabled:opacity-50 transition-colors">
          Adicionar exceção
        </button>
      </div>
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
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200 shrink-0">
          <span className="font-semibold text-gray-900 text-base">{title}</span>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-700 text-xl leading-none w-7 h-7 flex items-center justify-center rounded hover:bg-gray-100 transition-colors"
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
          <div className="flex justify-end gap-2 px-5 py-3.5 border-t border-gray-200 shrink-0 bg-gray-50">
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
        <p className="text-xs font-medium text-gray-700 leading-tight">{label}</p>
        <p className="text-[10px] text-gray-400 leading-tight">{hint}</p>
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
      <span className="w-12 text-right text-xs font-mono font-bold text-gray-700 shrink-0">
        {value} <span className="text-gray-400 font-normal">/{pct}%</span>
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

const DINAMICOS_META: Array<{
  key: keyof RoutingWeightsDinamicos
  label: string
  hint: string
  default: number
}> = [
  { key: 'sla',      label: 'Urgência SLA',     hint: 'Tempo de espera ÷ SLA alvo',       default: 9 },
  { key: 'wait',     label: 'Tempo de espera',  hint: 'Espera absoluta normalizada',       default: 7 },
  { key: 'tier',     label: 'Tier do cliente',  hint: 'platinum > gold > standard',        default: 5 },
  { key: 'churn',    label: 'Risco de churn',   hint: 'Score de churn do perfil',          default: 8 },
  { key: 'business', label: 'Valor de negócio', hint: 'business_score do cliente',         default: 3 },
]

const PoolsPage: React.FC = () => {
  const { session } = useAuth()
  const { t } = useTranslation('configRecursos')
  const { t: tCommon } = useTranslation('common')

  const [pools,     setPools]     = useState<Pool[]>([])
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
    pool_id:       '',
    description:   '',
    channel_types: [] as string[],
    sla_target_ms: 30000,
    calendar_id:   '',
    routing_weights: { ...ROUTING_WEIGHTS_DEFAULTS } as RoutingWeights,
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

  useEffect(() => {
    void loadPools()
    void loadCalendars()
    void loadCompetencySkills()
  }, [loadPools, loadCalendars, loadCompetencySkills])

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
      pool_id: '', description: '', channel_types: [], sla_target_ms: 30000,
      calendar_id: '', routing_weights: { ...ROUTING_WEIGHTS_DEFAULTS },
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
      channel_types:   pool.channel_types,
      sla_target_ms:   pool.sla_target_ms,
      calendar_id:     pool.calendar_id || '',
      routing_weights: buildDefaultWeights(pool),
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

      const payload = {
        description:   formData.description,
        channel_types: formData.channel_types,
        sla_target_ms: formData.sla_target_ms,
        ...(formData.calendar_id ? { calendar_id: formData.calendar_id } : { calendar_id: undefined }),
        ...(routing_skills.length ? { routing_skills } : {}),
        routing_weights: rw,
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
    } catch {
      setError(tCommon('failedToSave'))
    } finally {
      setIsSaving(false)
    }
  }

  // ── table columns ─────────────────────────────────────────────────────────────

  const columns = [
    { key: 'pool_id', label: t('pools.fields.poolId') },
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
      label: 'Calendário',
      render: (id?: string) => id
        ? <span className="text-xs font-mono text-secondary">{calendars.find(c => c.id === id)?.name ?? id}</span>
        : <span className="text-xs text-gray">—</span>,
    },
    {
      key: 'routing_weights',
      label: 'Prioridade',
      render: (rw?: RoutingWeights, row?: Pool) => {
        const weights = rw ?? (row ? buildDefaultWeights(row) : null)
        if (!weights) return <span className="text-xs text-gray">padrão</span>
        const skills = Object.entries(weights.fixos).filter(([, w]) => w > 0)
        return (
          <div className="flex flex-col gap-0.5">
            <span className="text-[10px] font-mono text-gray" title={
              `SLA:${weights.dinamicos.sla} Wait:${weights.dinamicos.wait} ` +
              `Tier:${weights.dinamicos.tier} Churn:${weights.dinamicos.churn} ` +
              `Biz:${weights.dinamicos.business}`
            }>
              SLA·{weights.dinamicos.sla} Churn·{weights.dinamicos.churn}
            </span>
            {skills.length > 0 && (
              <div className="flex gap-1 flex-wrap">
                {skills.map(([k, w]) => (
                  <span key={k} className="text-[10px] px-1 py-0 rounded bg-green-50 text-green-700 border border-green-200 font-mono">
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
    { value: '', label: '— Nenhum —' },
    ...calendars.map(c => ({ value: c.id, label: c.name })),
  ]

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
            <div className="bg-red-50 border border-red-300 text-red-700 px-3 py-2 rounded text-sm">
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
          <Input
            label={t('pools.fields.slaTargetMs')}
            type="number"
            value={formData.sla_target_ms}
            onChange={e => setFormData({ ...formData, sla_target_ms: parseInt(e.target.value) })}
          />

          {/* ── Calendar ──────────────────────────────────────────────────────── */}
          <div>
            <Select
              label="Calendário"
              value={formData.calendar_id}
              onChange={e => setFormData({ ...formData, calendar_id: e.target.value })}
              options={calendarOptions}
            />
            {calendars.length === 0 && (
              <p className="text-xs text-gray mt-1">
                Nenhum calendário disponível. Crie em Configuração → Calendários.
              </p>
            )}
          </div>

          {/* ── Pool-level exceptions (Level 2) ──────────────────────────────── */}
          {formData.calendar_id && (
            <div>
              <div className="mb-2">
                <p className="text-sm font-semibold text-dark">Exceções deste Pool</p>
                <p className="text-xs text-gray mt-0.5">
                  Datas únicas que sobrescrevem as regras do calendário somente para este pool.
                  Prioridade 1 — acima de feriados e do horário semanal.
                </p>
              </div>
              <PoolExceptionsEditor exceptions={calExceptions} onChange={setCalExceptions} />
            </div>
          )}

          {/* ── Routing weights — Fixos ───────────────────────────────────────── */}
          <div>
            <div className="mb-2">
              <p className="text-sm font-semibold text-dark">Pesos de Roteamento — Fixos</p>
              <p className="text-xs text-gray mt-0.5">
                Importância de cada competency skill neste pool.
                Defina 0 para ignorar a skill no roteamento.
              </p>
            </div>

            {competencySkills.length === 0 ? (
              <p className="text-xs text-gray italic py-2">
                Nenhuma competency skill cadastrada.
                Configure em Recursos → Skills.
              </p>
            ) : (
              <div className="flex flex-col gap-2.5 border border-gray-200 rounded p-3 bg-gray-50">
                {competencySkills.map(skill => (
                  <WeightSlider
                    key={skill.key}
                    label={skill.key}
                    hint={`padrão: ${skill.domain}`}
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
              <p className="text-sm font-semibold text-dark">Pesos de Roteamento — Dinâmicos</p>
              <p className="text-xs text-gray mt-0.5">
                Influência de cada fator em tempo real na ordenação da fila.
                0 = ignorar, 9 = máxima influência.
              </p>
            </div>
            <div className="flex flex-col gap-2.5 border border-gray-200 rounded p-3 bg-gray-50">
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
              ↺ Restaurar padrões dinâmicos
            </button>
          </div>
        </div>
      </Drawer>
    </div>
  )
}

export default PoolsPage
