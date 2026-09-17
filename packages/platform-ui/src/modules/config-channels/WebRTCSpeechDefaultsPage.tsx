/**
 * WebRTCSpeechDefaultsPage.tsx
 * Configuration → Channels → WebRTC → Settings — model, language and voice WITHOUT a profile (VOZ-17).
 *
 * These four used to live only in the gateway's env (`PLUGHUB_WEBRTC_STT_MODEL` and siblings), against
 * the house rule that env is for secrets and topology and every config field has a screen. They now
 * live in the same `webrtc` namespace, under the same key names a profile uses — so the layers read
 * alike: profile → tenant (here) → gateway env.
 *
 * Two things this screen must never do, both of which the old text inputs did: offer a model the
 * service does not have (the call is where that showed up, one 404 per utterance), and show a value
 * without saying WHICH layer it came from — "reset" is meaningless if you cannot see whether the
 * tenant chose anything at all.
 */
import React, { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'
import { fetchSpeechDefaults, fetchSpeechModels, putSpeechDefaults } from './api/speech'
import type { SpeechDefaults, SpeechModels } from './api/speech'

export const VOICE_KEYS = ['stt_model', 'stt_language', 'tts_model', 'tts_voice'] as const

/** The voices the effective TTS model offers — the only list that can be chosen from. */
export function voicesOf(models: SpeechModels | null, modelId: string): string[] {
  return models?.tts.find(m => m.id === modelId)?.voices.map(v => v.name) ?? []
}

export function languagesOf(models: SpeechModels | null, modelId: string): string[] {
  const l = models?.stt.find(m => m.id === modelId)?.languages ?? []
  return l.includes('multilingual') ? [] : [...l].sort()
}

const WebRTCSpeechDefaultsPage: React.FC = () => {
  const { t } = useTranslation('channels')
  const { tenantId } = useAuth()

  const [models, setModels] = useState<SpeechModels | null>(null)
  const [atual,  setAtual]  = useState<SpeechDefaults | null>(null)
  const [draft,  setDraft]  = useState<Record<string, string>>({})
  const [busy,   setBusy]   = useState(false)
  const [erro,   setErro]   = useState<string | null>(null)
  const [aviso,  setAviso]  = useState<string | null>(null)
  const [catErro, setCatErro] = useState<string | null>(null)

  const carregar = useCallback(async () => {
    if (!tenantId) return
    setErro(null)
    try {
      const d = await fetchSpeechDefaults(tenantId)
      setAtual(d)
      setDraft(Object.fromEntries(VOICE_KEYS.map(k => [k, d.tenant[k] ?? ''])))
    } catch (e) { setErro(String(e)) }
    try { setModels(await fetchSpeechModels(tenantId)); setCatErro(null) } catch (e) { setCatErro(String(e)) }
  }, [tenantId])
  useEffect(() => { carregar() }, [carregar])

  async function salvar() {
    if (!atual) return
    const mudou = Object.fromEntries(VOICE_KEYS.filter(k => (draft[k] ?? '') !== (atual.tenant[k] ?? ''))
                                               .map(k => [k, draft[k] ?? '']))
    if (!Object.keys(mudou).length) { setAviso(t('speechDefaults.noChange')); return }
    setBusy(true); setErro(null); setAviso(null)
    try {
      await putSpeechDefaults(tenantId, mudou)
      setAviso(t('speechDefaults.saved'))
      await carregar()
    } catch (e) { setErro(String(e)) } finally { setBusy(false) }
  }

  const inp = 'w-full text-xs font-mono px-2 py-1.5 border border-border-strong rounded focus:outline-none focus:border-primary disabled:bg-surface-alt'
  const efetivoTts = draft.tts_model || atual?.env.tts_model || ''
  const efetivoStt = draft.stt_model || atual?.env.stt_model || ''
  const vozes = voicesOf(models, efetivoTts)
  const linguas = languagesOf(models, efetivoStt)

  const opcoes = (k: string): { valor: string; rotulo: string }[] => {
    if (k === 'stt_model') return (models?.stt ?? []).map(m => ({ valor: m.id, rotulo: m.id }))
    if (k === 'tts_model') return (models?.tts ?? []).map(m => ({ valor: m.id, rotulo: m.id }))
    if (k === 'tts_voice') return vozes.map(v => ({ valor: v, rotulo: v }))
    return linguas.map(l => ({ valor: l, rotulo: l }))
  }

  return (
    <section className="max-w-3xl bg-white border border-border rounded-lg p-5 space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-sm font-semibold text-dark mb-1">{t('speechDefaults.title')}</h3>
          <p className="text-xs text-muted">{t('speechDefaults.intro')}</p>
        </div>
        {(!atual || busy) && <Spinner />}
      </div>

      {catErro && (
        <p className="text-xs text-warning-text">
          ⚠ {t('speechDefaults.catalogDown')} <span className="font-mono text-2xs">{catErro}</span>
        </p>
      )}

      <div className="grid grid-cols-2 gap-3">
        {VOICE_KEYS.map(k => {
          // três rótulos, não dois: o config-api distingue o default da PLATAFORMA (escopo
          // `global`) do que este tenant escolheu, e chamar os dois de "do tenant" mentiria
          // sobre quem pode mudá-lo.
          const proc = atual?.provenance[k] ?? 'env'
          const daCasa = proc === 'env'
          const lista = opcoes(k)
          return (
            <div key={k}>
              <label className="text-xs font-medium text-dark block mb-1">
                {t(`speechProfiles.fields.${k}`)} <code className="text-2xs text-muted-light">{k}</code>
              </label>
              <select
                className={inp}
                disabled={!models || busy}
                value={draft[k] ?? ''}
                onChange={e => setDraft(d => ({ ...d, [k]: e.target.value }))}
              >
                <option value="">
                  {t('speechDefaults.useEnv', { value: atual?.env[k] || '—' })}
                </option>
                {lista.map(o => <option key={o.valor} value={o.valor}>{o.rotulo}</option>)}
                {/* valor gravado que o serviço não oferece mais: continua visível, e dito */}
                {draft[k] && !lista.some(o => o.valor === draft[k]) && (
                  <option value={draft[k]}>{draft[k]} — {t('speechDefaults.notInstalled')}</option>
                )}
              </select>
              <p className="text-2xs text-muted mt-0.5">
                {t('speechDefaults.inForce', { value: atual?.effective[k] || '—' })}
                {' · '}
                <span className={daCasa ? 'text-muted-light' : 'text-secondary'}>
                  {daCasa ? t('speechDefaults.fromEnv')
                    : proc === 'global' ? t('speechDefaults.fromPlatform')
                    : t('speechDefaults.fromTenant')}
                </span>
              </p>
            </div>
          )
        })}
      </div>

      <p className="text-2xs text-muted">{t('speechDefaults.note')}</p>

      <div className="flex gap-2">
        <button onClick={salvar} disabled={busy || !atual || !models}
                className="px-3 py-1.5 rounded text-xs font-semibold bg-primary text-white disabled:opacity-40 hover:bg-primary-dark transition-colors">
          {busy ? t('webrtcSpeech.saving') : t('actions.save')}
        </button>
        <button onClick={carregar} disabled={busy}
                className="px-3 py-1.5 rounded text-xs border border-border-strong text-muted hover:text-dark transition-colors">
          {t('actions.reload')}
        </button>
      </div>

      {erro  && <p className="text-xs text-red-text">{erro}</p>}
      {aviso && <p className="text-xs text-green-text">{aviso}</p>}
    </section>
  )
}

export default WebRTCSpeechDefaultsPage
