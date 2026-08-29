/**
 * AccountTokensPanel — a lente de token da **Superfície B** (F3 · T3 metade B · D2).
 *
 * A pergunta da OFERTA: *quanto cada CONTA LLM gastou*. A da superfície A é outra —
 * *quanto estes contatos custaram* — e as duas **não são comparáveis**, porque a
 * população difere: lá o consumo é juntado às sessões filtradas; aqui é o
 * `usage_events` inteiro do período. Medido em 2026-08-29: 945 de 1 991 tokens.
 * Ver o cabeçalho de `resources_query.py`.
 *
 * ── Três ausências, três linhas, nunca uma soma ──────────────────────────────
 * O rodapé conta separadamente o que o backend separa: pré-época (história, não se
 * conserta), sem conta (defeito de propagação) e sem cadastro (a conta existe e
 * consumiu; falta cadastrá-la em `llm_accounts`). Somá-las esconderia o defeito dentro
 * da história — a regra do `usage_attribution`, e o mesmo erro que o `sla_target_ms`
 * cometia antes de ganhar contador próprio.
 *
 * ⚠️ **Não existe `tokens_total`**, pela mesma razão da superfície A: entrada e saída
 * têm preços diferentes em todo provedor, e somá-las dá o número mais fácil de publicar
 * e o menos utilizável.
 */
