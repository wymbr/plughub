import { useCallback, useEffect, useRef, useState } from 'react'
import type React from 'react'
import { Play } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useSessionStream, useSupervisor } from '../api/hooks'
import { SupervisorJoinButton, SupervisorPanel } from './SupervisorPanel'
import { renderWithTokens, useMaskingDisplayRules } from '@/components/MaskedToken'
import type { ContactSegment, StreamEntry } from '../types'

// ─── Business event types ─────────────────────────────────────────────────────

interface InsightRow {
  insight_id:   string
  tenant_id:    string
  session_id:   string
  insight_type: string
  category:     string | null
  value:        string | null
  tags:         string[]
  agent_id:     string | null
  timestamp:    string
}

// ─── useSessionInsights hook ──────────────────────────────────────────────────

function useSessionInsights(tenantId: string, sessionId: string | null): {
  insights: InsightRow[]; loading: boolean
} {
  const [insights, setInsights] = useState<InsightRow[]>([])
  const [loading, setLoading]   = useState(false)

  useEffect(() => {
    if (!tenantId || !sessionId) { setInsights([]); return }
    let cancelled = false
    setLoading(true)

    const params = new URLSearchParams({ tenant_id: tenantId, session_id: sessionId, page_size: '200' })
    fetch(`/reports/contact-insights?${params}`)
      .then(r => r.json())
      .then((data: { data?: InsightRow[] } | InsightRow[]) => {
        if (cancelled) return
        setInsights(Array.isArray(data) ? data : (data.data ?? []))
      })
      .catch(() => { if (!cancelled) setInsights([]) })
      .finally(() => { if (!cancelled) setLoading(false) })

    return () => { cancelled = true }
  }, [tenantId, sessionId])

  return { insights, loading }
}

// System event types that should always pass through specialist filtering

const SYSTEM_TYPES_SET = new Set([
  'session_opened','session_closed','participant_joined','participant_left',
  'flow_step_completed','customer_identified','medium_transitioned',
])

// ─── Specialist visibility filter ────────────────────────────────────────────

/**
 * Safely parse visibility — handles both parsed arrays and JSON-encoded strings.
 * Redis stores visibility as JSON.stringify(array), and some code paths deliver
 * it to the frontend still as a string like '["cust_xxx"]' instead of a real array.
 */
function parseVis(vis: unknown): string | string[] {
  if (Array.isArray(vis)) return vis
  if (typeof vis === 'string' && vis.startsWith('[')) {
    try { return JSON.parse(vis) as string[] } catch { return vis }
  }
  return (vis as string) ?? 'all'
}

/**
 * Checks whether a segment's agent sends messages that target customers.
 * Returns true if any message from this segment uses visibility "all" or
 * a visibility array containing a cust_* participant ID.
 */
function segmentTalksToCustomers(
  segment: ContactSegment,
  allEntries: StreamEntry[],
): boolean {
  for (const entry of allEntries) {
    // Match entries FROM this segment's agent (by author_id).
    // We cannot rely on segment_id because it is not written to the stream
    // by all code paths (message_send omits it).
    if (entry.author_id !== segment.participant_id) continue
    const vis = parseVis(entry.visibility)
    if (vis === 'all') return true
    if (Array.isArray(vis) && vis.some(v => typeof v === 'string' && v.startsWith('cust_'))) return true
  }
  return false
}

/**
 * Checks whether a segment's agent sends messages that target a specific
 * participant (by ID). Returns true if any message from this segment uses
 * visibility "all", "agents_only", or a visibility array containing the ID.
 */
function segmentTalksToParticipant(
  segment: ContactSegment,
  participantId: string,
  allEntries: StreamEntry[],
): boolean {
  for (const entry of allEntries) {
    if (entry.author_id !== segment.participant_id) continue
    const vis = parseVis(entry.visibility)
    if (vis === 'all') return true
    if (vis === 'agents_only') return true
    if (Array.isArray(vis) && vis.includes(participantId)) return true
  }
  return false
}

