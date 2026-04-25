/**
 * PricingPanel.tsx
 * Capacity-based pricing view for the Operator Console.
 *
 * Two tabs:
 *   Invoice    — capacity invoice for the current billing cycle
 *                (base resources + reserve pools with activate/deactivate)
 *   Consumption — variable usage data (tokens, sessions, messages, …)
 *                 available for quality curation — NOT used in billing
 *
 * Layout:
 *   Left sidebar (240px) — resource list grouped by pool_type
 *   Right main (flex 1)  — tab content
 */
import React, { useState, useCallback, useMemo } from 'react'
import {
  useInvoice, useResources, useActivationLog,
  activateReservePool, deactivateReservePool,
  type Invoice, type ReserveGroup, type InvoiceLineItem, type InstallationResource,
} from '../api/pricing-hooks'

interface Props {
  tenantId: string
  onBack:   () => void
}

// ─── Colour helpers ────────────────────────────────────────────────────────────

const RESOURCE_ICONS: Record<string, string> = {
  ai_agent:          '🤖',
  human_agent:       '👤',
  whatsapp_number:   '📱',
  voice_trunk_in:    '📞',
  voice_trunk_out:   '📡',
  email_inbox:       '📧',
  sms_number:        '💬',
  webchat_instance:  '🌐',
}

function icon(rt: string): string { return RESOURCE_ICONS[rt] ?? '📦' }

