import type React from 'react'
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
    <div style={s.container}>
      <div style={s.header}>
        <button style={s.backBtn} onClick={onBack}>← Pools</button>
        <span style={s.title}>{poolId.replace(/_/g, ' ')}</span>
        <span style={s.badge}>{sessions.length} ativas</span>
        {loading && <span style={{ fontSize: 14, color: '#64748b' }}>⟳</span>}
      </div>

      <div style={s.colHeaders}>
        <span style={{ width: 140 }}>Session ID</span>
        <span style={{ width: 90 }}>Canal</span>
        <span style={{ width: 110, textAlign: 'right' }}>Duração</span>
        <span style={{ width: 90, textAlign: 'right' }}>Score</span>
        <span style={{ flex: 1 }}>Sentimento</span>
      </div>

      <div style={s.list}>
        {sessions.length === 0 && !loading && (
          <div style={s.empty}>Nenhuma sessão ativa neste pool.</div>
        )}
        {sessions.map(sess => (
          <SessionRow key={sess.session_id} session={sess} onClick={() => onSelect(sess.session_id)} />
        ))}
      </div>
    </div>
  )
}

function SessionRow({ session: sess, onClick }: { session: ActiveSession; onClick: () => void }) {
  const color    = scoreToColor(sess.latest_score)
  const category = sess.latest_category ?? scoreToCategory(sess.latest_score)

  return (
    <div style={s.row} onClick={onClick}>
      <div style={{ ...s.accent, backgroundColor: color }} />
      <span style={{ ...s.cell, width: 140, fontFamily: 'monospace', fontSize: 12 }}>
        {sess.session_id.length > 14 ? '…' + sess.session_id.slice(-12) : sess.session_id}
      </span>
      <span style={{ ...s.cell, width: 90 }}>
        <ChannelBadge channel={sess.channel} />
      </span>
      <span style={{ ...s.cell, width: 110, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
        {formatMs(sess.handle_time_ms)}
      </span>
      <span style={{ ...s.cell, width: 90, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
        {formatScore(sess.latest_score)}
      </span>
      <span style={{ ...s.cell, flex: 1 }}>
        <span style={{ fontSize: 11, borderRadius: 4, padding: '2px 8px', fontWeight: 600, backgroundColor: color + '33', color }}>
          {category}
        </span>
      </span>
      <span style={{ marginLeft: 'auto', color: '#334155', fontSize: 18, paddingRight: 4 }}>›</span>
    </div>
  )
}

function ChannelBadge({ channel }: { channel: string }) {
  const icons: Record<string, string> = { webchat: '💬', whatsapp: '📱', voice: '📞', email: '✉️', sms: '📟', instagram: '📷', telegram: '✈️', webrtc: '🎥' }
  return <span style={{ fontSize: 12, color: '#94a3b8' }}>{icons[channel] ?? '⬡'} {channel}</span>
}

const s: Record<string, React.CSSProperties> = {
  container: { display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden', backgroundColor: '#0f172a', color: '#e2e8f0' },
  header: { display: 'flex', alignItems: 'center', gap: 12, padding: '12px 16px', borderBottom: '1px solid #1e293b', flexShrink: 0 },
  backBtn: { background: 'none', border: '1px solid #334155', color: '#94a3b8', borderRadius: 6, padding: '4px 10px', cursor: 'pointer', fontSize: 13 },
  title: { fontWeight: 600, fontSize: 15, color: '#e2e8f0' },
  badge: { fontSize: 12, color: '#64748b', backgroundColor: '#1e293b', borderRadius: 4, padding: '2px 8px' },
  colHeaders: { display: 'flex', alignItems: 'center', padding: '6px 20px 6px 24px', borderBottom: '1px solid #1e293b', fontSize: 11, fontWeight: 600, color: '#475569', letterSpacing: '0.06em', textTransform: 'uppercase', flexShrink: 0 },
  list: { flex: 1, overflowY: 'auto' },
  empty: { padding: '40px 24px', textAlign: 'center', color: '#475569', fontSize: 14 },
  row: { display: 'flex', alignItems: 'center', padding: '10px 16px 10px 0', borderBottom: '1px solid #1e293b', cursor: 'pointer', position: 'relative' },
  accent: { width: 4, alignSelf: 'stretch', marginRight: 16, borderRadius: 2, flexShrink: 0 },
  cell: { display: 'flex', alignItems: 'center', paddingRight: 8, fontSize: 13 },
}
