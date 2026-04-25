/**
 * MetricsPanel.tsx
 * Sidebar panel showing detailed metrics for the selected pool.
 * When no pool is selected, shows the 24h aggregate summary.
 */
import type { Metrics24h, PoolView } from '../types'
import { formatMs, formatScore, scoreToAccent, scoreToCategory } from '../utils/sentiment'

interface Props {
  pool:    PoolView | null
  metrics: Metrics24h | null
  onClose: () => void
}

export function MetricsPanel({ pool, metrics, onClose }: Props) {
  return (
    <aside style={panelStyle}>
      <div style={headerStyle}>
        <div>
          <div style={{ fontSize: 11, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
            {pool ? 'Pool details' : '24h summary'}
          </div>
          <div style={{ fontSize: 16, fontWeight: 700, color: '#e2e8f0', marginTop: 2 }}>
            {pool ? pool.pool_id.replace(/_/g, ' ') : 'Platform metrics'}
          </div>
        </div>
        <button style={closeBtnStyle} onClick={onClose} title="Close panel">✕</button>
      </div>

      <div style={scrollStyle}>
        {pool ? <PoolDetails pool={pool} /> : null}
        {metrics ? <SummarySection metrics={metrics} /> : (
          <div style={{ color: '#475569', fontSize: 13, padding: '12px 0' }}>
            Loading metrics…
          </div>
        )}
      </div>
    </aside>
  )
}

// ─── PoolDetails ──────────────────────────────────────────────────────────────

function PoolDetails({ pool }: { pool: PoolView }) {
  const accent   = scoreToAccent(pool.avg_score)
  const category = scoreToCategory(pool.avg_score)

  return (
    <div>
      {/* Sentiment score */}
      <Section label="Sentiment" accent={accent}>
        <StatRow label="Score"    value={formatScore(pool.avg_score)} large accent={accent} />
        <StatRow label="Category" value={category} />
        <StatRow label="Sessions" value={String(pool.sentiment_count)} />
        {pool.distribution && (
          <div style={{ marginTop: 8 }}>
            <DistBar label="Satisfied"  value={pool.distribution.satisfied}  total={pool.sentiment_count} color="#22c55e" />
            <DistBar label="Neutral"    value={pool.distribution.neutral}    total={pool.sentiment_count} color="#eab308" />
            <DistBar label="Frustrated" value={pool.distribution.frustrated} total={pool.sentiment_count} color="#f97316" />
            <DistBar label="Angry"      value={pool.distribution.angry}      total={pool.sentiment_count} color="#ef4444" />
          </div>
        )}
      </Section>

      {/* Availability */}
      <Section label="Availability">
        <StatRow label="Agents available" value={String(pool.available)} />
        <StatRow label="Queue depth"      value={String(pool.queue_length)} />
        <StatRow label="SLA target"       value={formatMs(pool.sla_target_ms)} />
      </Section>

      {/* Channels */}
      {pool.channel_types.length > 0 && (
        <Section label="Channels">
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 4 }}>
            {pool.channel_types.map(ch => (
              <span key={ch} style={chipStyle}>{ch}</span>
            ))}
          </div>
        </Section>
      )}

      {/* Last update */}
      <div style={{ fontSize: 11, color: '#475569', marginTop: 16 }}>
        Last snapshot: {new Date(pool.updated_at).toLocaleTimeString()}
      </div>
    </div>
  )
}

// ─── SummarySection ───────────────────────────────────────────────────────────

