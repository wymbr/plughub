/**
 * ProcessosPage — /flow/processos
 *
 * Workflow instance lifecycle view. Shows all instances across all states.
 * Extracted from MonitorTab's ProcessosView scope.
 */
import React, { useState } from 'react'
import { useAuth } from '@/auth/useAuth'
import {
  useWorkflowInstances, useWorkflowInstance, cancelWorkflow,
} from '@/modules/workflows/api/hooks'
import type { WorkflowStatus } from '@/modules/workflows/api/hooks'
import { Link } from 'react-router-dom'

// ── Config ────────────────────────────────────────────────────────────────────

const WF_STATUS_COLORS: Record<WorkflowStatus, string> = {
  active:    '#3b82f6',
  suspended: '#eab308',
  completed: '#22c55e',
  failed:    '#ef4444',
  timed_out: '#ef4444',
  cancelled: '#6b7280',
}

const WF_STATUS_LABELS: Record<WorkflowStatus | 'all', string> = {
  all:       'Todos',
  active:    'Ativo',
  suspended: 'Suspenso',
  completed: 'Concluído',
  failed:    'Falhou',
  timed_out: 'Expirado',
  cancelled: 'Cancelado',
}

const WF_SUSPEND_LABELS: Record<string, string> = {
  approval: '⏳ Aprovação pendente',
  input:    '✏️ Aguardando entrada',
  webhook:  '🔗 Aguardando webhook',
  timer:    '⏰ Timer agendado',
}

function fmtDt(ts: string | null | undefined) {
  if (!ts) return '—'
  return new Date(ts).toLocaleString('pt-BR')
}

// ── ProcessosPage ─────────────────────────────────────────────────────────────

