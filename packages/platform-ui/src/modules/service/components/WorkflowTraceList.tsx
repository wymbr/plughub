/**
 * WorkflowTraceList
 * Replaces SegmentList for webhook sessions in SessionsPage (Level 2).
 *
 * Fetches GET /sessions/{id}/workflow-trace and renders an ordered list:
 *   1. input_origin  — intake agent segment (from origin_session_id)
 *   2. webhook_exec  — webhook execution windows (one per suspend/resume cycle)
 *   3. specialist_output — task/delegate sub-segments
 *
 * Each node type gets a distinct visual treatment. Clicking a node:
 *   - input_origin / specialist_output → SessionTranscript (regular agent view)
 *   - webhook_exec                     → WebhookSegmentDetail (rich step view)
 */
import React, { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { apiFetch } from '@/api/apiFetch'
import { Loader2, Timer } from 'lucide-react'
import type { ContactSegment } from '../types'

// ── Types ─────────────────────────────────────────────────────────────────────

export type TraceNodeType = 'input_origin' | 'webhook_exec' | 'specialist_output' | 'delegate_child'

export interface TraceNode extends ContactSegment {
  node_type:  TraceNodeType
  is_origin:  boolean
}

interface Props {
  tenantId:           string
  sessionId:          string
  onSelectAgent:      (segment: ContactSegment) => void
  onSelectWebhook:    (node: TraceNode) => void
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtDuration(ms: number | null): string {
  if (ms === null || ms < 0) return '—'
  const s = Math.floor(ms / 1000)
  if (s < 60)  return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60)  return `${m}m ${s % 60}s`
  const h = Math.floor(m / 60)
  if (h < 48)  return `${h}h ${m % 60}m`
  const d = Math.floor(h / 24)
  return `${d}d ${h % 24}h`
}

function fmtTime(iso: string | null): string {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) }
  catch { return iso }
}

// ── Node type config ──────────────────────────────────────────────────────────

const NODE_CONFIG: Record<TraceNodeType, {
  label:     string
  icon:      string
  bgClass:   string
  textClass: string
  dotClass:  string
}> = {
  input_origin: {
    label:     'trace.type.input',
    icon:      '↘',
    bgClass:   'bg-blue-50 border-blue-200',
    textClass: 'text-blue-800',
    dotClass:  'bg-blue-400',
  },
  webhook_exec: {
    label:     'trace.type.exec',
    icon:      '⚙',
    bgClass:   'bg-surface-muted border-border',
    textClass: 'text-muted',
    dotClass:  'bg-amber-400',
  },
  specialist_output: {
    label:     'trace.type.output',
    icon:      '↗',
    bgClass:   'bg-green-50 border-green-200',
    textClass: 'text-green-800',
    dotClass:  'bg-green-400',
  },
  delegate_child: {
    label:     'trace.type.delegate',
    icon:      '⇄',
    bgClass:   'bg-purple-50 border-purple-200',
    textClass: 'text-purple-800',
    dotClass:  'bg-purple-400',
  },
}

// ── TraceNodeRow ──────────────────────────────────────────────────────────────

function TraceNodeRow({ node, index, onClick }: {
  node:    TraceNode
  index:   number
  onClick: () => void
}) {
  const { t } = useTranslation('contacts')
  const cfg      = NODE_CONFIG[node.node_type]
  const isActive = node.ended_at === null
  const label    = node.agent_type_id.replace(/_/g, ' ').replace(/\bv\d+$/, '').trim()

  return (
    <div
      onClick={onClick}
      className={`flex items-start gap-3 px-4 py-3 cursor-pointer border-b border-border transition-colors hover:bg-surface-muted last:border-b-0`}
    >
      {/* Step number + dot */}
      <div className="flex flex-col items-center gap-1 flex-shrink-0 pt-1">
        <span className="text-xs font-mono text-muted-light w-5 text-center">{index + 1}</span>
        <div className={`w-2.5 h-2.5 rounded-full ${cfg.dotClass}`} />
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        {/* Row 1: type badge + agent label */}
        <div className="flex items-center gap-2 mb-1 flex-wrap">
          <span className={`text-xs px-1.5 py-0.5 rounded border font-medium ${cfg.bgClass} ${cfg.textClass}`}>
            {cfg.icon} {t(cfg.label, { defaultValue: node.node_type })}
          </span>
          <span className="text-sm font-medium text-dark truncate">{label}</span>
          {node.agent_type === 'human' && (
            <span className="text-xs px-1.5 py-0.5 rounded bg-warning-light text-warning-text font-medium flex-shrink-0">
              {t('segments.human')}
            </span>
          )}
          <span className={`ml-auto text-xs font-semibold px-1.5 py-0.5 rounded-full flex-shrink-0 ${
            isActive ? 'bg-green-light text-green-text' : 'bg-surface-alt text-muted'
          }`}>
            {isActive ? t('segments.live') : (node.outcome ?? t('segments.closed'))}
          </span>
        </div>

        {/* Row 2: pool + timing */}
        <div className="flex items-center gap-3 text-xs text-muted">
          <span className="font-mono">{node.pool_id}</span>
          <span>·</span>
          <span>{fmtTime(node.started_at)}</span>
          {!isActive && node.ended_at && (
            <>
              <span>→</span>
              <span>{fmtTime(node.ended_at)}</span>
            </>
          )}
          {node.duration_ms !== null && (
            <span className="inline-flex items-center gap-0.5 font-mono">
              <Timer className="w-3 h-3" aria-hidden="true" />
              {fmtDuration(node.duration_ms)}
            </span>
          )}
        </div>
      </div>

      <span className="flex-shrink-0 text-xs text-border-strong self-center">›</span>
    </div>
  )
}

