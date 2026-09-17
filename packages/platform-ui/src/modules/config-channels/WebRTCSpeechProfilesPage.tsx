/**
 * WebRTCSpeechProfilesPage.tsx
 * Configuration → Channels → WebRTC → Settings — speech profiles per entry point (VOZ-25).
 *
 * A profile lives in config-api namespace `speech_profiles` (one key per profile, value = object) and
 * is pointed to by a WebRTC endpoint (`settings.speech_profile_id`). Each field is OPTIONAL: an empty
 * field inherits the tenant segmentation (namespace `webrtc`) or, for model/language/voice, the
 * gateway deployment default. Order: menu → profile → tenant → platform → code default.
 *
 * ⚠️ Ranges and name patterns repeat `channel-gateway/speech_config.py` (`PARAMS`, `VOICE_PARAMS`,
 * `PROFILE_ID_RE`), which is what VALIDATES at call time (config-api does not validate values). The
 * screen refuses earlier only so it never offers what the gateway would ignore.
 *
 * VOZ-17: model, language and voice are no longer free text, and the write no longer goes straight
 * to config-api. Both changes are the same fact — a name that matches the pattern says nothing about
 * the model EXISTING, and the call was what found out (404 per utterance, speech lost). The screen
 * now offers what `GET /v1/speech-models` reports as installed, and saves through the gateway route
 * that re-checks it. With the speech service unreachable the catalogue is empty and saving is
 * disabled, saying so: falling back to free text would be offering exactly what nobody can verify.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import { useNamespace } from '../config-plataforma/api/config-hooks'
import { deleteSpeechProfile, fetchSpeechDefaults, fetchSpeechModels, putSpeechProfile } from './api/speech'
import type { SpeechDefaults, SpeechModels } from './api/speech'
import { languagesOf, voicesOf } from './WebRTCSpeechDefaultsPage'
import { listChannelEndpoints } from '@/api/registry'
import type { ChannelEndpoint } from '@/types'
import Spinner from '@/components/ui/Spinner'

export const SPEECH_PROFILES_NS = 'speech_profiles'
export const PROFILE_ID_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/

const MODEL_RE = /^[A-Za-z0-9._/:-]{1,200}$/
const TEXT_FIELDS: { key: string; re: RegExp }[] = [
  { key: 'stt_model',    re: MODEL_RE },
  { key: 'stt_language', re: /^[a-z]{2,3}(-[A-Z]{2})?$/ },
  { key: 'tts_model',    re: MODEL_RE },
  { key: 'tts_voice',    re: /^[A-Za-z0-9._-]{1,100}$/ },
]
const NUM_FIELDS: { key: string; min: number; max: number; step: number }[] = [
  { key: 'stt_energy_threshold', min: 50,   max: 5000,  step: 50 },
  { key: 'stt_end_silence_ms',   min: 100,  max: 5000,  step: 50 },
  { key: 'stt_gap_ms',           min: 100,  max: 5000,  step: 50 },
  { key: 'stt_min_speech_ms',    min: 50,   max: 2000,  step: 50 },
  { key: 'stt_max_speech_ms',    min: 1000, max: 60000, step: 500 },
]

type Draft = Record<string, string>   // every field as typed; '' = inherit

function toDraft(v: unknown): Draft {
  const o = (v && typeof v === 'object' ? v : {}) as Record<string, unknown>
  const d: Draft = { description: typeof o.description === 'string' ? o.description : '' }
  for (const f of TEXT_FIELDS) d[f.key] = typeof o[f.key] === 'string' ? (o[f.key] as string) : ''
  for (const f of NUM_FIELDS) d[f.key] = typeof o[f.key] === 'number' ? String(o[f.key]) : ''
  d.stt_vad_filter = typeof o.stt_vad_filter === 'boolean' ? String(o.stt_vad_filter) : ''
  return d
}

/** The object to store, or the key of the first invalid field. Empty fields are left out (inherit). */
function fromDraft(d: Draft): { value: Record<string, unknown> } | { invalid: string } {
  const out: Record<string, unknown> = {}
  if (d.description.trim()) out.description = d.description.trim()
  for (const f of TEXT_FIELDS) {
    const v = d[f.key].trim()
    if (!v) continue
    if (!f.re.test(v)) return { invalid: f.key }
    out[f.key] = v
  }
  for (const f of NUM_FIELDS) {
    const v = d[f.key].trim()
    if (!v) continue
    const n = Number(v)
    if (!Number.isFinite(n) || n < f.min || n > f.max) return { invalid: f.key }
    out[f.key] = n
  }
  if (d.stt_vad_filter) out.stt_vad_filter = d.stt_vad_filter === 'true'
  return { value: out }
}

