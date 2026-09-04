/**
 * dialog-hooks.ts
 * API hooks for the Dialog primitive editor (DialogForm store — dialog-api,
 * port 3760, via the /v1/dialog Vite/nginx proxy). Custom fetch (mirrors
 * evaluation-hooks). Tenant scoping via the X-Tenant-ID header (dialog-api
 * requires it). ESCRITA exige `config.dialog_forms` (read_write) desde 2026-08-27:
 * o portao do dialog-api virou dual (admin-token de sistema OU Bearer + ABAC), e
 * ate entao `PLUGHUB_DIALOG_ADMIN_TOKEN` vazio o deixava inerte — criar E publicar
 * um form anonimamente devolvia 200.
 *
 * The DialogForm shape mirrors @plughub/schemas/dialog.ts but is defined
 * locally to keep platform-ui decoupled from the schemas package.
 */
import { useCallback, useEffect, useState } from 'react'
import { getAccessToken } from '@/auth/token-store'
import { apiFetch } from '@/api/apiFetch'

const BASE = '/v1/dialog/forms'

export type LocalizedText = string | Record<string, string>

export interface DialogValidation {
  /**
   * Nome de entrada do catálogo `dialog.formats` — a declaração PRIMÁRIA desde
   * o ADR `adr-dialog-input-format-catalog`. Decide afordância (máscara de
   * digitação, teclado, comprimento) e veredicto em dois níveis.
   */
  format?: string
  numeric?: boolean
  // `pattern` REMOVIDO na F3 (2026-09-04), junto com o campo livre de regex no
  // editor. Censo antes de remover: 0 de 12 formas publicadas, 0 semeadas, 0
  // YAMLs.
  min_length?: number
  max_length?: number
  min?: number
  max?: number
}

export interface DialogCapture {
  /** Legacy standalone metric (its own 1-item dimension). */
  metric?: string
  /** Contributes to a declared DialogForm.dimensions[] entry. */
  dimension_id?: string
  /** Weight within its dimension (relative; re-normalized at compose). Default 1. */
  weight?: number
  /** Fixed machine value for an option/field (e.g. button "4" → score 4). */
  value?: number | string
  /**
   * Arc 12 event shape (`DialogCaptureSchema.kind` no canonico). AUSENTE aqui ate
   * 2026-09-04, e a ausencia era o que tornava o descarte em `flattenBlocks`
   * invisivel ao compilador: um tipo espelhado que perde um campo transforma
   * perda de dado em codigo que compila. `deriveAgentEvents` exige `kind` E
   * `metric` — sem `kind` a metrica simplesmente para de ser emitida.
   */
  kind?: 'scored' | 'nominal'
}

/** Numeric scale of an instrument (inherited by member questions). */
export interface ScoreScale {
  min?: number
  max: number
}

export type ScoreAggregation = 'weighted_mean' | 'min'

/**
 * A survey instrument (csat, nps, …) composed of one or more questions.
 * The dimension owns the scale + aggregation; questions bind via
 * capture.dimension_id + capture.weight. See adr-survey-form-scoring-composition.
 */
export interface DialogDimension {
  dimension_id: string
  label?: LocalizedText
  scale: ScoreScale
  aggregation?: ScoreAggregation
  /** Instrument-level render, inherited by member questions (materialized on save). */
  interaction?: DialogInteraction
  /** Anchor label per scale point (length = max−min+1); absent = numeric label. */
  anchors?: LocalizedText[]
  /** Reserved for a future form-level composite (health score). */
  weight?: number
}

export interface DialogOption {
  id: string
  label: LocalizedText
  value?: string
  capture?: DialogCapture
}

export interface DialogField {
  id: string
  label: LocalizedText
  type: string
  required?: boolean
  /**
   * `false | string`: a string NOMEIA um tipo de `masking.types` (T1 do
   * `adr-masked-typed-declaration`); `true` legado resolve para `opaque`.
   * Era `boolean` aqui, e o estreitamento tornava a declaração TIPADA
   * inexprimível pela tela — terceira ocorrência da família DTO-01.
   */
  masked?: boolean | string
  validation?: DialogValidation
  capture?: DialogCapture
}

export type DialogVisibility = 'all' | 'agents_only' | string[]

export type AskWhenOp = 'lt' | 'lte' | 'gt' | 'gte' | 'eq' | 'ne' | 'in'

/** Declarative skip-logic guard (references a prior question's output_key). */
export interface AskWhen {
  field: string
  op: AskWhenOp
  value: number | string | boolean | Array<number | string>
}

export interface StatementNode {
  id: string
  kind: 'statement'
  text: LocalizedText
  visibility?: DialogVisibility
  ask_when?: AskWhen
}

export type DialogInteraction = 'text' | 'button' | 'list' | 'checklist' | 'form'

export interface QuestionNode {
  id: string
  kind: 'question'
  prompt: LocalizedText
  interaction: DialogInteraction
  options?: DialogOption[]
  fields?: DialogField[]
  /**
   * `false | string`: a string NOMEIA um tipo de `masking.types` (T1 do
   * `adr-masked-typed-declaration`); `true` legado resolve para `opaque`.
   * Era `boolean` aqui, e o estreitamento tornava a declaração TIPADA
   * inexprimível pela tela — terceira ocorrência da família DTO-01.
   */
  masked?: boolean | string
  output_key: string
  capture?: DialogCapture
  validation?: DialogValidation
  retry?: { reprompt: LocalizedText; max_attempts?: number }
  visibility?: DialogVisibility
  timeout_s?: number
  ask_when?: AskWhen
}

