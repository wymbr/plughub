/**
 * TaxonomyTreeLens — a lente de **taxonomia** da superfície A.
 *
 * Desenha a árvore de respostas do wrap-up com a contagem subindo de cada folha para
 * as pastas que a contêm (D10 do `adr-dialog-tree-options`). Fonte:
 * `GET /reports/agent-events/tree` — nenhum dado novo foi produzido para esta tela.
 *
 * ── Três contagens, e por que não duas ──────────────────────────────────────
 *
 *   próprio   eventos parados EXATAMENTE neste nó
 *   marcações no ramo (este nó + descendentes) — SOMA
 *   contatos  atendimentos DISTINTOS no ramo — **NÃO SOMA**
 *
 * As duas últimas coincidem em pergunta de resposta única e divergem em `checklist`:
 * medido em 2026-09-05, `servico.cadastro` com 2 marcações para 1 atendimento. Exibir
 * só a soma faria a lente totalizar mais que o número de atendimentos, e alguém
 * tiraria percentual disso.
 *
 * **`contatos` não é derivável aqui.** `uniqExact` não soma — não há como obtê-lo a
 * partir das folhas —, e é por isso que a lente consome um endpoint próprio em vez de
 * fazer rollup em cima do `/summary`.
 *
 * ── O que esta tela NÃO tem, e é decisão ────────────────────────────────────
 *
 * **Sem coluna de participação.** Ela dividiria por marcações, e em `checklist` a
 * soma passa do número de atendimentos — um percentual que fecha 100% sobre a base
 * errada é pior que percentual nenhum. Quem quiser proporção tem os dois números.
 *
 * **Sem soma do rodapé.** Somar a coluna de contatos dá mais que o universo (um
 * atendimento que marca serviço em duas pastas conta nas duas). É correto e seria
 * lido como erro; o total honesto está na raiz da árvore, que o endpoint já devolve.
 *
 * ── O que ela HONRA da barra, e o que não ───────────────────────────────────
 *
 * Só o período (`honors: 'period_only'` no contrato). E o filtro de pool não está
 * faltando: o pool é o **primeiro segmento** da categoria por construção do Arc 12,
 * então escolher a raiz JÁ escolhe o pool. Aplicar os dois seria filtrar duas vezes
 * pela mesma coisa, com chance de discordarem.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronDown, ChevronRight, AlertTriangle } from 'lucide-react'
import { useAuth } from '@/auth/useAuth'
import { apiFetch } from '@/api/apiFetch'
import Spinner from '@/components/ui/Spinner'
import EmptyState from '@/components/ui/EmptyState'

interface TreeNode {
  prefix:          string
  depth:           number
  own:             number
  branch_marks:    number
  branch_contacts: number
  derived_leaf:    number | boolean
  first_seen?:     string
  last_seen?:      string
}

interface Vocabulary { form_id: string; version: string | null; events: number }

interface TreeMeta {
  root?:              string
  vocabularies?:      Vocabulary[]
  unstamped_events?:  number
  single_vocabulary?: boolean
}

interface Props { fromDt?: string; toDt?: string }

const isLeaf = (n: TreeNode) => n.derived_leaf === true || n.derived_leaf === 1

export default function TaxonomyTreeLens({ fromDt, toDt }: Props) {
  const { t } = useTranslation('contacts')
  const { tenantId } = useAuth()

  const [roots,   setRoots]   = useState<string[]>([])
  const [root,    setRoot]    = useState<string>('')
  const [nodes,   setNodes]   = useState<TreeNode[]>([])
  const [meta,    setMeta]    = useState<TreeMeta>({})
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState<string | null>(null)
  const [fechados, setFechados] = useState<Set<string>>(() => new Set())

  const qs = useCallback((extra: Record<string, string>) => {
    const p = new URLSearchParams({ tenant_id: tenantId ?? '', ...extra })
    if (fromDt) p.set('from_dt', fromDt)
    if (toDt)   p.set('to_dt',   toDt)
    return p.toString()
  }, [tenantId, fromDt, toDt])

  // ── Raízes: DERIVADAS do dado, nunca listadas à mão ────────────────────────
  // Uma lista fixa de taxonomias envelheceria a cada pergunta nova no editor de
  // formulário — que é exatamente o acoplamento que a fatia 3 do wrap-up removeu.
  // A raiz é `pool.skill.métrica`, os três primeiros segmentos da categoria.
  useEffect(() => {
    if (!tenantId) return
    let vivo = true
    apiFetch(`/reports/agent-events/categories?${qs({})}`)
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then(body => {
        if (!vivo) return
        const achadas = new Set<string>()
        for (const row of (body?.data ?? []) as Array<{ category?: string }>) {
          const segs = String(row.category ?? '').split('.')
          if (segs.length >= 4) achadas.add(segs.slice(0, 3).join('.'))
        }
        const lista = [...achadas].sort()
        setRoots(lista)
        setRoot(r => r || lista[0] || '')
      })
      .catch(() => { if (vivo) setRoots([]) })
    return () => { vivo = false }
  }, [tenantId, qs])

  useEffect(() => {
    if (!tenantId || !root) { setNodes([]); return }
    let vivo = true
    setLoading(true); setError(null)
    apiFetch(`/reports/agent-events/tree?${qs({ root })}`)
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then(body => {
        if (!vivo) return
        setNodes((body?.data ?? []) as TreeNode[])
        setMeta((body?.meta ?? {}) as TreeMeta)
      })
      .catch(e => { if (vivo) { setError(String(e)); setNodes([]) } })
      .finally(() => { if (vivo) setLoading(false) })
    return () => { vivo = false }
  }, [tenantId, root, qs])

  const raizProf = root ? root.split('.').length : 0

  /** Visíveis: um nó só aparece se nenhum ancestral está fechado. */
  const visiveis = useMemo(() => nodes.filter(n => {
    const segs = n.prefix.split('.')
    for (let i = raizProf; i < segs.length; i++) {
      if (fechados.has(segs.slice(0, i).join('.'))) return false
    }
    return true
  }), [nodes, fechados, raizProf])

  const orfas = useMemo(() => nodes.filter(n => !isLeaf(n) && n.own > 0), [nodes])

  if (!root && !loading) {
    return <EmptyState title={t('lens.taxonomy.noRoots')} description={t('lens.taxonomy.noRootsHint')} />
  }

  return (
    <div className="flex flex-col gap-3">
      {/* Seletor de raiz. A raiz carrega o pool no primeiro segmento — trocar de
          raiz é trocar de taxonomia E de pool ao mesmo tempo, de propósito. */}
      <div className="flex items-center gap-2 flex-wrap">
        <label className="text-xs font-medium text-muted" htmlFor="tax-root">
          {t('lens.taxonomy.root')}
        </label>
        <select
          id="tax-root"
          value={root}
          onChange={e => { setRoot(e.target.value); setFechados(new Set()) }}
          className="text-xs border border-border rounded-md px-2 py-1 bg-white text-dark"
        >
          {roots.map(r => <option key={r} value={r}>{r}</option>)}
        </select>
      </div>

      {/* Comparabilidade DECLARADA (`comparability: 'same_form'`). Com mais de um
          vocabulário na janela os totais misturam taxonomias, e a tela tem de dizer
          isso — somar em silêncio é o defeito que a D14 mediu. */}
      {meta.single_vocabulary === false && (
        <div className="flex items-start gap-2 px-3 py-2 rounded-lg bg-warning/10 border border-warning/30 text-xs text-dark">
          <AlertTriangle className="w-4 h-4 text-warning flex-shrink-0 mt-0.5" aria-hidden="true" />
          <div>
            <b>{t('lens.taxonomy.mixed')}</b>{' '}
            {(meta.vocabularies ?? []).map(v => `${v.form_id}${v.version ? ` v${v.version}` : ''} (${v.events})`).join(' · ')}
            {(meta.unstamped_events ?? 0) > 0 &&
              ` · ${t('lens.taxonomy.unstamped', { n: meta.unstamped_events })}`}
            <div className="text-muted mt-0.5">{t('lens.taxonomy.mixedHint')}</div>
          </div>
        </div>
      )}

      {/* Integridade: resposta que parou numa PASTA. Não é comportamento — pasta não
          é selecionável —, então é sintoma de superfície que não desenha a árvore. */}
      {orfas.length > 0 && (
        <div className="flex items-start gap-2 px-3 py-2 rounded-lg bg-warning/10 border border-warning/30 text-xs text-dark">
          <AlertTriangle className="w-4 h-4 text-warning flex-shrink-0 mt-0.5" aria-hidden="true" />
          <div>
            <b>{t('lens.taxonomy.orphan', { n: orfas.reduce((s, n) => s + n.own, 0) })}</b>{' '}
            {orfas.map(n => n.prefix).join(', ')}
            <div className="text-muted mt-0.5">{t('lens.taxonomy.orphanHint')}</div>
          </div>
        </div>
      )}

      {loading && nodes.length === 0 && <Spinner />}
      {error && (
        <div className="px-3 py-2 rounded-lg bg-red-50 border border-red-200 text-red-800 text-xs">
          {t('lens.errorBackend')} — {error}
        </div>
      )}
      {!loading && !error && nodes.length === 0 && (
        <EmptyState title={t('lens.noSample')} description={t('lens.noSampleHint')} />
      )}

      {nodes.length > 0 && (
        <div className="bg-white rounded-xl border border-border overflow-auto">
          <table className="w-full text-sm">
            <thead className="bg-surface-muted border-b border-border">
              <tr>
                <th className="text-left text-xs font-semibold text-muted uppercase tracking-wide px-4 py-2.5">
                  {t('lens.taxonomy.col.node')}
                </th>
                <th className="text-right text-xs font-semibold text-muted uppercase tracking-wide px-4 py-2.5 whitespace-nowrap"
                    title={t('lens.taxonomy.col.ownHint')}>
                  {t('lens.taxonomy.col.own')}
                </th>
                <th className="text-right text-xs font-semibold text-muted uppercase tracking-wide px-4 py-2.5 whitespace-nowrap"
                    title={t('lens.taxonomy.col.marksHint')}>
                  {t('lens.taxonomy.col.marks')}
                </th>
                <th className="text-right text-xs font-semibold text-muted uppercase tracking-wide px-4 py-2.5 whitespace-nowrap"
                    title={t('lens.taxonomy.col.contactsHint')}>
                  {t('lens.taxonomy.col.contacts')}
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {visiveis.map(n => {
                const folha    = isLeaf(n)
                const recuo    = Math.max(0, n.depth - raizProf)
                const rotulo   = n.prefix.split('.').slice(-1)[0]
                const anomalia = !folha && n.own > 0
                const diverge  = n.branch_contacts !== n.branch_marks
                return (
                  <tr key={n.prefix} className={anomalia ? 'bg-warning/5' : undefined}>
                    <td className="px-4 py-2">
                      <span className="flex items-center gap-2" style={{ paddingLeft: recuo * 18 }}>
                        {folha ? <span className="w-3.5" /> : (
                          <button
                            onClick={() => setFechados(s => {
                              const x = new Set(s)
                              x.has(n.prefix) ? x.delete(n.prefix) : x.add(n.prefix)
                              return x
                            })}
                            aria-label={fechados.has(n.prefix) ? t('lens.taxonomy.expand') : t('lens.taxonomy.collapse')}
                            className="text-muted hover:text-dark"
                          >
                            {fechados.has(n.prefix)
                              ? <ChevronRight size={13} />
                              : <ChevronDown size={13} />}
                          </button>
                        )}
                        <span className={folha ? '' : 'font-semibold'}>{rotulo}</span>
                        <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                          folha ? 'bg-green-50 text-green-700' : 'bg-surface-muted text-muted'}`}>
                          {folha ? t('lens.taxonomy.leaf') : t('lens.taxonomy.folder')}
                        </span>
                        {anomalia && <AlertTriangle size={12} className="text-warning" aria-hidden="true" />}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-right tabular-nums text-xs">
                      {n.own > 0 ? <span className="text-warning font-semibold">{n.own}</span>
                                 : <span className="text-muted">—</span>}
                    </td>
                    <td className="px-4 py-2 text-right tabular-nums font-medium">{n.branch_marks}</td>
                    <td className={`px-4 py-2 text-right tabular-nums ${diverge ? 'text-warning font-semibold' : ''}`}>
                      {n.branch_contacts}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      <p className="text-[11px] text-muted leading-snug">{t('lens.taxonomy.footnote')}</p>
    </div>
  )
}
