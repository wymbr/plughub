import { useEffect, useRef, useState } from 'react'
import type React from 'react'
import { useSessionStream, useSupervisor } from '../api/hooks'
import { SupervisorJoinButton, SupervisorPanel } from './SupervisorPanel'
import type { ContactSegment, StreamEntry } from '../types'

interface Props {
  tenantId:  string
  sessionId: string
  onBack:    () => void
  /** When false (ended segment), supervisor join is hidden — only read-only view available. Default: true */
  canJoin?:  boolean
  /**
   * When present, entries are split into three accordion buckets:
   *   - before segment.started_at  → collapsed by default
   *   - during [started_at, ended_at] → expanded
   *   - after segment.ended_at       → collapsed by default
   */
  segment?:  ContactSegment
}

export function SessionTranscript({ tenantId, sessionId, onBack, canJoin = true, segment }: Props) {
  const { entries, status }                        = useSessionStream(tenantId, sessionId)
  const { state: supState, join, message, leave }  = useSupervisor(tenantId, sessionId)
  const bottomRef   = useRef<HTMLDivElement>(null)
  const duringRef   = useRef<HTMLDivElement>(null)

  // Accordion state for before/after buckets
  const [showBefore, setShowBefore] = useState(false)
  const [showAfter,  setShowAfter]  = useState(false)

  // Auto-scroll: when no segment, scroll to bottom; when segment, scroll to "during" block
  useEffect(() => {
    if (segment) {
      duringRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    } else {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entries.length])

  const isSupActive  = supState.status === 'active'
  const isSupJoining = supState.status === 'joining'

  // ── Segment filtering ───────────────────────────────────────────────────────
  let before: StreamEntry[] = []
  let during: StreamEntry[] = []
  let after:  StreamEntry[] = []

  if (segment) {
    const startMs = new Date(segment.started_at).getTime()
    const endMs   = segment.ended_at ? new Date(segment.ended_at).getTime() : Infinity

    for (const e of entries) {
      if (!e.timestamp) { during.push(e); continue }
      const ts = new Date(e.timestamp).getTime()
      if (ts < startMs) before.push(e)
      else if (ts <= endMs) during.push(e)
      else after.push(e)
    }
  } else {
    during = entries
  }

  // ── Header label ────────────────────────────────────────────────────────────
  const segmentLabel = segment
    ? `${segment.role} · ${segment.agent_type === 'human' ? '👤' : '🤖'} ${segment.participant_id}`
    : null

  return (
    <div style={s.container}>
      {/* Header */}
      <div style={s.header}>
        <button style={s.backBtn} onClick={onBack}>← Segmentos</button>
        <span style={{ fontSize: 14, color: '#94a3b8' }}>
          Sessão{' '}
          <code style={{ fontSize: 12, color: '#e2e8f0', backgroundColor: '#1e293b', borderRadius: 4, padding: '1px 6px' }}>
            {sessionId}
          </code>
        </span>
        {segmentLabel && (
          <span style={{ fontSize: 11, color: '#818cf8', border: '1px solid #818cf844', borderRadius: 4, padding: '2px 8px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 260 }}>
            🔍 {segmentLabel}
          </span>
        )}
        <StatusDot status={status} />
        {canJoin && !isSupActive && (
          <SupervisorJoinButton onJoin={() => join()} joining={isSupJoining} error={supState.status === 'error' ? supState.error : null} />
        )}
        {canJoin && isSupActive && (
          <span style={{ fontSize: 12, color: '#f59e0b', border: '1px solid #f59e0b44', borderRadius: 4, padding: '2px 8px', marginLeft: 'auto', fontWeight: 600 }}>
            👁 supervisionando
          </span>
        )}
        {!isSupActive && (
          <span style={{ fontSize: 11, color: canJoin ? '#475569' : '#374151', border: `1px solid ${canJoin ? '#334155' : '#1f2937'}`, borderRadius: 4, padding: '2px 6px', marginLeft: 'auto' }}>
            {canJoin ? 'leitura' : 'encerrado · somente leitura'}
          </span>
        )}
      </div>

      {/* Stream */}
      <div style={s.stream}>
        {entries.length === 0 && status === 'connecting' && <div style={s.placeholder}>Conectando ao stream…</div>}
        {entries.length === 0 && status === 'connected'  && <div style={s.placeholder}>Nenhum evento nesta sessão.</div>}
        {entries.length === 0 && status === 'error'      && <div style={{ ...s.placeholder, color: '#ef4444' }}>Falha ao conectar ao stream.</div>}

        {segment ? (
          /* ── Segment accordion view ── */
          <>
            {/* Before bucket */}
            {before.length > 0 && (
              <AccordionBucket
                label={`Antes do segmento · ${before.length} evento${before.length !== 1 ? 's' : ''}`}
                open={showBefore}
                onToggle={() => setShowBefore(v => !v)}
                accent="#475569"
              >
                {before.map(e => <EntryRow key={e.entry_id} entry={e} />)}
              </AccordionBucket>
            )}

            {/* During bucket — always expanded, scrolled into view */}
            <div ref={duringRef}>
              <SegmentDivider label="▶ Início do segmento" color="#818cf8" ts={segment.started_at} />
              {during.length === 0 ? (
                <div style={{ ...s.placeholder, padding: 20 }}>Nenhum evento durante este segmento.</div>
              ) : (
                during.map(e => <EntryRow key={e.entry_id} entry={e} />)
              )}
              {segment.ended_at && <SegmentDivider label="■ Fim do segmento" color="#818cf8" ts={segment.ended_at} />}
            </div>

            {/* After bucket */}
            {after.length > 0 && (
              <AccordionBucket
                label={`Depois do segmento · ${after.length} evento${after.length !== 1 ? 's' : ''}`}
                open={showAfter}
                onToggle={() => setShowAfter(v => !v)}
                accent="#475569"
              >
                {after.map(e => <EntryRow key={e.entry_id} entry={e} />)}
              </AccordionBucket>
            )}
          </>
        ) : (
          /* ── Full stream view ── */
          <>
            {during.map(e => <EntryRow key={e.entry_id} entry={e} />)}
            <div ref={bottomRef} />
          </>
        )}
      </div>

      {/* Supervisor panel */}
      {isSupActive && <SupervisorPanel state={supState} onMessage={message} onLeave={leave} />}
    </div>
  )
}

// ─── Accordion bucket ─────────────────────────────────────────────────────────

function AccordionBucket({ label, open, onToggle, accent, children }: {
  label: string; open: boolean; onToggle: () => void; accent: string; children: React.ReactNode
}) {
  return (
    <div style={{ marginBottom: 4 }}>
      <button
        onClick={onToggle}
        style={{
          width: '100%', display: 'flex', alignItems: 'center', gap: 8,
          background: 'none', border: `1px solid ${accent}44`, borderRadius: 6,
          color: accent, fontSize: 11, padding: '5px 10px', cursor: 'pointer',
          textAlign: 'left', letterSpacing: '0.04em',
        }}
      >
        <span style={{ flexShrink: 0 }}>{open ? '▾' : '▸'}</span>
        <span style={{ flex: 1 }}>{label}</span>
      </button>
      {open && (
        <div style={{ paddingLeft: 8, marginTop: 4, borderLeft: `2px solid ${accent}44` }}>
          {children}
        </div>
      )}
    </div>
  )
}

// ─── Segment divider ─────────────────────────────────────────────────────────

function SegmentDivider({ label, color, ts }: { label: string; color: string; ts: string }) {
  const time = ts ? fmtTs(ts) : ''
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, margin: '10px 0 6px', color }}>
      <span style={{ flex: 1, height: 1, backgroundColor: color + '44', display: 'block' }} />
      <span style={{ fontSize: 11, whiteSpace: 'nowrap', fontWeight: 600, letterSpacing: '0.05em' }}>
        {label}{time ? ` · ${time}` : ''}
      </span>
      <span style={{ flex: 1, height: 1, backgroundColor: color + '44', display: 'block' }} />
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
