/**
 * TaxonomyTreeLens — a lente de **taxonomia** da superfície A.
 *
 * Desenha a árvore de respostas do wrap-up com a contagem subindo de cada folha para
 * as pastas que a contêm (D10 do `adr-dialog-tree-options`), **recortada por ÉPOCA**
 * de vocabulário (D13). Fontes: `GET /reports/agent-events/{epochs,tree}` — nenhum
 * dado novo foi produzido para esta tela.
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
 * tiraria percentual disso. **`contatos` não é derivável aqui** — `uniqExact` não
 * soma —, e é por isso que a lente consome um endpoint próprio.
 *
 * ── Por que ÉPOCA, e não um aviso ───────────────────────────────────────────
 *
 * Repontar o hook de um pool troca o vocabulário **sob a mesma série**: medido, o
 * mesmo `segunda_via` existindo na raiz (forma plana) e sob `cadastro` (forma em
 * árvore), 9 e 1, sem nenhuma linha dizendo 10. A primeira versão desta tela avisava
 * e somava assim mesmo. Recortar o período por época faz o conflito **deixar de
 * existir** em vez de ser tratado — dentro de um bloco há um vocabulário só, então
 * total e percentual voltam a ser legítimos sem guarda nenhuma.
 *
 * O bloco é por **run contíguo**, não por forma: rollback do hook faria "um bloco por
 * forma" fundir duas fases e apagar a do meio.
 *
 * ⚠️ **A época NÃO conserta o que não foi gravado.** Evento anterior ao carimbo
 * (2026-09-05) não tem forma; ele vira uma época própria, rotulada como tal, e essa
 * ainda pode misturar vocabulários — o endpoint declara e a tela repassa. Forward-only
 * por construção: a forma vigente no passado não é recuperável, porque a tabela
 * `pools` é atualizada no lugar e não guarda histórico de hook.
 *
 * ── O que esta tela NÃO tem, e é decisão ────────────────────────────────────
 *
 * **Sem coluna de participação** (dividiria por marcações, e em `checklist` a soma
 * passa do número de atendimentos) e **sem soma de rodapé** (somar contatos dá mais
 * que o universo). O total honesto está na raiz de cada bloco.
 *
 * ── O que ela HONRA da barra ────────────────────────────────────────────────
 *
 * Só o período (`honors: 'period_only'`). O filtro de pool não está faltando: o pool
 * é o **primeiro segmento** da categoria por construção do Arc 12, então escolher a
 * raiz JÁ escolhe o pool.
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
}

interface Epoch {
  form_id:  string
  from_dt:  string
  to_dt:    string
  events:   number
  contacts: number
  versions: string[]
  stamped:  boolean
}

interface TreeMeta {
  unstamped_events?:  number
  single_vocabulary?: boolean
}

interface Props { fromDt?: string; toDt?: string }

const isLeaf = (n: TreeNode) => n.derived_leaf === true || n.derived_leaf === 1

const quando = (iso: string) => {
  try { return new Date(iso).toLocaleString() } catch { return iso }
}

// ── Um bloco por época ───────────────────────────────────────────────────────

function EpochBlock({
  root, epoch, unica,
}: { root: string; epoch: Epoch | null; unica: boolean }) {
  const { t } = useTranslation('contacts')
  const { tenantId } = useAuth()
  const [nodes,   setNodes]   = useState<TreeNode[]>([])
  const [meta,    setMeta]    = useState<TreeMeta>({})
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState<string | null>(null)
  const [fechados, setFechados] = useState<Set<string>>(() => new Set())

  useEffect(() => {
    if (!tenantId || !root) return
    let vivo = true
    setLoading(true); setError(null)
    const p = new URLSearchParams({ tenant_id: tenantId, root })
    if (epoch) {
      p.set('from_dt', epoch.from_dt)
      p.set('to_dt',   epoch.to_dt)
      // String VAZIA é a época anterior ao carimbo — uma época legítima. Por isso o
      // parâmetro é sempre enviado quando há época: omiti-lo significaria "sem
      // filtro", que é outra coisa.
      p.set('form_id', epoch.form_id)
    }
    apiFetch(`/reports/agent-events/tree?${p}`)
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then(body => {
        if (!vivo) return
        setNodes((body?.data ?? []) as TreeNode[])
        setMeta((body?.meta ?? {}) as TreeMeta)
      })
      .catch(e => { if (vivo) { setError(String(e)); setNodes([]) } })
      .finally(() => { if (vivo) setLoading(false) })
    return () => { vivo = false }
  }, [tenantId, root, epoch])

  const raizProf = root ? root.split('.').length : 0

  const visiveis = useMemo(() => nodes.filter(n => {
    const segs = n.prefix.split('.')
    for (let i = raizProf; i < segs.length; i++) {
      if (fechados.has(segs.slice(0, i).join('.'))) return false
    }
    return true
  }), [nodes, fechados, raizProf])

  const orfas = useMemo(() => nodes.filter(n => !isLeaf(n) && n.own > 0), [nodes])

  /**
   * Rótulos que aparecem em MAIS DE UM caminho.
   *
   * Dentro de uma época carimbada isto deve ficar vazio — é o sinal de que o recorte
   * funcionou. Quando aparece (tipicamente na época SEM carimbo, que não é
   * separável), é o mesmo item do mundo real em dois caminhos: fundir seria errado, e
   * deixar dois rótulos iguais é pior, então desambigua-se pelo pai.
   */
  const ambiguos = useMemo(() => {
    const conta = new Map<string, number>()
    for (const n of nodes) {
      const r = n.prefix.split('.').slice(-1)[0] ?? ''
      conta.set(r, (conta.get(r) ?? 0) + 1)
    }
    return new Set([...conta].filter(([, c]) => c > 1).map(([r]) => r))
  }, [nodes])

  return (
    <div className="flex flex-col gap-2">
      {!unica && epoch && (
        <div className="flex items-baseline gap-2 flex-wrap pt-1">
          <span className={`text-sm font-semibold ${epoch.stamped ? 'text-dark' : 'text-muted'}`}>
            {epoch.stamped ? epoch.form_id : t('lens.taxonomy.epoch.unstamped')}
          </span>
          {epoch.versions.length > 0 && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-surface-muted text-muted">
              v{epoch.versions.join(', v')}
            </span>
          )}
          <span className="text-xs text-muted">
            {quando(epoch.from_dt)} → {quando(epoch.to_dt)}
          </span>
          <span className="text-xs text-muted">
            · {t('lens.taxonomy.epoch.counts', { events: epoch.events, contacts: epoch.contacts })}
          </span>
        </div>
      )}

      {/* Só sobra aviso DENTRO de uma época quando ela é a sem-carimbo: ali o recorte
          não separa, porque a forma nunca foi gravada. */}
      {meta.single_vocabulary === false && (
        <div className="flex items-start gap-2 px-3 py-2 rounded-lg bg-warning/10 border border-warning/30 text-xs text-dark">
          <AlertTriangle className="w-4 h-4 text-warning flex-shrink-0 mt-0.5" aria-hidden="true" />
          <div>
            <b>{t('lens.taxonomy.mixedInEpoch')}</b>
            <div className="text-muted mt-0.5">{t('lens.taxonomy.mixedInEpochHint')}</div>
          </div>
        </div>
      )}

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
                    title={t('lens.taxonomy.col.ownHint')}>{t('lens.taxonomy.col.own')}</th>
                <th className="text-right text-xs font-semibold text-muted uppercase tracking-wide px-4 py-2.5 whitespace-nowrap"
                    title={t('lens.taxonomy.col.marksHint')}>{t('lens.taxonomy.col.marks')}</th>
                <th className="text-right text-xs font-semibold text-muted uppercase tracking-wide px-4 py-2.5 whitespace-nowrap"
                    title={t('lens.taxonomy.col.contactsHint')}>{t('lens.taxonomy.col.contacts')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {visiveis.map(n => {
                const folha    = isLeaf(n)
                const recuo    = Math.max(0, n.depth - raizProf)
                const segs     = n.prefix.split('.')
                const rotulo   = segs[segs.length - 1] ?? ''
                const paiAmbiguo = ambiguos.has(rotulo) && segs.length > raizProf + 1
                  ? segs[segs.length - 2] : null
                const anomalia = !folha && n.own > 0
                const diverge  = n.branch_contacts !== n.branch_marks
                return (
                  <tr key={n.prefix} title={n.prefix}
                      className={anomalia ? 'bg-warning/5' : undefined}>
                    <td className="px-4 py-2">
                      <span className="flex items-center gap-2" style={{ paddingLeft: recuo * 18 }}>
                        {folha ? <span className="w-3.5" /> : (
                          <button
                            onClick={() => setFechados(s => {
                              const x = new Set(s)
                              x.has(n.prefix) ? x.delete(n.prefix) : x.add(n.prefix)
                              return x
                            })}
                            aria-label={fechados.has(n.prefix)
                              ? t('lens.taxonomy.expand') : t('lens.taxonomy.collapse')}
                            className="text-muted hover:text-dark"
                          >
                            {fechados.has(n.prefix) ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
                          </button>
                        )}
                        <span className={folha ? '' : 'font-semibold'}>
                          {paiAmbiguo && <span className="text-muted font-normal">{paiAmbiguo} › </span>}
                          {rotulo}
                        </span>
                        <span className={`text-[10px] px-1.5 py-0.5 rounded ${
                          folha ? 'bg-green-50 text-green-700' : 'bg-surface-muted text-muted'}`}>
                          {folha ? t('lens.taxonomy.leaf') : t('lens.taxonomy.folder')}
                        </span>
                        {anomalia && <AlertTriangle size={12} className="text-warning" aria-hidden="true" />}
                      </span>
                    </td>
                    {/* `own` só tem informação em PASTA: numa folha ele é IGUAL a
                        `branch_marks` por definição, e pintá-lo de alerta ali dilui o
                        único sinal que a coluna existe para dar. */}
                    <td className="px-4 py-2 text-right tabular-nums text-xs">
                      {folha
                        ? <span className="text-muted" title={t('lens.taxonomy.col.ownLeaf')}>—</span>
                        : n.own > 0
                          ? <span className="text-warning font-semibold">{n.own}</span>
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

      {ambiguos.size > 0 && (
        <p className="text-[11px] text-muted leading-snug">{t('lens.taxonomy.ambiguous')}</p>
      )}
    </div>
  )
}

// ── A lente ──────────────────────────────────────────────────────────────────

export default function TaxonomyTreeLens({ fromDt, toDt }: Props) {
  const { t } = useTranslation('contacts')
  const { tenantId } = useAuth()

  const [roots,  setRoots]  = useState<string[]>([])
  const [root,   setRoot]   = useState<string>('')
  const [epochs, setEpochs] = useState<Epoch[]>([])
  const [carregandoEpocas, setCarregandoEpocas] = useState(false)

  const qs = useCallback((extra: Record<string, string>) => {
    const p = new URLSearchParams({ tenant_id: tenantId ?? '', ...extra })
    if (fromDt) p.set('from_dt', fromDt)
    if (toDt)   p.set('to_dt',   toDt)
    return p.toString()
  }, [tenantId, fromDt, toDt])

  // Raízes DERIVADAS do dado, nunca listadas à mão: lista fixa envelheceria a cada
  // pergunta nova no editor de formulário. A raiz é `pool.skill.métrica`.
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
    if (!tenantId || !root) { setEpochs([]); return }
    let vivo = true
    setCarregandoEpocas(true)
    apiFetch(`/reports/agent-events/epochs?${qs({ root })}`)
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then(body => { if (vivo) setEpochs((body?.data ?? []) as Epoch[]) })
      .catch(() => { if (vivo) setEpochs([]) })
      .finally(() => { if (vivo) setCarregandoEpocas(false) })
    return () => { vivo = false }
  }, [tenantId, root, qs])

  if (!root && !carregandoEpocas) {
    return <EmptyState title={t('lens.taxonomy.noRoots')} description={t('lens.taxonomy.noRootsHint')} />
  }

  // Uma época (ou nenhuma detectada) ⇒ um bloco só, sem cabeçalho de época: a janela
  // inteira fala um vocabulário, e ganhar cabeçalho ali seria ruído.
  const unica = epochs.length <= 1

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2 flex-wrap">
        <label className="text-xs font-medium text-muted" htmlFor="tax-root">
          {t('lens.taxonomy.root')}
        </label>
        <select
          id="tax-root"
          value={root}
          onChange={e => setRoot(e.target.value)}
          className="text-xs border border-border rounded-md px-2 py-1 bg-white text-dark"
        >
          {roots.map(r => <option key={r} value={r}>{r}</option>)}
        </select>
        {!unica && (
          <span className="text-xs text-muted">
            · {t('lens.taxonomy.epoch.count', { n: epochs.length })}
          </span>
        )}
      </div>

      {carregandoEpocas && epochs.length === 0 && <Spinner />}

      {unica
        ? <EpochBlock root={root} epoch={epochs[0] ?? null} unica />
        : epochs.map(e => (
            <EpochBlock key={`${e.form_id}|${e.from_dt}`} root={root} epoch={e} unica={false} />
          ))}

      <p className="text-[11px] text-muted leading-snug">{t('lens.taxonomy.footnote')}</p>
      {!unica && (
        <p className="text-[11px] text-muted leading-snug">{t('lens.taxonomy.epoch.hint')}</p>
      )}
    </div>
  )
}
