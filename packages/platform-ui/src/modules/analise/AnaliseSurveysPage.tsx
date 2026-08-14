/**
 * AnaliseSurveysPage — Navegador de respostas de survey (S8) — /analise/surveys
 *
 * Lista resposta-a-resposta (PG survey_response+instance via evaluation-api), com
 * verbatim (LGPD, gate ABAC evaluation). Complementa a agregação da bancada/Voz do
 * Cliente: aqui é inspeção qualitativa individual. Fonte:
 *   GET /v1/evaluation/survey/responses (proxy /v1/evaluation → evaluation-api).
 */
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { apiFetch } from '@/api/apiFetch'
import { useNavigate } from 'react-router-dom'
import { ClipboardList, MessageSquare, X, ExternalLink } from 'lucide-react'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'
import { PoolMultiSelect } from '@/components/ui/PoolMultiSelect'
import * as registryApi from '@/api/registry'

// ── Types ────────────────────────────────────────────────────────────────────

interface Signal { metric: string; value: number; value_label?: string | null }
interface Verbatim { question_id?: string; text: string }

interface SurveyResponseRow {
  response_id:       string
  instance_id:       string
  signals:           Signal[]
  open_text:         string | null
  verbatims:         Verbatim[]
  audio_ref:         string | null
  transcript_ref:    string | null
  response_channel:  string | null
  responded_at:      string
  survey_id:         string | null
  grain:             string
  origin_session_id: string | null
  segment_id:        string | null
  agent_key:         string | null
  pool_id:           string | null
  customer_key:      string | null
  channel:           string | null
  session_at:        string | null
}

const METRICS = ['nps', 'csat', 'ces', 'pmf', 'fcr'] as const
const GRAINS  = ['session', 'segment', 'workflow', 'journey'] as const

// ── Helpers ──────────────────────────────────────────────────────────────────

function isoToday(): string { return new Date().toISOString().slice(0, 10) }
function iso30DaysAgo(): string {
  const d = new Date(); d.setDate(d.getDate() - 29)
  return d.toISOString().slice(0, 10)
}
function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' }) }
  catch { return iso }
}
function truncateId(id: string | undefined | null): string {
  if (!id) return '—'
  return id.length > 16 ? `…${id.slice(-12)}` : id
}
type TFunc = (key: string, opts?: Record<string, unknown>) => string

function metricLabel(m: string): string { return m.toUpperCase() }

function SignalChips({ signals }: { signals: Signal[] }) {
  if (!signals || signals.length === 0) return <span className="text-border-strong">—</span>
  return (
    <span className="inline-flex flex-wrap gap-1">
      {signals.map((s, i) => (
        <span key={i} className="inline-flex items-baseline gap-1 px-1.5 py-0.5 rounded border border-border bg-surface-alt">
          <span className="text-muted-light text-[10px] uppercase">{metricLabel(s.metric)}</span>
          <span className="text-dark font-medium">{s.value}</span>
        </span>
      ))}
    </span>
  )
}

// ── Detail drawer ────────────────────────────────────────────────────────────

