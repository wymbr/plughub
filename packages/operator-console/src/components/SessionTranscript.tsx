/**
 * SessionTranscript.tsx
 * Live read-only transcript for a single session.
 *
 * Connects to SSE GET /sessions/{id}/stream:
 *   - "history" event: all existing stream entries
 *   - "entry" event:   new entries as they arrive
 *
 * Read-only — no participant is registered; the stream is tailed via XREAD
 * on the backend, same mechanism as WebChat reconnect.
 */
import { useEffect, useRef } from 'react'
import { useSessionStream } from '../api/hooks'
import { scoreToColor } from '../utils/sentiment'
import type { StreamEntry } from '../types'

interface Props {
  tenantId:  string
  sessionId: string
  onBack:    () => void
}

export function SessionTranscript({ tenantId, sessionId, onBack }: Props) {
  const { entries, status } = useSessionStream(tenantId, sessionId)
  const bottomRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom when new entries arrive
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [entries.length])

  return (
    <div style={styles.container}>
      {/* Header */}
      <div style={styles.header}>
        <button style={styles.backBtn} onClick={onBack} title="Back to session list">
          ← Sessions
        </button>
        <span style={styles.sessionLabel}>
          Session{' '}
          <code style={styles.sessionId}>{sessionId}</code>
        </span>
        <StatusDot status={status} />
        <span style={styles.readOnly}>read-only</span>
      </div>

      {/* Stream entries */}
      <div style={styles.stream}>
        {entries.length === 0 && status === 'connecting' && (
          <div style={styles.placeholder}>Connecting to stream…</div>
        )}
        {entries.length === 0 && status === 'connected' && (
          <div style={styles.placeholder}>No events yet in this session.</div>
        )}
        {entries.length === 0 && status === 'error' && (
          <div style={{ ...styles.placeholder, color: '#ef4444' }}>
            Failed to connect to stream.
          </div>
        )}

        {entries.map(e => (
          <EntryRow key={e.entry_id} entry={e} />
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}

// ─── EntryRow ─────────────────────────────────────────────────────────────────

function EntryRow({ entry: e }: { entry: StreamEntry }) {
  const isCustomer = e.author_role === 'customer' || e.author_role === null
  const isAgent    = ['primary', 'specialist', 'supervisor', 'evaluator', 'reviewer'].includes(e.author_role ?? '')
  const isInternal = e.visibility === 'agents_only' || (Array.isArray(e.visibility))
  const isSystem   = ['session_opened', 'session_closed', 'participant_joined', 'participant_left',
                       'flow_step_completed', 'customer_identified', 'medium_transitioned'].includes(e.type)

  if (isSystem) return <SystemEvent entry={e} />

  const align = isCustomer ? 'flex-start' : 'flex-end'

  return (
    <div style={{ ...styles.bubbleRow, justifyContent: align }}>
      {isAgent && isInternal && (
        <div style={styles.internalNote}>
          <span style={styles.internalLabel}>agents only</span>
          <MessageBubble entry={e} internal />
        </div>
      )}
      {!isInternal && (
        <MessageBubble entry={e} internal={false} />
      )}
    </div>
  )
}

function MessageBubble({ entry: e, internal }: { entry: StreamEntry; internal: boolean }) {
  const isAgent = ['primary', 'specialist', 'supervisor', 'evaluator', 'reviewer'].includes(e.author_role ?? '')
  const bg      = internal
    ? '#1e2d1e'
    : isAgent ? '#1e293b' : '#0f3460'
  const border  = internal ? '1px dashed #22c55e44' : 'none'
  const text    = extractText(e.content)

  return (
    <div style={{ ...styles.bubble, backgroundColor: bg, border }}>
      <div style={styles.bubbleMeta}>
        <RoleBadge role={e.author_role} />
        {e.timestamp && (
          <span style={styles.time}>{formatTimestamp(e.timestamp)}</span>
        )}
      </div>
      <div style={styles.bubbleText}>{text}</div>
      {e.type !== 'message' && (
        <div style={styles.entryType}>{e.type}</div>
      )}
    </div>
  )
}

function SystemEvent({ entry: e }: { entry: StreamEntry }) {
  return (
    <div style={styles.systemRow}>
      <span style={styles.systemLine} />
      <span style={styles.systemText}>
        {e.type.replace(/_/g, ' ')}
        {e.timestamp ? ` · ${formatTimestamp(e.timestamp)}` : ''}
      </span>
      <span style={styles.systemLine} />
    </div>
  )
}

function RoleBadge({ role }: { role: string | null }) {
  const roleColors: Record<string, string> = {
    customer:   '#3b82f6',
    primary:    '#8b5cf6',
    specialist: '#ec4899',
    supervisor: '#f59e0b',
    evaluator:  '#14b8a6',
    reviewer:   '#94a3b8',
  }
  const color = role ? (roleColors[role] ?? '#64748b') : '#64748b'
  const label = role ?? 'unknown'

  return (
    <span style={{ ...styles.roleBadge, color, borderColor: color + '44' }}>
      {label}
    </span>
  )
}

function StatusDot({ status }: { status: string }) {
  const colors: Record<string, string> = {
    connecting: '#f59e0b',
    connected:  '#22c55e',
    error:      '#ef4444',
    closed:     '#64748b',
  }
  return (
    <span
      title={status}
      style={{
        display: 'inline-block', width: 8, height: 8, borderRadius: '50%',
        backgroundColor: colors[status] ?? '#64748b',
        boxShadow: status === 'connected' ? `0 0 0 3px ${colors.connected}33` : 'none',
      }}
    />
  )
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function extractText(content: unknown): string {
  if (!content) return ''
  if (typeof content === 'string') return content
  if (typeof content === 'object' && content !== null) {
    const c = content as Record<string, unknown>
    if (typeof c.text === 'string') return c.text
    return JSON.stringify(content, null, 2)
  }
  return String(content)
}

function formatTimestamp(ts: string): string {
  try {
    const d = new Date(ts)
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch {
    return ts
  }
}

// ─── Styles ───────────────────────────────────────────────────────────────────

import type React from 'react'

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex', flexDirection: 'column',
    height: '100%', overflow: 'hidden',
    backgroundColor: '#0a1628', color: '#e2e8f0',
  },
  header: {
    display: 'flex', alignItems: 'center', gap: 12,
    padding: '12px 16px',
    borderBottom: '1px solid #1e293b',
    backgroundColor: '#0f172a',
    flexShrink: 0,
  },
  backBtn: {
    background: 'none', border: '1px solid #334155',
    color: '#94a3b8', borderRadius: 6, padding: '4px 10px',
    cursor: 'pointer', fontSize: 13,
  },
  sessionLabel: { fontSize: 14, color: '#94a3b8' },
  sessionId: {
    fontSize: 12, color: '#e2e8f0',
    backgroundColor: '#1e293b', borderRadius: 4,
    padding: '1px 6px',
  },
  readOnly: {
    fontSize: 11, color: '#475569',
    border: '1px solid #334155', borderRadius: 4,
    padding: '2px 6px', marginLeft: 'auto',
  },
  stream: {
    flex: 1, overflowY: 'auto',
    padding: '16px 20px',
    display: 'flex', flexDirection: 'column', gap: 8,
  },
  placeholder: {
    padding: '40px', textAlign: 'center',
    color: '#475569', fontSize: 14,
  },
  bubbleRow: {
    display: 'flex',
    marginBottom: 4,
  },
  bubble: {
    maxWidth: '72%',
    borderRadius: 10,
    padding: '8px 12px',
    fontSize: 13,
  },
  bubbleMeta: {
    display: 'flex', alignItems: 'center', gap: 8,
    marginBottom: 4,
  },
  roleBadge: {
    fontSize: 10, fontWeight: 700,
    border: '1px solid', borderRadius: 4,
    padding: '1px 5px', letterSpacing: '0.04em', textTransform: 'uppercase',
  },
  time: { fontSize: 10, color: '#475569' },
  bubbleText: { lineHeight: 1.5, color: '#e2e8f0', whiteSpace: 'pre-wrap', wordBreak: 'break-word' },
  entryType: { fontSize: 10, color: '#475569', marginTop: 4 },
  internalNote: {
    display: 'flex', flexDirection: 'column', gap: 2,
    maxWidth: '72%',
  },
  internalLabel: {
    fontSize: 10, color: '#22c55e', letterSpacing: '0.06em',
    textTransform: 'uppercase', paddingLeft: 2,
  },
  systemRow: {
    display: 'flex', alignItems: 'center', gap: 10,
    margin: '6px 0', color: '#475569',
  },
  systemLine: {
    flex: 1, height: 1,
    backgroundColor: '#1e293b', display: 'block',
  },
  systemText: { fontSize: 11, whiteSpace: 'nowrap' },
}