/**
 * Determines if a stream entry belongs to a specialist segment.
 *
 * Uses the same visibility-based distribution logic as the platform:
 *
 *   1. **segment_id UUID match** — definitive when present on both sides.
 *   2. **author_id match** — the entry's author IS this segment's agent → include.
 *      A known agent that is NOT this segment's agent falls through to step 3
 *      (it may still belong here, e.g. human agent replying to wrap-up questions).
 *   3. **Time window gate** — entry must fall within [started_at, ended_at].
 *   4. **Visibility routing**:
 *        - Array of pids → include only if segment.participant_id is listed.
 *        - "agents_only" → include in all agent segments.
 *        - "all" / undefined → use conversation membership: include only if this
 *          segment's agent communicates with the entry's author (checked via the
 *          visibility values on the segment's own messages in allEntries).
 */
function entryBelongsToSpecialist(
  e: StreamEntry,
  segment: ContactSegment,
  allEntries?: StreamEntry[],
): boolean {
  if (SYSTEM_TYPES_SET.has(e.type)) return true

  // ── 1. segment_id UUID match (definitive) ──
  if (e.segment_id && segment.segment_id) {
    return e.segment_id === segment.segment_id
  }

  // ── 2. author_id (instance_id) match ──
  const authorId = e.author_id
  const isCustomerEntry = e.author_role === 'customer'
    || authorId === 'customer'
    || (typeof authorId === 'string' && authorId.startsWith('cust_'))

  const isKnownAgent = !!authorId
    && authorId !== 'orchestrator'
    && authorId !== 'customer'
    && !isCustomerEntry

  if (isKnownAgent && segment.participant_id) {
    if (authorId === segment.participant_id) return true
    // Known agent but NOT this segment's agent — don't exclude yet.
    // Fall through: the message may be directed to this segment's agent
    // (e.g., human agent replying to wrap-up agent's questions).
  }

  // ── 3. Time window gate ──
  if (!e.timestamp || !segment.started_at) return false

  const eTime = new Date(e.timestamp).getTime()
  const start = new Date(segment.started_at).getTime()
  if (eTime < start) return false
  if (segment.ended_at) {
    const end = new Date(segment.ended_at).getTime()
    if (eTime > end) return false
  }

  // ── 4. Visibility routing ──
  const vis = parseVis(e.visibility)

  // Array of participant_ids → include if this segment's agent is listed,
  // OR if the message author is someone this segment's agent communicates with
  // (e.g. human agent replying to wrap-up menu — the reply's visibility is the
  //  same array that the wrap-up prompt used to target the human, so it doesn't
  //  contain the wrap-up agent's ID, but it IS part of the wrap-up conversation).
  if (Array.isArray(vis) && segment.participant_id) {
    if (vis.includes(segment.participant_id)) return true
    // Check: does this segment's agent talk to the entry's author?
    if (allEntries && authorId) {
      return segmentTalksToParticipant(segment, authorId, allEntries)
    }
    return false
  }

  // "agents_only" → delivered to all agents → include in every agent segment
  if (vis === 'agents_only') return true

  // "all" / undefined / null — visible to everyone.
  // When multiple segments overlap in time, determine membership by checking
  // whether this segment's agent communicates with the entry's author.
  if (allEntries && allEntries.length > 0 && authorId) {
    if (isCustomerEntry) {
      // Customer message: include only if this segment's agent targets customers
      return segmentTalksToCustomers(segment, allEntries)
    }
    if (isKnownAgent) {
      // Non-matching agent: include only if this segment's agent targets them
      return segmentTalksToParticipant(segment, authorId, allEntries)
    }
  }

  // Fallback (orchestrator, unknown, no allEntries) → include
  return true
}

// ─── Sensitive data note ──────────────────────────────────────────────────────
// Masking tokens ([category:tk_xxx:display]) are rendered by renderWithTokens()
// in EntryRow. No crude keyword-based scrubbing — the platform uses structured
// tokens for all sensitive data; keyword scrubbing was a temporary fallback.

