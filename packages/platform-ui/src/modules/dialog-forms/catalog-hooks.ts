/**
 * catalog-hooks.ts — os dois catálogos que o editor de DialogForm consulta.
 *
 *   dialog.formats  → formatos de entrada (afordância + veredicto)
 *   masking.types   → tipos de mascaramento (política + classe LGPD)
 *
 * Ambos vêm do **config-api**, e não de `@plughub/schemas`, por duas razões que
 * se somam: o `platform-ui` não depende do pacote de schemas, e — a que
 * importa — a fonte de verdade em runtime é o STORE. Ler o default embutido
 * faria a tela mostrar um catálogo e o tenant ter outro, sem nada acusar.
 *
 * ── Cache por tenant, e por que ele existe ───────────────────────────────────
 * `QuestionEditor` é renderizado uma vez POR PERGUNTA. Sem cache, um formulário
 * de dez perguntas dispararia dez buscas idênticas a cada render. O cache é um
 * mapa de PROMESSAS (não de resultados): assim dez montagens simultâneas
 * compartilham a MESMA requisição em voo, e não dez que chegam quase juntas.
 *
 * Não há invalidação por tempo: a vida do cache é a da aba. Editar o catálogo é
 * ato raro e feito noutra tela, que recarrega ao navegar.
 */
import { useEffect, useState } from 'react'
import { apiFetch } from '@/api/apiFetch'

export interface FormatEntry {
  id:                string
  label?:            string | Record<string, string>
  from_masked_type?: string
  affordance?:       { mask?: string; inputmode?: string; maxlength?: number }
  verdict?:          { shape?: string; semantic?: string }
}

export interface MaskedTypeEntry {
  id:      string
  label?:  string
  icon?:   string
  lgpd?:   string
  formato?: { display?: string }
}

type Cache = Map<string, Promise<unknown>>
const cache: Cache = new Map()

async function fetchKey(tenantId: string, ns: string, key: string): Promise<unknown> {
  const ck = `${tenantId}::${ns}.${key}`
  const hit = cache.get(ck)
  if (hit) return hit
  const p = apiFetch(`/config/${ns}/${key}?tenant_id=${encodeURIComponent(tenantId)}`)
    .then(async r => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const j = (await r.json()) as { value?: unknown }
      return j.value
    })
    .catch(e => {
      // Uma falha não pode ficar cacheada para sempre: a próxima montagem
      // tentaria de novo e continuaria vendo o erro antigo.
      cache.delete(ck)
      throw e
    })
  cache.set(ck, p)
  return p
}

function useCatalog<T>(tenantId: string, ns: string, key: string, pick: (v: unknown) => T[]) {
  const [itens, setItens] = useState<T[]>([])
  const [erro,  setErro]  = useState<string | null>(null)

  useEffect(() => {
    if (!tenantId) return
    let vivo = true
    fetchKey(tenantId, ns, key)
      .then(v => { if (vivo) { setItens(pick(v)); setErro(null) } })
      // Degradação NOMEADA: a lista fica vazia e o motivo viaja, para a tela
      // poder dizer "catálogo indisponível" em vez de "nenhum formato existe" —
      // que são fatos diferentes e levam a decisões diferentes.
      .catch(e => { if (vivo) { setItens([]); setErro(String(e)) } })
    return () => { vivo = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenantId, ns, key])

  return { itens, erro }
}

export function useFormatCatalog(tenantId: string): {
  formats: FormatEntry[]
  erro:    string | null
} {
  const { itens, erro } = useCatalog<FormatEntry>(tenantId, 'dialog', 'formats', v =>
    ((v as { formats?: FormatEntry[] } | undefined)?.formats) ?? [])
  return { formats: itens, erro }
}

export function useMaskedTypes(tenantId: string): {
  types: MaskedTypeEntry[]
  erro:  string | null
} {
  const { itens, erro } = useCatalog<MaskedTypeEntry>(tenantId, 'masking', 'types', v =>
    ((v as { types?: MaskedTypeEntry[] } | undefined)?.types) ?? [])
  return { types: itens, erro }
}

/** Rótulo legível de uma entrada de catálogo (o `label` pode ser i18n embutida). */
export function formatLabel(f: FormatEntry, locale: string): string {
  const l = f.label
  if (!l) return f.id
  if (typeof l === 'string') return l
  return l[locale] ?? Object.values(l)[0] ?? f.id
}

/**
 * D8 — dado o tipo mascarado declarado num campo, qual formato vale sem o autor
 * declarar de novo? `undefined` é desfecho LEGÍTIMO: `credential` e `opaque`
 * mascaram sem formatar. Mascarar e formatar são eixos ortogonais.
 */
export function formatForMasked(
  maskedType: string | undefined,
  formats:    FormatEntry[],
): FormatEntry | undefined {
  if (!maskedType) return undefined
  return formats.find(f => f.from_masked_type === maskedType)
}