// ── WorkflowTraceList (main) ──────────────────────────────────────────────────

export function WorkflowTraceList({ tenantId, sessionId, onSelectAgent, onSelectWebhook }: Props) {
  const { t } = useTranslation('contacts')
  const [nodes,   setNodes]   = useState<TraceNode[]>([])
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState<string | null>(null)

  const fetch_ = useCallback(async () => {
    if (!tenantId || !sessionId) return
    setLoading(true)
    try {
      const res  = await apiFetch(`/sessions/${encodeURIComponent(sessionId)}/workflow-trace?tenant_id=${encodeURIComponent(tenantId)}`)
      const data = await res.json() as { nodes: TraceNode[]; error?: string }
      if (data.error) { setError(t('trace.errorLoad')); return }
      setNodes(data.nodes ?? [])
      setError(null)
    } catch (err) {
      setError(`${err}`)
    } finally {
      setLoading(false)
    }
  }, [tenantId, sessionId, t])

  useEffect(() => {
    setNodes([])
    setError(null)
    fetch_()
  }, [fetch_])

  function handleSelect(node: TraceNode) {
    if (node.node_type === 'webhook_exec') {
      onSelectWebhook(node)
    } else {
      // input_origin, specialist_output, delegate_child → agent transcript
      onSelectAgent(node)
    }
  }

  if (loading && nodes.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 text-muted-light py-16">
        <Loader2 className="w-6 h-6 animate-spin" aria-hidden="true" />
        <span className="text-sm">{t('trace.loading')}</span>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 py-16 px-4">
        <span className="text-3xl">⚠️</span>
        <span className="text-sm text-red-text font-medium text-center">{error}</span>
      </div>
    )
  }

  if (nodes.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 text-muted-light py-16">
        <span className="text-3xl">📭</span>
        <span className="text-sm">{t('trace.empty')}</span>
      </div>
    )
  }

  const execCount    = nodes.filter(n => n.node_type === 'webhook_exec').length
  const suspendCount = nodes.filter(n => n.node_type === 'webhook_exec' && n.ended_at !== null).length

  return (
    <div className="flex flex-col h-full overflow-hidden bg-white rounded-xl border border-border">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-border flex-shrink-0 bg-surface-muted">
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-dark">{t('trace.title')}</p>
          <p className="text-xs text-muted font-mono truncate">{sessionId.slice(-14)}</p>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0 text-xs text-muted">
          <span>{t('trace.executions', { count: execCount })}</span>
          {suspendCount > 0 && (
            <span className="px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 border border-amber-200 font-medium">
              {t('trace.suspensions', { count: suspendCount })}
            </span>
          )}
        </div>
      </div>

      {/* Connector line legend */}
      <div className="px-4 py-2 bg-primary-light border-b border-primary/20 flex-shrink-0">
        <p className="text-xs text-primary">
          {t('trace.hint')}
        </p>
      </div>

      {/* Nodes list */}
      <div className="flex-1 overflow-y-auto">
        {nodes.map((node, idx) => (
          <TraceNodeRow
            key={`${node.node_type}-${node.segment_id}`}
            node={node}
            index={idx}
            onClick={() => handleSelect(node)}
          />
        ))}
      </div>
    </div>
  )
}