function fmtCurrency(amount: number, currency: string): string {
  return `${currency} ${amount.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

// ─── Invoice tab ──────────────────────────────────────────────────────────────

function InvoiceTab({
  tenantId,
  adminToken,
}: {
  tenantId:   string
  adminToken: string
}) {
  const { invoice, loading, error, refresh } = useInvoice(tenantId)
  const { refresh: refreshResources }        = useResources(tenantId)
  const [toggling, setToggling] = useState<string | null>(null)
  const [toggleError, setToggleError] = useState<string | null>(null)

  const toggleReserve = useCallback(async (poolId: string, currentlyActive: boolean) => {
    setToggling(poolId)
    setToggleError(null)
    try {
      if (currentlyActive) {
        await deactivateReservePool(tenantId, poolId, adminToken)
      } else {
        await activateReservePool(tenantId, poolId, adminToken)
      }
      await Promise.all([refresh(), refreshResources()])
    } catch (err) {
      setToggleError(String(err))
    } finally {
      setToggling(null)
    }
  }, [tenantId, adminToken, refresh, refreshResources])

  if (loading) return <div style={styles.placeholder}>Calculating invoice…</div>
  if (error)   return <div style={styles.error}>pricing-api unavailable: {error}</div>
  if (!invoice) return null

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 0, overflowY: 'auto', flex: 1 }}>
      {/* Cycle header */}
      <div style={styles.cycleHeader}>
        <div>
          <div style={{ fontSize: 11, color: '#64748b', textTransform: 'uppercase', letterSpacing: 0.5 }}>
            Billing cycle
          </div>
          <div style={{ fontSize: 13, fontWeight: 700, color: '#e2e8f0', marginTop: 2 }}>
            {invoice.cycle_start} → {invoice.cycle_end}
            <span style={{ fontSize: 11, color: '#475569', marginLeft: 8 }}>
              ({invoice.billing_days} days)
            </span>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {toggleError && (
            <span style={{ fontSize: 11, color: '#ef4444' }}>{toggleError}</span>
          )}
          <button onClick={refresh} style={styles.refreshBtn}>↺ Refresh</button>
          <a
            href={`/v1/pricing/invoice/${tenantId}?format=xlsx`}
            download
            style={styles.exportBtn}
          >
            ↓ Export XLSX
          </a>
        </div>
      </div>

      {/* Base capacity table */}
      <SectionHeader label="Base Capacity" color="#3b82f6" />
      <InvoiceTable items={invoice.base_items} currency={invoice.currency} />
      <TotalRow label="Base Total" amount={invoice.base_total} currency={invoice.currency} color="#3b82f6" />

      {/* Reserve pools */}
      {invoice.reserve_groups.length > 0 && (
        <>
          <SectionHeader label="Reserve Pools" color="#22c55e" />
          {invoice.reserve_groups.map(group => (
            <ReserveGroupBlock
              key={group.pool_id}
              group={group}
              currency={invoice.currency}
              toggling={toggling === group.pool_id}
              onToggle={() => toggleReserve(group.pool_id, group.active)}
              adminToken={adminToken}
            />
          ))}
          <TotalRow
            label="Reserve Total"
            amount={invoice.reserve_total}
            currency={invoice.currency}
            color="#22c55e"
          />
        </>
      )}

      {/* Grand total */}
      <GrandTotal invoice={invoice} />
    </div>
  )
}

function SectionHeader({ label, color }: { label: string; color: string }) {
  return (
    <div style={{
      padding:       '8px 20px',
      background:    '#090e1a',
      borderBottom:  '1px solid #1e293b',
      fontSize:      11,
      fontWeight:    700,
      color,
      textTransform: 'uppercase',
      letterSpacing: 1,
    }}>
      {label}
    </div>
  )
}

function InvoiceTable({ items, currency }: { items: InvoiceLineItem[]; currency: string }) {
  if (items.length === 0) return (
    <div style={{ padding: '12px 20px', fontSize: 12, color: '#334155' }}>No resources configured.</div>
  )
  return (
    <div>
      {/* Column headers */}
      <div style={{ display: 'flex', padding: '6px 20px', background: '#0d1117', borderBottom: '1px solid #1e293b' }}>
        {['Resource', 'Type', 'Qty', 'Unit Price / month', 'Period', 'Subtotal'].map((h, i) => (
          <div key={h} style={{ ...colStyle(i), fontSize: 10, color: '#334155', fontWeight: 700, textTransform: 'uppercase' }}>
            {h}
          </div>
        ))}
      </div>
      {items.map(item => (
        <div key={item.resource_type} style={styles.tableRow}>
          <div style={colStyle(0)}>
            <span style={{ marginRight: 6 }}>{icon(item.resource_type)}</span>
            <span style={{ fontSize: 12, color: '#e2e8f0' }}>{item.label}</span>
          </div>
          <div style={{ ...colStyle(1), fontSize: 11, color: '#64748b', fontFamily: 'monospace' }}>
            {item.resource_type}
          </div>
          <div style={{ ...colStyle(2), fontSize: 13, fontWeight: 700, color: '#94a3b8' }}>
            {item.quantity}
          </div>
          <div style={{ ...colStyle(3), fontSize: 12, color: '#94a3b8' }}>
            {fmtCurrency(item.unit_price, currency)}
          </div>
          <div style={{ ...colStyle(4), fontSize: 11, color: '#64748b' }}>
            {item.days_active !== null
              ? `${item.days_active}/${item.billing_days} days`
              : 'full cycle'}
          </div>
          <div style={{ ...colStyle(5), fontSize: 13, fontWeight: 700, color: '#e2e8f0', textAlign: 'right' }}>
            {fmtCurrency(item.subtotal, currency)}
          </div>
        </div>
      ))}
    </div>
  )
}

function TotalRow({ label, amount, currency, color }: {
  label: string; amount: number; currency: string; color: string
}) {
  return (
    <div style={{
      display:       'flex',
      justifyContent: 'flex-end',
      padding:        '8px 20px',
      background:     '#090e1a',
      borderBottom:   '1px solid #1e293b',
      gap:            24,
    }}>
      <span style={{ fontSize: 12, color, fontWeight: 700 }}>{label}</span>
      <span style={{ fontSize: 13, color, fontWeight: 700, minWidth: 120, textAlign: 'right' }}>
        {fmtCurrency(amount, currency)}
      </span>
    </div>
  )
}

function ReserveGroupBlock({
  group, currency, toggling, onToggle, adminToken,
}: {
  group: ReserveGroup; currency: string; toggling: boolean;
  onToggle: () => void; adminToken: string;
}) {
  return (
    <div style={{ borderBottom: '1px solid #1e293b' }}>
      {/* Pool header with toggle */}
      <div style={{
        display:        'flex',
        alignItems:     'center',
        justifyContent: 'space-between',
        padding:        '10px 20px',
        background:     '#0a1628',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{
            width: 8, height: 8, borderRadius: '50%',
            background: group.active ? '#22c55e' : '#475569',
            boxShadow:  group.active ? '0 0 6px #22c55e' : 'none',
          }} />
          <span style={{ fontSize: 13, fontWeight: 700, color: '#e2e8f0' }}>{group.label}</span>
          <span style={{ fontSize: 10, color: '#475569' }}>
            {group.days_active}/{group.billing_days} days this cycle
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 11, color: group.active ? '#22c55e' : '#475569' }}>
            {group.active ? 'ACTIVE' : 'inactive'}
          </span>
          <button
            onClick={onToggle}
            disabled={toggling || !adminToken}
            title={!adminToken ? 'Set admin token to toggle' : undefined}
            style={{
              padding:      '4px 12px',
              borderRadius: 4,
              border:       group.active ? '1px solid #7f1d1d' : '1px solid #14532d',
              background:   'transparent',
              color:        group.active ? '#ef4444' : '#22c55e',
              cursor:       (toggling || !adminToken) ? 'not-allowed' : 'pointer',
              fontSize:     11,
              fontWeight:   600,
              opacity:      !adminToken ? 0.4 : 1,
            }}
          >
            {toggling ? '…' : group.active ? 'Deactivate' : 'Activate'}
          </button>
        </div>
      </div>
      <InvoiceTable items={group.items} currency={currency} />
    </div>
  )
}

function GrandTotal({ invoice }: { invoice: Invoice }) {
  return (
    <div style={{
      display:        'flex',
      justifyContent: 'flex-end',
      alignItems:     'center',
      gap:            24,
      padding:        '14px 20px',
      background:     '#1e3a8a',
      borderTop:      '2px solid #3b82f6',
      marginTop:      'auto',
      flexShrink:     0,
    }}>
      <span style={{ fontSize: 14, fontWeight: 700, color: '#fff' }}>GRAND TOTAL</span>
      <span style={{ fontSize: 18, fontWeight: 800, color: '#fff', minWidth: 140, textAlign: 'right' }}>
        {fmtCurrency(invoice.grand_total, invoice.currency)}
      </span>
    </div>
  )
}

// ─── Consumption tab ──────────────────────────────────────────────────────────
// Shows variable usage data from analytics-api for quality curation.
// This data is NOT used in billing calculations.

const ANALYTICS_BASE = import.meta.env.VITE_ANALYTICS_BASE_URL ?? ''

interface UsageRow { dimension: string; total: number }

function ConsumptionTab({ tenantId }: { tenantId: string }) {
  const [usage,   setUsage]   = useState<UsageRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!tenantId) return
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${ANALYTICS_BASE}/reports/usage?tenant_id=${encodeURIComponent(tenantId)}&page_size=100`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json() as { data: Array<{ dimension: string; total: number }> }
      // Aggregate by dimension
      const agg: Record<string, number> = {}
      for (const row of data.data) {
        agg[row.dimension] = (agg[row.dimension] ?? 0) + row.total
      }
      setUsage(Object.entries(agg).map(([dimension, total]) => ({ dimension, total }))
        .sort((a, b) => b.total - a.total))
    } catch (err) {
      setError(String(err))
    } finally {
      setLoading(false)
    }
  }, [tenantId])

  React.useEffect(() => { void load() }, [load])

  const DIMENSION_LABELS: Record<string, { label: string; unit: string; color: string }> = {
    sessions:                { label: 'Sessions attended',      unit: 'sessions',   color: '#3b82f6' },
    messages:                { label: 'Messages (all)',         unit: 'messages',   color: '#06b6d4' },
    llm_tokens_input:        { label: 'LLM tokens in',         unit: 'tokens',     color: '#8b5cf6' },
    llm_tokens_output:       { label: 'LLM tokens out',        unit: 'tokens',     color: '#a78bfa' },
    voice_minutes:           { label: 'Voice minutes',         unit: 'min',        color: '#f59e0b' },
    whatsapp_conversations:  { label: 'WhatsApp conversations', unit: 'convs',     color: '#22c55e' },
    sms_segments:            { label: 'SMS segments',          unit: 'segments',   color: '#ec4899' },
    email_messages:          { label: 'Email messages',        unit: 'emails',     color: '#f97316' },
    webchat_attachments:     { label: 'Webchat attachments',   unit: 'files',      color: '#64748b' },
  }

  return (
    <div style={{ flex: 1, overflowY: 'auto', padding: '0 0 20px 0' }}>
      <div style={{
        padding:     '12px 20px',
        borderBottom: '1px solid #1e293b',
        background:  '#090e1a',
        display:     'flex',
        justifyContent: 'space-between',
        alignItems:  'center',
      }}>
        <div>
          <div style={{ fontSize: 12, fontWeight: 700, color: '#e2e8f0' }}>Variable Consumption</div>
          <div style={{ fontSize: 11, color: '#475569', marginTop: 2 }}>
            For quality curation purposes only — not included in billing
          </div>
        </div>
        <button onClick={load} style={styles.refreshBtn}>↺ Refresh</button>
      </div>

      {loading && <div style={styles.placeholder}>Loading consumption data…</div>}
      {error   && <div style={styles.error}>analytics-api unavailable</div>}

      {!loading && usage.length === 0 && (
        <div style={styles.placeholder}>No usage data for current period.</div>
      )}

      {usage.map(row => {
        const meta = DIMENSION_LABELS[row.dimension] ?? {
          label: row.dimension, unit: 'units', color: '#64748b'
        }
        const formatted = row.total >= 1_000_000
          ? `${(row.total / 1_000_000).toFixed(2)}M`
          : row.total >= 1_000
          ? `${(row.total / 1_000).toFixed(1)}K`
          : String(row.total)

        return (
          <div key={row.dimension} style={styles.tableRow}>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 12, color: '#e2e8f0' }}>{meta.label}</div>
              <div style={{ fontSize: 10, color: '#475569', fontFamily: 'monospace', marginTop: 2 }}>
                {row.dimension}
              </div>
            </div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 4 }}>
              <span style={{ fontSize: 20, fontWeight: 800, color: meta.color }}>{formatted}</span>
              <span style={{ fontSize: 10, color: '#475569' }}>{meta.unit}</span>
            </div>
          </div>
        )
      })}

      <div style={{
        margin:    '20px 20px 0',
        padding:   '10px 14px',
        background: '#1e2030',
        borderRadius: 6,
        border:    '1px solid #334155',
        fontSize:  11,
        color:     '#475569',
        lineHeight: 1.6,
      }}>
        ⓘ  These metrics are captured by the metering layer (usage.events Kafka topic)
        and persisted by the analytics-api consumer into ClickHouse.
        They reflect actual platform consumption and are intended for operational
        quality review, capacity planning, and cost auditing — not for invoice generation.
      </div>
    </div>
  )
}

