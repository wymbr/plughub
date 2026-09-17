/**
 * WebRTCSpeechChecksPage.tsx
 * Configuration → Channels → WebRTC → Settings — active speech-path verification (VOZ-27).
 *
 * The backend (VOZ-23) could already run a check, store the result and compare it against a baseline;
 * all of it lived behind `curl`. This screen is the half that makes it usable by the person who
 * DECIDES: run one now, read the history per profile, mark the baseline, see what regressed.
 *
 * Three things it deliberately does NOT do:
 *   · it never calls a regression — it shows the deltas and the items that changed, and the reader
 *     decides. There is no threshold anywhere in this file;
 *   · it never marks a baseline by itself. The baseline is the sentence "this is how it sounded when
 *     it was right", and only a person can say it;
 *   · it never implies periodic coverage. A profile without an Agenda says so, because "nobody has
 *     measured this in months" and "measured and fine" must not look alike.
 *
 * ⚠️ "Run now" goes to `POST /v1/speech-checks` on the channel-gateway (Bearer + `config.channels`
 * in write), which relays to the internal executor. The browser never touches the executor's service
 * token nor the anonymous webhook pool.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import { apiFetch } from '@/api/apiFetch'
import { useNamespace, putConfig } from '../config-plataforma/api/config-hooks'
import { SPEECH_PROFILES_NS } from './WebRTCSpeechProfilesPage'
import Spinner from '@/components/ui/Spinner'

/** Namespace where the baseline marked by a person lives (one key per profile). */
const BASELINES_NS = 'speech_check_baselines'
/** Key of the "no profile" case — the tenant's own speech config. Mirrors `SPEECH_CHECK_NO_PROFILE_KEY`. */
const NO_PROFILE_KEY = '_tenant'
/** Pool an Agenda fires to ask for a check. Mirrors `infra/registry/tenant_demo.yaml`. */
const TRIGGER_POOL = 'speech_check_trigger'

interface Check {
  check_id:            string
  started_at:          string
  requested_by:        string
  status:              string
  failure_reason:      string | null
  speech_profile_id:   string | null
  profile_in_effect:   string | null
  stt_model:           string | null
  reference_version:   string
  session_id:          string | null
  accuracy:            number | null
  phrases_correct:     number | null
  phrases_total:       number | null
  confidence_p50:      number | null
  hallucinations:      number | null
}

interface Comparison {
  status:     string
  detail?:    string
  baseline:   Check | null
  latest:     Check | null
  comparison: {
    accuracy_delta?:   number | null
    wer_delta?:        number | null
    confidence_delta?: number | null
    items_regressed?:  string[]
    items_improved?:   string[]
    changes?:          Record<string, { baseline: unknown; latest: unknown }>
  } | null
}

interface Agenda {
  id:             string
  name:           string
  status:         string
  target_pool_id: string
  next_fire_at:   string | null
  payload:        Record<string, unknown> | null
}

function pct(v: number | null | undefined): string {
  return v === null || v === undefined ? '—' : `${(v * 100).toFixed(1)}%`
}
function delta(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  const s = (v * 100).toFixed(1)
  return v > 0 ? `+${s} pp` : `${s} pp`
}
function quando(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleString()
}