export type DialogNode = StatementNode | QuestionNode

export interface DialogForm {
  form_id: string
  tenant_id?: string
  name: string
  description?: string
  status?: 'draft' | 'published'
  version?: number
  default_locale: string
  locales: string[]
  nodes: DialogNode[]
  /** Composed instruments (survey_definition layer). Empty for plain dialogs. */
  dimensions?: DialogDimension[]
  /** Optional form-level composite (health score) rolled up over the dimensions. */
  composite?: { metric: string }
  tags?: string[]
  created_at?: string
  updated_at?: string
  /**
   * Arquivado (ADR adr-dialog-form-deletion). Preenchido = fora do catálogo e de vínculos
   * novos; NÃO significa inalcançável — quem já está vinculado continua resolvendo o form.
   * Por isso a tela diz "arquivar", nunca "apagar".
   */
  deleted_at?: string | null
  /**
   * Só na LISTA. Decide se o DELETE arquiva (reversível) ou PURGA (definitivo), e por isso
   * é lido ANTES de perguntar. Não é derivável de `status`: a última versão pode ser
   * rascunho e existir uma publicada mais antiga.
   */
  ever_published?: boolean
}

/** Fields the store accepts on create/update (the rest is server-owned). */
export type DialogFormUpsert = Pick<
  DialogForm,
  'form_id' | 'name' | 'description' | 'default_locale' | 'locales' | 'nodes' | 'dimensions' | 'composite' | 'tags'
>

function headers(tenantId: string): Record<string, string> {
  // O Bearer viaja em TODA chamada, leitura inclusive. As leituras do dialog-api sao
  // abertas, entao mandar o token nelas nao muda nada hoje — mas separar "headers de
  // leitura" de "headers de escrita" cria dois lugares onde alguem pode esquecer, e o
  // esquecimento aparece como um botao que nao salva, nao como erro.
  const h: Record<string, string> = { 'Content-Type': 'application/json', 'X-Tenant-ID': tenantId }
  // O token vive em MEMORIA (`auth/token-store`, alimentado pelo AuthContext) — nunca no
  // localStorage. Ler `localStorage['plughub_access_token']` era ler uma chave que NINGUEM
  // escreve: o Bearer nunca ia, e desde que o portao de escrita fechou (2026-08-27) toda
  // gravacao desta pagina saia anonima e voltava 401. Falha muda ate alguem clicar em
  // salvar — o `if (token)` transformava "nao ha token" em "manda sem token".
  const token = getAccessToken()
  if (token) h['Authorization'] = `Bearer ${token}`
  return h
}

// ── List (latest version metadata per form) ───────────────────────────────────

export function useDialogForms(tenantId: string, includeDeleted = false) {
  const [forms, setForms]     = useState<DialogForm[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const url = includeDeleted ? `${BASE}?include_deleted=true` : BASE
      const r = await apiFetch(url, { headers: headers(tenantId) })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json()
      setForms(Array.isArray(d) ? d : (d?.forms ?? []))
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [tenantId, includeDeleted])

  useEffect(() => { load() }, [load])

  return { forms, loading, error, reload: load }
}

// ── Get one (full, with nodes) — highest version (draft or published) ─────────

export async function getDialogForm(tenantId: string, formId: string): Promise<DialogForm> {
  const r = await apiFetch(`${BASE}/${encodeURIComponent(formId)}`, { headers: headers(tenantId) })
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return r.json()
}

// ── Create / update / publish ─────────────────────────────────────────────────

export async function createDialogForm(tenantId: string, body: DialogFormUpsert): Promise<DialogForm> {
  const r = await apiFetch(BASE, { method: 'POST', headers: headers(tenantId), body: JSON.stringify(body) })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text().catch(() => '')}`)
  return r.json()
}

export async function updateDialogForm(tenantId: string, formId: string, body: DialogFormUpsert): Promise<DialogForm> {
  const r = await apiFetch(`${BASE}/${encodeURIComponent(formId)}`, {
    method: 'PUT', headers: headers(tenantId), body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text().catch(() => '')}`)
  return r.json()
}

export async function publishDialogForm(tenantId: string, formId: string): Promise<DialogForm> {
  const r = await apiFetch(`${BASE}/${encodeURIComponent(formId)}/publish`, {
    method: 'POST', headers: headers(tenantId),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text().catch(() => '')}`)
  return r.json()
}

// ── Arquivar / restaurar (ADR adr-dialog-form-deletion) ───────────────────────

export interface DialogFormDeleteResult {
  form_id: string
  /** true = as linhas foram REMOVIDAS (form nunca publicado). Não há undelete depois. */
  purged: boolean
  deleted_at: string | null
  versions: number
  already_deleted: boolean
}

export async function deleteDialogForm(
  tenantId: string, formId: string,
): Promise<DialogFormDeleteResult> {
  const r = await apiFetch(`${BASE}/${encodeURIComponent(formId)}`, {
    method: 'DELETE', headers: headers(tenantId),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text().catch(() => '')}`)
  return r.json()
}

export async function undeleteDialogForm(
  tenantId: string, formId: string,
): Promise<{ form_id: string; restored_versions: number; was_deleted: boolean }> {
  const r = await apiFetch(`${BASE}/${encodeURIComponent(formId)}/undelete`, {
    method: 'POST', headers: headers(tenantId),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text().catch(() => '')}`)
  return r.json()
}
