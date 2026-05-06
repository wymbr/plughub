import React, { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import * as registryApi from '@/api/registry'
import { Pool, RoutingExpression, ROUTING_EXPRESSION_DEFAULTS } from '@/types'
import Button from '@/components/ui/Button'
import Table from '@/components/ui/Table'
import Modal from '@/components/ui/Modal'
import Input from '@/components/ui/Input'
import Select from '@/components/ui/Select'
import Badge from '@/components/ui/Badge'
import EmptyState from '@/components/ui/EmptyState'
import Spinner from '@/components/ui/Spinner'

interface CalendarOption { id: string; name: string }
interface RoutingSkill   { key: string; domain: string }

const PoolsPage: React.FC = () => {
  const { session } = useAuth()
  const { t } = useTranslation('configRecursos')
  const { t: tCommon } = useTranslation('common')

  const [pools,      setPools]      = useState<Pool[]>([])
  const [calendars,  setCalendars]  = useState<CalendarOption[]>([])
  const [routingSkills, setRoutingSkills] = useState<RoutingSkill[]>([])
  const [isLoading,  setIsLoading]  = useState(true)
  const [isOpen,     setIsOpen]     = useState(false)
  const [editingPool, setEditingPool] = useState<Pool | null>(null)
  const [isSaving,   setIsSaving]   = useState(false)
  const [error,      setError]      = useState('')

  const [formData, setFormData] = useState({
    pool_id:            '',
    description:        '',
    channel_types:      [] as string[],
    sla_target_ms:      30000,
    calendar_id:        '',
    routing_skills:     [] as string[],
    routing_expression: { ...ROUTING_EXPRESSION_DEFAULTS } as RoutingExpression,
  })

  const channelOptions = [
    { value: 'webchat',   label: 'WebChat'   },
    { value: 'whatsapp',  label: 'WhatsApp'  },
    { value: 'voice',     label: 'Voice'     },
    { value: 'email',     label: 'Email'     },
    { value: 'sms',       label: 'SMS'       },
    { value: 'instagram', label: 'Instagram' },
    { value: 'telegram',  label: 'Telegram'  },
    { value: 'webrtc',    label: 'WebRTC'    },
  ]

  // ── Fetch calendars from calendar-api ──────────────────────────────────────
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
    } catch { /* stale ok — calendar-api optional */ }
  }, [session])

  // ── Fetch routing (competency) skills from config-api ─────────────────────
  const loadRoutingSkills = useCallback(async () => {
    if (!session) return
    try {
      const res = await fetch(`/config/routing?tenant_id=${encodeURIComponent(session.tenantId)}`)
      if (res.ok) {
        const data = await res.json() as { entries?: Record<string, unknown> }
        const entries = data.entries ?? {}
        const skills: RoutingSkill[] = Object.entries(entries).map(([k, v]) => ({
          key: k,
          domain: typeof v === 'object' && v !== null
            ? ((v as Record<string, unknown>).domain as string) ?? ''
            : String(v ?? ''),
        }))
        setRoutingSkills(skills)
      }
    } catch { /* stale ok */ }
  }, [session])

  // ── Load pools ─────────────────────────────────────────────────────────────
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
    void loadRoutingSkills()
  }, [loadPools, loadCalendars, loadRoutingSkills])

  // ── Form helpers ───────────────────────────────────────────────────────────
  const handleOpenCreate = () => {
    setEditingPool(null)
    setFormData({
      pool_id: '', description: '', channel_types: [], sla_target_ms: 30000,
      calendar_id: '', routing_skills: [],
      routing_expression: { ...ROUTING_EXPRESSION_DEFAULTS },
    })
    setError('')
    setIsOpen(true)
  }

  const handleOpenEdit = (pool: Pool) => {
    setEditingPool(pool)
    setFormData({
      pool_id:            pool.pool_id,
      description:        pool.description || '',
      channel_types:      pool.channel_types,
      sla_target_ms:      pool.sla_target_ms,
      calendar_id:        pool.calendar_id  || '',
      routing_skills:     pool.routing_skills || [],
      routing_expression: pool.routing_expression ?? { ...ROUTING_EXPRESSION_DEFAULTS },
    })
    setError('')
    setIsOpen(true)
  }

  const setRoutingWeight = (key: keyof RoutingExpression, val: string) => {
    const n = parseFloat(val)
    if (isNaN(n)) return
    setFormData(prev => ({
      ...prev,
      routing_expression: { ...prev.routing_expression, [key]: Math.max(0, n) },
    }))
  }

  const handleClose = () => { setIsOpen(false); setEditingPool(null) }

  const handleChannelToggle = (channel: string) => {
    setFormData(prev => ({
      ...prev,
      channel_types: prev.channel_types.includes(channel)
        ? prev.channel_types.filter(c => c !== channel)
        : [...prev.channel_types, channel],
    }))
  }

  const handleRoutingSkillToggle = (key: string) => {
    setFormData(prev => ({
      ...prev,
      routing_skills: prev.routing_skills.includes(key)
        ? prev.routing_skills.filter(s => s !== key)
        : [...prev.routing_skills, key],
    }))
  }

  const handleSubmit = async () => {
    if (!session || !formData.pool_id.trim()) {
      setError(t('pools.fields.poolId') + ' ' + tCommon('isRequired'))
      return
    }
    setIsSaving(true)
    setError('')
    try {
      // Only send routing_expression if it differs from defaults
      const re = formData.routing_expression
      const reChanged = (Object.keys(ROUTING_EXPRESSION_DEFAULTS) as Array<keyof RoutingExpression>)
        .some(k => re[k] !== ROUTING_EXPRESSION_DEFAULTS[k])

      const payload = {
        description:    formData.description,
        channel_types:  formData.channel_types,
        sla_target_ms:  formData.sla_target_ms,
        ...(formData.calendar_id    ? { calendar_id:    formData.calendar_id }    : {}),
        ...(formData.routing_skills.length ? { routing_skills: formData.routing_skills } : {}),
        ...(reChanged ? { routing_expression: re } : {}),
      }
      if (editingPool) {
        await registryApi.updatePool(editingPool.pool_id, payload, session.tenantId)
      } else {
        await registryApi.createPool({ pool_id: formData.pool_id, ...payload }, session.tenantId)
      }
      await loadPools()
      handleClose()
    } catch {
      setError(tCommon('failedToSave'))
    } finally {
      setIsSaving(false)
    }
  }

  // ── Table columns ──────────────────────────────────────────────────────────
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
      key: 'routing_skills',
      label: 'Skills Roteamento',
      render: (skills?: string[]) => skills && skills.length > 0
        ? (
          <div className="flex gap-1 flex-wrap">
            {skills.map(s => (
              <span key={s} className="text-xs px-1.5 py-0.5 rounded bg-green/10 text-green border border-green/20 font-mono">{s}</span>
            ))}
          </div>
        )
        : <span className="text-xs text-gray">—</span>,
    },
    {
      key: 'routing_expression',
      label: 'Prioridade',
      render: (re?: import('@/types').RoutingExpression) => re
        ? (
          <span className="text-[10px] font-mono text-gray" title={
            `SLA:${re.weight_sla} Wait:${re.weight_wait} Tier:${re.weight_tier} Churn:${re.weight_churn} Biz:${re.weight_business}`
          }>
            SLA·{re.weight_sla} Churn·{re.weight_churn}
          </span>
        )
        : <span className="text-xs text-gray">padrão</span>,
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

      <Modal
        isOpen={isOpen}
        onClose={handleClose}
        title={editingPool ? t('pools.title') : t('pools.createPool')}
        footer={
          <>
            <Button variant="ghost" onClick={handleClose}>{tCommon('cancel')}</Button>
            <Button variant="primary" onClick={handleSubmit} disabled={isSaving}>
              {isSaving ? tCommon('saving') : tCommon('save')}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          {error && (
            <div className="bg-red/10 border border-red text-red px-3 py-2 rounded text-sm">{error}</div>
          )}

          {/* Pool ID */}
          <Input
            label={t('pools.fields.poolId')}
            value={formData.pool_id}
            onChange={e => setFormData({ ...formData, pool_id: e.target.value })}
            disabled={!!editingPool}
            required
          />

          {/* Description */}
          <Input
            label={t('pools.fields.description')}
            value={formData.description}
            onChange={e => setFormData({ ...formData, description: e.target.value })}
            placeholder={tCommon('optionalDescription')}
          />

          {/* Channel types */}
          <div>
            <label className="text-sm font-semibold text-dark mb-2 block">
              {t('pools.fields.channelTypes')}
            </label>
            <div className="space-y-2">
              {channelOptions.map(ch => (
                <label key={ch.value} className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={formData.channel_types.includes(ch.value)}
                    onChange={() => handleChannelToggle(ch.value)}
                    className="w-4 h-4 rounded border-lightGray text-primary"
                  />
                  <span className="text-sm text-dark">{ch.label}</span>
                </label>
              ))}
            </div>
          </div>

          {/* SLA */}
          <Input
            label={t('pools.fields.slaTargetMs')}
            type="number"
            value={formData.sla_target_ms}
            onChange={e => setFormData({ ...formData, sla_target_ms: parseInt(e.target.value) })}
          />

          {/* Calendar */}
          <Select
            label="Template de Calendário"
            value={formData.calendar_id}
            onChange={e => setFormData({ ...formData, calendar_id: e.target.value })}
            options={calendarOptions}
          />
          {calendars.length === 0 && (
            <p className="text-xs text-gray -mt-3">
              Nenhum calendário disponível. Crie em Configuração → Plataforma → Calendários.
            </p>
          )}

          {/* Routing skills */}
          {routingSkills.length > 0 && (
            <div>
              <label className="text-sm font-semibold text-dark mb-1 block">
                Skills de Roteamento
              </label>
              <p className="text-xs text-gray mb-2">
                Skills de competência usados para ordenação estática da fila neste pool.
              </p>
              <div className="space-y-1.5 max-h-40 overflow-y-auto border border-lightGray rounded p-2">
                {routingSkills.map(skill => (
                  <label key={skill.key} className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={formData.routing_skills.includes(skill.key)}
                      onChange={() => handleRoutingSkillToggle(skill.key)}
                      className="w-4 h-4 rounded border-lightGray"
                    />
                    <span className="text-sm font-mono text-dark">{skill.key}</span>
                    <span className="text-xs text-gray">{skill.domain}</span>
                  </label>
                ))}
              </div>
            </div>
          )}
          {routingSkills.length === 0 && (
            <p className="text-xs text-gray">
              Nenhuma competency skill cadastrada. Configure em Plataforma → Roteamento.
            </p>
          )}

          {/* Routing expression — priority scoring weights */}
          <div>
            <label className="text-sm font-semibold text-dark mb-1 block">
              Expressão de Prioridade
            </label>
            <p className="text-xs text-gray mb-3">
              Pesos aplicados ao cálculo de prioridade dinâmica quando um agente fica livre.
              Valores maiores aumentam a influência desse critério na ordenação da fila.
            </p>
            <div className="grid grid-cols-2 gap-3">
              {(
                [
                  { key: 'weight_sla',      label: 'Urgência SLA',        hint: 'Tempo de espera ÷ SLA alvo' },
                  { key: 'weight_wait',     label: 'Tempo de espera',     hint: 'Espera normalizada' },
                  { key: 'weight_tier',     label: 'Tier do cliente',     hint: 'platinum > gold > standard' },
                  { key: 'weight_churn',    label: 'Risco de churn',      hint: 'Score de churn do cliente' },
                  { key: 'weight_business', label: 'Valor de negócio',    hint: 'business_score do perfil' },
                ] as { key: keyof RoutingExpression; label: string; hint: string }[]
              ).map(({ key, label, hint }) => (
                <div key={key}>
                  <label className="text-xs font-medium text-dark block mb-0.5">{label}</label>
                  <p className="text-[10px] text-gray mb-1">{hint}</p>
                  <input
                    type="number"
                    min={0}
                    step={0.1}
                    value={formData.routing_expression[key]}
                    onChange={e => setRoutingWeight(key, e.target.value)}
                    className="w-full text-xs font-mono px-2 py-1.5 border border-lightGray rounded focus:outline-none focus:border-primary text-dark"
                  />
                </div>
              ))}
            </div>
            <button
              type="button"
              onClick={() => setFormData(prev => ({ ...prev, routing_expression: { ...ROUTING_EXPRESSION_DEFAULTS } }))}
              className="mt-2 text-xs text-secondary hover:text-primary transition-colors"
            >
              ↺ Restaurar padrões
            </button>
          </div>
        </div>
      </Modal>
    </div>
  )
}

export default PoolsPage
