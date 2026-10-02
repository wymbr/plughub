/**
 * attachmentName — o nome com que o Console SALVA um anexo (ATT-09, 2026-10-02).
 *
 * O balão mostra `[Anexo: nome] legenda`, e o nome ali já passou pela máscara (um CPF no nome
 * vira token `[cpf:tk_…:***00]`). O download, até aqui, saía como `document-a8383516.pdf`: o
 * `attachment` do stream não leva o nome de propósito (VOZ-28 — um nome com dado pessoal não
 * escapa da rede por um campo lateral), e o Console inventava um.
 *
 * O nome sai do MESMO texto que o balão desenha, com a MESMA regra de exibição do token
 * (`token_display` do catálogo de máscara). Nunca do `Content-Disposition` da porta interna: ele
 * traz o nome cru, e quem não atende o contato (supervisor, avaliador) só vê o mascarado.
 *
 * Puro e sem React, para o gate `probe_att09_download_name.sh` exercitá-lo direto.
 */
import type { MaskingRulesMap, TokenDisplayMode } from './MaskedToken'

const TOKEN = String.raw`\[[\w_]+:tk_[a-f0-9]+:[^\]]+\]`
/** `[Anexo: <nome>]` no começo do texto; o nome pode conter tokens (que têm `]` dentro). */
const INDICATOR_RE = new RegExp(String.raw`^\[Anexo: ((?:${TOKEN}|[^\]])+)\]`)
const TOKEN_RE = /\[([\w_]+):(tk_[a-f0-9]+):([^\]]+)\]/g

export const ATTACHMENT_EXT: Record<string, string> = {
  'application/pdf': 'pdf', 'video/mp4': 'mp4', 'video/webm': 'webm', 'audio/ogg': 'ogg',
  'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp', 'image/gif': 'gif',
}

/** O valor de um token NA TELA — a regra que o `MaskedToken` aplica ao chip. `null` = só o rótulo. */
export function tokenScreenValue(mode: TokenDisplayMode | undefined, display: string): string | null {
  if (mode === 'hidden')    return null
  if (mode === 'full_mask') return '•••••'
  return display
}

function tokensAsText(text: string, rules?: MaskingRulesMap): string {
  return text.replace(TOKEN_RE, (_m, category: string, _id: string, display: string) =>
    tokenScreenValue(rules?.[category]?.token_display, display) ?? category.toUpperCase())
}

export interface AttachmentNameRef {
  media_type?: string
  file_id?:    string
  mime_type?:  string
}

/** O nome genérico de antes — quando o texto não traz o indicador (entrada antiga, outro formato). */
export function fallbackAttachmentName(att: AttachmentNameRef): string {
  return `${att.media_type ?? 'attachment'}-${(att.file_id ?? '').slice(0, 8)}.${ATTACHMENT_EXT[att.mime_type ?? ''] ?? 'bin'}`
}

/**
 * Nome de download a partir do texto da mensagem. Mascarado como no balão (o `*` vira `•`), sem caractere que
 * o sistema de arquivos recuse, com a extensão do tipo REAL (o que o servidor conferiu) quando o
 * nome não a traz. Sem indicador legível, o nome genérico.
 */
export function attachmentDownloadName(text: string | undefined, att: AttachmentNameRef,
                                       rules?: MaskingRulesMap): string {
  const m = (text ?? '').match(INDICATOR_RE)
  if (!m) return fallbackAttachmentName(att)
  let nome = tokensAsText(m[1]!, rules)
    // `*` é proibido em nome de arquivo no Windows; vira `•`, e o `***00` da máscara continua
    // parecendo máscara (`•••00`) em vez de sumir num `___00`
    .replace(/\*/g, '•')
    // eslint-disable-next-line no-control-regex
    .replace(/[\\/:*?"<>|\u0000-\u001f]/g, '_')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/^[.\s]+|[.\s]+$/g, '')
  if (!nome) return fallbackAttachmentName(att)
  const ext = ATTACHMENT_EXT[att.mime_type ?? '']
  if (ext && !nome.toLowerCase().endsWith(`.${ext}`) && !(ext === 'jpg' && /\.jpeg$/i.test(nome))) {
    nome = `${nome}.${ext}`
  }
  return nome.length > 180 ? nome.slice(0, 180 - (ext ? ext.length + 1 : 0)) + (ext ? `.${ext}` : '') : nome
}
