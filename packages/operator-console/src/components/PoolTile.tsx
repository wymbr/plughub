/**
 * PoolTile.tsx
 * A single tile in the heatmap grid representing one pool.
 *
 * Visual encoding:
 *   Background color → avg_sentiment score (green → amber → orange → red)
 *   Availability badge → agents available vs queue depth
 *   SLA indicator → ok / warning / breach ring on the tile border
 */
import type { PoolView } from '../types'
import { formatMs, formatScore, scoreToAccent, scoreToCategory, scoreToColor, slaStatus } from '../utils/sentiment'

interface Props {
  pool:        PoolView
  selected:    boolean
  onClick:     () => void
  onDrillDown?: () => void
}

export function PoolTile({ pool, selected, onClick, onDrillDown }: Props) {
  const bg       = scoreToColor(pool.avg_score)
  const accent   = scoreToAccent(pool.avg_score)
  const category = scoreToCategory(pool.avg_score)
  const sla      = slaStatus(pool.queue_length, pool.sla_target_ms, null)

  const slaBorderColor: Record<typeof sla, string> = {
    ok:      'transparent',
    warning: '#fbbf24',
    breach:  '#ef4444',
  }

  const styles: React.CSSProperties = {
    background:  bg,
    border:      selected
      ? `2px solid #60a5fa`
      : `2px solid ${slaBorderColor[sla]}`,
    borderRadius: 12,
    padding:     '16px 14px',
    cursor:      'pointer',
    position:    'relative',
    transition:  'transform 0.12s ease, box-shadow 0.12s ease',
    boxShadow:   selected
      ? '0 0 0 3px rgba(96,165,250,0.4)'
      : sla === 'breach'
        ? '0 0 0 2px rgba(239,68,68,0.3)'
        : '0 2px 8px rgba(0,0,0,0.3)',
    minHeight:   120,
    display:     'flex',
    flexDirection: 'column',
    justifyContent: 'space-between',
    userSelect:  'none',
  }

  const nameStyle: React.CSSProperties = {
    fontSize:    13,
    fontWeight:  700,
    color:       '#fff',
    textShadow:  '0 1px 3px rgba(0,0,0,0.6)',
    letterSpacing: '0.3px',
    marginBottom: 4,
    overflow:    'hidden',
    textOverflow: 'ellipsis',
    whiteSpace:  'nowrap',
  }

  const scoreStyle: React.CSSProperties = {
    fontSize:    28,
    fontWeight:  800,
    color:       '#fff',
    textShadow:  '0 1px 4px rgba(0,0,0,0.5)',
    lineHeight:  1,
  }

  const categoryStyle: React.CSSProperties = {
    fontSize:   10,
    fontWeight: 600,
    color:      'rgba(255,255,255,0.75)',
    textTransform: 'uppercase',
    letterSpacing: '0.5px',
    marginTop:  2,
  }

  return (
    <div style={styles} onClick={onClick} title={pool.pool_id}>
      {/* Pool name */}
      <div style={nameStyle}>{pool.pool_id.replace(/_/g, ' ')}</div>

      {/* Score */}
      <div>
        <div style={scoreStyle}>{formatScore(pool.avg_score)}</div>
        <div style={categoryStyle}>{pool.avg_score !== null ? category : 'no data'}</div>
      </div>

      {/* Footer row */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 8 }}>
        {/* Availability */}
        <div style={badgeStyle('#00000033')}>
          <span style={{ color: '#86efac' }}>●</span>
          {' '}{pool.available} avail
        </div>
        {/* Queue */}
        <div style={badgeStyle('#00000033')}>
          {pool.queue_length > 0
            ? <><span style={{ color: '#fca5a5' }}>▲</span>{' '}{pool.queue_length} queued</>
            : <span style={{ color: 'rgba(255,255,255,0.5)' }}>queue empty</span>
          }
        </div>
      </div>

      {/* Drill-down button */}
      {onDrillDown && (
        <button
          style={{
            position: 'absolute', bottom: 8, right: 8,
            background: 'rgba(0,0,0,0.35)', border: '1px solid rgba(255,255,255,0.2)',
            color: 'rgba(255,255,255,0.8)', borderRadius: 5,
            fontSize: 10, fontWeight: 700, padding: '2px 7px',
            cursor: 'pointer', letterSpacing: '0.03em',
          }}
          onClick={e => { e.stopPropagation(); onDrillDown() }}
          title="View active sessions"
        >
          sessions →
        </button>
      )}

      {/* SLA badge (only if breach/warning) */}
      {sla !== 'ok' && (
        <div style={{
          position:  'absolute',
          top:       8,
          right:     8,
          fontSize:  9,
          fontWeight: 700,
          color:     sla === 'breach' ? '#fca5a5' : '#fde68a',
          textTransform: 'uppercase',
          letterSpacing: '0.5px',
        }}>
          {sla === 'breach' ? '⚠ SLA BREACH' : '⚠ SLA WARN'}
        </div>
      )}

      {/* Accent bar */}
      <div style={{
        position:  'absolute',
        left:      0,
        top:       0,
        bottom:    0,
        width:     4,
        borderRadius: '12px 0 0 12px',
        background: accent,
      }} />
    </div>
  )
}

function badgeStyle(bg: string): React.CSSProperties {
  return {
    fontSize:     11,
    fontWeight:   600,
    color:        'rgba(255,255,255,0.85)',
    background:   bg,
    borderRadius: 6,
    padding:      '2px 7px',
  }
}