export default function ProcessosPage() {
  const { tenantId } = useAuth()
  const [filterStatus, setFilterStatus] = useState<WorkflowStatus | 'all'>('all')
  const [selectedId,   setSelectedId]   = useState<string | null>(null)

  const statusParam = filterStatus === 'all' ? undefined : filterStatus
  const { instances, loading, refresh } = useWorkflowInstances(tenantId ?? '', statusParam, 10_000)
  const { instance: detail }            = useWorkflowInstance(selectedId, 10_000)

  const sorted = [...instances].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  )

  async function handleCancel() {
    if (!selectedId || !tenantId) return
    if (!confirm('Cancelar esta instância de processo?')) return
    try {
      await cancelWorkflow(selectedId, tenantId)
      setSelectedId(null)
      refresh()
    } catch (e) { alert(String(e)) }
  }

  if (!tenantId) return (
    <div className="flex items-center justify-center h-full text-gray-400 text-sm">
      Nenhum tenant selecionado.
    </div>
  )

  return (
    <div className="flex flex-col h-full overflow-hidden bg-[#0a1628] text-slate-200">

      {/* Header */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-slate-800 flex-shrink-0">
        <span className="font-bold text-base text-slate-100">Processos</span>
        <div className="flex items-center gap-2">
          {loading && <span className="text-xs text-slate-500 animate-pulse">carregando…</span>}
          <button onClick={refresh}
            className="text-xs px-3 py-1.5 rounded border border-slate-700 text-slate-400 hover:text-slate-200 hover:border-slate-500 transition-colors">
            ↻ Atualizar
          </button>
        </div>
      </div>

      <div className="flex flex-1 overflow-hidden">

        {/* Left: list */}
        <div className="w-80 flex-shrink-0 border-r border-slate-800 flex flex-col overflow-hidden">

          {/* Status filter */}
          <div className="flex flex-wrap gap-1.5 px-3 py-2.5 border-b border-slate-800 flex-shrink-0">
            {(['all', 'active', 'suspended', 'completed', 'failed'] as const).map(s => {
              const active = filterStatus === s
              const color  = s === 'all' ? '#3b82f6' : WF_STATUS_COLORS[s as WorkflowStatus]
              return (
                <button key={s} onClick={() => { setFilterStatus(s); setSelectedId(null) }}
                  className="text-xs px-2.5 py-1 rounded-md font-medium transition-all"
                  style={{
                    border:     `1px solid ${active ? color : '#334155'}`,
                    background: active ? color + '22' : 'transparent',
                    color:      active ? color : '#64748b',
                    fontWeight: active ? 600 : 400,
                  }}>
                  {WF_STATUS_LABELS[s]}
                </button>
              )
            })}
          </div>

          {/* Instance list */}
          <div className="flex-1 overflow-y-auto">
            {sorted.length === 0 && !loading && (
              <div className="flex flex-col items-center justify-center py-12 text-slate-500 text-sm gap-2">
                <span className="text-2xl">📋</span>
                <span>Nenhum processo encontrado</span>
              </div>
            )}
            {sorted.map(inst => {
              const color      = WF_STATUS_COLORS[inst.status]
              const isSelected = inst.id === selectedId
              return (
                <div key={inst.id}
                  onClick={() => setSelectedId(inst.id === selectedId ? null : inst.id)}
                  className="px-4 py-3 cursor-pointer transition-colors hover:bg-slate-800/50"
                  style={{
                    borderBottom: '1px solid #1e293b',
                    background:   isSelected ? '#1e293b' : 'transparent',
                    borderLeft:   isSelected ? `3px solid ${color}` : '3px solid transparent',
                  }}>
                  <div className="flex justify-between items-start gap-2">
                    <div className="flex-1 min-w-0">
                      <code className="text-xs font-semibold text-blue-300">{inst.id.slice(0, 8)}…</code>
                      <div className="text-xs text-slate-500 mt-0.5 truncate">{inst.flow_id}</div>
                      {inst.origin_session_id && (
                        <div className="text-xs text-slate-600 mt-0.5 truncate font-mono">
                          sessão: …{inst.origin_session_id.slice(-10)}
                        </div>
                      )}
                    </div>
                    <span className="text-[11px] font-bold px-1.5 py-0.5 rounded flex-shrink-0"
                      style={{ background: color + '33', color }}>
                      {WF_STATUS_LABELS[inst.status] ?? inst.status}
                    </span>
                  </div>
                  <div className="text-[11px] text-slate-500 mt-1.5">{fmtDt(inst.created_at)}</div>
                  {inst.suspend_reason && (
                    <div className="text-[11px] text-yellow-400 mt-1">
                      {WF_SUSPEND_LABELS[inst.suspend_reason] ?? inst.suspend_reason}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>

        {/* Right: detail */}
        {detail ? (
          <div className="flex-1 flex flex-col overflow-hidden">
            <div className="flex justify-between items-start px-5 py-3.5 border-b border-slate-800 flex-shrink-0">
              <div>
                <code className="text-xs text-blue-300">{detail.id}</code>
                <div className="text-xs text-slate-500 mt-0.5">{detail.flow_id}</div>
              </div>
              <button onClick={() => setSelectedId(null)}
                className="text-slate-500 hover:text-slate-200 text-lg leading-none">✕</button>
            </div>

            <div className="flex-1 overflow-y-auto p-5 space-y-5">

              {/* Status */}
              <div>
                <div className="text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-2">Status</div>
                <span className="text-xs font-bold px-2.5 py-1 rounded"
                  style={{ background: WF_STATUS_COLORS[detail.status] + '33', color: WF_STATUS_COLORS[detail.status] }}>
                  {WF_STATUS_LABELS[detail.status] ?? detail.status}
                </span>
                {detail.current_step && (
                  <div className="mt-2 text-xs text-slate-400">
                    Step atual: <code className="text-slate-200">{detail.current_step}</code>
                  </div>
                )}
                {detail.outcome && (
                  <div className="mt-1 text-xs text-slate-400">
                    Outcome: <code className="text-slate-200">{detail.outcome}</code>
                  </div>
                )}
              </div>

              {/* Timeline */}
              <div>
                <div className="text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-2">Timeline</div>
                <div className="space-y-1.5">
                  {[
                    { dot: '#22c55e', label: 'Criado',   ts: detail.created_at },
                    detail.suspended_at ? { dot: '#eab308', label: 'Suspenso',  ts: detail.suspended_at } : null,
                    detail.resumed_at   ? { dot: '#3b82f6', label: 'Retomado',  ts: detail.resumed_at   } : null,
                    detail.completed_at ? { dot: '#22c55e', label: 'Concluído', ts: detail.completed_at } : null,
                  ].filter(Boolean).map((entry, i) => (
                    <div key={i} className="flex items-center gap-2 text-xs">
                      <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: entry!.dot }} />
                      <span className="text-slate-400 w-20 flex-shrink-0">{entry!.label}</span>
                      <span className="text-slate-500">{fmtDt(entry!.ts)}</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Suspend reason */}
              {detail.suspend_reason && (
                <div>
                  <div className="text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-2">Motivo da suspensão</div>
                  <span className="text-xs px-2.5 py-1 rounded border border-yellow-900/60 bg-yellow-900/20 text-yellow-300">
                    {WF_SUSPEND_LABELS[detail.suspend_reason] ?? detail.suspend_reason}
                  </span>
                </div>
              )}

              {/* Resume token */}
              {detail.resume_token && (
                <div>
                  <div className="text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-2">Token de retomada</div>
                  <div
                    className="bg-slate-900 border border-slate-700 rounded px-3 py-2 text-[11px] font-mono text-slate-400 break-all cursor-pointer hover:border-slate-500"
                    onClick={() => void navigator.clipboard.writeText(detail.resume_token!)}
                    title="Clique para copiar">
                    {detail.resume_token}
                  </div>
                  {detail.resume_expires_at && (
                    <div className="mt-1 text-[11px] text-slate-600">
                      Expira: {fmtDt(detail.resume_expires_at)}
                    </div>
                  )}
                </div>
              )}

              {/* Origin session link */}
              {detail.origin_session_id && (
                <div>
                  <div className="text-[11px] font-bold text-slate-500 uppercase tracking-wider mb-2">Sessão de origem</div>
                  <Link
                    to={`/contacts/sessions?sessionId=${detail.origin_session_id}`}
                    className="text-xs text-blue-400 font-mono hover:underline">
                    {detail.origin_session_id}
                  </Link>
                </div>
              )}
            </div>

            {/* Cancel button */}
            {['active', 'suspended'].includes(detail.status) && (
              <div className="px-4 py-3 border-t border-slate-800 flex-shrink-0">
                <button onClick={handleCancel}
                  className="w-full py-2 rounded border border-red-800 bg-red-950 text-red-300 text-sm font-semibold hover:bg-red-900 transition-colors">
                  Cancelar processo
                </button>
              </div>
            )}
          </div>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center text-slate-500">
            <div className="text-4xl mb-3">⚙️</div>
            <div className="text-sm">Selecione um processo para ver detalhes</div>
          </div>
        )}
      </div>
    </div>
  )
}
