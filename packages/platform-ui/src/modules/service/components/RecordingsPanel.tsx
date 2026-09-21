import { useEffect, useRef, useState } from 'react'
import { Download, Mic, Play } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { apiFetch } from '@/api/apiFetch'

// ─── Gravação da chamada (VOZ-36) ─────────────────────────────────────────────
//
// O gateway decide TUDO: capacidade (`contacts.recording` — read_only ouve, read_write exporta),
// o pool que atendeu cada parte e a trilha LGPD. A tela só mostra o que ele devolve:
//   - 401/403 na lista  → a seção não aparece (quem não pode ouvir não vê nem que há gravação)
//   - `omitted` > 0     → diz quantas partes existem fora do escopo, sem mostrá-las
//   - `can_export`      → o botão de exportar só aparece quando o servidor já disse que pode
//
// ⚠️ O áudio NÃO é buscado ao abrir a transcrição: cada busca é uma ESCUTA na trilha de
// auditoria, então só acontece quando a pessoa clica em ouvir.

interface RecordingPart {
  file_id:     string
  part:        number | null
  pools:       string[]
  duration_ms: number | null
  started_at:  string | null
  ended_at:    string | null
  size_bytes:  number
  expires_at:  string | null
  can_export:  boolean
}

interface RecordingList {
  session_id: string
  parts:      RecordingPart[]
  omitted:    number
}

function fmtDuration(ms: number | null): string {
  if (ms == null) return '—'
  const s = Math.round(ms / 1000)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

function fmtTime(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleTimeString()
}

export function RecordingsPanel({ sessionId }: { sessionId: string }) {
  const { t } = useTranslation('contacts')
  const [list, setList]       = useState<RecordingList | null>(null)
  const [failed, setFailed]   = useState(false)
  const [audio, setAudio]     = useState<Record<string, string>>({})
  const [busy, setBusy]       = useState<string | null>(null)
  const [error, setError]     = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setList(null); setFailed(false); setError(null)
    apiFetch(`/v1/recordings/sessions/${encodeURIComponent(sessionId)}`)
      .then(async r => {
        if (cancelled) return
        if (r.status === 401 || r.status === 403) { setList(null); return }   // sem o campo: nada
        if (!r.ok) { setFailed(true); return }
        setList(await r.json() as RecordingList)
      })
      .catch(() => { if (!cancelled) setFailed(true) })
    return () => { cancelled = true }
  }, [sessionId])

  // libera os object URLs ao trocar de sessão / desmontar — por ref: um cleanup preso ao estado
  // `audio` revogaria, a cada escuta nova, a URL da parte que ainda está tocando
  const urls = useRef<string[]>([])
  useEffect(() => () => {
    urls.current.forEach(u => URL.revokeObjectURL(u))
    urls.current = []
    setAudio({})
  }, [sessionId])

  async function fetchBlob(part: RecordingPart, kind: 'audio' | 'export'): Promise<Blob | null> {
    setBusy(`${kind}:${part.file_id}`); setError(null)
    try {
      const r = await apiFetch(`/v1/recordings/${encodeURIComponent(part.file_id)}/${kind}`)
      if (!r.ok) {
        setError(r.status === 403 ? t('recording.denied')
               : r.status === 410 ? t('recording.expired')
               : t('recording.loadError', { status: r.status }))
        return null
      }
      return await r.blob()
    } catch {
      setError(t('recording.loadError', { status: '—' }))
      return null
    } finally {
      setBusy(null)
    }
  }

  async function listen(part: RecordingPart) {
    const blob = await fetchBlob(part, 'audio')
    if (!blob) return
    const url = URL.createObjectURL(blob)
    urls.current.push(url)
    setAudio(prev => ({ ...prev, [part.file_id]: url }))
  }

  async function exportPart(part: RecordingPart) {
    const blob = await fetchBlob(part, 'export')
    if (!blob) return
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${sessionId}-part${part.part ?? ''}.ogg`
    a.click()
    URL.revokeObjectURL(url)
  }

  if (failed) {
    return <div className="px-4 py-2 text-xs text-warning">{t('recording.listError')}</div>
  }
  if (!list || (list.parts.length === 0 && list.omitted === 0)) return null

  return (
    <div className="px-4 py-2 border-b border-slate-700 flex flex-col gap-2">
      <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-400 flex items-center gap-1">
        <Mic className="w-3 h-3" aria-hidden="true" /> {t('recording.title', { count: list.parts.length })}
      </span>
      <span className="text-[11px] text-slate-500">{t('recording.auditNotice')}</span>
      {list.parts.map(part => (
        <div key={part.file_id} className="flex flex-wrap items-center gap-3 text-xs text-slate-300">
          <span className="font-semibold">{t('recording.part', { n: part.part ?? '?' })}</span>
          <span>{fmtTime(part.started_at)} · {fmtDuration(part.duration_ms)}</span>
          <span className="text-slate-500">{part.pools.join(', ')}</span>
          {audio[part.file_id] ? (
            // Sem "Baixar" no player (VOZ-37): o menu nativo do <audio> oferecia a cópia a quem só
            // pode OUVIR, contornando o `read_write` do exportar. Não é trava — o áudio tocado no
            // navegador sempre pode ser capturado —, é a tela não OFERECER o que o grant não dá.
            <audio controls autoPlay src={audio[part.file_id]} className="h-8"
                   controlsList="nodownload noremoteplayback"
                   onContextMenu={e => e.preventDefault()} />
          ) : (
            <button
              className="flex items-center gap-1 rounded border border-slate-600 px-2 py-0.5 hover:bg-slate-700 disabled:opacity-50"
              disabled={busy !== null}
              onClick={() => listen(part)}
            >
              <Play className="w-3 h-3" aria-hidden="true" />
              {busy === `audio:${part.file_id}` ? t('recording.loading') : t('recording.listen')}
            </button>
          )}
          {part.can_export && (
            <button
              className="flex items-center gap-1 rounded border border-slate-600 px-2 py-0.5 hover:bg-slate-700 disabled:opacity-50"
              disabled={busy !== null}
              onClick={() => exportPart(part)}
            >
              <Download className="w-3 h-3" aria-hidden="true" />
              {busy === `export:${part.file_id}` ? t('recording.loading') : t('recording.export')}
            </button>
          )}
        </div>
      ))}
      {list.omitted > 0 && (
        <span className="text-[11px] text-slate-500">{t('recording.omitted', { count: list.omitted })}</span>
      )}
      {error && <span className="text-xs text-red">{error}</span>}
    </div>
  )
}