interface Props {
  tenantId:  string
  sessionId: string
  onBack:    () => void
  canJoin?:  boolean
  segment?:  ContactSegment
}

export function SessionTranscript({ tenantId, sessionId, onBack, canJoin = true, segment }: Props) {
  const { t }                                      = useTranslation('contacts')
  const { entries, status }                        = useSessionStream(tenantId, sessionId)
  const { state: supState, join, message, leave }  = useSupervisor(tenantId, sessionId)
  const maskingRules                               = useMaskingDisplayRules()
  const { insights }                               = useSessionInsights(tenantId, sessionId)
  const bottomRef   = useRef<HTMLDivElement>(null)
  const duringRef   = useRef<HTMLDivElement>(null)

  const [showBefore, setShowBefore] = useState(false)
  const [showAfter,  setShowAfter]  = useState(false)
  const [showEvents, setShowEvents] = useState(true)

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

  const handleSend = useCallback((text: string) => {
    if (isSupActive) message(text)
  }, [isSupActive, message])

  // ── partition entries when viewing a segment ──
  let before: StreamEntry[] = []
  let during: StreamEntry[] = []
  let after:  StreamEntry[] = []

  if (segment) {
    const startMs = new Date(segment.started_at).getTime()
    const endMs   = segment.ended_at ? new Date(segment.ended_at).getTime() : Infinity

    const isSpecialist = segment.role === 'specialist'

    for (const entry of entries) {
      const ts = entry.timestamp ? new Date(entry.timestamp).getTime() : 0
      if (ts < startMs) { before.push(entry); continue }
      if (ts > endMs)   { after.push(entry); continue }

      if (isSpecialist) {
        if (entryBelongsToSpecialist(entry, segment, entries)) {
          during.push(entry)
        }
      } else {
        during.push(entry)
      }
    }
  } else {
    during = entries.slice()
  }

  const segmentInsights = segment
    ? insights.filter(i => {
        if (!i.timestamp) return false
        const ts    = new Date(i.timestamp).getTime()
        const start = new Date(segment.started_at).getTime()
        const end   = segment.ended_at ? new Date(segment.ended_at).getTime() : Infinity
        return ts >= start && ts <= end
      })
    : insights

  const segmentAgent = segment
    ? (segment.agent_type === 'human' && segment.user_login
        ? segment.user_login
        : segment.participant_id)
    : ''
  const segmentLabel = segment
    ? `${segment.role} · ${segment.agent_type === 'human' ? '\u{1F464}' : '\u{1F916}'} ${segmentAgent}`
    : null

  return (
    <div style={s.container}>
      <div style={s.header}>
        <button style={s.backBtn} onClick={onBack}>{t('transcript.back')}</button>
        <span style={{ fontSize: 14, color: '#94a3b8' }}>
          {t('transcript.session')}{' '}
          <code style={{ fontSize: 12, color: '#e2e8f0', backgroundColor: '#1e293b', borderRadius: 4, padding: '1px 6px' }}>
            {sessionId}
          </code>
        </span>
        {segmentLabel && (
          <span style={{ fontSize: 11, color: '#818cf8', border: '1px solid #818cf844', borderRadius: 4, padding: '2px 8px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 260 }}>
            {'\u{1F50D}'} {segmentLabel}
          </span>
        )}
        <StatusDot status={status} />
        {canJoin && !isSupActive && (
          <SupervisorJoinButton onJoin={() => join()} joining={isSupJoining} error={supState.status === 'error' ? supState.error : null} />
        )}
        {canJoin && isSupActive && (
          <span style={{ fontSize: 12, color: '#f59e0b', border: '1px solid #f59e0b44', borderRadius: 4, padding: '2px 8px', marginLeft: 'auto', fontWeight: 600 }}>
            {t('transcript.supervising')}
          </span>
        )}
        {!isSupActive && (
          <span style={{ fontSize: 11, color: canJoin ? '#475569' : '#374151', border: `1px solid ${canJoin ? '#334155' : '#1f2937'}`, borderRadius: 4, padding: '2px 6px', marginLeft: 'auto' }}>
            {canJoin ? t('transcript.readOnly') : t('transcript.closedReadOnly')}
          </span>
        )}
      </div>

      <div style={s.stream}>
        {/* ── Before segment ── */}
        {segment && before.length > 0 && (
          <div style={{ marginBottom: 8 }}>
            <button onClick={() => setShowBefore(!showBefore)} style={{ background: 'none', border: 'none', color: '#475569', cursor: 'pointer', fontSize: 12, padding: '4px 0' }}>
              {showBefore ? '▾' : '▸'} {t('transcript.beforeSegment', { count: before.length })}
            </button>
            {showBefore && before.map(e => <EntryRow key={e.entry_id} e={e} showEvents={showEvents} maskingRules={maskingRules} />)}
          </div>
        )}

        {/* ── Segment start marker ── */}
        {segment && (
          <div ref={duringRef} style={{ display: 'flex', alignItems: 'center', gap: 10, margin: '8px 0' }}>
            <span style={{ flex: 1, height: 2, background: 'linear-gradient(90deg, transparent, #22c55e)', display: 'block' }} />
            <span style={{ fontSize: 11, color: '#22c55e', fontWeight: 600, whiteSpace: 'nowrap' }}>
              <Play className="w-3 h-3 inline-block" aria-hidden="true" /> {t('transcript.segmentStart')} · {segment.started_at ? fmtTs(segment.started_at) : ''}
            </span>
            <span style={{ flex: 1, height: 2, background: 'linear-gradient(90deg, #22c55e, transparent)', display: 'block' }} />
          </div>
        )}

        {/* ── During segment ── */}
        {during.length === 0 && <p style={s.placeholder}>{t('transcript.noEvents')}</p>}
        {during.map(e => <EntryRow key={e.entry_id} e={e} showEvents={showEvents} maskingRules={maskingRules} />)}

        {/* ── Segment insights ── */}
        {segmentInsights.length > 0 && (
          <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 6 }}>
            <span style={{ fontSize: 11, color: '#64748b', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
              {t('transcript.insights', { count: segmentInsights.length })}
            </span>
            {segmentInsights.map(row => <InsightCard key={row.insight_id} row={row} />)}
          </div>
        )}

        {/* ── Segment end marker ── */}
        {segment && (() => {
          // Prefer the authoritative ended_at from ClickHouse.
          // Fallback: find participant_left event for this segment's agent in the
          // during/after entries (covers cases where ended_at is not yet written,
          // e.g. specialist that escalated without an explicit close).
          const endedAt = segment.ended_at
          if (endedAt) {
            return (
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, margin: '8px 0' }}>
                <span style={{ flex: 1, height: 2, background: 'linear-gradient(90deg, transparent, #ef4444)', display: 'block' }} />
                <span style={{ fontSize: 11, color: '#ef4444', fontWeight: 600, whiteSpace: 'nowrap' }}>
                  {'■'} {t('transcript.segmentEnd')} · {fmtTs(endedAt)}
                </span>
                <span style={{ flex: 1, height: 2, background: 'linear-gradient(90deg, #ef4444, transparent)', display: 'block' }} />
              </div>
            )
          }
          // Look for participant_left event.
          // Strategy: try exact participant_id match first (author_id or payload field),
          // then fall back to any participant_left in 'during' — which is already
          // time-filtered to this segment's window, so it is always the right event.
          const matchesParticipant = (e: StreamEntry) =>
            e.author_id === segment.participant_id ||
            (e as unknown as Record<string, unknown>).participant_id === segment.participant_id

          const leftEvent =
            [...during, ...after].find(e => e.type === 'participant_left' && matchesParticipant(e)) ??
            during.find(e => e.type === 'participant_left')
          if (leftEvent) {
            return (
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, margin: '8px 0' }}>
                <span style={{ flex: 1, height: 2, background: 'linear-gradient(90deg, transparent, #f97316)', display: 'block' }} />
                <span style={{ fontSize: 11, color: '#f97316', fontWeight: 600, whiteSpace: 'nowrap' }}>
                  {'↩'} {t('transcript.participantLeft')} · {leftEvent.timestamp ? fmtTs(leftEvent.timestamp) : ''}
                </span>
                <span style={{ flex: 1, height: 2, background: 'linear-gradient(90deg, #f97316, transparent)', display: 'block' }} />
              </div>
            )
          }
          return null
        })()}

        {/* ── After segment ── */}
        {segment && after.length > 0 && (
          <div style={{ marginTop: 8 }}>
            <button onClick={() => setShowAfter(!showAfter)} style={{ background: 'none', border: 'none', color: '#475569', cursor: 'pointer', fontSize: 12, padding: '4px 0' }}>
              {showAfter ? '▾' : '▸'} {t('transcript.afterSegment', { count: after.length })}
            </button>
            {showAfter && after.map(e => <EntryRow key={e.entry_id} e={e} showEvents={showEvents} maskingRules={maskingRules} />)}
          </div>
        )}

        {/* ── Events toggle ── */}
        {!segment && (
          <div style={{ marginTop: 8, textAlign: 'center' }}>
            <button onClick={() => setShowEvents(!showEvents)} style={{ background: 'none', border: '1px solid #334155', color: '#94a3b8', borderRadius: 6, padding: '4px 12px', cursor: 'pointer', fontSize: 12 }}>
              {showEvents ? t('transcript.hideEvents') : t('transcript.showEvents')}
            </button>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {isSupActive && (
        <SupervisorPanel state={supState} onMessage={message} onLeave={() => leave()} />
      )}
    </div>
  )
}

