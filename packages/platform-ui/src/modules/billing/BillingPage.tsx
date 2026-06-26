import React, { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Receipt, BarChart2, AlertTriangle, Bot, User, Globe, Gauge } from 'lucide-react'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'
import Badge from '@/components/ui/Badge'
import EmptyState from '@/components/ui/EmptyState'
import type { Invoice, InvoiceLineItem, ReserveGroup, InstallationResource } from '@/types'

// ── Constants ──────────────────────────────────────────────────────────────────

const RESOURCE_ICONS: Record<string, string> = {
  whatsapp_number: '📱',
  voice_trunk_in:  '📞',
  voice_trunk_out: '☎️',
  email_inbox:     '📧',
  sms_number:      '💬',
}
type ResourceLucideIcon = React.FC<{ className?: string }>
const RESOURCE_LUCIDE_ICONS: Record<string, ResourceLucideIcon> = {
  ai_agent:        Bot,
  human_agent:     User,
  webchat_instance: Globe,
}

function ResourceIcon({ type }: { type: string }) {
  const LucideIcon = RESOURCE_LUCIDE_ICONS[type]
  if (LucideIcon) return <LucideIcon className="w-4 h-4 flex-shrink-0" aria-hidden="true" />
  return <span className="text-sm">{RESOURCE_ICONS[type] ?? '📦'}</span>
}

const DIMENSION_LABELS: Record<string, { label: string; unit: string }> = {
  sessions:            { label: 'Sessões',              unit: 'sessões'  },
  messages:            { label: 'Mensagens',             unit: 'msgs'     },
  llm_tokens_input:    { label: 'Tokens LLM (entrada)',  unit: 'tokens'   },
  llm_tokens_output:   { label: 'Tokens LLM (saída)',    unit: 'tokens'   },
  webchat_attachments: { label: 'Anexos WebChat',        unit: 'arquivos' },
}

// ── Helpers ────────────────────────────────────────────────────────────────────

function fmtCurrency(n: number, currency = 'BRL') {
  return new Intl.NumberFormat('pt-BR', { style: 'currency', currency }).format(n)
}

function fmtDate(s: string) {
  try { return new Date(s).toLocaleDateString('pt-BR') }
  catch { return s }
}

// ── Inline hooks ───────────────────────────────────────────────────────────────