// ─── Resource sidebar ─────────────────────────────────────────────────────────

function ResourceSidebar({
  tenantId,
  adminToken,
  setAdminToken,
}: {
  tenantId:      string
  adminToken:    string
  setAdminToken: (t: string) => void
}) {
  const { resources, loading } = useResources(tenantId)
  const [tokenInput, setTokenInput] = useState(adminToken)
  const [tokenSaved, setTokenSaved] = useState(false)

  const baseResources = resources.filter(r => r.pool_type === 'base')
  const reserveIds    = [...new Set(resources.filter(r => r.pool_type === 'reserve').map(r => r.reserve_pool_id ?? 'reserve'))]

  return (
    <div style={styles.sidebar}>
      {/* Resources list */}
      <div style={{ flex: 1, overflowY: 'auto' }}>
        <div style={styles.sidebarSection}>BASE CAPACITY</div>
        {loading && <div style={{ padding: '10px 12px', fontSize: 11, color: '#475569' }}>Loading…</div>}
        {baseResources.map(r => (
          <ResourceRow key={r.id} resource={r} />
        ))}
        {!loading && baseResources.length === 0 && (
          <div style={{ padding: '10px 12px', fontSize: 11, color: '#334155' }}>No base resources</div>
        )}

        {reserveIds.length > 0 && (
          <>
            <div style={styles.sidebarSection}>RESERVE POOLS</div>
            {reserveIds.map(pid => {
              const poolResources = resources.filter(r => r.reserve_pool_id === pid)
              const active = poolResources.some(r => r.active)
              return (
                <div key={pid} style={{ padding: '8px 12px', borderBottom: '1px solid #0f172a' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <div style={{
                      width: 6, height: 6, borderRadius: '50%',
                      background: active ? '#22c55e' : '#475569',
                    }} />
                    <span style={{ fontSize: 11, fontWeight: 700, color: active ? '#22c55e' : '#94a3b8' }}>
                      {pid}
                    </span>
                  </div>
                  {poolResources.map(r => (
                    <ResourceRow key={r.id} resource={r} indent />
                  ))}
                </div>
              )
            })}
          </>
        )}
      </div>

      {/* Admin token */}
      <div style={styles.tokenBox}>
        <div style={{ fontSize: 10, color: '#475569', fontWeight: 700, marginBottom: 4 }}>
          ADMIN TOKEN
        </div>
        <div style={{ display: 'flex', gap: 4 }}>
          <input
            type="password"
            value={tokenInput}
            onChange={e => { setTokenInput(e.target.value); setTokenSaved(false) }}
            placeholder="required for activate/deactivate"
            style={styles.tokenInput}
          />
          <button
            onClick={() => { setAdminToken(tokenInput); setTokenSaved(true) }}
            style={{
              ...styles.tokenBtn,
              borderColor: tokenSaved ? '#22c55e' : '#334155',
              color:       tokenSaved ? '#22c55e' : '#64748b',
            }}
          >
            {tokenSaved ? '✓' : 'Set'}
          </button>
        </div>
      </div>
    </div>
  )
}

function ResourceRow({ resource: r, indent }: { resource: InstallationResource; indent?: boolean }) {
  return (
    <div style={{
      display:     'flex',
      alignItems:  'center',
      gap:         8,
      padding:     indent ? '4px 6px 4px 18px' : '6px 12px',
      borderBottom: '1px solid #0f172a',
    }}>
      <span style={{ fontSize: 13 }}>{icon(r.resource_type)}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 11, color: '#94a3b8', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {r.label || r.resource_type.replace(/_/g, ' ')}
        </div>
      </div>
      <span style={{
        fontSize:   12,
        fontWeight: 700,
        color:      '#e2e8f0',
        minWidth:   20,
        textAlign:  'right',
      }}>
        ×{r.quantity}
      </span>
    </div>
  )
}

// ─── Main component ───────────────────────────────────────────────────────────

export function PricingPanel({ tenantId, onBack }: Props) {
  const [activeTab,   setActiveTab]   = useState<'invoice' | 'consumption'>('invoice')
  const [adminToken,  setAdminToken]  = useState('')

  return (
    <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>

      {/* ── Sidebar ───────────────────────────────────────────────────────── */}
      <div style={{ width: 220, borderRight: '1px solid #1e293b', display: 'flex', flexDirection: 'column', flexShrink: 0, overflow: 'hidden' }}>
        {/* Back button */}
        <div style={{ padding: '10px 12px', borderBottom: '1px solid #1e293b', display: 'flex', gap: 8, alignItems: 'center' }}>
          <button onClick={onBack} style={styles.backBtn}>← Back</button>
          <span style={{ fontSize: 11, fontWeight: 700, color: '#64748b', textTransform: 'uppercase', letterSpacing: 0.5 }}>
            Pricing
          </span>
        </div>
        <ResourceSidebar
          tenantId={tenantId}
          adminToken={adminToken}
          setAdminToken={setAdminToken}
        />
      </div>

      {/* ── Main area ─────────────────────────────────────────────────────── */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {/* Tab bar */}
        <div style={{ display: 'flex', gap: 0, borderBottom: '1px solid #1e293b', flexShrink: 0 }}>
          {(['invoice', 'consumption'] as const).map(tab => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              style={{
                padding:      '10px 20px',
                border:       'none',
                borderBottom: activeTab === tab ? '2px solid #3b82f6' : '2px solid transparent',
                background:   'transparent',
                color:        activeTab === tab ? '#e2e8f0' : '#475569',
                fontSize:     12,
                fontWeight:   activeTab === tab ? 700 : 400,
                cursor:       'pointer',
                textTransform: 'capitalize',
              }}
            >
              {tab === 'invoice' ? '📄 Invoice' : '📊 Consumption'}
            </button>
          ))}
          <div style={{ flex: 1 }} />
          <div style={{ padding: '10px 16px', fontSize: 11, color: '#334155' }}>
            tenant: {tenantId}
          </div>
        </div>

        {/* Tab content */}
        <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
          {activeTab === 'invoice'     && <InvoiceTab tenantId={tenantId} adminToken={adminToken} />}
          {activeTab === 'consumption' && <ConsumptionTab tenantId={tenantId} />}
        </div>
      </div>
    </div>
  )
}

// ─── Column layout helpers ─────────────────────────────────────────────────────

function colStyle(i: number): React.CSSProperties {
  const widths = [200, 160, 50, 130, 110, 120]
  const aligns: React.CSSProperties['textAlign'][] = ['left', 'left', 'center', 'right', 'center', 'right']
  return {
    width:    i < 5 ? widths[i] : undefined,
    flex:     i === 5 ? 1 : undefined,
    flexShrink: 0,
    textAlign:  aligns[i],
    paddingRight: 12,
  }
}

// ─── Styles ───────────────────────────────────────────────────────────────────

const styles: Record<string, React.CSSProperties> = {
  sidebar: {
    flex:          1,
    display:       'flex',
    flexDirection: 'column',
    overflow:      'hidden',
  },
  sidebarSection: {
    padding:        '6px 12px',
    fontSize:       9,
    fontWeight:     700,
    color:          '#334155',
    letterSpacing:  1,
    textTransform:  'uppercase',
    background:     '#090e1a',
    borderBottom:   '1px solid #1e293b',
  },
  tokenBox: {
    padding:    '10px 12px',
    borderTop:  '1px solid #1e293b',
    background: '#090e1a',
  },
  tokenInput: {
    flex:       1,
    background: '#1e293b',
    border:     '1px solid #334155',
    borderRadius: 4,
    color:      '#e2e8f0',
    fontSize:   10,
    padding:    '3px 6px',
    outline:    'none',
    fontFamily: 'monospace',
  },
  tokenBtn: {
    padding:    '3px 8px',
    borderRadius: 4,
    border:     '1px solid #334155',
    background: 'transparent',
    cursor:     'pointer',
    fontSize:   10,
  },
  backBtn: {
    padding:    '4px 10px',
    borderRadius: 4,
    border:     '1px solid #334155',
    background: '#0f172a',
    color:      '#94a3b8',
    cursor:     'pointer',
    fontSize:   11,
  },
  cycleHeader: {
    display:        'flex',
    alignItems:     'center',
    justifyContent: 'space-between',
    padding:        '12px 20px',
    borderBottom:   '1px solid #1e293b',
    flexShrink:     0,
  },
  tableRow: {
    display:      'flex',
    alignItems:   'center',
    padding:      '8px 20px',
    borderBottom: '1px solid #0f172a',
  },
  refreshBtn: {
    padding:    '4px 10px',
    borderRadius: 4,
    border:     '1px solid #334155',
    background: 'transparent',
    color:      '#64748b',
    cursor:     'pointer',
    fontSize:   11,
  },
  exportBtn: {
    padding:        '4px 12px',
    borderRadius:   4,
    border:         '1px solid #14532d',
    background:     'transparent',
    color:          '#22c55e',
    cursor:         'pointer',
    fontSize:       11,
    fontWeight:     600,
    textDecoration: 'none',
    display:        'inline-block',
  },
  placeholder: {
    padding:   40,
    textAlign: 'center',
    fontSize:  12,
    color:     '#334155',
  },
  error: {
    padding:  '12px 20px',
    fontSize: 12,
    color:    '#ef4444',
  },
}
