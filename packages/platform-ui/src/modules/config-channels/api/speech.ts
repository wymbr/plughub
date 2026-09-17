/**
 * speech.ts — the ONE door the screen uses for speech config (VOZ-17).
 *
 * Profiles and the tenant default used to be written straight to config-api, where nothing checks
 * that the named model exists: the call was the first thing to find out, one 404 per utterance.
 * These routes live on the channel-gateway, which asks the speech service what it actually has
 * INSTALLED before writing, and then writes to config-api carrying the caller's own Bearer.
 *
 * `apiFetch` is mandatory here: it attaches the token and renews once on 401 — a raw `fetch` loses
 * both (`probe_ui_credential_coverage.sh`).
 */
import { apiFetch } from '@/api/apiFetch'

export interface SpeechVoice { name: string; language?: string }
export interface SpeechModels {
  stt: { id: string; languages: string[] }[]
  tts: { id: string; voices: SpeechVoice[] }[]
}
export interface SpeechDefaults {
  env:        Record<string, string>
  tenant:     Record<string, string>
  effective:  Record<string, string>
  provenance: Record<string, string>
}

/** The refusal as the screen should show it: the gateway names every reason, and they all matter. */
async function orThrow(res: Response): Promise<unknown> {
  if (res.ok) return res.status === 204 ? null : res.json().catch(() => null)
  const body = await res.json().catch(() => null) as { detail?: unknown } | null
  const d = body?.detail as { reasons?: string[]; detail?: unknown; what?: string } | string | undefined
  if (d && typeof d === 'object' && Array.isArray(d.reasons)) throw new Error(d.reasons.join(' · '))
  if (d && typeof d === 'object' && d.detail) throw new Error(`${d.what ?? ''} ${String(d.detail)}`.trim())
  throw new Error(typeof d === 'string' ? d : `HTTP ${res.status}`)
}

function headers(tenantId: string | null): Record<string, string> {
  return { 'Content-Type': 'application/json', ...(tenantId ? { 'x-tenant-id': tenantId } : {}) }
}

export async function fetchSpeechModels(tenantId: string | null): Promise<SpeechModels> {
  return await orThrow(await apiFetch('/v1/speech-models', { headers: headers(tenantId) })) as SpeechModels
}

export async function fetchSpeechDefaults(tenantId: string | null): Promise<SpeechDefaults> {
  return await orThrow(await apiFetch('/v1/speech-defaults', { headers: headers(tenantId) })) as SpeechDefaults
}

/** `''` in a field REMOVES the tenant override (back to the gateway env); an absent key is untouched. */
export async function putSpeechDefaults(tenantId: string | null, campos: Record<string, string>): Promise<void> {
  await orThrow(await apiFetch('/v1/speech-defaults', {
    method: 'PUT', headers: headers(tenantId), body: JSON.stringify(campos),
  }))
}

export async function putSpeechProfile(
  tenantId: string | null, id: string, value: Record<string, unknown>,
): Promise<void> {
  await orThrow(await apiFetch(`/v1/speech-profiles/${encodeURIComponent(id)}`, {
    method: 'PUT', headers: headers(tenantId), body: JSON.stringify(value),
  }))
}

export async function deleteSpeechProfile(tenantId: string | null, id: string): Promise<void> {
  await orThrow(await apiFetch(`/v1/speech-profiles/${encodeURIComponent(id)}`, {
    method: 'DELETE', headers: headers(tenantId),
  }))
}
