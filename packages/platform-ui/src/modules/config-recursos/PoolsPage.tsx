import React, { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import * as registryApi from '@/api/registry'
import { Pool } from '@/types'
import Button from '@/components/ui/Button'
import Table from '@/components/ui/Table'
import Modal from '@/components/ui/Modal'
import Input from '@/components/ui/Input'
import Select from '@/components/ui/Select'
import Badge from '@/components/ui/Badge'
import EmptyState from '@/components/ui/EmptyState'
import Spinner from '@/components/ui/Spinner'

const PoolsPage: React.FC = () => {
  const { session } = useAuth()
  const { t } = useTranslation('configRecursos')
  const [pools, setPools] = useState<Pool[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [isOpen, setIsOpen] = useState(false)
  const [editingPool, setEditingPool] = useState<Pool | null>(null)
  const [isSaving, setIsSaving] = useState(false)
  const [error, setError] = useState('')

  const [formData, setFormData] = useState({
    pool_id: '',
    description: '',
    channel_types: [] as string[],
    sla_target_ms: 30000
  })

  const channelOptions = [
    { value: 'webchat', label: 'WebChat' },
    { value: 'whatsapp', label: 'WhatsApp' },
    { value: 'voice', label: 'Voice' },
    { value: 'email', label: 'Email' },
    { value: 'sms', label: 'SMS' }
  ]

  useEffect(() => {
    loadPools()
  }, [])

  const loadPools = async () => {
    if (!session) return
    setIsLoading(true)
    try {
      const result = await registryApi.listPools(session.tenantId)
      setPools(result.items || [])
    } catch (err) {
      setError('Failed to load pools')
    } finally {
      setIsLoading(false)
    }
  }

  const handleOpenCreate = () => {
    setEditingPool(null)
    setFormData({
      pool_id: '',
      description: '',
      channel_types: [],
      sla_target_ms: 30000
    })
    setError('')
    setIsOpen(true)
  }

  const handleOpenEdit = (pool: Pool) => {
    setEditingPool(pool)
    setFormData({
      pool_id: pool.pool_id,
      description: pool.description || '',
      channel_types: pool.channel_types,
      sla_target_ms: pool.sla_target_ms
    })
    setError('')
    setIsOpen(true)
  }

  const handleClose = () => {
    setIsOpen(false)
    setEditingPool(null)
  }

  const handleSubmit = async () => {
    if (!session || !formData.pool_id.trim()) {
      setError(t('pools.fields.poolId') + ' is required')
      return
    }

    setIsSaving(true)
    setError('')

    try {
      if (editingPool) {
        await registryApi.updatePool(editingPool.pool_id, {
          description: formData.description,
          channel_types: formData.channel_types,
          sla_target_ms: formData.sla_target_ms
        }, session.tenantId)
      } else {
        await registryApi.createPool(formData, session.tenantId)
      }
      await loadPools()
      handleClose()
    } catch (err) {
      setError('Failed to save pool')
    } finally {
      setIsSaving(false)
    }
  }

  const handleChannelToggle = (channel: string) => {
    setFormData(prev => ({
      ...prev,
      channel_types: prev.channel_types.includes(channel)
        ? prev.channel_types.filter(c => c !== channel)
        : [...prev.channel_types, channel]
    }))
  }

  const columns = [
    { key: 'pool_id', label: t('pools.fields.poolId') },
    {
      key: 'channel_types',
      label: t('pools.fields.channelTypes'),
      render: (channels: string[]) => (
        <div className="flex gap-1">
          {channels.map(ch => (
            <Badge key={ch} variant="default" className="text-xs">
              {ch}
            </Badge>
          ))}
        </div>
      )
    },
    { key: 'sla_target_ms', label: t('pools.fields.slaTargetMs') },
    {
      key: 'status',
      label: t('pools.fields.status'),
      render: (status: string) => <Badge variant="active">{status}</Badge>
    }
  ]

  return (
    <div>
      <div className="mb-6 flex justify-end">
        <Button variant="primary" onClick={handleOpenCreate}>
          + {t('pools.createPool')}
        </Button>
      </div>

      {isLoading ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : pools.length === 0 ? (
        <EmptyState
          title={t('pools.empty')}
          action={<Button onClick={handleOpenCreate}>{t('pools.createFirst')}</Button>}
        />
      ) : (
        <Table
          columns={columns}
          data={pools}
          keyField="pool_id"
          onRowClick={handleOpenEdit}
        />
      )}

      <Modal
        isOpen={isOpen}
        onClose={handleClose}
        title={editingPool ? t('pools.fields.poolId') : t('pools.createPool')}
        footer={
          <>
            <Button variant="ghost" onClick={handleClose}>
              Cancel
            </Button>
            <Button variant="primary" onClick={handleSubmit} disabled={isSaving}>
              {isSaving ? 'Saving...' : 'Save'}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          {error && (
            <div className="bg-red/10 border border-red text-red px-3 py-2 rounded text-sm">
              {error}
            </div>
          )}

          <Input
            label={t('pools.fields.poolId')}
            value={formData.pool_id}
            onChange={(e) => setFormData({ ...formData, pool_id: e.target.value })}
            disabled={!!editingPool}
            required
          />

          <Input
            label={t('pools.fields.description')}
            value={formData.description}
            onChange={(e) => setFormData({ ...formData, description: e.target.value })}
            placeholder="Optional description"
          />

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

          <Input
            label={t('pools.fields.slaTargetMs')}
            type="number"
            value={formData.sla_target_ms}
            onChange={(e) => setFormData({ ...formData, sla_target_ms: parseInt(e.target.value) })}
          />
        </div>
      </Modal>
    </div>
  )
}

export default PoolsPage
