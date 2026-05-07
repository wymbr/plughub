import type React from 'react'
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
            {pool ? 'Detalhes do pool' : 'Resumo 24h'}
          </div>
          <div style={{ fontSize: 16, fontWeight: 700, color: '#e2e8f0', marginTop: 2 }}>
            {pool ? pool.pool_id.replace(/_/g, ' ') : 'Métricas da plataforma'}
          </div>
        </div>
        <button style={closeBtnStyle} onClick={onClose} title="Fechar painel">✕</button>
      </div>

      <div style={scrollStyle}>
        {pool && <PoolDetails pool={pool} />}
        {metrics ? <SummarySection metrics={metrics} /> : (
          <div style={{ color: '#475569', fontSize: 13, padding: '12px 0' }}>Carregando métricas…</div>
        )}
      </div>
    </aside>
  )
}

function PoolDetails({ pool }: { pool: PoolView }) {
  const accent   = scoreToAccent(pool.avg_score)
  const category = scoreToCategory(pool.avg_score)
  return (
    <div>
      <Section label="Sentimento" accent={accent}>
        <StatRow label="Score"    value={formatScore(pool.avg_score)} large accent={accent} />
        <StatRow label="Categoria" value={category} />
        <StatRow label="Sessões"  value={String(pool.sentiment_count)} />
        {pool.distribution && (
          <div style={{ marginTop: 8 }}>
            <DistBar label="Satisfeito"  value={pool.distribution.satisfied}  total={pool.sentiment_count} color="#22c55e" />
            <DistBar label="Neutro"      value={pool.distribution.neutral}    total={pool.sentiment_count} color="#eab308" />
            <DistBar label="Frustrado"   value={pool.distribution.frustrated} total={pool.sentiment_count} color="#f97316" />
            <DistBar label="Irritado"    value={pool.distribution.angry}      total={pool.sentiment_count} color="#ef4444" />
          </div>
        )}
      </Section>
      <Section label="Disponibilidade">
        <StatRow label="Agentes disponíveis" value={String(pool.available)} />
        <StatRow label="Fila"                value={String(pool.queue_length)} />
        <StatRow label="SLA alvo"            value={formatMs(pool.sla_target_ms)} />
      </Section>
      {pool.channel_types.length > 0 && (
        <Section label="Canais">
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 4 }}>
            {pool.channel_types.map(ch => (
              <span key={ch} style={chipStyle}>{ch}</span>
            ))}
          </div>
        </Section>
      )}
      <div style={{ fontSize: 11, color: '#475569', marginTop: 16 }}>
        Último snapshot: {new Date(pool.updated_at).toLocaleTimeString()}
      </div>
    </div>
  )
}

function SummarySection({ metrics }: { metrics: Metrics24h }) {
  const { sessions: s, agent_events: a, usage: u, sentiment: sent } = metrics
  return (
    <div style={{ marginTop: 24 }}>
      <div style={sectionLabelStyle}>Últimas 24 horas</div>
      <Section label="Sessões">
        <StatRow label="Total"       value={String(s.total)} large />
        <StatRow label="Handle médio" value={formatMs(s.avg_handle_ms)} />
        {Object.entries(s.by_outcome).map(([k, v]) => (
          <StatRow key={k} label={k} value={String(v)} indent />
        ))}
      </Section>
      <Section label="Eventos de agente">
        <StatRow label="Roteados" value={String(a.total_routed)} />
        <StatRow label="Concluídos" value={String(a.total_done)} />
      </Section>
      <Section label="Sentimento">
        <StatRow label="Score médio" value={formatScore(sent.avg_score)} />
        <StatRow label="Amostras"    value={String(sent.sample_count)} />
        {Object.entries(sent.by_category).map(([k, v]) => (
          <StatRow key={k} label={k} value={String(v)} indent />
        ))}
      </Section>
      <Section label="Uso de tokens">
        {Object.entries(u.by_dimension)
          .filter(([k]) => k.startsWith('llm'))
          .map(([k, v]) => (
            <StatRow key={k} label={k.replace('llm_tokens_', '')} value={v.toLocaleString()} />
          ))}
      </Section>
    </div>
  )
}

function Section({ label, children, accent }: { label: string; children: React.ReactNode; accent?: string }) {
  return (
    <div style={{ marginBottom: 20 }}>
      <div style={{ ...sectionLabelStyle, borderLeft: `3px solid ${accent ?? '#334155'}`, paddingLeft: 8 }}>{label}</div>
      <div style={{ marginTop: 8 }}>{children}</div>
    </div>
  )
}

function StatRow({ label, value, large, accent, indent }: { label: string; value: string; large?: boolean; accent?: string; indent?: boolean }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 5, paddingLeft: indent ? 12 : 0 }}>
      <span style={{ fontSize: 12, color: '#64748b' }}>{label}</span>
      <span style={{ fontSize: large ? 20 : 13, fontWeight: large ? 700 : 500, color: accent ?? '#e2e8f0' }}>{value}</span>
    </div>
  )
}

function DistBar({ label, value, total, color }: { label: string; value: number; total: number; color: string }) {
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

const panelStyle: React.CSSProperties = { width: 300, flexShrink: 0, background: '#111827', borderLeft: '1px solid #1e293b', display: 'flex', flexDirection: 'column', overflow: 'hidden' }
const headerStyle: React.CSSProperties = { padding: '20px 20px 16px', borderBottom: '1px solid #1e293b', display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }
const scrollStyle: React.CSSProperties = { flex: 1, overflowY: 'auto', padding: '16px 20px' }
const closeBtnStyle: React.CSSProperties = { background: 'transparent', border: 'none', color: '#475569', cursor: 'pointer', fontSize: 16, padding: 4 }
const sectionLabelStyle: React.CSSProperties = { fontSize: 11, fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.6px', marginBottom: 6 }
const chipStyle: React.CSSProperties = { fontSize: 11, fontWeight: 600, color: '#94a3b8', background: '#1e293b', borderRadius: 4, padding: '2px 8px' }