import React, { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { apiFetch } from '@/api/apiFetch'

interface Row {
  account_config_id: string
  account_key_id:    string
  model_id:          string
  model_profile:     string
  source:            string
  tokens_in:         number
  tokens_out:        number
  sessions:          number
  events:            number
}

interface Meta {
  from?: string
  to?:   string
  attribution_epoch?:   string
  pre_epoch_events?:    number
  unidentified_events?: number
  uncatalogued_events?: number
}

const num = (n: number) => n.toLocaleString()

export default function AccountTokensPanel({ tenantId, fromDt, toDt }: {
  tenantId: string
  fromDt:   string
  toDt:     string
}) {
  const { t } = useTranslation('agentReports')
  const [rows,    setRows]    = useState<Row[]>([])
  const [meta,    setMeta]    = useState<Meta | null>(null)
  const [loading, setLoading] = useState(true)
  const [erro,    setErro]    = useState('')

  useEffect(() => {
    let cancelado = false
    setLoading(true); setErro('')
    const qs = new URLSearchParams({ tenant_id: tenantId, from_dt: fromDt, to_dt: toDt })
    apiFetch(`/reports/resources/tokens?${qs}`)
      .then(async r => ({ ok: r.ok, body: await r.json() }))
      .then(({ ok, body }) => {
        if (cancelado) return
        // 503 com `error` nomeado é DEGRADAÇÃO, não série vazia. Sem esta distinção a
        // tela diria "nenhum consumo" para uma consulta que falhou — e numa tela de
        // custo esse é o zero mais caro que existe.
        if (!ok || body.error) { setErro(body.error || `HTTP`); setRows([]); setMeta(null) }
        else { setRows(body.data ?? []); setMeta(body.meta ?? null) }
      })
      .catch(e => { if (!cancelado) { setErro(String(e)); setRows([]) } })
      .finally(() => { if (!cancelado) setLoading(false) })
    return () => { cancelado = true }
  }, [tenantId, fromDt, toDt])

  if (loading) return (
    <div className="p-8 text-sm text-muted-light animate-pulse">{t('pools.volume.loading')}</div>
  )

  if (erro) return (
    <div className="p-8 flex flex-col items-center gap-2 text-sm">
      <span className="text-3xl" aria-hidden="true">⚠️</span>
      <span className="text-dark font-medium">{t('resources.tokens.error')}</span>
      <span className="text-xs text-muted-light font-mono">{erro}</span>
    </div>
  )

  const totalIn  = rows.reduce((s, r) => s + Number(r.tokens_in  || 0), 0)
  const totalOut = rows.reduce((s, r) => s + Number(r.tokens_out || 0), 0)

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-lg border border-border p-4">
        <div className="flex items-baseline justify-between flex-wrap gap-2">
          <h2 className="text-sm font-semibold text-dark">{t('resources.tokens.title')}</h2>
          {/* A afirmação de escopo vai na TELA, não só no contrato: esta lente ignora
              o filtro de pool da barra porque o gasto da conta é do tenant. */}
          <span className="text-2xs text-warning">{t('resources.tokens.periodOnly')}</span>
        </div>
        <p className="text-2xs text-muted-light mt-1">{t('resources.tokens.hint')}</p>

        {rows.length === 0 ? (
          <div className="py-10 text-center text-sm text-muted-light">
            {t('resources.tokens.noData')}
            {!!meta?.attribution_epoch && (
              <div className="text-xs mt-1">
                {t('resources.tokens.epochHint', { date: meta.attribution_epoch })}
              </div>
            )}
          </div>
        ) : (
          <>
            <div className="flex gap-8 mt-3">
              <div>
                <div className="text-2xl font-bold text-dark">{num(totalIn)}</div>
                <div className="text-2xs text-muted uppercase tracking-wide">{t('resources.tokens.in')}</div>
              </div>
              <div>
                <div className="text-2xl font-bold text-dark">{num(totalOut)}</div>
                <div className="text-2xs text-muted uppercase tracking-wide">{t('resources.tokens.out')}</div>
              </div>
            </div>

            <div className="overflow-x-auto mt-4">
              <table className="w-full text-xs">
                <thead className="text-muted border-b border-border">
                  <tr>
                    <th className="text-left px-3 py-2">{t('resources.tokens.cols.account')}</th>
                    <th className="text-left px-3 py-2">{t('resources.tokens.cols.model')}</th>
                    <th className="text-left px-3 py-2">{t('resources.tokens.cols.profile')}</th>
                    <th className="text-left px-3 py-2">{t('resources.tokens.cols.source')}</th>
                    <th className="text-right px-3 py-2">{t('resources.tokens.cols.in')}</th>
                    <th className="text-right px-3 py-2">{t('resources.tokens.cols.out')}</th>
                    <th className="text-right px-3 py-2">{t('resources.tokens.cols.sessions')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {rows.map((r, i) => (
                    <tr key={i} className="hover:bg-surface-muted">
                      <td className="px-3 py-2">
                        {/* TRÊS estados, não dois — e a primeira versão desta célula
                            colapsava dois deles, chamando de "fora do catálogo" uma
                            linha que não tinha chave nenhuma:
                              config_id presente ....... conta cadastrada
                              só key_id ................ conta REAL, fora do catálogo
                              nenhum dos dois .......... não se sabe quem gastou
                            `config_id` sobrevive à rotação de chave; `key_id` não. A
                            distinção é o que separa "falta cadastrar" de "não sabemos". */}
                        {r.account_config_id ? (
                          <span className="text-dark font-medium">{r.account_config_id}</span>
                        ) : r.account_key_id ? (
                          <span className="text-muted">
                            {t('resources.tokens.uncatalogued')}
                            <span className="font-mono text-2xs ml-1">{r.account_key_id.slice(0, 8)}…</span>
                          </span>
                        ) : (
                          <span className="text-warning">{t('resources.tokens.unidentified')}</span>
                        )}
                      </td>
                      <td className="px-3 py-2 text-dark">{r.model_id || '—'}</td>
                      <td className="px-3 py-2 text-muted">{r.model_profile || '—'}</td>
                      <td className="px-3 py-2 text-muted">{r.source || '—'}</td>
                      <td className="px-3 py-2 text-right text-dark">{num(Number(r.tokens_in || 0))}</td>
                      <td className="px-3 py-2 text-right text-dark">{num(Number(r.tokens_out || 0))}</td>
                      <td className="px-3 py-2 text-right text-muted">{num(Number(r.sessions || 0))}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}

        {/* As três ausências, uma linha cada. Nunca somadas — ver o cabeçalho. */}
        <div className="mt-3 space-y-0.5">
          {!!meta?.uncatalogued_events && (
            <p className="text-2xs text-muted-light">
              {t('resources.tokens.note.uncatalogued', { count: meta.uncatalogued_events })}
            </p>
          )}
          {!!meta?.unidentified_events && (
            <p className="text-2xs text-warning">
              {t('resources.tokens.note.unidentified', { count: meta.unidentified_events })}
            </p>
          )}
          {!!meta?.pre_epoch_events && (
            <p className="text-2xs text-muted-light">
              {t('resources.tokens.note.preEpoch', {
                count: meta.pre_epoch_events, date: meta.attribution_epoch,
              })}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