// ─── Sub-components ────────────────────────────────────────────────────────────

function EntryRow({ e, showEvents, maskingRules }: {
  e: StreamEntry
  showEvents: boolean
  maskingRules?: import('@/components/MaskedToken').MaskingRulesMap
}) {
  const { t } = useTranslation('contacts')
  const isEvent = SYSTEM_TYPES_SET.has(e.type)
  if (isEvent) return showEvents ? <EventRow e={e} /> : null

  const isAgent    = e.author_role !== 'customer'
  const isInternal = e.visibility === 'agents_only'
  const normalized = normalizeContent(e.content)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: isAgent ? 'flex-end' : 'flex-start' }}>
      <div style={{
        maxWidth: '75%',
        padding: '8px 12px',
        borderRadius: 8,
        fontSize: 13,
        lineHeight: 1.5,
        ...(isInternal
          ? { backgroundColor: '#fef3c7', border: '1px dashed #f59e0b', color: '#92400e' }
          : isAgent
            ? { backgroundColor: '#1e293b', color: '#e2e8f0' }
            : { backgroundColor: '#1e3a5f', color: '#bfdbfe' })
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
          {isInternal && <span style={{ fontSize: 9, fontWeight: 700, color: '#d97706', textTransform: 'uppercase' }}>{t('transcript.internal')}</span>}
          <RoleBadge role={e.author_role} />
          <span style={{ fontSize: 10, color: '#64748b' }}>{e.timestamp ? fmtTs(e.timestamp) : ''}</span>
        </div>
        <ContentRenderer normalized={normalized} maskingRules={maskingRules} />
      </div>
    </div>
  )
}

