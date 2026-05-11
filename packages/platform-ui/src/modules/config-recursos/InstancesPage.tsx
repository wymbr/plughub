import React, { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import * as registryApi from '@/api/registry'
import { Instance, Pool } from '@/types'
import Table from '@/components/ui/Table'
import Select from '@/components/ui/Select'
import Badge from '@/components/ui/Badge'
import EmptyState from '@/components/ui/EmptyState'
import Spinner from '@/components/ui/Spinner'

const InstancesPage: React.FC = () => {
  const { session } = useAuth()
  const { t } = useTranslation('configRecursos')
  const [instances, setInstances] = useState<Instance[]>([])
  const [pools, setPools] = useState<Pool[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [statusFilter, setStatusFilter] = useState('')
  const [poolFilter, setPoolFilter] = useState('')

  // Arc 9 — non-empty means supervisor scope; admin keeps empty = unrestricted
  const supervisedAgentTypes = session?.supervisedAgentTypes ?? []

  const loadInstances = useCallback(async () => {
    if (!session) return
    try {
      const result = await registryApi.listInstances(
        session.tenantId,
        poolFilter || undefined,
        statusFilter || undefined,
      )
      const items = result.items || []
      // Transparent scope filter: only show AI instances the supervisor owns
      setInstances(
        supervisedAgentTypes.length > 0
          ? items.filter(i => supervisedAgentTypes.includes(i.agent_type_id))
          : items,
      )
    } catch { /* stale ok */ }
    finally { setIsLoading(false) }
  }, [session, supervisedAgentTypes, poolFilter, statusFilter])

  // Load pools once for the filter dropdown
  useEffect(() => {
    if (!session) return
    registryApi.listPools(session.tenantId)
      .then(r => setPools(r.items || []))
      .catch(() => {})
  }, [session])

  useEffect(() => {
    setIsLoading(true)
    loadInstances()
    const interval = setInterval(loadInstances, 15_000)
    return () => clearInterval(interval)
  }, [loadInstances])

  const statusOptions = [
    { value: '',         label: t('instances.filters.allStatuses') },
    { value: 'ready',    label: t('instances.filters.ready')       },
    { value: 'busy',     label: t('instances.filters.busy')        },
    { value: 'paused',   label: t('instances.filters.paused')      },
    { value: 'draining', label: t('instances.filters.draining')    },
  ]

  const poolOptions = [
    { value: '', label: t('instances.filters.allPools') },
    ...pools.map(p => ({ value: p.pool_id, label: p.pool_id })),
  ]

  const statusVariant = (s: string): 'active' | 'default' => {
    return s === 'ready' ? 'active' : 'default'
  }

  const columns = [
    {
      key: 'instance_id',
      label: t('instances.fields.instanceId'),
      render: (id: string) => <code className="text-xs text-secondary">{id}</code>,
    },
    { key: 'agent_type_id', label: t('instances.fields.agentType') },
    { key: 'pool_id',       label: t('instances.fields.pool') },
    {
      key: 'channel_types',
      label: t('instances.fields.channels'),
      render: (channels: string[]) => (
        <div className="flex gap-1 flex-wrap">
          {(channels || []).map(ch => (
            <span key={ch} className="text-xs px-1.5 py-0.5 rounded bg-secondary/10 text-secondary border border-secondary/20">
              {ch}
            </span>
          ))}
        </div>
      ),
    },
    {
      key: 'status',
      label: t('instances.fields.status'),
      render: (s: string) => (
        <Badge variant={statusVariant(s)}>{s}</Badge>
      ),
    },
    {
      key: 'updated_at',
      label: t('instances.fields.updatedAt'),
      render: (ts: string) => new Date(ts).toLocaleString(),
    },
  ]

  return (
    <div>
      <p className="text-sm text-gray mb-4">{t('instances.readOnly')}</p>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
        <Select
          label={t('instances.filters.status')}
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value)}
          options={statusOptions}
        />
        <Select
          label={t('instances.filters.pool')}
          value={poolFilter}
          onChange={e => setPoolFilter(e.target.value)}
          options={poolOptions}
        />
      </div>

      {isLoading ? (
        <div className="flex justify-center py-8"><Spinner /></div>
      ) : instances.length === 0 ? (
        <EmptyState title={t('instances.empty')} />
      ) : (
        <Table columns={columns} data={instances} keyField="instance_id" />
      )}

      <div className="mt-4 text-xs text-gray text-center">
        {t('instances.autoRefresh')} · {t('instances.instanceCount', { count: instances.length })}
      </div>
    </div>
  )
}

export default InstancesPage
