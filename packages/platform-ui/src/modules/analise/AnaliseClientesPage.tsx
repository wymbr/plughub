/**
 * AnaliseClientesPage — /analise/customers  (Customer History H5)
 *
 * A lente ANALYTICS do Cliente 360 (ADR `adr-customer-360-two-surfaces.md`): a visão
 * retrospectiva/supervisão do cliente, FORA do atendimento ao vivo. Reusa 100% dos
 * endpoints já construídos — busca de cadastro (C1a), 360 agregado (C1b), jornadas
 * (HJ / D2) e contatos+transcrição+busca (H1/H2/H3) — via os componentes do Console
 * (Customer360Card + HistoricoTab), agora keyed por um cliente ESCOLHIDO (não pela
 * sessão viva). Deep-link `?customer=<id>` pré-seleciona.
 *
 * Fora de escopo (backlog): índice GIN(tsvector) p/ escala de busca (segue ClickHouse
 * substring, H2). Ver `docs/arcos/customer-contact-history.md` §9 (H5).
 */
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Search, User, Users } from 'lucide-react'
import { useAuth } from '@/auth/useAuth'
import { Customer360Card } from '@/modules/agent-assist/components/Customer360Card'
import { HistoricoTab } from '@/modules/agent-assist/components/tabs/HistoricoTab'

const TENANT_ID = import.meta.env.VITE_TENANT_ID ?? 'tenant_demo'

interface CustomerResult {
  customer_id: string
  status:      string
  attributes:  Record<string, unknown>
}

function attrName(attrs: Record<string, unknown> | undefined): string | null {
  const n = attrs?.['nome'] ?? attrs?.['name']
  return typeof n === 'string' && n.trim() ? n : null
}

export default function AnaliseClientesPage() {
  const { t } = useTranslation('contacts')
  const { tenantId } = useAuth()
  const tenant = tenantId ?? TENANT_ID
  const [searchParams, setSearchParams] = useSearchParams()

  const [query,     setQuery]     = useState('')
  const [results,   setResults]   = useState<CustomerResult[]>([])
  const [searching, setSearching] = useState(false)
  const [searched,  setSearched]  = useState(false)

  // Cliente selecionado (deep-link `?customer=` pré-seleciona).
  const initial = searchParams.get('customer')
  const [selected, setSelected] = useState<{ id: string; name: string | null } | null>(
    initial ? { id: initial, name: null } : null,
  )

  useEffect(() => {
    // Sincroniza a URL quando a seleção muda (compartilhável, back/forward).
    const cur = searchParams.get('customer') ?? ''
    if ((selected?.id ?? '') !== cur) {
      const next = new URLSearchParams(searchParams)
      if (selected) next.set('customer', selected.id)
      else next.delete('customer')
      setSearchParams(next, { replace: true })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected])

  async function doSearch() {
    const q = query.trim()
    if (!q) return
    setSearching(true)
    try {
      const res = await fetch(`/v1/channels/webhook/identity/customers/search?${new URLSearchParams({
        tenant_id: tenant, q, limit: '20',
      })}`)
      const data = res.ok ? await res.json() : { results: [] }
      setResults(data.results ?? [])
    } catch {
      setResults([])
    } finally {
      setSearching(false); setSearched(true)
    }
  }

  function pick(r: CustomerResult) {
    setSelected({ id: r.customer_id, name: attrName(r.attributes) })
    setResults([]); setSearched(false); setQuery('')
  }

  if (!tenantId) {
    return (
      <div className="flex items-center justify-center h-full text-muted-light text-sm">
        {t('customers.noTenant', { defaultValue: 'No tenant.' })}
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full overflow-hidden bg-surface-muted">

      {/* Top bar: título + busca de cliente */}
      <div className="bg-white border-b border-border px-5 py-3 flex-shrink-0">
        <div className="flex items-center gap-2 mb-2">
          <Users className="w-4 h-4 text-primary" aria-hidden="true" />
          <h1 className="text-sm font-semibold text-dark">{t('customers.title', { defaultValue: 'Customers' })}</h1>
          <span className="text-xs text-muted-light">{t('customers.subtitle', { defaultValue: 'Retrospective 360 view' })}</span>
        </div>
        <div className="relative max-w-md">
          <div className="flex items-center gap-1.5">
            <div className="relative flex-1">
              <Search className="w-3.5 h-3.5 text-muted-light absolute left-2 top-1/2 -translate-y-1/2" aria-hidden="true" />
              <input
                type="text" value={query}
                onChange={e => setQuery(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') doSearch() }}
                placeholder={t('customers.searchPlaceholder', { defaultValue: 'Name or customer_id…' })}
                className="w-full text-xs border border-border-strong rounded pl-7 pr-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-primary/40 text-dark bg-white placeholder-muted-light"
              />
            </div>
            <button onClick={doSearch} disabled={searching || !query.trim()}
              className="text-xs px-3 py-1.5 rounded bg-primary text-white font-medium disabled:opacity-40 hover:bg-primary-dark transition-colors">
              {searching ? '…' : t('customers.search', { defaultValue: 'Search' })}
            </button>
          </div>

          {/* Dropdown de resultados */}
          {(results.length > 0 || (searched && !searching)) && (
            <div className="absolute z-20 mt-1 w-full bg-white border border-border rounded-lg shadow-lg max-h-72 overflow-y-auto">
              {results.length === 0 ? (
                <div className="text-xs text-muted-light px-3 py-2">{t('customers.noResults', { defaultValue: 'No customer found.' })}</div>
              ) : results.map(r => (
                <button key={r.customer_id} onClick={() => pick(r)}
                  className="w-full text-left px-3 py-2 hover:bg-primary-light border-b border-border last:border-0 flex items-center gap-2">
                  <User className="w-3.5 h-3.5 text-muted-light shrink-0" aria-hidden="true" />
                  <div className="min-w-0">
                    <div className="text-xs font-medium text-dark truncate">
                      {attrName(r.attributes) ?? <span className="text-muted-light italic">{t('customers.noName', { defaultValue: '(no name)' })}</span>}
                    </div>
                    <div className="font-mono text-2xs text-muted-light truncate">{r.customer_id}</div>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 min-h-0 p-4">
        {!selected ? (
          <div className="h-full flex flex-col items-center justify-center text-muted-light gap-2">
            <Users className="w-8 h-8" aria-hidden="true" />
            <span className="text-sm">{t('customers.empty', { defaultValue: 'Search a customer to see their 360 view.' })}</span>
          </div>
        ) : (
          <div className="h-full grid grid-cols-[340px_1fr] gap-4">

            {/* Coluna esquerda: ficha (identidade + 360) */}
            <div className="overflow-y-auto space-y-3">
              <div className="bg-white border border-border rounded-lg p-3">
                <div className="text-2xs font-semibold text-muted uppercase tracking-wide mb-1">
                  {t('customers.selected', { defaultValue: 'Customer' })}
                </div>
                {selected.name && <div className="text-sm font-semibold text-dark">{selected.name}</div>}
                <div className="font-mono text-xs text-muted break-all">{selected.id}</div>
                <button onClick={() => setSelected(null)}
                  className="mt-2 text-2xs text-primary hover:text-primary-dark transition-colors">
                  {t('customers.clear', { defaultValue: 'Clear selection' })}
                </button>
              </div>
              <Customer360Card customerId={selected.id} />
            </div>

            {/* Coluna direita: Histórico (jornadas + contatos + busca + drill) */}
            <div className="border border-border rounded-lg bg-white overflow-hidden h-full">
              <HistoricoTab customerId={selected.id} tenantId={tenant} />
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
