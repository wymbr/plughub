/**
 * SessionList.tsx
 * Drill-down panel: list of active sessions for a pool, sorted worst-first.
 * Opened by clicking a pool tile in the heatmap. Clicking a session row opens
 * the transcript viewer.
 */
import { useActiveSessions } from '../api/hooks'
import { scoreToColor, scoreToCategory, formatMs, formatScore } from '../utils/sentiment'
import type { ActiveSession } from '../types'

interface Props {
  tenantId: string
  poolId:   string
  onSelect: (sessionId: string) => void
  onBack:   () => void
}

export function SessionList({ tenantId, poolId, onSelect, onBack }: Props) {
  const { sessions, loading } = useActiveSessions(tenantId, poolId, 10_000)

  return (
    <div style={styles.container}>
      {/* Header */}
      <div style={styles.header}>
        <button style={styles.backBtn} onClick={onBack} title="Back to heatmap">
          ← Pools
        </button>
        <span style={styles.title}>{poolId}</span>
        <span style={styles.badge}>{sessions.length} active</span>
        {loading && <span style={styles.spinner}>⟳</span>}
      </div>

      {/* Column headers */}
      <div style={styles.colHeaders}>
        <span style={{ width: 140 }}>Session ID</span>
        <span style={{ width: 90 }}>Channel</span>
        <span style={{ width: 110, textAlign: 'right' }}>Handle time</span>
        <span style={{ width: 90, textAlign: 'right' }}>Score</span>
        <span style={{ flex: 1 }}>Sentiment</span>
      </div>

      {/* Session rows */}
      <div style={styles.list}>
        {sessions.length === 0 && !loading && (
          <div style={styles.empty}>No active sessions in this pool.</div>
        )}
        {sessions.map(s => (
          <SessionRow
            key={s.session_id}
            session={s}
            onClick={() => onSelect(s.session_id)}
          />
        ))}
      </div>
    </div>
  )
}

// ─── SessionRow ────────────────────────────────────────────────────────────────

function SessionRow({ session: s, onClick }: { session: ActiveSession; onClick: () => void }) {
  const color    = scoreToColor(s.latest_score)
  const category = s.latest_category ?? scoreToCategory(s.latest_score)

  return (
    <div style={styles.row} onClick={onClick} title={`Open transcript for ${s.session_id}`}>
      {/* Sentiment accent */}
      <div style={{ ...styles.accent, backgroundColor: color }} />

      {/* Session ID (truncated) */}
      <span style={{ ...styles.cell, width: 140, fontFamily: 'monospace', fontSize: 12 }}>
        {s.session_id.length > 14 ? '…' + s.session_id.slice(-12) : s.session_id}
      </span>

      {/* Channel */}
      <span style={{ ...styles.cell, width: 90 }}>
        <ChannelBadge channel={s.channel} />
      </span>

      {/* Handle time */}
      <span style={{ ...styles.cell, width: 110, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
        {formatMs(s.handle_time_ms)}
      </span>

      {/* Score */}
      <span style={{ ...styles.cell, width: 90, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
        {formatScore(s.latest_score)}
      </span>

      {/* Category pill */}
      <span style={{ ...styles.cell, flex: 1 }}>
        <CategoryPill category={category} color={color} />
      </span>

      {/* Arrow */}
      <span style={styles.arrow}>›</span>
    </div>
  )
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function ChannelBadge({ channel }: { channel: string }) {
  const icons: Record<string, string> = {
    webchat: '💬', whatsapp: '📱', voice: '📞', email: '✉️',
    sms: '📟', instagram: '📷', telegram: '✈️', webrtc: '🎥',
  }
  return (
    <span style={styles.channelBadge}>
      {icons[channel] ?? '⬡'} {channel}
    </span>
  )
}

function CategoryPill({ category, color }: { category: string; color: string }) {
  return (
    <span style={{ ...styles.pill, backgroundColor: color + '33', color }}>
      {category}
    </span>
  )
}

// ─── Styles ───────────────────────────────────────────────────────────────────

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: 'flex', flexDirection: 'column',
    height: '100%', overflow: 'hidden',
    backgroundColor: '#0f172a', color: '#e2e8f0',
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
  title: { fontWeight: 600, fontSize: 15, color: '#e2e8f0' },
  badge: {
    fontSize: 12, color: '#64748b',
    backgroundColor: '#1e293b', borderRadius: 4,
    padding: '2px 8px',
  },
  spinner: { fontSize: 16, color: '#64748b', animation: 'spin 1s linear infinite' },
  colHeaders: {
    display: 'flex', alignItems: 'center', gap: 0,
    padding: '6px 20px 6px 24px',
    borderBottom: '1px solid #1e293b',
    fontSize: 11, fontWeight: 600,
    color: '#475569', letterSpacing: '0.06em', textTransform: 'uppercase',
    flexShrink: 0,
  },
  list: { flex: 1, overflowY: 'auto' },
  empty: {
    padding: '40px 24px', textAlign: 'center',
    color: '#475569', fontSize: 14,
  },
  row: {
    display: 'flex', alignItems: 'center',
    padding: '10px 16px 10px 0',
    borderBottom: '1px solid #1e293b',
    cursor: 'pointer',
    transition: 'background 0.1s',
    position: 'relative',
  },
  accent: {
    width: 4, alignSelf: 'stretch',
    marginRight: 16, borderRadius: 2, flexShrink: 0,
  },
  cell: { display: 'flex', alignItems: 'center', paddingRight: 8, fontSize: 13 },
  channelBadge: { fontSize: 12, color: '#94a3b8' },
  pill: {
    fontSize: 11, borderRadius: 4,
    padding: '2px 8px', fontWeight: 600,
  },
  arrow: { marginLeft: 'auto', color: '#334155', fontSize: 18, paddingRight: 4 },
}

// React import for CSSProperties
import type React from 'react'