const WebRTCSpeechChecksPage: React.FC = () => {
  const { t } = useTranslation('channels')
  const { tenantId, session } = useAuth()
  const token = session?.accessToken ?? ''
  const profilesNs = useNamespace(tenantId ?? '', SPEECH_PROFILES_NS)

  const [profile,  setProfile]  = useState('')          // '' = the tenant's own config
  const [checks,   setChecks]   = useState<Check[]>([])
  const [cmp,      setCmp]      = useState<Comparison | null>(null)
  const [agendas,  setAgendas]  = useState<Agenda[] | null>(null)   // null = could not read
  const [loading,  setLoading]  = useState(false)
  const [busy,     setBusy]     = useState(false)
  const [erro,     setErro]     = useState<string | null>(null)
  const [aviso,    setAviso]    = useState<string | null>(null)

  const baselineKey = profile || NO_PROFILE_KEY
  const profileIds = useMemo(
    () => Object.keys(profilesNs.entries).sort((a, b) => a.localeCompare(b)),
    [profilesNs.entries],
  )

  const load = useCallback(async () => {
    if (!tenantId) return
    setLoading(true); setErro(null)
    const qs = `tenant_id=${encodeURIComponent(tenantId)}&speech_profile_id=${encodeURIComponent(profile)}`
    try {
      const [rc, rk] = await Promise.all([
        apiFetch(`/reports/speech/checks?${qs}&limit=25`),
        apiFetch(`/reports/speech/checks/compare?${qs}`),
      ])
      if (!rc.ok) throw new Error(`/reports/speech/checks → HTTP ${rc.status}`)
      if (!rk.ok) throw new Error(`/reports/speech/checks/compare → HTTP ${rk.status}`)
      setChecks(((await rc.json()).data ?? []) as Check[])
      setCmp(await rk.json() as Comparison)
    } catch (e) {
      setErro(String(e))
    } finally {
      setLoading(false)
    }
  }, [tenantId, profile])

  useEffect(() => { load() }, [load])

  // A agenda é lida SEPARADAMENTE e a falha não derruba a tela: não conseguir ler o scheduler é
  // diferente de não haver agenda, e as duas coisas se dizem com frases diferentes (`agendas === null`).
  useEffect(() => {
    let vivo = true
    if (!tenantId) return
    apiFetch('/v1/agendas', { headers: { 'X-Tenant-ID': tenantId } })
      .then(r => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      // A listagem é `{agendas, total}`; um corpo em outra forma é "não consegui ler" (null),
      // nunca uma lista vazia — que a tela leria como "não há agenda" e diria a frase errada.
      .then(d => { if (vivo) setAgendas(Array.isArray(d?.agendas) ? d.agendas as Agenda[] : null) })
      .catch(() => { if (vivo) setAgendas(null) })
    return () => { vivo = false }
  }, [tenantId])

  const agendaDoPerfil = useMemo(() => {
    if (!agendas) return null
    return agendas.find(a =>
      a.target_pool_id === TRIGGER_POOL &&
      String((a.payload ?? {}).speech_profile_id ?? '') === profile) ?? null
  }, [agendas, profile])

  async function executar() {
    setBusy(true); setErro(null); setAviso(null)
    try {
      const r = await apiFetch('/v1/speech-checks', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ speech_profile_id: profile || null }),
      })
      const corpo = await r.json().catch(() => ({}))
      if (r.status === 202) {
        setAviso(t('speechChecks.runAccepted'))
      } else if (r.status === 409) {
        // Recusa legítima, não erro de quem clicou — e o serviço já a registrou no histórico.
        setAviso(t('speechChecks.runBusy'))
      } else if (r.status === 403) {
        setErro(t('speechChecks.runForbidden'))
      } else if (r.status === 503) {
        setErro(t('speechChecks.runUnconfigured'))
      } else {
        const d = (corpo as { detail?: unknown }).detail
        setErro(t('speechChecks.runFailed', { detail: typeof d === 'string' ? d : JSON.stringify(d ?? r.status) }))
      }
    } catch (e) {
      setErro(String(e))
    } finally {
      setBusy(false)
    }
  }

  async function marcarBase(c: Check) {
    if (!confirm(t('speechChecks.baselineConfirm', { when: quando(c.started_at), accuracy: pct(c.accuracy) }))) return
    setBusy(true); setErro(null); setAviso(null)
    try {
      // `marked_by` é QUEM aprovou esta referência — a pergunta que a linha de base existe para
      // responder daqui a seis meses. Sem identidade na sessão, o campo fica FORA: gravar "desconhecido"
      // seria inventar autoria, e o `marked_at` sozinho ainda diz quando.
      const quem = session?.email || session?.userId || ''
      await putConfig(BASELINES_NS, baselineKey, {
        check_id:  c.check_id,
        marked_at: new Date().toISOString(),
        ...(quem ? { marked_by: quem } : {}),
      }, tenantId, '', token)
      setAviso(t('speechChecks.baselineMarked', { when: quando(c.started_at) }))
      load()
    } catch (e) {
      setErro(String(e))
    } finally {
      setBusy(false)
    }
  }

  const baselineId = cmp?.baseline?.check_id ?? null
  const c = cmp?.comparison ?? null
  const regrediram = c?.items_regressed ?? []
  const melhoraram = c?.items_improved ?? []

  const btn = 'px-3 py-1.5 rounded text-xs font-semibold transition-colors disabled:opacity-40'

  return (
    <section className="max-w-4xl bg-white border border-border rounded-lg p-5 space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-sm font-semibold text-dark mb-1">{t('speechChecks.title')}</h3>
          <p className="text-xs text-muted">{t('speechChecks.intro')}</p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {(loading || busy) && <Spinner />}
          <select
            value={profile}
            onChange={e => setProfile(e.target.value)}
            className="text-xs font-mono px-2 py-1.5 border border-border-strong rounded focus:outline-none focus:border-primary"
          >
            <option value="">{t('speechChecks.tenantConfig')}</option>
            {profileIds.map(id => <option key={id} value={id}>{id}</option>)}
          </select>
          <button onClick={load} disabled={loading} className={`${btn} border border-border-strong text-dark hover:bg-surface`}>
            {t('speechChecks.refresh')}
          </button>
          <button onClick={executar} disabled={busy || !token} className={`${btn} bg-primary text-white hover:bg-primary-dark`}>
            {t('speechChecks.run')}
          </button>
        </div>
      </div>

      {erro  && <p className="text-xs text-red-text">⚠ {erro}</p>}
      {aviso && <p className="text-xs text-green-text">{aviso}</p>}

      {/* Periodicidade — a AUSÊNCIA é dita, nunca deduzida como cobertura. */}
      <p className="text-xs text-muted">
        {agendas === null
          ? t('speechChecks.agendaUnreadable')
          : agendaDoPerfil
            ? t('speechChecks.agendaFound', {
                name:   agendaDoPerfil.name,
                status: agendaDoPerfil.status,
                next:   quando(agendaDoPerfil.next_fire_at),
              })
            : t('speechChecks.agendaMissing')}
      </p>

      {/* Comparação com a linha de base */}
      <div className="border border-border rounded p-3 space-y-2">
        <p className="text-xs font-semibold text-dark">{t('speechChecks.comparisonTitle')}</p>
        <p className="text-xs text-muted">
          {t(`speechChecks.cmpStatus.${cmp?.status ?? 'loading'}`, { defaultValue: cmp?.status ?? '' })}
          {cmp?.detail ? ` — ${cmp.detail}` : ''}
        </p>
        {cmp?.status === 'compared' && (
          <div className="space-y-1">
            <div className="flex flex-wrap gap-4 text-xs">
              <span>{t('speechChecks.accuracyDelta')}: <strong className="font-mono">{delta(c?.accuracy_delta)}</strong></span>
              <span>{t('speechChecks.werDelta')}: <strong className="font-mono">{delta(c?.wer_delta)}</strong></span>
              <span>{t('speechChecks.confidenceDelta')}: <strong className="font-mono">{delta(c?.confidence_delta)}</strong></span>
            </div>
            <p className="text-xs">
              {t('speechChecks.itemsRegressed')}: <strong className="font-mono">{regrediram.length}</strong>
              {regrediram.length > 0 && <span className="font-mono text-muted"> ({regrediram.join(', ')})</span>}
            </p>
            <p className="text-xs">
              {t('speechChecks.itemsImproved')}: <strong className="font-mono">{melhoraram.length}</strong>
              {melhoraram.length > 0 && <span className="font-mono text-muted"> ({melhoraram.join(', ')})</span>}
            </p>
            {c?.changes && Object.keys(c.changes).length > 0 && (
              <p className="text-xs text-muted">
                {t('speechChecks.configChanged')}:{' '}
                <span className="font-mono">
                  {Object.entries(c.changes)
                    .map(([k, v]) => `${k}: ${String(v.baseline ?? '—')} → ${String(v.latest ?? '—')}`)
                    .join(' · ')}
                </span>
              </p>
            )}
          </div>
        )}
      </div>

      {/* Histórico */}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-muted border-b border-border">
              <th className="py-1.5 pr-3 font-medium">{t('speechChecks.col.when')}</th>
              <th className="py-1.5 pr-3 font-medium">{t('speechChecks.col.requestedBy')}</th>
              <th className="py-1.5 pr-3 font-medium">{t('speechChecks.col.outcome')}</th>
              <th className="py-1.5 pr-3 font-medium">{t('speechChecks.col.accuracy')}</th>
              <th className="py-1.5 pr-3 font-medium">{t('speechChecks.col.phrases')}</th>
              <th className="py-1.5 pr-3 font-medium">{t('speechChecks.col.confidence')}</th>
              <th className="py-1.5 pr-3 font-medium">{t('speechChecks.col.hallucinations')}</th>
              <th className="py-1.5 pr-3 font-medium">{t('speechChecks.col.applied')}</th>
              <th className="py-1.5 font-medium" />
            </tr>
          </thead>
          <tbody>
            {checks.length === 0 && !loading && (
              <tr><td colSpan={9} className="py-3 text-muted">{t('speechChecks.empty')}</td></tr>
            )}
            {checks.map(ck => {
              const base = ck.check_id === baselineId
              const falhou = ck.status !== 'completed'
              return (
                <tr key={ck.check_id} className={`border-b border-border ${base ? 'bg-surface' : ''}`}>
                  <td className="py-1.5 pr-3 whitespace-nowrap">
                    {quando(ck.started_at)}
                    {base && <span className="ml-2 px-1.5 py-0.5 rounded bg-primary text-white">{t('speechChecks.baselineTag')}</span>}
                  </td>
                  <td className="py-1.5 pr-3 font-mono text-muted">{ck.requested_by}</td>
                  <td className="py-1.5 pr-3">
                    {falhou
                      // A falha diz O QUE não mediu; "failed" sozinho mandaria procurar no log.
                      ? <span className="text-red-text">{t(`speechChecks.failure.${ck.failure_reason ?? 'unknown'}`, { defaultValue: ck.failure_reason ?? ck.status })}</span>
                      : <span className="text-green-text">{t('speechChecks.completed')}</span>}
                  </td>
                  <td className="py-1.5 pr-3 font-mono">{pct(ck.accuracy)}</td>
                  <td className="py-1.5 pr-3 font-mono">
                    {ck.phrases_total ? `${ck.phrases_correct ?? 0}/${ck.phrases_total}` : '—'}
                  </td>
                  <td className="py-1.5 pr-3 font-mono">{ck.confidence_p50 === null ? '—' : ck.confidence_p50?.toFixed(2)}</td>
                  <td className="py-1.5 pr-3 font-mono">{ck.hallucinations ?? '—'}</td>
                  <td className="py-1.5 pr-3 font-mono text-muted">
                    {/* O que o gateway APLICOU, não o que o pedido pediu. */}
                    {ck.profile_in_effect ?? t('speechChecks.tenantConfig')}
                    {ck.stt_model ? ` · ${ck.stt_model}` : ''}
                  </td>
                  <td className="py-1.5 text-right">
                    {!falhou && !base && (
                      <button
                        onClick={() => marcarBase(ck)}
                        disabled={busy || !token}
                        className={`${btn} border border-border-strong text-dark hover:bg-surface`}
                      >
                        {t('speechChecks.markBaseline')}
                      </button>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted">{t('speechChecks.footnote')}</p>
    </section>
  )
}

export default WebRTCSpeechChecksPage
