import type React from 'react'
import type { PoolView } from '../types'
import { PoolTile } from './PoolTile'

interface Props {
  pools:          PoolView[]
  selectedPoolId: string | null
  onSelect:       (poolId: string | null) => void
  onDrillDown?:   (poolId: string) => void
}

export function HeatmapGrid({ pools, selectedPoolId, onSelect, onDrillDown }: Props) {
  const sorted = [...pools].sort((a, b) => {
    if (a.avg_score === null && b.avg_score === null) return 0
    if (a.avg_score === null) return 1
    if (b.avg_score === null) return -1
    return a.avg_score - b.avg_score
  })

  if (sorted.length === 0) {
    return (
      <div style={emptyStyle}>
        <div style={{ fontSize: 40, marginBottom: 12 }}>📡</div>
        <div style={{ fontSize: 16, fontWeight: 600, color: '#94a3b8' }}>Aguardando dados dos pools…</div>
        <div style={{ fontSize: 13, color: '#64748b', marginTop: 6 }}>
          Snapshots chegam a cada 5 segundos. Verifique se o Routing Engine está rodando.
        </div>
      </div>
    )
  }

  return (
    <div style={gridStyle}>
      {sorted.map(pool => (
        <PoolTile
          key={pool.pool_id}
          pool={pool}
          selected={pool.pool_id === selectedPoolId}
          onClick={() => onSelect(pool.pool_id === selectedPoolId ? null : pool.pool_id)}
          onDrillDown={onDrillDown ? () => onDrillDown(pool.pool_id) : undefined}
        />
      ))}
    </div>
  )
}

const gridStyle: React.CSSProperties = {
  display: 'grid',
  gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))',
  gap: 16,
  padding: 24,
  flex: 1,
  alignContent: 'start',
  overflowY: 'auto',
}

const emptyStyle: React.CSSProperties = {
  flex: 1, display: 'flex', flexDirection: 'column',
  alignItems: 'center', justifyContent: 'center',
  color: '#64748b', textAlign: 'center', padding: 48,
}
