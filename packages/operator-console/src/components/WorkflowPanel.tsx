/**
 * WorkflowPanel.tsx
 * Workflow instances list and detail view.
 *
 * Left side: filtered list of workflow instances, sorted by created_at descending
 * Right side: selected instance detail with timeline, status, and actions
 */
import React, { useState } from 'react'
import { useWorkflowInstances, useWorkflowInstance, cancelInstance } from '../api/workflow-hooks'
import type { WorkflowInstance, WorkflowStatus } from '../types'

interface Props {
  tenantId: string
  onBack: () => void
}

const STATUS_COLORS: Record<WorkflowStatus, string> = {
  active: '#3b82f6',
  suspended: '#eab308',
  completed: '#22c55e',
  failed: '#ef4444',
  timed_out: '#ef4444',
  cancelled: '#6b7280',
}

const SUSPEND_REASON_LABELS: Record<string, string> = {
  approval: 'Waiting Approval',
  input: 'Waiting Input',
  webhook: 'Waiting Webhook',
  timer: 'Waiting Timer',
}

export function WorkflowPanel({ tenantId, onBack }: Props) {
  const [selectedStatus, setSelectedStatus] = useState<WorkflowStatus | 'all'>('all')
  const [selectedInstanceId, setSelectedInstanceId] = useState<string | null>(null)

  const statusFilter = selectedStatus === 'all' ? undefined : selectedStatus
  const { instances, loading: listLoading } = useWorkflowInstances(tenantId, statusFilter)
  const { instance: selectedInstance } = useWorkflowInstance(selectedInstanceId)

  const sorted = instances.sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  )

  const handleCancel = async () => {
    if (!selectedInstanceId || !selectedInstance) return
    try {
      await cancelInstance(selectedInstanceId, tenantId)
      setSelectedInstanceId(null)
    } catch (err) {
      console.error('Failed to cancel instance:', err)
    }
  }

  const canCancel = selectedInstance && ['active', 'suspended'].includes(selectedInstance.status)

  return (
    <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
      {/* Left: List */}
      <div
        style={{
          flex: 1,
          borderRight: '1px solid #1e293b',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}
      >
        {/* Status filter tabs */}
        <div
          style={{
            display: 'flex',
            gap: 8,
            padding: '12px 16px',
            borderBottom: '1px solid #1e293b',
            flexWrap: 'wrap',
            overflowY: 'auto',
            maxHeight: 60,
          }}
        >
          {(['all', 'active', 'suspended', 'completed', 'failed'] as const).map(status => (
            <button
              key={status}
              onClick={() => setSelectedStatus(status)}
              style={{
                padding: '4px 12px',
                borderRadius: 6,
                border: selectedStatus === status ? '1px solid #3b82f6' : '1px solid #334155',
                background: selectedStatus === status ? '#1e40af' : '#0f172a',
                color: '#e2e8f0',
                cursor: 'pointer',
                fontSize: 12,
                fontWeight: selectedStatus === status ? 600 : 400,
              }}
            >
              {status === 'all' ? 'All' : status.charAt(0).toUpperCase() + status.slice(1)}
            </button>
          ))}
        </div>

        {/* Back button and header */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            padding: '8px 16px',
            borderBottom: '1px solid #1e293b',
          }}
        >
          <button
            onClick={onBack}
            style={{
              padding: '4px 12px',
              borderRadius: 4,
              border: '1px solid #334155',
              background: '#0f172a',
              color: '#94a3b8',
              cursor: 'pointer',
              fontSize: 12,
            }}
          >
            ← Back to Heatmap
          </button>
          <div style={{ fontSize: 12, color: '#64748b' }}>
            {listLoading ? 'Loading...' : `${sorted.length} instance${sorted.length !== 1 ? 's' : ''}`}
          </div>
        </div>

        {/* Instances list */}
        <div
          style={{
            flex: 1,
            overflowY: 'auto',
            overflowX: 'hidden',
          }}
        >
          {sorted.map(inst => (
            <div
              key={inst.id}
              onClick={() => setSelectedInstanceId(inst.id)}
              style={{
                padding: '12px 16px',
                borderBottom: '1px solid #1e293b',
                cursor: 'pointer',
                background: selectedInstanceId === inst.id ? '#1e293b' : 'transparent',
                borderLeft:
                  selectedInstanceId === inst.id ? `3px solid ${STATUS_COLORS[inst.status]}` : 'none',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start' }}>
                <div style={{ flex: 1 }}>
                  <div
                    style={{
                      fontSize: 13,
                      fontWeight: 600,
                      color: '#e2e8f0',
                      fontFamily: 'monospace',
                    }}
                  >
                    {inst.id.slice(0, 8)}
                  </div>
                  <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 2 }}>
                    {inst.flow_id}
                  </div>
                </div>
                <div
                  style={{
                    display: 'inline-block',
                    padding: '2px 8px',
                    borderRadius: 4,
                    background: STATUS_COLORS[inst.status],
                    color: '#000',
                    fontSize: 11,
                    fontWeight: 600,
                  }}
                >
                  {inst.status === 'timed_out' ? 'timed out' : inst.status}
                </div>
              </div>
              <div style={{ fontSize: 11, color: '#475569', marginTop: 6 }}>
                {new Date(inst.created_at).toLocaleString()}
              </div>
              {inst.suspend_reason && (
                <div
                  style={{
                    fontSize: 11,
                    color: '#fbbf24',
                    marginTop: 4,
                  }}
                >
                  {SUSPEND_REASON_LABELS[inst.suspend_reason] ?? inst.suspend_reason}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Right: Detail */}
      {selectedInstance && (
        <div
          style={{
            width: 400,
            borderLeft: '1px solid #1e293b',
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              padding: '16px',
              borderBottom: '1px solid #1e293b',
              flex: 1,
              overflowY: 'auto',
            }}
          >
            {/* Timeline */}
            <div style={{ marginBottom: 24 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: '#94a3b8', marginBottom: 12 }}>
                TIMELINE
              </div>
              <div style={{ fontSize: 12, color: '#64748b' }}>
                <div style={{ marginBottom: 8 }}>
                  <span style={{ color: '#22c55e' }}>●</span> Created{' '}
                  {new Date(selectedInstance.created_at).toLocaleTimeString()}
                </div>
                {selectedInstance.suspended_at && (
                  <div style={{ marginBottom: 8 }}>
                    <span style={{ color: '#eab308' }}>●</span> Suspended{' '}
                    {new Date(selectedInstance.suspended_at).toLocaleTimeString()}
                  </div>
                )}
                {selectedInstance.resumed_at && (
                  <div style={{ marginBottom: 8 }}>
                    <span style={{ color: '#3b82f6' }}>●</span> Resumed{' '}
                    {new Date(selectedInstance.resumed_at).toLocaleTimeString()}
                  </div>
                )}
                {selectedInstance.completed_at && (
                  <div style={{ marginBottom: 8 }}>
                    <span style={{ color: '#22c55e' }}>●</span> Completed{' '}
                    {new Date(selectedInstance.completed_at).toLocaleTimeString()}
                  </div>
                )}
              </div>
            </div>

            {/* Status and step */}
            <div style={{ marginBottom: 24 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: '#94a3b8', marginBottom: 8 }}>
                STATUS
              </div>
              <div
                style={{
                  display: 'inline-block',
                  padding: '4px 12px',
                  borderRadius: 4,
                  background: STATUS_COLORS[selectedInstance.status],
                  color: '#000',
                  fontSize: 12,
                  fontWeight: 600,
                  marginBottom: 12,
                }}
              >
                {selectedInstance.status === 'timed_out' ? 'timed out' : selectedInstance.status}
              </div>
              {selectedInstance.current_step && (
                <div style={{ fontSize: 12, color: '#e2e8f0' }}>
                  Current step:{' '}
                  <span style={{ fontFamily: 'monospace', fontWeight: 600 }}>
                    {selectedInstance.current_step}
                  </span>
                </div>
              )}
              {selectedInstance.outcome && (
                <div style={{ fontSize: 12, color: '#e2e8f0', marginTop: 8 }}>
                  Outcome:{' '}
                  <span style={{ fontFamily: 'monospace', fontWeight: 600 }}>
                    {selectedInstance.outcome}
                  </span>
                </div>
              )}
            </div>

            {/* Suspend reason */}
            {selectedInstance.suspend_reason && (
              <div style={{ marginBottom: 24 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: '#94a3b8', marginBottom: 8 }}>
                  SUSPEND REASON
                </div>
                <div
                  style={{
                    display: 'inline-block',
                    padding: '4px 12px',
                    borderRadius: 4,
                    background: '#664400',
                    color: '#fde047',
                    fontSize: 12,
                    fontWeight: 600,
                  }}
                >
                  {SUSPEND_REASON_LABELS[selectedInstance.suspend_reason] ?? selectedInstance.suspend_reason}
                </div>
              </div>
            )}

            {/* Resume token */}
            {selectedInstance.resume_token && (
              <div style={{ marginBottom: 24 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: '#94a3b8', marginBottom: 8 }}>
                  RESUME TOKEN
                </div>
                <div
                  style={{
                    background: '#0f172a',
                    border: '1px solid #334155',
                    borderRadius: 4,
                    padding: '8px 12px',
                    fontSize: 11,
                    fontFamily: 'monospace',
                    color: '#94a3b8',
                    wordBreak: 'break-all',
                    cursor: 'pointer',
                  }}
                  onClick={() => {
                    navigator.clipboard.writeText(selectedInstance.resume_token!)
                  }}
                  title="Click to copy"
                >
                  {selectedInstance.resume_token}
                </div>
              </div>
            )}

            {/* Resume expires */}
            {selectedInstance.resume_expires_at && (
              <div style={{ marginBottom: 24 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: '#94a3b8', marginBottom: 8 }}>
                  RESUME EXPIRES
                </div>
                <div style={{ fontSize: 12, color: '#e2e8f0' }}>
                  {new Date(selectedInstance.resume_expires_at).toLocaleString()}
                </div>
              </div>
            )}

            {/* Flow ID */}
            <div style={{ marginBottom: 24 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: '#94a3b8', marginBottom: 8 }}>
                FLOW
              </div>
              <div
                style={{
                  fontSize: 12,
                  fontFamily: 'monospace',
                  color: '#e2e8f0',
                }}
              >
                {selectedInstance.flow_id}
              </div>
            </div>
          </div>

          {/* Cancel button */}
          {canCancel && (
            <div style={{ padding: '12px 16px', borderTop: '1px solid #1e293b' }}>
              <button
                onClick={handleCancel}
                style={{
                  width: '100%',
                  padding: '8px 12px',
                  borderRadius: 4,
                  border: '1px solid #ef4444',
                  background: '#7f1d1d',
                  color: '#fca5a5',
                  cursor: 'pointer',
                  fontSize: 12,
                  fontWeight: 600,
                }}
              >
                Cancel Instance
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