function SummarySection({ metrics }: { metrics: Metrics24h }) {
  const s = metrics.sessions
  const a = metrics.agent_events
  const u = metrics.usage
  const sent = metrics.sentiment

  return (
    <div style={{ marginTop: 24 }}>
      <div style={sectionLabelStyle}>Last 24 hours</div>

      <Section label="Sessions">
        <StatRow label="Total"          value={String(s.total)} large />
        <StatRow label="Avg handle"     value={formatMs(s.avg_handle_ms)} />
        {Object.entries(s.by_outcome).map(([k, v]) => (
          <StatRow key={k} label={k} value={String(v)} indent />
        ))}
      </Section>

      <Section label="Agent events">
        <StatRow label="Routed" value={String(a.total_routed)} />
        <StatRow label="Done"   value={String(a.total_done)} />
      </Section>

      <Section label="Sentiment">
        <StatRow label="Avg score"   value={formatScore(sent.avg_score)} />
        <StatRow label="Samples"     value={String(sent.sample_count)} />
        {Object.entries(sent.by_category).map(([k, v]) => (
          <StatRow key={k} label={k} value={String(v)} indent />
        ))}
      </Section>

      <Section label="Token usage">
        {Object.entries(u.by_dimension)
          .filter(([k]) => k.startsWith('llm'))
          .map(([k, v]) => (
            <StatRow key={k} label={k.replace('llm_tokens_', '')} value={v.toLocaleString()} />
          ))
        }
      </Section>
    </div>
  )
}

// ─── sub-components ───────────────────────────────────────────────────────────

function Section({ label, children, accent }: {
  label:    string
  children: React.ReactNode
  accent?:  string
}) {
  return (
    <div style={{ marginBottom: 20 }}>
      <div style={{
        ...sectionLabelStyle,
        borderLeft: accent ? `3px solid ${accent}` : '3px solid #334155',
        paddingLeft: 8,
      }}>
        {label}
      </div>
      <div style={{ marginTop: 8 }}>{children}</div>
    </div>
  )
}

function StatRow({ label, value, large, accent, indent }: {
  label:   string
  value:   string
  large?:  boolean
  accent?: string
  indent?: boolean
}) {
  return (
    <div style={{
      display:        'flex',
      justifyContent: 'space-between',
      alignItems:     'baseline',
      marginBottom:   5,
      paddingLeft:    indent ? 12 : 0,
    }}>
      <span style={{ fontSize: 12, color: '#64748b' }}>{label}</span>
      <span style={{
        fontSize:   large ? 20 : 13,
        fontWeight: large ? 700 : 500,
        color:      accent ?? '#e2e8f0',
      }}>
        {value}
      </span>
    </div>
  )
}

function DistBar({ label, value, total, color }: {
  label: string; value: number; total: number; color: string
}) {
  const pct = total > 0 ? Math.round((value / total) * 100) : 0
  return (
    <div style={{ marginBottom: 4 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}>
        <span style={{ fontSize: 11, color: '#64748b' }}>{label}</span>
        <span style={{ fontSize: 11, color: '#94a3b8' }}>{value} ({pct}%)</span>
      </div>
      <div style={{ height: 4, background: '#1e293b', borderRadius: 2, overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: 2 }} />
      </div>
    </div>
  )
}

// ─── styles ───────────────────────────────────────────────────────────────────

const panelStyle: React.CSSProperties = {
  width:       320,
  flexShrink:  0,
  background:  '#111827',
  borderLeft:  '1px solid #1e293b',
  display:     'flex',
  flexDirection: 'column',
  overflow:    'hidden',
}

const headerStyle: React.CSSProperties = {
  padding:        '20px 20px 16px',
  borderBottom:   '1px solid #1e293b',
  display:        'flex',
  justifyContent: 'space-between',
  alignItems:     'flex-start',
}

const scrollStyle: React.CSSProperties = {
  flex:      1,
  overflowY: 'auto',
  padding:   '16px 20px',
}

const closeBtnStyle: React.CSSProperties = {
  background: 'transparent',
  border:     'none',
  color:      '#475569',
  cursor:     'pointer',
  fontSize:   16,
  padding:    4,
}

const sectionLabelStyle: React.CSSProperties = {
  fontSize:      11,
  fontWeight:    700,
  color:         '#475569',
  textTransform: 'uppercase',
  letterSpacing: '0.6px',
  marginBottom:  6,
}

const chipStyle: React.CSSProperties = {
  fontSize:     11,
  fontWeight:   600,
  color:        '#94a3b8',
  background:   '#1e293b',
  borderRadius: 4,
  padding:      '2px 8px',
}

import type React from 'react'
