/**
 * WebRTCSpeechConfigPage.tsx
 * Configuration → Channels → WebRTC — segmentação da fala do bot leg (VOZ-21).
 *
 * Lê e escreve o namespace `webrtc` do config-api (`stt_*`). Salvar grava override do TENANT; a
 * plataforma continua dona do default (`__global__`, semeado). O escopo em vigor de cada chave vem
 * de `/config/webrtc/_provenance` — sem ele a tela não saberia dizer se o valor é do tenant.
 *
 * ⚠️ As faixas repetem `channel-gateway/speech_config.py::PARAMS`, que é quem VALIDA de verdade (o
 * config-api não valida valor). A tela recusa antes só para não oferecer o que o gateway recusaria.
 */
import React, { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import { useNamespace, useProvenance, putConfig, deleteConfig } from '../config-plataforma/api/config-hooks'
import Spinner from '@/components/ui/Spinner'

const NS = 'webrtc'

interface ParamDef { key: string; min?: number; max?: number; step?: number; bool?: boolean; text?: boolean }

const PARAMS: ParamDef[] = [
  { key: 'stt_energy_threshold', min: 50,   max: 5000,  step: 50 },
  { key: 'stt_end_silence_ms',   min: 100,  max: 5000,  step: 50 },
  { key: 'stt_gap_ms',           min: 100,  max: 5000,  step: 50 },
  { key: 'stt_min_speech_ms',    min: 50,   max: 2000,  step: 50 },
  { key: 'stt_max_speech_ms',    min: 1000, max: 60000, step: 500 },
  { key: 'stt_vad_filter',       bool: true },
  // VOZ-06 — aviso dito ao cliente antes de gravar; valida em `channel-gateway/recording_config.py`
  { key: 'recording_notice',     text: true, max: 1000 },
]

function valid(p: ParamDef, v: unknown): boolean {
  if (p.bool) return typeof v === 'boolean'
  if (p.text) return typeof v === 'string' && v.trim().length > 0 && v.length <= (p.max ?? Infinity)
  return typeof v === 'number' && Number.isFinite(v) && v >= (p.min ?? -Infinity) && v <= (p.max ?? Infinity)
}

const WebRTCSpeechConfigPage: React.FC = () => {
  const { t } = useTranslation('channels')
  const { tenantId, session } = useAuth()
  const token = session?.accessToken ?? ''
  const ns   = useNamespace(tenantId, NS)
  const prov = useProvenance(tenantId, NS)

  const [draft,  setDraft]  = useState<Record<string, unknown>>({})
  const [busy,   setBusy]   = useState<string | null>(null)
  const [erro,   setErro]   = useState<string | null>(null)
  const [aviso,  setAviso]  = useState<string | null>(null)

  useEffect(() => {
    if (ns.loading) return
    setDraft(Object.fromEntries(PARAMS.map(p => [p.key, ns.entries[p.key]?.value])))
  }, [ns.loading, ns.entries])

  const reload = () => { ns.reload(); prov.reload() }

  async function salvar(p: ParamDef) {
    const v = draft[p.key]
    if (!valid(p, v)) { setErro(t('webrtcSpeech.invalid', { key: p.key, min: p.min, max: p.max })); return }
    setBusy(p.key); setErro(null); setAviso(null)
    try {
      await putConfig(NS, p.key, v, tenantId, '', token)
      setAviso(t('webrtcSpeech.saved'))
      reload()
    } catch (e) { setErro(String(e)) } finally { setBusy(null) }
  }

  async function voltarAoPadrao(p: ParamDef) {
    setBusy(p.key); setErro(null); setAviso(null)
    try {
      await deleteConfig(NS, p.key, tenantId, '', token)
      setAviso(t('webrtcSpeech.restored'))
      reload()
    } catch (e) { setErro(String(e)) } finally { setBusy(null) }
  }

  const inputCls = 'w-32 text-xs font-mono px-2 py-1.5 border border-border-strong rounded focus:outline-none focus:border-primary'

  return (
    <div className="max-w-3xl space-y-4">
      <div className="flex items-center gap-2 justify-end">
        {(ns.loading || prov.loading) && <Spinner />}
        {ns.error   && <span className="text-xs text-red-text">⚠ {ns.error}</span>}
        {prov.error && <span className="text-xs text-red-text">⚠ {t('webrtcSpeech.provenanceError', { error: prov.error })}</span>}
        <button onClick={reload} className="text-xs text-secondary hover:text-primary" aria-label={t('webrtcSpeech.reload')}>↻</button>
      </div>

      <section className="bg-white border border-border rounded-lg p-5">
        <h3 className="text-sm font-semibold text-dark mb-1">{t('webrtcSpeech.title')}</h3>
        <p className="text-xs text-muted mb-4">{t('webrtcSpeech.intro')}</p>

        <div className="divide-y divide-border">
          {PARAMS.map(p => {
            const kp    = prov.keys[p.key]
            const doTen = kp?.tenant_present === true
            const ausente = !ns.loading && !(p.key in ns.entries)
            return (
              <div key={p.key} className="py-3 flex items-start gap-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-medium text-dark">{t(`webrtcSpeech.fields.${p.key}.label`)}</span>
                    <code className="text-2xs font-mono text-muted-light">{p.key}</code>
                    {kp && (
                      <span className={`text-2xs px-1.5 py-0.5 rounded-full ${doTen ? 'bg-warning-light text-warning-text' : 'bg-surface-alt text-muted'}`}>
                        {doTen ? t('webrtcSpeech.scope.tenant') : t('webrtcSpeech.scope.global')}
                      </span>
                    )}
                    {ausente && <span className="text-2xs text-red-text">{t('webrtcSpeech.missing')}</span>}
                  </div>
                  <p className="text-2xs text-muted mt-0.5">{t(`webrtcSpeech.fields.${p.key}.help`)}</p>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  {p.text ? (
                    <textarea
                      value={typeof draft[p.key] === 'string' ? (draft[p.key] as string) : ''}
                      onChange={e => setDraft(d => ({ ...d, [p.key]: e.target.value }))}
                      rows={3} maxLength={p.max}
                      className="w-72 text-xs px-2 py-1.5 border border-border-strong rounded focus:outline-none focus:border-primary"
                      aria-label={t(`webrtcSpeech.fields.${p.key}.label`)}
                    />
                  ) : p.bool ? (
                    <input
                      type="checkbox"
                      checked={draft[p.key] === true}
                      onChange={e => setDraft(d => ({ ...d, [p.key]: e.target.checked }))}
                      aria-label={t(`webrtcSpeech.fields.${p.key}.label`)}
                    />
                  ) : (
                    <input
                      type="number" min={p.min} max={p.max} step={p.step}
                      value={typeof draft[p.key] === 'number' ? (draft[p.key] as number) : ''}
                      onChange={e => setDraft(d => ({ ...d, [p.key]: e.target.value === '' ? undefined : Number(e.target.value) }))}
                      className={inputCls}
                      aria-label={t(`webrtcSpeech.fields.${p.key}.label`)}
                    />
                  )}
                  <button
                    onClick={() => salvar(p)}
                    disabled={busy !== null || !token}
                    className="px-3 py-1.5 rounded text-xs font-semibold bg-primary text-white disabled:opacity-40 hover:bg-primary-dark transition-colors"
                  >
                    {busy === p.key ? t('webrtcSpeech.saving') : t('webrtcSpeech.save')}
                  </button>
                  {doTen && (
                    <button
                      onClick={() => voltarAoPadrao(p)}
                      disabled={busy !== null || !token}
                      className="px-3 py-1.5 rounded text-xs border border-border-strong text-muted hover:text-dark disabled:opacity-40 transition-colors"
                    >
                      {t('webrtcSpeech.restore')}
                    </button>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </section>

      {erro  && <p className="text-xs text-red-text">{erro}</p>}
      {aviso && <p className="text-xs text-green-text">{aviso}</p>}

      <section className="bg-info-light border border-info/30 rounded-lg p-4 text-xs text-info-text space-y-1">
        <p>{t('webrtcSpeech.noteNextCall')}</p>
        <p>{t('webrtcSpeech.noteMenu')}</p>
        <p>{t('webrtcSpeech.noteVad')}</p>
      </section>
    </div>
  )
}

export default WebRTCSpeechConfigPage
