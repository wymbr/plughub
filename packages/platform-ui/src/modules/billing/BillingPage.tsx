import React, { useCallback, useEffect, useState } from 'react'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'
import Badge from '@/components/ui/Badge'
import EmptyState from '@/components/ui/EmptyState'
import type { Invoice, InvoiceLineItem, ReserveGroup, InstallationResource } from '@/types'

// ── Constants ──────────────────────────────────────────────────────────────────

const RESOURCE_ICONS: Record<string, string> = {
  ai_agent:        '🤖',
  human_agent:     '👤',
  whatsapp_number: '📱',
  voice_trunk_in:  '📞',
  voice_trunk_out: '☎️',
  email_inbox:     '📧',
  sms_number:      '💬',
  webchat_instance:'🌐',
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

// ── ResourceSidebar ────────────────────────────────────────────────────────────

interface SidebarProps {
  resources:    InstallationResource[]
  loading:      boolean
  adminToken:   string
  onAdminToken: (v: string) => void
}

function ResourceSidebar({ resources, loading, adminToken, onAdminToken }: SidebarProps) {
  const base    = resources.filter(r => r.pool_type === 'base')
  const reserve = resources.filter(r => r.pool_type === 'reserve')

  const reserveGroups = reserve.reduce<Record<string, InstallationResource[]>>((acc, r) => {
    const key = r.reserve_pool_id ?? 'outros'
    return { ...acc, [key]: [...(acc[key] ?? []), r] }
  }, {})

  return (
    <div className="w-56 shrink-0 bg-gray-50 border-r border-lightGray flex flex-col">
      <div className="px-4 py-3 border-b border-lightGray">
        <h3 className="text-xs font-semibold text-gray uppercase tracking-wide">Recursos</h3>
      </div>

      <div className="flex-1 overflow-y-auto px-3 py-3 space-y-4">
        {loading ? (
          <div className="flex justify-center py-8"><Spinner /></div>
        ) : (
          <>
            {/* Base resources */}
            <div>
              <p className="text-xs font-semibold text-gray mb-2 uppercase tracking-wide">Base</p>
              <div className="space-y-0.5">
                {base.map(r => (
                  <div key={r.id} className="flex items-center gap-2 text-xs text-dark px-2 py-1 rounded hover:bg-white">
                    <span className="text-sm">{RESOURCE_ICONS[r.resource_type] ?? '📦'}</span>
                    <span className="flex-1 truncate">{r.label || r.resource_type}</span>
                    <span className="text-gray font-mono shrink-0">×{r.quantity}</span>
                  </div>
                ))}
                {base.length === 0 && (
                  <p className="text-xs text-gray/60 px-2">Nenhum recurso base</p>
                )}
              </div>
            </div>

            {/* Reserve groups */}
            {Object.entries(reserveGroups).length > 0 && (
              <div>
                <p className="text-xs font-semibold text-gray mb-2 uppercase tracking-wide">Reserva</p>
                <div className="space-y-2">
                  {Object.entries(reserveGroups).map(([poolId, items]) => (
                    <div key={poolId} className="rounded border border-lightGray bg-white px-2 py-1.5">
                      <p className="text-xs font-semibold text-dark truncate mb-1">{poolId}</p>
                      {items.map(r => (
                        <div key={r.id} className="flex items-center gap-1 text-xs text-gray">
                          <span className="text-sm">{RESOURCE_ICONS[r.resource_type] ?? '📦'}</span>
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

      {/* Admin Token input */}
      <div className="border-t border-lightGray px-3 py-3 shrink-0">
        <label className="block text-xs font-semibold text-gray mb-1">Admin Token</label>
        <input
          type="password"
          value={adminToken}
          onChange={e => onAdminToken(e.target.value)}
          placeholder="Token de administrador"
          className="w-full px-2 py-1.5 text-xs border border-lightGray rounded focus:outline-none focus:border-secondary bg-white text-dark placeholder-gray/50"
        />
        <p className="text-xs text-gray/60 mt-1">Necessário para ativar / desativar pools</p>
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
  const [toggling, setToggling] = useState<string | null>(null)
  const [toast,    setToast]    = useState<string | null>(null)

  const showToast = (msg: string) => {
    setToast(msg)
    setTimeout(() => setToast(null), 3500)
  }

  const handleToggle = async (group: ReserveGroup) => {
    if (!adminToken) {
      showToast('⚠️ Informe o Admin Token no painel lateral')
      return
    }
    setToggling(group.pool_id)
    try {
      const action = group.active ? 'deactivate' : 'activate'
      const res = await fetch(
        `/v1/pricing/reserve/${encodeURIComponent(tenantId)}/${encodeURIComponent(group.pool_id)}/${action}`,
        { method: 'POST', headers: { 'X-Admin-Token': adminToken } }
      )
      if (!res.ok) {
        const body = await res.json().catch(() => ({})) as { detail?: string }
        throw new Error(body.detail ?? `HTTP ${res.status}`)
      }
      showToast(`✅ Pool ${group.pool_id} ${group.active ? 'desativado' : 'ativado'}`)
      onRefresh()
    } catch (err) {
      showToast(`❌ ${String(err)}`)
    } finally {
      setToggling(null)
    }
  }

  if (loading) return <div className="flex justify-center items-center py-16"><Spinner /></div>
  if (error)   return <p className="p-6 text-sm text-red">Erro ao carregar fatura: {error}</p>
  if (!invoice) return (
    <div className="p-6">
      <EmptyState title="Nenhuma fatura disponível" description="A fatura do ciclo atual não pôde ser carregada." />
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
          <h3 className="text-base font-semibold text-dark">Fatura — Ciclo Atual</h3>
          <p className="text-xs text-gray mt-0.5">
            {fmtDate(invoice.cycle_start)} – {fmtDate(invoice.cycle_end)}
            <span className="ml-2 text-gray/60">· {invoice.billing_days} dias úteis ·</span>
            <span className="ml-1 text-gray/60">{invoice.installation_id}</span>
          </p>
        </div>
        <a
          href={`/v1/pricing/invoice/${encodeURIComponent(tenantId)}?format=xlsx`}
          download
          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-secondary text-white rounded-md hover:opacity-90 transition-opacity shrink-0 ml-4"
        >
          ⬇️ Exportar XLSX
        </a>
      </div>

      {/* Base items table */}
      {invoice.base_items.length > 0 && (
        <section>
          <h4 className="text-sm font-semibold text-dark mb-2">Capacidade Base</h4>
          <div className="rounded-lg border border-lightGray overflow-hidden">
            <table className="w-full text-xs">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-2.5 text-left font-semibold text-gray">Recurso</th>
                  <th className="px-4 py-2.5 text-right font-semibold text-gray">Qtd</th>
                  <th className="px-4 py-2.5 text-right font-semibold text-gray">Preço unit.</th>
                  <th className="px-4 py-2.5 text-right font-semibold text-gray">Dias</th>
                  <th className="px-4 py-2.5 text-right font-semibold text-gray">Subtotal</th>
                </tr>
              </thead>
              <tbody>
                {invoice.base_items.map((item: InvoiceLineItem, i: number) => (
                  <tr key={i} className={i % 2 === 1 ? 'bg-tableAlt' : 'bg-white'}>
                    <td className="px-4 py-2 text-dark">
                      <span className="mr-1.5">{RESOURCE_ICONS[item.resource_type] ?? '📦'}</span>
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
                <tr className="bg-gray-50">
                  <td colSpan={4} className="px-4 py-2.5 text-right text-sm font-semibold text-dark">
                    Total Base
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
          <h4 className="text-sm font-semibold text-dark mb-3">Pools de Reserva</h4>
          <div className="space-y-3">
            {invoice.reserve_groups.map((group: ReserveGroup) => (
              <div key={group.pool_id} className="rounded-lg border border-lightGray overflow-hidden">
                {/* Group header */}
                <div
                  className={`flex items-center justify-between px-4 py-2.5 ${
                    group.active ? 'bg-green/10' : 'bg-gray-50'
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
                      {group.active ? 'Ativo' : 'Inativo'}
                    </Badge>
                    {group.active && group.days_active > 0 && (
                      <span className="text-xs text-gray shrink-0">{group.days_active} dias ativos</span>
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
                    {toggling === group.pool_id ? '…' : group.active ? 'Desativar' : 'Ativar'}
                  </button>
                </div>

                {/* Group items */}
                {group.items.length > 0 && (
                  <table className="w-full text-xs">
                    <tbody>
                      {group.items.map((item: InvoiceLineItem, i: number) => (
                        <tr key={i} className={i % 2 === 1 ? 'bg-tableAlt' : 'bg-white'}>
                          <td className="px-4 py-1.5 text-dark">
                            <span className="mr-1.5">{RESOURCE_ICONS[item.resource_type] ?? '📦'}</span>
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
                      <tr className="bg-gray-50">
                        <td colSpan={3} className="px-4 py-1.5 text-right text-xs font-semibold text-dark">
                          Subtotal
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
          <div className="mt-2 px-4 py-2 bg-gray-50 rounded-lg border border-lightGray flex justify-between items-center">
            <span className="text-sm font-semibold text-dark">Total Reserva</span>
            <span className="text-sm font-semibold text-dark font-mono">
              {fmtCurrency(invoice.reserve_total, currency)}
            </span>
          </div>
        </section>
      )}

      {/* Grand Total */}
      <div className="rounded-xl bg-primary/5 border-2 border-primary/20 px-6 py-5 flex items-center justify-between">
        <div>
          <p className="text-xs font-semibold text-gray uppercase tracking-wide">Total Geral</p>
          <p className="text-xs text-gray/60 mt-0.5">
            Gerado em {fmtDate(invoice.generated_at)}
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
  const { rows, loading } = useUsage(tenantId)

  if (loading) return <div className="flex justify-center items-center py-16"><Spinner /></div>

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-5">
      <div>
        <h3 className="text-base font-semibold text-dark">Consumo Variável</h3>
        <div className="mt-2 flex items-start gap-2 bg-blue-50 border border-blue-200 rounded-lg px-4 py-3 text-xs text-blue-700">
          <span className="text-base shrink-0">ℹ️</span>
          <span>
            Esses dados de consumo <strong>não são incluídos no faturamento</strong> — disponíveis
            para curadoria de qualidade operacional. O faturamento é baseado exclusivamente em
            capacidade configurada.
          </span>
        </div>
      </div>

      {rows.length === 0 ? (
        <EmptyState
          title="Sem dados de consumo"
          description="Nenhum evento de uso registrado no ciclo atual."
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
                <p className="text-xs text-gray/60 mt-0.5">{meta?.unit ?? 'eventos'}</p>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── BillingPage ────────────────────────────────────────────────────────────────

type BillingTab = 'invoice' | 'consumption'

const BillingPage: React.FC = () => {
  const { tenantId } = useAuth()

  const [activeTab,  setActiveTab]  = useState<BillingTab>('invoice')
  const [adminToken, setAdminToken] = useState('')

  const { invoice, loading: loadingInvoice, error: errorInvoice, refresh: refreshInvoice } =
    useInvoice(tenantId)
  const { resources, loading: loadingRes } = useResources(tenantId)

  return (
    <div className="flex flex-col h-full">
      {/* Page header */}
      <div className="px-6 py-4 border-b border-lightGray bg-white shrink-0">
        <h2 className="text-lg font-semibold text-dark">Faturamento</h2>
        <p className="text-xs text-gray mt-0.5">
          Capacidade configurada e consumo por ciclo de cobrança
        </p>
      </div>

      {/* Body: sidebar + main */}
      <div className="flex flex-1 overflow-hidden">
        <ResourceSidebar
          resources={resources}
          loading={loadingRes}
          adminToken={adminToken}
          onAdminToken={setAdminToken}
        />

        {/* Right area */}
        <div className="flex flex-col flex-1 overflow-hidden">
          {/* Tab bar */}
          <div className="flex border-b border-lightGray bg-white px-4 shrink-0">
            {(['invoice', 'consumption'] as BillingTab[]).map(tab => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={`px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
                  activeTab === tab
                    ? 'border-primary text-primary'
                    : 'border-transparent text-gray hover:text-dark'
                }`}
              >
                {tab === 'invoice' ? '🧾 Fatura' : '📊 Consumo'}
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
            ) : (
              <ConsumptionTab tenantId={tenantId} />
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

export default BillingPage