function useInvoice(tenantId: string) {
  const [invoice, setInvoice] = useState<Invoice | null>(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState<string | null>(null)

  const refresh = useCallback(async () => {
    if (!tenantId) return
    setLoading(true); setError(null)
    try {
      const res = await fetch(`/v1/pricing/invoice/${encodeURIComponent(tenantId)}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setInvoice(await res.json() as Invoice)
    } catch (err) { setError(String(err)) }
    finally { setLoading(false) }
  }, [tenantId])

  useEffect(() => { void refresh() }, [refresh])
  return { invoice, loading, error, refresh }
}

function useResources(tenantId: string) {
  const [resources, setResources] = useState<InstallationResource[]>([])
  const [loading,   setLoading]   = useState(true)

  const refresh = useCallback(async () => {
    if (!tenantId) return
    setLoading(true)
    try {
      const res = await fetch(`/v1/pricing/resources/${encodeURIComponent(tenantId)}`)
      if (res.ok) {
        const data = await res.json() as { resources: InstallationResource[] }
        setResources(data.resources)
      }
    } catch { /* stale ok */ }
    finally { setLoading(false) }
  }, [tenantId])

  useEffect(() => { void refresh() }, [refresh])
  return { resources, loading }
}

function useUsage(tenantId: string) {
  const [rows,    setRows]    = useState<Array<{ dimension: string; total: number }>>([])
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    if (!tenantId) return
    setLoading(true)
    try {
      const res = await fetch(`/reports/usage?tenant_id=${encodeURIComponent(tenantId)}`)
      if (res.ok) {
        const data = await res.json() as { rows?: Array<{ dimension: string; total: number }> }
        setRows(data.rows ?? [])
      }
    } catch { /* stale ok */ }
    finally { setLoading(false) }
  }, [tenantId])

  useEffect(() => { void refresh() }, [refresh])
  return { rows, loading }
}

// ── Capacity (capacity-governance item 4) ──────────────────────────────────────
// Contratado (pricing /v1/pricing/capacity) × alocado (provisionada do occupancy)
// × saldo, + reservas/shared/conformidade (registry /v1/pools/capacity/conformance).

interface CapacityByType { resource_type: string; base: number; reserve_active: number; total: number }
interface Conformance {
  contracted: number | null; reserved_total: number; shared: number | null; conform: boolean
  pools: Array<{ pool_id: string; session_reservation: number | null }>
}

function useCapacityGov(tenantId: string) {
  const [byType,      setByType]      = useState<CapacityByType[]>([])
  const [contracted,  setContracted]  = useState<number>(0)
  const [allocated,   setAllocated]   = useState<number | null>(null)
  const [conformance, setConformance] = useState<Conformance | null>(null)
  const [loading,     setLoading]     = useState(true)

  useEffect(() => {
    if (!tenantId) return
    let cancelled = false
    setLoading(true)
    const enc = encodeURIComponent(tenantId)
    const since = new Date(Date.now() - 86400000).toISOString().slice(0, 10)

    void Promise.allSettled([
      fetch(`/v1/pricing/capacity/${enc}`).then(r => r.ok ? r.json() : null),
      fetch('/v1/pools/capacity/conformance', { headers: { 'x-tenant-id': tenantId } })
        .then(r => r.ok ? r.json() : null),
      fetch(`/reports/pools/occupancy?tenant_id=${enc}&from_dt=${since}&bucket=hour`)
        .then(r => r.ok ? r.json() : null),
    ]).then(([cap, conf, occ]) => {
      if (cancelled) return
      if (cap.status === 'fulfilled' && cap.value) {
        setByType((cap.value.by_type ?? []) as CapacityByType[])
        setContracted(Number(cap.value.agent_capacity_total ?? 0))
      }
      if (conf.status === 'fulfilled' && conf.value) setConformance(conf.value as Conformance)
      if (occ.status === 'fulfilled' && occ.value) {
        // Alocado agora = provisionada do último bucket do total (fallback: agregado do período)
        const d = occ.value.data ?? {}
        const last = Array.isArray(d.total_series) && d.total_series.length > 0
          ? d.total_series[d.total_series.length - 1] : null
        setAllocated(last ? Number(last.capacity ?? 0)
          : d.total ? Number(d.total.provisioned_capacity ?? d.total.capacity ?? 0) : null)
      }
      setLoading(false)
    })
    return () => { cancelled = true }
  }, [tenantId])

  return { byType, contracted, allocated, conformance, loading }
}

function CapacityTab({ tenantId }: { tenantId: string }) {
  const { t } = useTranslation('billing')
  const { byType, contracted, allocated, conformance, loading } = useCapacityGov(tenantId)

  if (loading) return <div className="flex justify-center items-center py-16 flex-1"><Spinner /></div>

  const hasContract   = contracted > 0
  const balance       = hasContract && allocated !== null ? contracted - allocated : null
  const overAllocated = balance !== null && balance < 0
  const nonConform    = conformance !== null && !conformance.conform

  const kpi = (label: string, value: React.ReactNode, hint: string, color?: string) => (
    <div className="rounded-lg border border-lightGray bg-white p-4">
      <p className="text-xs font-semibold text-gray uppercase tracking-wide">{label}</p>
      <p className="text-2xl font-bold font-mono mt-1" style={color ? { color } : undefined}>{value}</p>
      <p className="text-xs text-gray/60 mt-0.5">{hint}</p>
    </div>
  )

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-5">
      <div>
        <h3 className="text-base font-semibold text-dark">{t('capacity.title')}</h3>
        <p className="text-xs text-gray mt-0.5">{t('capacity.subtitle')}</p>
      </div>

      {/* Alertas de não-conformidade */}
      {nonConform && (
        <div className="flex items-start gap-2 bg-red/10 border border-red/30 rounded-lg px-4 py-3 text-xs text-red">
          <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
          <span>
            {t('capacity.alertNonConform')}{' '}
            <span className="font-mono">
              (C={conformance?.contracted ?? '—'} · Σ={conformance?.reserved_total} · shared={conformance?.shared})
            </span>
          </span>
        </div>
      )}
      {overAllocated && (
        <div className="flex items-start gap-2 bg-warning-light border border-warning/40 rounded-lg px-4 py-3 text-xs text-warning-text">
          <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
          <span>
            {t('capacity.alertOverAllocated')}{' '}
            <span className="font-mono">(C={contracted} · alocado={allocated})</span>
          </span>
        </div>
      )}
      {!hasContract && (
        <div className="flex items-start gap-2 bg-info-light border border-info/30 rounded-lg px-4 py-3 text-xs text-info-text">
          <span className="text-base shrink-0">ℹ️</span>
          <span>{t('capacity.noContract')}</span>
        </div>
      )}

      {/* KPIs */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        {kpi(t('capacity.contracted'), hasContract ? contracted : '—', t('capacity.contractedHint'))}
        {kpi(t('capacity.allocated'), allocated ?? '—', t('capacity.allocatedHint'))}
        {kpi(t('capacity.balance'), balance ?? '—', t('capacity.balanceHint'),
             balance === null ? undefined : balance < 0 ? '#DC2626' : '#059669')}
        {kpi(t('capacity.reserved'), conformance?.reserved_total ?? '—', '')}
        {kpi(t('capacity.shared'), conformance?.shared ?? '—', '',
             nonConform ? '#DC2626' : undefined)}
      </div>

      {/* Por tipo de recurso */}
      {byType.length > 0 && (
        <section>
          <h4 className="text-sm font-semibold text-dark mb-2">{t('capacity.byType')}</h4>
          <div className="rounded-lg border border-lightGray overflow-hidden">
            <table className="w-full text-xs">
              <thead className="bg-surface-muted">
                <tr>
                  <th className="px-4 py-2.5 text-left font-semibold text-gray">{t('capacity.cols.type')}</th>
                  <th className="px-4 py-2.5 text-right font-semibold text-gray">{t('capacity.cols.base')}</th>
                  <th className="px-4 py-2.5 text-right font-semibold text-gray">{t('capacity.cols.reserve')}</th>
                  <th className="px-4 py-2.5 text-right font-semibold text-gray">{t('capacity.cols.total')}</th>
                </tr>
              </thead>
              <tbody>
                {byType.map((r, i) => (
                  <tr key={r.resource_type} className={i % 2 === 1 ? 'bg-tableAlt' : 'bg-white'}>
                    <td className="px-4 py-2 text-dark">
                      <ResourceIcon type={r.resource_type} /> {r.resource_type}
                    </td>
                    <td className="px-4 py-2 text-right font-mono text-dark">{r.base}</td>
                    <td className="px-4 py-2 text-right font-mono text-gray">{r.reserve_active}</td>
                    <td className="px-4 py-2 text-right font-mono font-semibold text-dark">{r.total}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* Pools com reserva */}
      <section>
        <h4 className="text-sm font-semibold text-dark mb-2">{t('capacity.reservedPools')}</h4>
        {(conformance?.pools ?? []).length === 0 ? (
          <p className="text-xs text-gray/60">{t('capacity.noReservations')}</p>
        ) : (
          <div className="rounded-lg border border-lightGray overflow-hidden">
            <table className="w-full text-xs">
              <thead className="bg-surface-muted">
                <tr>
                  <th className="px-4 py-2.5 text-left font-semibold text-gray">{t('capacity.poolCols.pool')}</th>
                  <th className="px-4 py-2.5 text-right font-semibold text-gray">{t('capacity.poolCols.reservation')}</th>
                </tr>
              </thead>
              <tbody>
                {(conformance?.pools ?? []).map((p, i) => (
                  <tr key={p.pool_id} className={i % 2 === 1 ? 'bg-tableAlt' : 'bg-white'}>
                    <td className="px-4 py-2 text-dark">{p.pool_id}</td>
                    <td className="px-4 py-2 text-right font-mono text-dark">{p.session_reservation}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}

// ── ResourceSidebar ────────────────────────────────────────────────────────────

interface SidebarProps {
  resources:    InstallationResource[]
  loading:      boolean
}

function ResourceSidebar({ resources, loading }: SidebarProps) {
  const { t } = useTranslation('billing')
  const base    = resources.filter(r => r.pool_type === 'base')
  const reserve = resources.filter(r => r.pool_type === 'reserve')

  const reserveGroups = reserve.reduce<Record<string, InstallationResource[]>>((acc, r) => {
    const key = r.reserve_pool_id ?? 'outros'
    return { ...acc, [key]: [...(acc[key] ?? []), r] }
  }, {})

  return (
    <div className="w-56 shrink-0 bg-surface-muted border-r border-lightGray flex flex-col">
      <div className="px-4 py-3 border-b border-lightGray">
        <h3 className="text-xs font-semibold text-gray uppercase tracking-wide">{t('resources.title')}</h3>
      </div>

      <div className="flex-1 overflow-y-auto px-3 py-3 space-y-4">
        {loading ? (
          <div className="flex justify-center py-8"><Spinner /></div>
        ) : (
          <>
            {/* Base resources */}
            <div>
              <p className="text-xs font-semibold text-gray mb-2 uppercase tracking-wide">{t('resources.base')}</p>
              <div className="space-y-0.5">
                {base.map(r => (
                  <div key={r.id} className="flex items-center gap-2 text-xs text-dark px-2 py-1 rounded hover:bg-white">
                    <ResourceIcon type={r.resource_type} />
                    <span className="flex-1 truncate">{r.label || r.resource_type}</span>
                    <span className="text-gray font-mono shrink-0">×{r.quantity}</span>
                  </div>
                ))}
                {base.length === 0 && (
                  <p className="text-xs text-gray/60 px-2">{t('resources.noResources')}</p>
                )}
              </div>
            </div>

            {/* Reserve groups */}
            {Object.entries(reserveGroups).length > 0 && (
              <div>
                <p className="text-xs font-semibold text-gray mb-2 uppercase tracking-wide">{t('resources.reserve')}</p>
                <div className="space-y-2">
                  {Object.entries(reserveGroups).map(([poolId, items]) => (
                    <div key={poolId} className="rounded border border-lightGray bg-white px-2 py-1.5">
                      <p className="text-xs font-semibold text-dark truncate mb-1">{poolId}</p>
                      {items.map(r => (
                        <div key={r.id} className="flex items-center gap-1 text-xs text-gray">
                          <ResourceIcon type={r.resource_type} />
                          <span className="flex-1 truncate">{r.label || r.resource_type}</span>
                          <span className="font-mono shrink-0">×{r.quantity}</span>
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

// ── InvoiceTab ──────────────────────────────────────────────────────────────────

interface InvoiceTabProps {
  invoice:    Invoice | null
  loading:    boolean
  error:      string | null
  tenantId:   string
  adminToken: string
  onRefresh:  () => void
}

function InvoiceTab({ invoice, loading, error, tenantId, adminToken, onRefresh }: InvoiceTabProps) {
  const { t } = useTranslation('billing')
  const [toggling, setToggling] = useState<string | null>(null)
  const [toast,    setToast]    = useState<string | null>(null)

  const showToast = (msg: string) => {
    setToast(msg)
    setTimeout(() => setToast(null), 3500)
  }

  const handleToggle = async (group: ReserveGroup) => {
    if (!adminToken) {
      showToast(t('invoice.adminTokenRequired'))
      return
    }
    setToggling(group.pool_id)
    try {
      const action = group.active ? 'deactivate' : 'activate'
      const res = await fetch(
        `/v1/pricing/reserve/${encodeURIComponent(tenantId)}/${encodeURIComponent(group.pool_id)}/${action}`,
        { method: 'POST', headers: { 'Authorization': `Bearer ${adminToken}` } }
      )
      if (!res.ok) {
        const body = await res.json().catch(() => ({})) as { detail?: string }
        throw new Error(body.detail ?? `HTTP ${res.status}`)
      }
      showToast(`✅ ${t('invoice.toggleSuccess', { pool: group.pool_id, action: group.active ? t('invoice.deactivate') : t('invoice.activate') })}`)
      onRefresh()
    } catch (err) {
      showToast(`❌ ${String(err)}`)
    } finally {
      setToggling(null)
    }
  }

  if (loading) return <div className="flex justify-center items-center py-16"><Spinner /></div>
  if (error)   return <p className="p-6 text-sm text-red">{t('error.loadInvoice')}: {error}</p>
  if (!invoice) return (
    <div className="p-6">
      <EmptyState title={t('invoice.empty.title')} description={t('invoice.empty.description')} />
    </div>
  )

  const currency = invoice.currency ?? 'BRL'

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6 relative">
      {/* Toast */}
      {toast && (
        <div className="fixed top-4 right-4 bg-dark text-white text-xs px-4 py-2.5 rounded-lg shadow-lg z-50 max-w-xs">
          {toast}
        </div>
      )}

      {/* Cycle header */}
      <div className="flex items-start justify-between">
        <div>
          <h3 className="text-base font-semibold text-dark">{t('invoice.title')}</h3>
          <p className="text-xs text-gray mt-0.5">
            {fmtDate(invoice.cycle_start)} – {fmtDate(invoice.cycle_end)}
            <span className="ml-2 text-gray/60">· {t('invoice.billingDays', { days: invoice.billing_days })} ·</span>
            <span className="ml-1 text-gray/60">{invoice.installation_id}</span>
          </p>
        </div>
        <a
          href={`/v1/pricing/invoice/${encodeURIComponent(tenantId)}?format=xlsx`}
          download
          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-secondary text-white rounded-md hover:opacity-90 transition-opacity shrink-0 ml-4"
        >
          ⬇️ {t('invoice.exportXlsx')}
        </a>
      </div>

      {/* Base items table */}
      {invoice.base_items.length > 0 && (
        <section>
          <h4 className="text-sm font-semibold text-dark mb-2">{t('invoice.baseItems')}</h4>
          <div className="rounded-lg border border-lightGray overflow-hidden">
            <table className="w-full text-xs">
              <thead className="bg-surface-muted">
                <tr>
                  <th className="px-4 py-2.5 text-left font-semibold text-gray">{t('invoice.columns.resource')}</th>
                  <th className="px-4 py-2.5 text-right font-semibold text-gray">{t('invoice.columns.quantity')}</th>
                  <th className="px-4 py-2.5 text-right font-semibold text-gray">{t('invoice.columns.unitPrice')}</th>
                  <th className="px-4 py-2.5 text-right font-semibold text-gray">{t('invoice.columns.days')}</th>
                  <th className="px-4 py-2.5 text-right font-semibold text-gray">{t('invoice.columns.subtotal')}</th>
                </tr>
              </thead>
              <tbody>
                {invoice.base_items.map((item: InvoiceLineItem, i: number) => (
                  <tr key={i} className={i % 2 === 1 ? 'bg-tableAlt' : 'bg-white'}>
                    <td className="px-4 py-2 text-dark">
                      <ResourceIcon type={item.resource_type} />
                      {item.label || item.resource_type}
                    </td>
                    <td className="px-4 py-2 text-right font-mono text-dark">{item.quantity}</td>
                    <td className="px-4 py-2 text-right font-mono text-gray">
                      {fmtCurrency(item.unit_price, currency)}
                    </td>
                    <td className="px-4 py-2 text-right font-mono text-gray">{item.billing_days}</td>
                    <td className="px-4 py-2 text-right font-mono font-semibold text-dark">
                      {fmtCurrency(item.subtotal, currency)}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot className="border-t border-lightGray">
                <tr className="bg-surface-muted">
                  <td colSpan={4} className="px-4 py-2.5 text-right text-sm font-semibold text-dark">
                    {t('invoice.baseItems')}
                  </td>
                  <td className="px-4 py-2.5 text-right text-sm font-semibold text-dark font-mono">
                    {fmtCurrency(invoice.base_total, currency)}
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        </section>
      )}

      {/* Reserve groups */}
      {invoice.reserve_groups.length > 0 && (
        <section>
          <h4 className="text-sm font-semibold text-dark mb-3">{t('invoice.reserveItems')}</h4>
          <div className="space-y-3">
            {invoice.reserve_groups.map((group: ReserveGroup) => (
              <div key={group.pool_id} className="rounded-lg border border-lightGray overflow-hidden">
                {/* Group header */}
                <div
                  className={`flex items-center justify-between px-4 py-2.5 ${
                    group.active ? 'bg-green/10' : 'bg-surface-muted'
                  }`}
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <span
                      className={`w-2 h-2 rounded-full shrink-0 ${
                        group.active ? 'bg-green' : 'bg-gray/30'
                      }`}
                    />
                    <span className="text-sm font-semibold text-dark truncate">
                      {group.label || group.pool_id}
                    </span>
                    <Badge variant={group.active ? 'active' : 'default'}>
                      {group.active ? t('invoice.statusActive') : t('invoice.statusInactive')}
                    </Badge>
                    {group.active && group.days_active > 0 && (
                      <span className="text-xs text-gray shrink-0">{t('invoice.daysActive', { days: group.days_active })}</span>
                    )}
                  </div>
                  <button
                    onClick={() => void handleToggle(group)}
                    disabled={toggling === group.pool_id}
                    className={`ml-3 px-3 py-1 text-xs font-medium rounded-md transition-colors shrink-0 disabled:opacity-50 ${
                      group.active
                        ? 'bg-red/10 text-red hover:bg-red/20'
                        : 'bg-green/10 text-green hover:bg-green/20'
                    }`}
                  >
                    {toggling === group.pool_id ? '…' : group.active ? t('invoice.deactivate') : t('invoice.activate')}
                  </button>
                </div>

                {/* Group items */}
                {group.items.length > 0 && (
                  <table className="w-full text-xs">
                    <tbody>
                      {group.items.map((item: InvoiceLineItem, i: number) => (
                        <tr key={i} className={i % 2 === 1 ? 'bg-tableAlt' : 'bg-white'}>
                          <td className="px-4 py-1.5 text-dark">
                            <ResourceIcon type={item.resource_type} />
                            {item.label || item.resource_type}
                          </td>
                          <td className="px-4 py-1.5 text-right font-mono text-gray">×{item.quantity}</td>
                          <td className="px-4 py-1.5 text-right font-mono text-gray">
                            {fmtCurrency(item.unit_price, currency)}
                          </td>
                          <td className="px-4 py-1.5 text-right font-mono font-semibold text-dark">
                            {fmtCurrency(item.subtotal, currency)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                    <tfoot className="border-t border-lightGray">
                      <tr className="bg-surface-muted">
                        <td colSpan={3} className="px-4 py-1.5 text-right text-xs font-semibold text-dark">
                          {t('invoice.columns.subtotal')}
                        </td>
                        <td className="px-4 py-1.5 text-right font-mono text-xs font-semibold text-dark">
                          {fmtCurrency(group.subtotal, currency)}
                        </td>
                      </tr>
                    </tfoot>
                  </table>
                )}
              </div>
            ))}
          </div>

          {/* Reserve total row */}
          <div className="mt-2 px-4 py-2 bg-surface-muted rounded-lg border border-lightGray flex justify-between items-center">
            <span className="text-sm font-semibold text-dark">{t('invoice.reserveItems')}</span>
            <span className="text-sm font-semibold text-dark font-mono">
              {fmtCurrency(invoice.reserve_total, currency)}
            </span>
          </div>
        </section>
      )}

      {/* Grand Total */}
      <div className="rounded-xl bg-primary/5 border-2 border-primary/20 px-6 py-5 flex items-center justify-between">
        <div>
          <p className="text-xs font-semibold text-gray uppercase tracking-wide">{t('invoice.grandTotal')}</p>
          <p className="text-xs text-gray/60 mt-0.5">
            {t('invoice.generatedAt', { date: fmtDate(invoice.generated_at) })}
          </p>
        </div>
        <span className="text-3xl font-bold text-primary font-mono">
          {fmtCurrency(invoice.grand_total, currency)}
        </span>
      </div>
    </div>
  )
}

// ── ConsumptionTab ──────────────────────────────────────────────────────────────

function ConsumptionTab({ tenantId }: { tenantId: string }) {
  const { t } = useTranslation('billing')
  const { rows, loading } = useUsage(tenantId)

  if (loading) return <div className="flex justify-center items-center py-16"><Spinner /></div>

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-5">
      <div>
        <h3 className="text-base font-semibold text-dark">{t('consumption.title')}</h3>
        <div className="mt-2 flex items-start gap-2 bg-info-light border border-info/30 rounded-lg px-4 py-3 text-xs text-info-text">
          <span className="text-base shrink-0">ℹ️</span>
          <span>{t('consumption.notice')}</span>
        </div>
      </div>

      {rows.length === 0 ? (
        <EmptyState
          title={t('consumption.noData')}
          description={t('consumption.noDataDescription')}
        />
      ) : (
        <div className="grid grid-cols-2 gap-3">
          {rows.map(row => {
            const meta = DIMENSION_LABELS[row.dimension]
            return (
              <div key={row.dimension} className="rounded-lg border border-lightGray bg-white p-4">
                <p className="text-xs font-semibold text-gray uppercase tracking-wide">
                  {meta?.label ?? row.dimension}
                </p>
                <p className="text-2xl font-bold text-dark font-mono mt-1">
                  {row.total.toLocaleString('pt-BR')}
                </p>
                <p className="text-xs text-gray/60 mt-0.5">{meta?.unit ?? t('consumption.defaultUnit')}</p>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── BillingPage ────────────────────────────────────────────────────────────────

type BillingTab = 'invoice' | 'consumption' | 'capacity'

const BillingPage: React.FC = () => {
  const { t } = useTranslation('billing')
  // G-PROBE platform-wide: ações de pricing usam o Bearer do operador + ABAC
  // `config.plataforma` — sem caixa de admin-token.
  const { tenantId, session } = useAuth()
  const adminToken = session?.accessToken ?? ''

  const [activeTab,  setActiveTab]  = useState<BillingTab>('invoice')

  const { invoice, loading: loadingInvoice, error: errorInvoice, refresh: refreshInvoice } =
    useInvoice(tenantId)
  const { resources, loading: loadingRes } = useResources(tenantId)

  return (
    <div className="flex flex-col h-full">
      {/* Page header */}
      <div className="px-6 py-4 border-b border-lightGray bg-white shrink-0">
        <h2 className="text-lg font-semibold text-dark">{t('title')}</h2>
        <p className="text-xs text-gray mt-0.5">
          {t('pageSubtitle')}
        </p>
      </div>

      {/* Body: sidebar + main */}
      <div className="flex flex-1 overflow-hidden">
        <ResourceSidebar
          resources={resources}
          loading={loadingRes}
        />

        {/* Right area */}
        <div className="flex flex-col flex-1 overflow-hidden">
          {/* Tab bar */}
          <div className="flex border-b border-lightGray bg-white px-4 shrink-0">
            {(['invoice', 'consumption', 'capacity'] as BillingTab[]).map(tab => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={`px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
                  activeTab === tab
                    ? 'border-primary text-primary'
                    : 'border-transparent text-gray hover:text-dark'
                }`}
              >
                <>{tab === 'invoice'
                  ? <><Receipt className="w-3.5 h-3.5 inline mr-1.5" aria-hidden="true" />{t('tabs.invoice')}</>
                  : tab === 'consumption'
                  ? <><BarChart2 className="w-3.5 h-3.5 inline mr-1.5" aria-hidden="true" />{t('tabs.consumption')}</>
                  : <><Gauge className="w-3.5 h-3.5 inline mr-1.5" aria-hidden="true" />{t('tabs.capacity')}</>
                }</>

              </button>
            ))}
          </div>

          {/* Tab content */}
          <div className="flex flex-1 overflow-hidden">
            {activeTab === 'invoice' ? (
              <InvoiceTab
                invoice={invoice}
                loading={loadingInvoice}
                error={errorInvoice}
                tenantId={tenantId}
                adminToken={adminToken}
                onRefresh={refreshInvoice}
              />
            ) : activeTab === 'consumption' ? (
              <ConsumptionTab tenantId={tenantId} />
            ) : (
              <CapacityTab tenantId={tenantId} />
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

export default BillingPage