function EventRow({ e }: { e: StreamEntry }) {
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

function InsightCard({ row }: { row: InsightRow }) {
  const isHistorico = row.insight_type?.startsWith('insight.historico')
  const isConvo     = row.insight_type?.startsWith('insight.conversa')
  const borderColor = isHistorico ? '#a78bfa' : isConvo ? '#2dd4bf' : '#60a5fa'
  const badgeBg     = isHistorico ? '#a78bfa22' : isConvo ? '#2dd4bf22' : '#60a5fa22'
  const badgeColor  = isHistorico ? '#c4b5fd' : isConvo ? '#5eead4' : '#93c5fd'
  const dt          = row.timestamp ? fmtTs(row.timestamp) : ''
  const hasTags     = Array.isArray(row.tags) && row.tags.length > 0

  return (
    <div style={{ borderLeft: `3px solid ${borderColor}`, borderRadius: 6, padding: '6px 10px', backgroundColor: '#111827' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', marginBottom: 3 }}>
        <span style={{ fontSize: 10, fontWeight: 600, padding: '1px 6px', borderRadius: 10, backgroundColor: badgeBg, color: badgeColor }}>
          {row.insight_type}
        </span>
        {row.category && (
          <span style={{ fontSize: 10, padding: '1px 6px', borderRadius: 10, backgroundColor: '#1f2937', color: '#9ca3af' }}>
            {row.category}
          </span>
        )}
        {dt && <span style={{ fontSize: 10, color: '#475569', marginLeft: 'auto' }}>{dt}</span>}
      </div>
      {row.value && <p style={{ fontSize: 12, color: '#d1d5db', lineHeight: 1.4, margin: 0 }}>{row.value}</p>}
      {hasTags && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 4 }}>
          {row.tags.map(tag => (
            <span key={tag} style={{ fontSize: 10, padding: '1px 5px', borderRadius: 4, border: '1px solid #374151', color: '#6b7280', backgroundColor: '#0f172a' }}>
              #{tag}
            </span>
          ))}
        </div>
      )}
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

// ─── Content normalisation ────────────────────────────────────────────────────
//
// The stream stores content in several shapes depending on the sender:
//   • Human / customer  → plain string
//   • notify step       → { type:"text", text:"..." }
//   • menu step         → { type:"menu", text:"...", options:[{id,label},...] }
//   • Specialist outer  → { message_id:"...", content:{ type, text, options? } }
//   • Legacy            → { text:"..." }  (no type)
//
// normalizeContent() unwraps all shapes into a single NormalizedContent so
// ContentRenderer can render each type uniformly.

interface NormalizedText    { kind: 'text';   text: string }
interface NormalizedMenu    { kind: 'menu';   text: string; options: { id: string; label: string }[]; mode?: string }
interface NormalizedButton  { kind: 'button'; text: string; options: { id: string; label: string }[] }
interface NormalizedForm    { kind: 'form';   text: string; fields: { id: string; label: string; type?: string }[] }
interface NormalizedUnknown { kind: 'raw';    json: string }
type NormalizedContent = NormalizedText | NormalizedMenu | NormalizedButton | NormalizedForm | NormalizedUnknown

function normalizeContent(content: unknown): NormalizedContent {
  if (!content) return { kind: 'text', text: '' }
  if (typeof content === 'string') return { kind: 'text', text: content }

  if (typeof content === 'object' && content !== null) {
    let obj = content as Record<string, unknown>

    // Unwrap specialist wrapper: { message_id, content: { type, text, ... } }
    if (obj.message_id && obj.content && typeof obj.content === 'object') {
      obj = obj.content as Record<string, unknown>
    }

    const type    = typeof obj.type    === 'string' ? obj.type    : ''
    const text    = typeof obj.text    === 'string' ? obj.text    : ''
    const options = Array.isArray(obj.options) ? obj.options as { id: string; label: string }[] : []
    const fields  = Array.isArray(obj.fields)  ? obj.fields  as { id: string; label: string; type?: string }[] : []

    if (type === 'text' || (!type && text)) return { kind: 'text', text }
    if (type === 'menu')   return { kind: 'menu',   text, options, mode: typeof obj.mode === 'string' ? obj.mode : undefined }
    if (type === 'button') return { kind: 'button', text, options }
    if (type === 'form')   return { kind: 'form',   text, fields }
    if (type === 'list')   return { kind: 'menu',   text, options }

    // Fallback: try to extract any text-ish field before giving up
    if (text) return { kind: 'text', text }
  }

  return { kind: 'raw', json: JSON.stringify(content, null, 2) }
}

// ─── ContentRenderer ─────────────────────────────────────────────────────────

function ContentRenderer({ normalized, maskingRules }: {
  normalized: NormalizedContent
  maskingRules?: import('@/components/MaskedToken').MaskingRulesMap
}) {
  const { t } = useTranslation('contacts')
  if (normalized.kind === 'text') {
    return <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{renderWithTokens(normalized.text, maskingRules)}</div>
  }

  if (normalized.kind === 'menu' || normalized.kind === 'button') {
    const isButton = normalized.kind === 'button'
    return (
      <div>
        {normalized.text && (
          <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', marginBottom: normalized.options.length ? 8 : 0 }}>
            {renderWithTokens(normalized.text, maskingRules)}
          </div>
        )}
        {normalized.options.length > 0 && (
          <div style={{ display: 'flex', flexDirection: isButton ? 'row' : 'column', flexWrap: 'wrap', gap: 4, marginTop: 2 }}>
            {normalized.options.map((opt, i) => (
              <div key={opt.id ?? i} style={{
                display: 'inline-flex', alignItems: 'center', gap: 6,
                padding: isButton ? '4px 10px' : '3px 8px',
                borderRadius: isButton ? 12 : 4,
                border: '1px solid #334155',
                backgroundColor: '#0f172a',
                color: '#94a3b8',
                fontSize: 11,
                cursor: 'default',
              }}>
                {!isButton && <span style={{ color: '#475569', fontWeight: 600, minWidth: 14 }}>{i + 1}.</span>}
                {opt.label ?? opt.id}
              </div>
            ))}
          </div>
        )}
        {!isButton && normalized.options.length === 0 && (
          <span style={{ fontSize: 10, color: '#475569', fontStyle: 'italic' }}>{t('transcript.menuNoOptions')}</span>
        )}
      </div>
    )
  }

  if (normalized.kind === 'form') {
    return (
      <div>
        {normalized.text && (
          <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', marginBottom: 6 }}>
            {renderWithTokens(normalized.text, maskingRules)}
          </div>
        )}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          {normalized.fields.map((f, i) => (
            <div key={f.id ?? i} style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 11, color: '#64748b' }}>
              <span style={{ color: '#475569' }}>□</span>
              <span>{f.label ?? f.id}</span>
              {f.type && <span style={{ color: '#334155', fontSize: 10 }}>({f.type})</span>}
            </div>
          ))}
        </div>
      </div>
    )
  }

  // Raw fallback — collapse by default to avoid wall of JSON
  return (
    <details style={{ fontSize: 11 }}>
      <summary style={{ cursor: 'pointer', color: '#64748b', userSelect: 'none' }}>{t('transcript.structuredContent')}</summary>
      <pre style={{ marginTop: 6, padding: '6px 8px', backgroundColor: '#0f172a', borderRadius: 4, overflowX: 'auto', color: '#94a3b8', fontSize: 11, lineHeight: 1.4 }}>
        {normalized.json}
      </pre>
    </details>
  )
}

function fmtTs(iso: string): string {
  try {
    const d = new Date(iso)
    return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch { return iso }
}

const s: Record<string, React.CSSProperties> = {
  container:   { display: 'flex', flexDirection: 'column', height: '100%', backgroundColor: '#0f172a', color: '#e2e8f0' },
  header:      { display: 'flex', alignItems: 'center', gap: 10, padding: '10px 16px', borderBottom: '1px solid #1e293b', flexWrap: 'wrap', flexShrink: 0, position: 'sticky', top: 0, zIndex: 10, backgroundColor: '#0f172a' },
  backBtn:     { background: 'none', border: '1px solid #334155', color: '#94a3b8', borderRadius: 6, padding: '4px 12px', cursor: 'pointer', fontSize: 13 },
  stream:      { flex: 1, minHeight: 0, overflowY: 'auto', padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: 8 },
  placeholder: { color: '#475569', fontStyle: 'italic', fontSize: 13, textAlign: 'center', marginTop: 24 },
}