function ResponseDrawer({ t, row, onClose, onOpenSession }:
  { t: TFunc; row: SurveyResponseRow; onClose: () => void; onOpenSession: (sid: string) => void }) {
  const verbatims = row.verbatims ?? []
  return (
    <div className="fixed inset-0 z-40 flex justify-end" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-dark/30" onClick={onClose} aria-hidden="true" />
      <div className="relative w-full max-w-xl h-full bg-white shadow-xl flex flex-col animate-in slide-in-from-right">
        <div className="flex items-start justify-between px-5 py-3 border-b border-border flex-shrink-0">
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 text-sm font-medium text-dark">
              <ClipboardList className="w-4 h-4 text-primary" aria-hidden="true" />
              {t('surveys.detail.title', { defaultValue: 'Resposta de pesquisa' })}
            </div>
            <p className="text-xs text-muted-light mt-0.5 font-mono" title={row.response_id}>{truncateId(row.response_id)}</p>
          </div>
          <button onClick={onClose} title={t('surveys.detail.close', { defaultValue: 'Fechar' })}
            className="text-muted-light hover:text-dark transition-colors flex-shrink-0 -mr-1">
            <X className="w-4 h-4" aria-hidden="true" />
          </button>
        </div>

        <div className="flex-1 overflow-auto px-5 py-4 space-y-4 text-xs">
          {/* Signals */}
          <div>
            <div className="text-[10px] uppercase tracking-wide text-muted font-medium mb-1">{t('surveys.detail.signals', { defaultValue: 'Sinais' })}</div>
            <SignalChips signals={row.signals} />
          </div>

          {/* Verbatim (LGPD) */}
          <div>
            <div className="text-[10px] uppercase tracking-wide text-muted font-medium mb-1 flex items-center gap-1">
              <MessageSquare className="w-3 h-3" aria-hidden="true" />
              {t('surveys.detail.verbatim', { defaultValue: 'Texto aberto (verbatim)' })}
            </div>
            {(verbatims.length > 0 || row.open_text) ? (
              <div className="space-y-1.5">
                {row.open_text && (
                  <p className="bg-surface-muted border border-border rounded px-2 py-1.5 text-dark">{row.open_text}</p>
                )}
                {verbatims.map((v, i) => (
                  <div key={i} className="bg-surface-muted border border-border rounded px-2 py-1.5">
                    {v.question_id && <div className="text-[10px] text-muted-light font-mono mb-0.5">{v.question_id}</div>}
                    <p className="text-dark">{v.text}</p>
                  </div>
                ))}
              </div>
            ) : (
              <span className="text-border-strong">{t('surveys.detail.noVerbatim', { defaultValue: 'Sem texto aberto' })}</span>
            )}
          </div>

          {/* Meta */}
          <div className="grid grid-cols-2 gap-x-4 gap-y-1.5">
            <Meta label={t('surveys.columns.type', { defaultValue: 'Formulário' })} value={row.survey_id || '—'} mono />
            <Meta label={t('surveys.columns.grain', { defaultValue: 'Grão' })} value={t(`enums.grain.${row.grain}`, { defaultValue: row.grain })} />
            <Meta label={t('surveys.columns.pool', { defaultValue: 'Pool' })} value={row.pool_id || '—'} />
            <Meta label={t('surveys.detail.agent', { defaultValue: 'Agente' })} value={row.agent_key || '—'} mono />
            <Meta label={t('surveys.columns.channel', { defaultValue: 'Canal' })} value={row.channel ? t(`enums.channel.${row.channel}`, { defaultValue: row.channel }) : '—'} />
            <Meta label={t('surveys.columns.customer', { defaultValue: 'Cliente' })} value={row.customer_key || '—'} mono />
            <Meta label={t('surveys.detail.respondedAt', { defaultValue: 'Respondida' })} value={fmtDate(row.responded_at)} />
            <Meta label={t('surveys.detail.sessionAt', { defaultValue: 'Sessão em' })} value={fmtDate(row.session_at)} />
          </div>

          {/* Origin session link */}
          {row.origin_session_id && (
            <button
              onClick={() => onOpenSession(row.origin_session_id!)}
              className="inline-flex items-center gap-1.5 text-primary hover:underline">
              <ExternalLink className="w-3.5 h-3.5" aria-hidden="true" />
              {t('surveys.detail.openSession', { defaultValue: 'Abrir processo/sessão de origem' })}
              <span className="font-mono text-muted-light">{truncateId(row.origin_session_id)}</span>
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

function Meta({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] uppercase tracking-wide text-muted-light">{label}</div>
      <div className={`text-dark truncate ${mono ? 'font-mono' : ''}`} title={value}>{value}</div>
    </div>
  )
}

// ── Main page ────────────────────────────────────────────────────────────────

export default function AnaliseSurveysPage() {
  const { tenantId, currentUser } = useAuth()
  const { t } = useTranslation('contacts')
  const navigate = useNavigate()

  const [fromDt, setFromDt]   = useState(iso30DaysAgo)
  const [toDt, setToDt]       = useState(isoToday)
  const [grain, setGrain]     = useState('')
  const [metric, setMetric]   = useState('')
  const [poolIds, setPoolIds] = useState<string[]>([])
  const [domainPools, setDomainPools] = useState<string[]>([])
  const [rows, setRows]       = useState<SurveyResponseRow[]>([])
  const [total, setTotal]     = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState<string | null>(null)
  const [selected, setSelected] = useState<SurveyResponseRow | null>(null)

  const load = useCallback(() => {
    if (!tenantId) return
    setLoading(true); setError(null)
    const q = new URLSearchParams({ tenant_id: tenantId, from_dt: fromDt, to_dt: toDt, limit: '200' })
    if (grain)  q.set('grain', grain)
    if (metric) q.set('metric', metric)
    poolIds.forEach(p => q.append('pool_ids', p))
    apiFetch(`/v1/evaluation/survey/responses?${q.toString()}`)
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then(d => { setRows(d.data ?? []); setTotal(d.total ?? (d.data?.length ?? 0)) })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }, [tenantId, fromDt, toDt, grain, metric, poolIds])

  useEffect(() => { load() }, [load])

  // Combo de pool = DOMÍNIO do usuário (listPools ∩ accessiblePools; vazio = admin → todos).
  // O backend REINTERSECTA sempre — este filtro é conveniência, não a fronteira de segurança.
  useEffect(() => {
    if (!tenantId) return
    registryApi.listPools(tenantId)
      .then(r => {
        const all = r.items.map(p => p.pool_id)
        const dom = currentUser?.accessiblePools ?? []
        setDomainPools(dom.length ? all.filter(p => dom.includes(p)) : all)
      })
      .catch(() => setDomainPools([]))
  }, [tenantId, currentUser])

  if (!tenantId) {
    return <div className="flex items-center justify-center h-full text-muted-light text-sm">
      {t('surveys.noTenant', { defaultValue: 'Sem tenant' })}
    </div>
  }

  return (
    <div className="flex flex-col h-full overflow-hidden bg-surface-muted">
      {/* Filter bar */}
      <div className="bg-white border-b border-border px-5 py-2.5 flex items-center gap-3 flex-shrink-0 flex-wrap">
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-muted">{t('surveys.filters.from', { defaultValue: 'De' })}</label>
          <input type="date" value={fromDt} onChange={e => setFromDt(e.target.value)}
            className="text-xs border border-border-strong rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-primary/40" />
        </div>
        <div className="flex items-center gap-1.5">
          <label className="text-xs text-muted">–</label>
          <input type="date" value={toDt} onChange={e => setToDt(e.target.value)}
            className="text-xs border border-border-strong rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-primary/40" />
        </div>
        <select value={metric} onChange={e => setMetric(e.target.value)}
          className="text-xs border border-border-strong rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-primary/40">
          <option value="">{t('surveys.filters.allTypes', { defaultValue: 'Todos os tipos' })}</option>
          {METRICS.map(m => <option key={m} value={m}>{metricLabel(m)}</option>)}
        </select>
        <select value={grain} onChange={e => setGrain(e.target.value)}
          className="text-xs border border-border-strong rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-primary/40">
          <option value="">{t('surveys.filters.allGrains', { defaultValue: 'Todos os grãos' })}</option>
          {GRAINS.map(g => <option key={g} value={g}>{t(`enums.grain.${g}`, { defaultValue: g })}</option>)}
        </select>
        <PoolMultiSelect
          pools={domainPools}
          value={poolIds}
          onChange={setPoolIds}
          allLabel={t('surveys.filters.allPools', { defaultValue: 'Todos os pools do domínio' })}
          placeholder={t('surveys.filters.noPools', { defaultValue: 'Nenhum pool no domínio' })}
          countLabel={(n) => t('surveys.filters.poolCount', { count: n, defaultValue: `${n} pools` })}
        />
        <div className="flex-1" />
        {loading
          ? <Spinner />
          : <button onClick={load} className="text-xs text-muted-light hover:text-muted transition-colors px-2 py-1">
              {t('surveys.refresh', { defaultValue: 'Atualizar' })}
            </button>}
      </div>

      {/* Count */}
      <div className="flex items-center px-5 py-2 bg-white border-b border-border flex-shrink-0 text-xs">
        <span className="text-muted-light">
          <strong className="text-dark">{t('surveys.totalCount', { count: total, defaultValue: `${total} respostas` })}</strong>
        </span>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto px-5 py-4">
        {error ? (
          <div className="flex flex-col items-center justify-center py-20 text-red gap-2">
            <ClipboardList className="w-10 h-10 opacity-30" aria-hidden="true" />
            <span className="text-sm font-medium">{t('surveys.error', { defaultValue: 'Erro ao carregar respostas' })}</span>
            <span className="text-xs text-muted font-mono max-w-lg text-center break-all">{error}</span>
          </div>
        ) : rows.length === 0 && !loading ? (
          <div className="flex flex-col items-center justify-center py-20 text-muted-light gap-2">
            <MessageSquare className="w-10 h-10 opacity-30" aria-hidden="true" />
            <span className="text-sm">{t('surveys.empty', { defaultValue: 'Nenhuma resposta no período' })}</span>
          </div>
        ) : (
          <table className="w-full text-xs bg-white border border-border rounded-lg overflow-hidden border-separate border-spacing-0">
            <thead className="sticky top-0 z-10 bg-surface-muted border-b border-border">
              <tr>
                <th className="px-3 py-2.5 text-left text-muted font-medium">{t('surveys.columns.respondedAt', { defaultValue: 'Respondida' })}</th>
                <th className="px-3 py-2.5 text-left text-muted font-medium">{t('surveys.columns.signals', { defaultValue: 'Sinais' })}</th>
                <th className="px-3 py-2.5 text-left text-muted font-medium">{t('surveys.columns.grain', { defaultValue: 'Grão' })}</th>
                <th className="px-3 py-2.5 text-left text-muted font-medium">{t('surveys.columns.pool', { defaultValue: 'Pool' })}</th>
                <th className="px-3 py-2.5 text-left text-muted font-medium">{t('surveys.columns.customer', { defaultValue: 'Cliente' })}</th>
                <th className="px-3 py-2.5 text-left text-muted font-medium">{t('surveys.columns.verbatim', { defaultValue: 'Verbatim' })}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => {
                const hasV = (r.verbatims?.length ?? 0) > 0 || !!r.open_text
                return (
                  <tr key={r.response_id} onClick={() => setSelected(r)}
                    className="border-t border-border hover:bg-surface-muted transition-colors cursor-pointer">
                    <td className="px-3 py-2.5 text-muted-light whitespace-nowrap">{fmtDate(r.responded_at)}</td>
                    <td className="px-3 py-2.5"><SignalChips signals={r.signals} /></td>
                    <td className="px-3 py-2.5 text-muted-light">{t(`enums.grain.${r.grain}`, { defaultValue: r.grain })}</td>
                    <td className="px-3 py-2.5 text-muted-light">{r.pool_id || '—'}</td>
                    <td className="px-3 py-2.5 text-muted-light font-mono" title={r.customer_key ?? ''}>{truncateId(r.customer_key)}</td>
                    <td className="px-3 py-2.5">
                      {hasV
                        ? <span className="inline-flex items-center gap-1 text-primary"><MessageSquare className="w-3.5 h-3.5" aria-hidden="true" />{t('surveys.hasVerbatim', { defaultValue: 'sim' })}</span>
                        : <span className="text-border-strong">—</span>}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {selected && (
        <ResponseDrawer
          t={t}
          row={selected}
          onClose={() => setSelected(null)}
          onOpenSession={sid => navigate(`/analise/sessions?journey=${encodeURIComponent(sid)}`)}
        />
      )}
    </div>
  )
}
