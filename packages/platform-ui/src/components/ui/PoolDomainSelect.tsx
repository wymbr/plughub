import { useEffect, useState } from 'react'
import * as registryApi from '@/api/registry'

/**
 * PoolDomainSelect — combo (single-select) de pool restrito ao DOMÍNIO do usuário.
 *
 * Arco de segurança / Fase E (2026-07-23): o filtro de pool nas telas de Analytics deve
 * oferecer o domínio (`listPools ∩ accessiblePools`), não texto livre — assim o filtro
 * nunca oferece um pool fora do domínio. `accessiblePools` vazio = admin → lista cheia.
 * O scoping DURO continua no backend (`optional_pool_principal` reintersecta sempre);
 * este combo é conveniência + UX, nunca a fronteira de segurança.
 *
 * Single-select (mantém `ContactFilters.poolId: string`, sem tocar o tipo compartilhado
 * nem os endpoints analytics). Para multi-select ver `PoolMultiSelect` (usado no survey,
 * cujo endpoint aceita `pool_ids[]`).
 */
export function PoolDomainSelect({
  tenantId,
  accessiblePools,
  value,
  onChange,
  allLabel,
  className,
}: {
  tenantId:        string
  accessiblePools: string[]          // [] = admin (todos os pools)
  value:           string
  onChange:        (id: string) => void
  allLabel:        string
  className?:      string
}) {
  const [pools, setPools] = useState<string[]>([])

  useEffect(() => {
    if (!tenantId) return
    registryApi.listPools(tenantId)
      .then(r => {
        const all = r.items.map(p => p.pool_id)
        setPools(accessiblePools.length ? all.filter(p => accessiblePools.includes(p)) : all)
      })
      .catch(() => setPools([]))
    // join estabiliza a dep (a prop é um array novo a cada render do pai)
  }, [tenantId, accessiblePools.join(',')])

  return (
    <select value={value} onChange={e => onChange(e.target.value)} className={className}>
      <option value="">{allLabel}</option>
      {pools.map(p => <option key={p} value={p}>{p}</option>)}
    </select>
  )
}
