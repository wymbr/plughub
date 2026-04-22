import React, { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import * as registryApi from '@/api/registry'
import { Instance } from '@/types'
import Table from '@/components/ui/Table'
import Select from '@/components/ui/Select'
import Card from '@/components/ui/Card'
import Badge from '@/components/ui/Badge'
import EmptyState from '@/components/ui/EmptyState'
import Spinner from '@/components/ui/Spinner'

const InstancesPage: React.FC = () => {
  const { session } = useAuth()
  const { t } = useTranslation('configRecursos')
  const [instances, setInstances] = useState<Instance[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [poolFilter, setPoolFilter] = useState('')

  useEffect(() => {
    loadInstances()
    const interval = setInterval(loadInstances, 30000)
    return () => clearInterval(interval)
  }, [])

  const loadInstances = async () => {
    if (!session) return
    try {
      const result = await registryApi.listInstances(
        session.tenantId,
        poolFilter || undefined,
        statusFilter || undefined
      )
      setInstances(result.items || [])
    } catch (err) {
      setError('Failed to load instances')
    } finally {
      setIsLoading(false)
    }
  }

  const statusOptions = [
    { value: '', label: 'All statuses' },
    { value: 'active', label: 'Active' },
    { value: 'inactive', label: 'Inactive' },
    { value: 'suspended', label: 'Suspended' }
  ]

  const poolOptions = [
    { value: '', label: 'All pools' }
  ]

  const columns = [
    { key: 'instance_id', label: t('instances.fields.instanceId') },
    { key: 'agent_type_id', label: t('instances.fields.agentType') },
    { key: 'pool_id', label: t('instances.fields.pool') },
    {
      key: 'status',
      label: t('instances.fields.status'),
      render: (status: string) => (
        <Badge variant={status === 'active' ? 'active' : 'default'}>
          {status}
        </Badge>
      )
    },
    {
      key: 'updated_at',
      label: t('instances.fields.updatedAt'),
      render: (timestamp: string) => new Date(timestamp).toLocaleString()
    }
  ]

  return (
    <div>
      <Card title={t('instances.title')} className="mb-6">
        <p className="text-sm text-gray mb-4">
          {t('instances.readOnly')}
        </p>
      </Card>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
        <Select
          label="Status"
          value={statusFilter}
          onChange={(e) => {
            setStatusFilter(e.target.value)
            setIsLoading(true)
          }}
          options={statusOptions}
        />

        <Select
          label="Pool"
          value={poolFilter}
          onChange={(e) => {
            setPoolFilter(e.target.value)
            setIsLoading(true)
          }}
          options={poolOptions}
        />
      </div>

      {isLoading ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : instances.length === 0 ? (
        <EmptyState
          title={t('instances.empty')}
        />
      ) : (
        <Table
          columns={columns}
          data={instances}
          keyField="instance_id"
        />
      )}

      <div className="mt-6 text-xs text-gray text-center">
        Auto-refresh every 30 seconds
      </div>
    </div>
  )
}

export default InstancesPage
