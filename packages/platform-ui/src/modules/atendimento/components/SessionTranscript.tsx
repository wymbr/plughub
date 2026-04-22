import { useEffect, useRef } from 'react'
import type React from 'react'
import { useSessionStream, useSupervisor } from '../api/hooks'
import { SupervisorJoinButton, SupervisorPanel } from './SupervisorPanel'
import type { StreamEntry } from '../types'

interface Props {
  tenantId:  string
  sessionId: string
  onBack:    () => void
}

export function SessionTranscript({ tenantId, sessionId, onBack }: Props) {
  const { entries, status }                        = useSessionStream(tenantId, sessionId)
  const { state: supState, join, message, leave }  = useSupervisor(tenantId, sessionId)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [entries.length])

  const isSupActive  = supState.status === 'active'
  const isSupJoining = supState.status === 'joining'

  return (
    <div style={s.container}>
      {/* Header */}
      <div style={s.header}>
        <button style={s.backBtn} onClick={onBack}>← Sessões</button>
        <span style={{ fontSize: 14, color: '#94a3b8' }}>
          Sessão{' '}
          <code style={{ fontSize: 12, color: '#e2e8f0', backgroundColor: '#1e293b', borderRadius: 4, padding: '1px 6px' }}>
            {sessionId}
          </code>
        </span>
        <StatusDot status={status} />
        {!isSupActive ? (
          <SupervisorJoinButton onJoin={() => join()} joining={isSupJoining} error={supState.status === 'error' ? supState.error : null} />
        ) : (
          <span style={{ fontSize: 12, color: '#f59e0b', border: '1px solid #f59e0b44', borderRadius: 4, padding: '2px 8px', marginLeft: 'auto', fontWeight: 600 }}>
            👁 supervisionando
          </span>
        )}
        {!isSupActive && <span style={{ fontSize: 11, color: '#475569', border: '1px solid #334155', borderRadius: 4, padding: '2px 6px', marginLeft: 'auto' }}>leitura</span>}
      </div>

      {/* Stream */}
      <div style={s.stream}>
        {entries.length === 0 && status === 'connecting' && <div style={s.placeholder}>Conectando ao stream…</div>}
        {entries.length === 0 && status === 'connected'  && <div style={s.placeholder}>Nenhum evento nesta sessão.</div>}
        {entries.length === 0 && status === 'error'      && <div style={{ ...s.placeholder, color: '#ef4444' }}>Falha ao conectar ao stream.</div>}
        {entries.map(e => <EntryRow key={e.entry_id} entry={e} />)}
        <div ref={bottomRef} />
      </div>

      {/* Supervisor panel */}
      {isSupActive && <SupervisorPanel state={supState} onMessage={message} onLeave={leave} />}
    </div>
  )
}

// ─── EntryRow ─────────────────────────────────────────────────────────────────

const SYSTEM_TYPES = new Set(['session_opened','session_closed','participant_joined','participant_left','flow_step_completed','customer_identified','medium_transitioned'])

function EntryRow({ entry: e }: { entry: StreamEntry }) {
  if (SYSTEM_TYPES.has(e.type)) return <SystemEvent entry={e} />

  const isCustomer = e.author_role === 'customer' || e.author_role === null
  const isInternal = e.visibility === 'agents_only' || Array.isArray(e.visibility)
  const align      = isCustomer ? 'flex-start' : 'flex-end'

  return (
    <div style={{ display: 'flex', justifyContent: align, marginBottom: 4 }}>
      {isInternal ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2, maxWidth: '72%' }}>
          <span style={{ fontSize: 10, color: '#22c55e', letterSpacing: '0.06em', textTransform: 'uppercase', paddingLeft: 2 }}>
            apenas agentes
          </span>
          <MessageBubble entry={e} internal />
        </div>
      ) : (
        <MessageBubble entry={e} internal={false} />
      )}
    </div>
  )
}

function MessageBubble({ entry: e, internal }: { entry: StreamEntry; internal: boolean }) {
  const isAgent = ['primary','specialist','supervisor','evaluator','reviewer'].includes(e.author_role ?? '')
  const bg      = internal ? '#1e2d1e' : isAgent ? '#1e293b' : '#0f3460'
  const border  = internal ? '1px dashed #22c55e44' : 'none'
  const text    = extractText(e.content)

  return (
    <div style={{ maxWidth: '72%', borderRadius: 10, padding: '8px 12px', fontSize: 13, backgroundColor: bg, border }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
        <RoleBadge role={e.author_role} />
        {e.timestamp && <span style={{ fontSize: 10, color: '#475569' }}>{fmtTs(e.timestamp)}</span>}
      </div>
      <div style={{ lineHeight: 1.5, color: '#e2e8f0', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{text}</div>
      {e.type !== 'message' && <div style={{ fontSize: 10, color: '#475569', marginTop: 4 }}>{e.type}</div>}
    </div>
  )
}

function SystemEvent({ entry: e }: { entry: StreamEntry }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, margin: '6px 0', color: '#475569' }}>
      <span style={{ flex: 1, height: 1, backgroundColor: '#1e293b', display: 'block' }} />
      <span style={{ fontSize: 11, whiteSpace: 'nowrap' }}>
        {e.type.replace(/_/g, ' ')}{e.timestamp ? ` · ${fmtTs(e.timestamp)}` : ''}
      </span>
      <span style={{ flex: 1, height: 1, backgroundColor: '#1e293b', display: 'block' }} />
    </div>
  )
}

function RoleBadge({ role }: { role: string | null }) {
  const roleColors: Record<string, string> = { customer: '#3b82f6', primary: '#8b5cf6', specialist: '#ec4899', supervisor: '#f59e0b', evaluator: '#14b8a6', reviewer: '#94a3b8' }
  const color = role ? (roleColors[role] ?? '#64748b') : '#64748b'
  return (
    <span style={{ fontSize: 10, fontWeight: 700, border: '1px solid', borderRadius: 4, padding: '1px 5px', letterSpacing: '0.04em', textTransform: 'uppercase', color, borderColor: color + '44' }}>
      {role ?? 'unknown'}
    </span>
  )
}

function StatusDot({ status }: { status: string }) {
  const colors: Record<string, string> = { connecting: '#f59e0b', connected: '#22c55e', error: '#ef4444', closed: '#64748b' }
  const color = colors[status] ?? '#64748b'
  return <span title={status} style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', backgroundColor: color, boxShadow: status === 'connected' ? `0 0 0 3px ${color}33` : 'none' }} />
}

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

function fmtTs(ts: string): string {
  try { return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) }
  catch { return ts }
}

const s: Record<string, React.CSSProperties> = {
  container: { display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden', backgroundColor: '#0a1628', color: '#e2e8f0' },
  header: { display: 'flex', alignItems: 'center', gap: 12, padding: '12px 16px', borderBottom: '1px solid #1e293b', backgroundColor: '#0f172a', flexShrink: 0 },
  backBtn: { background: 'none', border: '1px solid #334155', color: '#94a3b8', borderRadius: 6, padding: '4px 10px', cursor: 'pointer', fontSize: 13 },
  stream: { flex: 1, overflowY: 'auto', padding: '16px 20px', display: 'flex', flexDirection: 'column', gap: 8 },
  placeholder: { padding: 40, textAlign: 'center', color: '#475569', fontSize: 14 },
}
