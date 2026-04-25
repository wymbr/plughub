/**
 * HeatmapGrid.tsx
 * Responsive grid of PoolTile components.
 * Tiles are ordered: worst sentiment first (most urgent at top-left).
 */
import type { PoolView } from '../types'
import { PoolTile } from './PoolTile'

interface Props {
  pools:          PoolView[]
  selectedPoolId: string | null
  onSelect:       (poolId: string | null) => void
  onDrillDown?:   (poolId: string) => void
}

export function HeatmapGrid({ pools, selectedPoolId, onSelect, onDrillDown }: Props) {
  // Sort: worst (most negative) score first; no-data pools at end
  const sorted = [...pools].sort((a, b) => {
    if (a.avg_score === null && b.avg_score === null) return 0
    if (a.avg_score === null) return 1
    if (b.avg_score === null) return -1
    return a.avg_score - b.avg_score   // ascending = worst first
  })

  if (sorted.length === 0) {
    return (
      <div style={emptyStyle}>
        <div style={{ fontSize: 40, marginBottom: 12 }}>📡</div>
        <div style={{ fontSize: 16, fontWeight: 600, color: '#94a3b8' }}>
          Waiting for pool data…
        </div>
        <div style={{ fontSize: 13, color: '#64748b', marginTop: 6 }}>
          Snapshots arrive every 5 seconds. Make sure the Routing Engine is running.
        </div>
      </div>
    )
  }

  return (
    <div style={gridStyle}>
      {sorted.map(pool => (
        <PoolTile
          key         ={pool.pool_id}
          pool        ={pool}
          selected    ={pool.pool_id === selectedPoolId}
          onClick     ={() => onSelect(
            pool.pool_id === selectedPoolId ? null : pool.pool_id
          )}
          onDrillDown ={onDrillDown ? () => onDrillDown(pool.pool_id) : undefined}
        />
      ))}
    </div>
  )
}

const gridStyle: React.CSSProperties = {
  display:             'grid',
  gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))',
  gap:                 16,
  padding:             24,
  flex:                1,
  alignContent:        'start',
  overflowY:           'auto',
}

const emptyStyle: React.CSSProperties = {
  flex:           1,
  display:        'flex',
  flexDirection:  'column',
  alignItems:     'center',
  justifyContent: 'center',
  color:          '#64748b',
  textAlign:      'center',
  padding:        48,
}

// React is needed for CSSProperties
import type React from 'react'
