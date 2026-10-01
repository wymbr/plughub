/**
 * AttachmentView — o anexo do cliente, pela porta INTERNA (ATT-02).
 *
 * Até a ATT-02 o Console e o transcript abriam a `url` gravada na mensagem: absoluta para a porta
 * pública do gateway, num `<img src>` sem credencial — quem tivesse o id via o arquivo, e ninguém
 * ficava sabendo. Agora o caminho é montado pelo `file_id` e buscado por `apiFetch` (Bearer) na
 * analytics-api, que confere `contacts.transcricao`, o pool da sessão e grava cada acesso em
 * `audit_access_log`. A `url` gravada deixou de ser lida: ela envelhece (ATT-03 a assina) e não
 * carrega credencial.
 *
 * Imagem: miniatura carregada ao montar; clicar abre a imagem numa aba. Outros tipos (PDF, vídeo,
 * nota de voz): botão que BAIXA — a mesma regra da porta (ATT-01), só imagem é exibida.
 */
import React, { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { apiFetch } from '@/api/apiFetch'

export interface AttachmentRef {
  media_type?: string
  file_id?:    string
  mime_type?:  string
}

interface Props {
  attachment: AttachmentRef
  /** namespace i18n do consumidor e o prefixo das chaves `attachment.*` dentro dele */
  ns:         'agentAssist' | 'contacts'
  keyPrefix:  'attachment' | 'transcript.attachment'
  className?: string
}

const EXT: Record<string, string> = {
  'application/pdf': 'pdf', 'video/mp4': 'mp4', 'video/webm': 'webm', 'audio/ogg': 'ogg',
  'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp', 'image/gif': 'gif',
}

function attachmentPath(fileId: string): string {
  return `/analytics/v1/attachments/${encodeURIComponent(fileId)}`
}

export const AttachmentView: React.FC<Props> = ({ attachment, ns, keyPrefix, className }) => {
  const { t } = useTranslation(ns, { keyPrefix })
  const kind = t(`kind.${attachment.media_type ?? ''}`, { defaultValue: attachment.media_type ?? '' })
  const fileId = attachment.file_id
  const isImage = attachment.media_type === 'image'
  const [thumb, setThumb] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy]   = useState(false)
  const urls = useRef<string[]>([])

  useEffect(() => () => { urls.current.forEach(u => URL.revokeObjectURL(u)); urls.current = [] }, [fileId])

  async function fetchBlob(): Promise<Blob | null> {
    if (!fileId) return null
    setBusy(true); setError(null)
    try {
      const r = await apiFetch(attachmentPath(fileId))
      if (!r.ok) {
        setError(r.status === 403 ? t('denied')
               : r.status === 410 ? t('expired')
               : r.status === 404 ? t('notFound')
               : t('loadError', { status: r.status }))
        return null
      }
      return await r.blob()
    } catch {
      setError(t('loadError', { status: '—' }))
      return null
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    if (!isImage || !fileId) return
    let cancelled = false
    fetchBlob().then(b => {
      if (cancelled || !b) return
      const u = URL.createObjectURL(b)
      urls.current.push(u)
      setThumb(u)
    })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fileId, isImage])

  async function download() {
    const b = await fetchBlob()
    if (!b || !fileId) return
    const u = URL.createObjectURL(b)
    const a = document.createElement('a')
    a.href = u
    a.download = `${attachment.media_type ?? 'attachment'}-${fileId.slice(0, 8)}.${EXT[attachment.mime_type ?? ''] ?? 'bin'}`
    a.click()
    URL.revokeObjectURL(u)
  }

  if (!fileId) {
    return <div className={className}><span className="text-xs italic opacity-80">📎 {t('noLink', { kind })}</span></div>
  }
  return (
    <div className={`mt-1 flex flex-col gap-1 ${className ?? ''}`}>
      {isImage && thumb && (
        <a href={thumb} target="_blank" rel="noopener noreferrer">
          <img src={thumb} alt={kind} className="max-h-40 max-w-full rounded-md object-contain" />
        </a>
      )}
      {!isImage && (
        <button type="button" onClick={download} disabled={busy} className="text-left text-xs underline">
          📎 {t('download', { kind })}
        </button>
      )}
      {busy && isImage && !thumb && <span className="text-xs italic opacity-80">{t('loading')}</span>}
      {error && <span className="text-xs italic opacity-80">📎 {error}</span>}
    </div>
  )
}