/** O que a tela pode OFERECER em cada campo: o catálogo do serviço, recortado pelo modelo em vigor
 *  naquele momento (o do rascunho, ou o herdado do default do tenant). */
function opcoesDe(key: string, models: SpeechModels | null, sttEmVigor: string, ttsEmVigor: string): string[] {
  if (key === 'stt_model') return (models?.stt ?? []).map(m => m.id)
  if (key === 'tts_model') return (models?.tts ?? []).map(m => m.id)
  if (key === 'tts_voice') return voicesOf(models, ttsEmVigor)
  return languagesOf(models, sttEmVigor)
}

const WebRTCSpeechProfilesPage: React.FC = () => {
  const { t } = useTranslation('channels')
  const { tenantId, session } = useAuth()
  const token = session?.accessToken ?? ''
  const ns = useNamespace(tenantId, SPEECH_PROFILES_NS)

  const [models,  setModels]  = useState<SpeechModels | null>(null)
  const [padrao,  setPadrao]  = useState<SpeechDefaults | null>(null)
  const [catErro, setCatErro] = useState<string | null>(null)

  const [endpoints, setEndpoints] = useState<ChannelEndpoint[]>([])
  const [editing,   setEditing]   = useState<string | null>(null)   // profile id, or '' for new
  const [newId,     setNewId]     = useState('')
  const [draft,     setDraft]     = useState<Draft>(toDraft({}))
  const [busy,      setBusy]      = useState(false)
  const [erro,      setErro]      = useState<string | null>(null)
  const [aviso,     setAviso]     = useState<string | null>(null)

  const loadEndpoints = useCallback(async () => {
    if (!tenantId) return
    try { setEndpoints(await listChannelEndpoints(tenantId, 'webrtc')) } catch (e) { setErro(String(e)) }
  }, [tenantId])
  useEffect(() => { loadEndpoints() }, [loadEndpoints])

  // O catálogo é o que a tela pode OFERECER; o default é o que um campo em branco herda — os dois
  // vêm do gateway, que é quem sabe o que o serviço tem instalado (VOZ-17).
  useEffect(() => {
    if (!tenantId) return
    let vivo = true
    ;(async () => {
      try {
        const [m, d] = await Promise.all([fetchSpeechModels(tenantId), fetchSpeechDefaults(tenantId)])
        if (vivo) { setModels(m); setPadrao(d); setCatErro(null) }
      } catch (e) { if (vivo) setCatErro(String(e)) }
    })()
    return () => { vivo = false }
  }, [tenantId])

  const profiles = useMemo(() => Object.entries(ns.entries).sort(([a], [b]) => a.localeCompare(b)), [ns.entries])
  const usedBy = (id: string) => endpoints.filter(ep => ep.settings?.speech_profile_id === id)

  function openEdit(id: string, value: unknown) {
    setEditing(id); setDraft(toDraft(value)); setErro(null); setAviso(null)
  }
  function openNew() {
    setEditing(''); setNewId(''); setDraft(toDraft({})); setErro(null); setAviso(null)
  }

  async function save() {
    const id = editing === '' ? newId.trim() : editing
    if (!id || !PROFILE_ID_RE.test(id)) { setErro(t('speechProfiles.invalidId')); return }
    if (editing === '' && id in ns.entries) { setErro(t('speechProfiles.exists', { id })); return }
    const r = fromDraft(draft)
    if ('invalid' in r) { setErro(t('speechProfiles.invalidField', { key: r.invalid })); return }
    setBusy(true); setErro(null); setAviso(null)
    try {
      await putSpeechProfile(tenantId, id, r.value)
      setAviso(t('speechProfiles.saved', { id }))
      setEditing(null)
      ns.reload()
    } catch (e) { setErro(String(e)) } finally { setBusy(false) }
  }

  async function remove(id: string) {
    const n = usedBy(id).length
    if (!confirm(n ? t('speechProfiles.deleteConfirmUsed', { id, count: n }) : t('speechProfiles.deleteConfirm', { id }))) return
    setBusy(true); setErro(null); setAviso(null)
    try {
      await deleteSpeechProfile(tenantId, id)
      setAviso(t('speechProfiles.deleted', { id }))
      ns.reload()
    } catch (e) { setErro(String(e)) } finally { setBusy(false) }
  }

  const inp = 'w-full text-xs font-mono px-2 py-1.5 border border-border-strong rounded focus:outline-none focus:border-primary'

  return (
    <section className="max-w-3xl bg-white border border-border rounded-lg p-5 space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-sm font-semibold text-dark mb-1">{t('speechProfiles.title')}</h3>
          <p className="text-xs text-muted">{t('speechProfiles.intro')}</p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {ns.loading && <Spinner />}
          <button
            onClick={openNew}
            disabled={editing !== null || !token}
            className="px-3 py-1.5 rounded text-xs font-semibold bg-primary text-white disabled:opacity-40 hover:bg-primary-dark transition-colors"
          >
            {t('speechProfiles.add')}
          </button>
        </div>
      </div>
      {ns.error && <p className="text-xs text-red-text">⚠ {ns.error}</p>}
      {catErro && (
        <p className="text-xs text-warning-text">
          ⚠ {t('speechProfiles.catalogDown')} <span className="font-mono text-2xs">{catErro}</span>
        </p>
      )}

      {editing !== null && (
        <div className="bg-surface-muted border border-border rounded-lg p-4 space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-medium text-dark block mb-1">{t('speechProfiles.id')}</label>
              <input
                className={inp + (editing !== '' ? ' bg-surface-alt text-muted' : '')}
                value={editing === '' ? newId : editing}
                readOnly={editing !== ''}
                placeholder="sip-g711"
                onChange={e => setNewId(e.target.value)}
              />
              <p className="text-2xs text-muted mt-0.5">{t('speechProfiles.idHint')}</p>
            </div>
            <div>
              <label className="text-xs font-medium text-dark block mb-1">{t('speechProfiles.description')}</label>
              <input className={inp} value={draft.description}
                     onChange={e => setDraft(d => ({ ...d, description: e.target.value }))} />
            </div>
            {TEXT_FIELDS.map(f => {
              const herdado = padrao?.effective[f.key] ?? ''
              const lista = opcoesDe(f.key, models, draft.stt_model || padrao?.effective.stt_model || '',
                                     draft.tts_model || padrao?.effective.tts_model || '')
              return (
                <div key={f.key}>
                  <label className="text-xs font-medium text-dark block mb-1">
                    {t(`speechProfiles.fields.${f.key}`)} <code className="text-2xs text-muted-light">{f.key}</code>
                  </label>
                  <select className={inp} disabled={!models}
                          value={draft[f.key]}
                          onChange={e => setDraft(d => ({ ...d, [f.key]: e.target.value }))}>
                    <option value="">{t('speechProfiles.inheritValue', { value: herdado || '—' })}</option>
                    {lista.map(o => <option key={o} value={o}>{o}</option>)}
                    {/* valor já gravado que o serviço não oferece mais: visível, e dito */}
                    {draft[f.key] && !lista.includes(draft[f.key]) && (
                      <option value={draft[f.key]}>{draft[f.key]} — {t('speechDefaults.notInstalled')}</option>
                    )}
                  </select>
                </div>
              )
            })}
            {NUM_FIELDS.map(f => (
              <div key={f.key}>
                <label className="text-xs font-medium text-dark block mb-1">
                  {t(`webrtcSpeech.fields.${f.key}.label`)} <code className="text-2xs text-muted-light">{f.min}–{f.max}</code>
                </label>
                <input type="number" min={f.min} max={f.max} step={f.step} className={inp}
                       value={draft[f.key]} placeholder={t('speechProfiles.inherit')}
                       onChange={e => setDraft(d => ({ ...d, [f.key]: e.target.value }))} />
              </div>
            ))}
            <div>
              <label className="text-xs font-medium text-dark block mb-1">{t('webrtcSpeech.fields.stt_vad_filter.label')}</label>
              <select className={inp} value={draft.stt_vad_filter}
                      onChange={e => setDraft(d => ({ ...d, stt_vad_filter: e.target.value }))}>
                <option value="">{t('speechProfiles.inherit')}</option>
                <option value="true">{t('speechProfiles.on')}</option>
                <option value="false">{t('speechProfiles.off')}</option>
              </select>
            </div>
          </div>
          <p className="text-2xs text-muted">{t('speechProfiles.inheritNote')}</p>
          <div className="flex gap-2">
            <button onClick={save} disabled={busy || !token || !models}
                    className="px-3 py-1.5 rounded text-xs font-semibold bg-primary text-white disabled:opacity-40 hover:bg-primary-dark transition-colors">
              {busy ? t('webrtcSpeech.saving') : t('actions.save')}
            </button>
            <button onClick={() => setEditing(null)}
                    className="px-3 py-1.5 rounded text-xs border border-border-strong text-muted hover:text-dark transition-colors">
              {t('actions.cancel')}
            </button>
          </div>
        </div>
      )}

      {profiles.length === 0 && !ns.loading ? (
        <p className="text-xs text-muted-light">{t('speechProfiles.empty')}</p>
      ) : (
        <div className="divide-y divide-border">
          {profiles.map(([id, entry]) => {
            const r = fromDraft(toDraft(entry.value))
            const campos = 'value' in r ? Object.keys(r.value).filter(k => k !== 'description') : []
            const eps = usedBy(id)
            return (
              <div key={id} className="py-3 flex items-start gap-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <code className="text-xs font-mono text-dark">{id}</code>
                    <span className="text-2xs text-muted">
                      {eps.length ? t('speechProfiles.usedBy', { count: eps.length }) : t('speechProfiles.unused')}
                    </span>
                  </div>
                  {typeof (entry.value as Record<string, unknown>)?.description === 'string' && (
                    <p className="text-2xs text-muted mt-0.5">{(entry.value as Record<string, string>).description}</p>
                  )}
                  <p className="text-2xs font-mono text-muted-light mt-0.5">
                    {campos.length ? campos.join(' · ') : t('speechProfiles.inheritsAll')}
                  </p>
                </div>
                <div className="flex gap-2 shrink-0">
                  <button onClick={() => openEdit(id, entry.value)} disabled={editing !== null || busy || !token}
                          className="text-xs text-secondary hover:text-primary disabled:opacity-40">
                    {t('actions.edit')}
                  </button>
                  <button onClick={() => remove(id)} disabled={editing !== null || busy || !token}
                          className="text-xs text-red hover:text-red-text disabled:opacity-40">
                    {t('actions.delete')}
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {erro  && <p className="text-xs text-red-text">{erro}</p>}
      {aviso && <p className="text-xs text-green-text">{aviso}</p>}
    </section>
  )
}

export default WebRTCSpeechProfilesPage
