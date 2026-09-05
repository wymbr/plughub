/**
 * FormBlockEditor.tsx
 * O corpo do bloco FORM — um turno que coleta N valores nomeados.
 *
 * ── Por que campo NÃO é pergunta ────────────────────────────────────────────
 *
 * A tentação é reusar o `QuestionEditor` ("cada campo é uma perguntinha"), e ela
 * está errada por MEDIÇÃO: os conjuntos de atributos não coincidem.
 *
 *   só a pergunta tem : retry · visibility · timeout_s · ask_when · interaction
 *   só o campo tem    : required · value
 *   coincidem         : id · label/prompt · options · masked · validation · capture
 *
 * Reusar mostraria QUATRO controles inertes e esconderia DOIS reais — que é
 * exatamente o defeito que este arco fechou no ramo `form` da pergunta. Editor
 * próprio, então; e a regra D8 (`masked` deriva `format`) vem de UMA casa
 * (`d8Verdict`), compartilhada com o editor de pergunta.
 *
 * ⚠️ ESCOPO MEDIDO (2026-09-05): entre os 10 campos publicados, os tipos em uso
 * são `text` (9) e `bool` (1); **zero** têm `options` (select) e **zero** têm
 * `capture`. Por isso as opções POR CAMPO — o único nível mais profundo que este
 * — não ganharam widget: elas sobrevivem ao round-trip (o spread as carrega) e a
 * tela as ANUNCIA. Widget para população zero é trabalho contra ninguém.
 */
import React, { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, ArrowUp, ArrowDown, ChevronDown, ChevronRight, Trash2 } from 'lucide-react'
import { useAuth } from '@/auth/useAuth'
import type { DialogField, LocalizedText, QuestionNode } from '@/api/dialog-hooks'
import {
  useFormatCatalog, useMaskedTypes, formatLabel, d8Verdict,
  type FormatEntry, type MaskedTypeEntry,
} from './catalog-hooks'

// ── helpers de LocalizedText (mesma semântica do editor de blocos) ────────────

function ltToStr(t: LocalizedText | undefined, locale: string, defaultLocale: string): string {
  if (t == null) return ''
  if (typeof t === 'string') return locale === defaultLocale ? t : ''
  return t[locale] ?? ''
}

function setLt(current: LocalizedText | undefined, locale: string, value: string, defaultLocale: string): LocalizedText {
  let map: Record<string, string> = {}
  if (typeof current === 'string') map[defaultLocale] = current
  else if (current) map = { ...current }
  if (value === '') delete map[locale]
  else map[locale] = value
  const keys = Object.keys(map)
  if (keys.length === 0) return ''
  if (keys.length === 1 && keys[0] === defaultLocale) return map[defaultLocale]!
  return map
}

/** Tipos que os renderizadores reconhecem. `DialogField.type` é string ABERTA no
 *  schema ("adapter maps to UI"), então valor desconhecido é PRESERVADO como
 *  opção extra — dropdown que não mostra o valor corrente o apaga no 1º toque. */
const FIELD_TYPES = ['text', 'number', 'money', 'date', 'bool', 'select']
const fieldTypeOptions = (atual?: string): string[] =>
  atual && !FIELD_TYPES.includes(atual) ? [...FIELD_TYPES, atual] : FIELD_TYPES

// ── Corpo do bloco ────────────────────────────────────────────────────────────

export const FormBlockEditor: React.FC<{
  node: QuestionNode
  locale: string
  defaultLocale: string
  onChange: (patch: Partial<QuestionNode>) => void
  /** A linha de visibilidade vive no editor de blocos; recebida como slot para
   *  não duplicar o mapeamento `all|agents_only|customer`. */
  visibilityRow?: React.ReactNode
}> = ({ node, locale, defaultLocale, onChange, visibilityRow }) => {
  const { t } = useTranslation('dialogForms')
  const { tenantId } = useAuth()
  const { formats, erro: errFmt } = useFormatCatalog(tenantId)
  const { types: maskedTypes, erro: errMsk } = useMaskedTypes(tenantId)
  const catalogoErro = errFmt ?? errMsk

  const fields = node.fields ?? []
  const setFields = (f: DialogField[]) => onChange({ fields: f })

  return (
    <div className="space-y-2 rounded border border-emerald-100 bg-emerald-50/40 p-2">
      <label className="block text-xs text-gray-600">
        {t('form.prompt')}
        <textarea value={ltToStr(node.prompt, locale, defaultLocale)} rows={2}
          placeholder={t('form.promptPlaceholder')}
          onChange={e => onChange({ prompt: setLt(node.prompt, locale, e.target.value, defaultLocale) })}
          className="mt-1 w-full border rounded px-2 py-1 text-sm bg-white" />
      </label>

      <div className="flex items-center gap-3 text-xs text-gray-600 flex-wrap">
        <label className="flex items-center gap-1">{t('field.outputKey')}
          <input value={node.output_key}
            onChange={e => onChange({ output_key: e.target.value })}
            className="w-32 border rounded px-1 py-0.5 bg-white font-mono" />
        </label>
        <label className="flex items-center gap-1">{t('field.timeout')}
          <input type="number" value={node.timeout_s ?? ''} className="w-16 border rounded px-1 py-0.5 bg-white"
            onChange={e => onChange({ timeout_s: e.target.value === '' ? undefined : Number(e.target.value) })} />
        </label>
        {visibilityRow}
      </div>

      <div className="flex items-center gap-2 border-t border-emerald-100 pt-1">
        <span className="text-xs font-medium text-gray-600">{t('form.fields', { n: fields.length })}</span>
        <button onClick={() => setFields([...fields, { id: '', label: '', type: 'text', required: false }])}
          className="rounded border px-1.5 py-0.5 text-[11px] hover:bg-white">+ {t('form.field')}</button>
        {catalogoErro && <span className="text-[11px] text-amber-700">{t('field.catalogUnavailable')}</span>}
      </div>

      {fields.length === 0 && (
        <p className="flex items-start gap-1 text-[11px] text-amber-700">
          <AlertTriangle size={12} className="mt-0.5 shrink-0" />{t('form.empty')}
        </p>
      )}

      {fields.map((f, i) => (
        <FieldRow key={i} field={f} locale={locale} defaultLocale={defaultLocale}
          formats={formats} maskedTypes={maskedTypes}
          first={i === 0} last={i === fields.length - 1}
          onChange={nf => setFields(fields.map((x, j) => (j === i ? nf : x)))}
          onRemove={() => setFields(fields.filter((_, j) => j !== i))}
          onMove={dir => {
            const j = i + dir
            if (j < 0 || j >= fields.length) return
            const next = fields.slice();[next[i], next[j]] = [next[j]!, next[i]!]; setFields(next)
          }} />
      ))}
    </div>
  )
}

/** Uma linha de campo: tira compacta + corpo expandido — mesmo padrão do
 *  `NodeRow`, um nível abaixo. */
const FieldRow: React.FC<{
  field: DialogField; locale: string; defaultLocale: string
  formats: FormatEntry[]; maskedTypes: MaskedTypeEntry[]
  first: boolean; last: boolean
  onChange: (f: DialogField) => void; onRemove: () => void; onMove: (dir: -1 | 1) => void
}> = ({ field: f, locale, defaultLocale, formats, maskedTypes, first, last, onChange, onRemove, onMove }) => {
  const { t } = useTranslation('dialogForms')
  const [aberto, setAberto] = useState(false)
  const { derivado, conflito } = d8Verdict(f.masked, f.validation?.format, formats)

  return (
    <div className="rounded border bg-white">
      <div className="flex items-center gap-2 px-2 py-1">
        <button onClick={() => setAberto(v => !v)} className="text-gray-400 hover:text-gray-700">
          {aberto ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        </button>
        <input value={f.id} placeholder="id"
          onChange={e => onChange({ ...f, id: e.target.value })}
          className="w-28 rounded border bg-white px-1 py-0.5 font-mono text-xs" />
        <input value={ltToStr(f.label, locale, defaultLocale)} placeholder={t('field.label')}
          onChange={e => onChange({ ...f, label: setLt(f.label, locale, e.target.value, defaultLocale) })}
          className="min-w-[80px] flex-1 rounded border bg-white px-1 py-0.5 text-xs" />
        <select value={f.type} onChange={e => onChange({ ...f, type: e.target.value })}
          className="rounded border bg-white px-1 py-0.5 text-xs">
          {fieldTypeOptions(f.type).map(ft => <option key={ft} value={ft}>{ft}</option>)}
        </select>
        <label className="flex items-center gap-1 text-[11px] text-gray-500">
          <input type="checkbox" checked={!!f.required}
            onChange={e => onChange({ ...f, required: e.target.checked })} />
          {t('form.required')}
        </label>
        {!!f.masked && <span className="text-[11px] text-amber-700">🔒</span>}
        {conflito && <AlertTriangle size={12} className="text-red-500" />}
        <button onClick={() => onMove(-1)} disabled={first}
          className="text-gray-400 hover:text-gray-700 disabled:opacity-30"><ArrowUp size={12} /></button>
        <button onClick={() => onMove(1)} disabled={last}
          className="text-gray-400 hover:text-gray-700 disabled:opacity-30"><ArrowDown size={12} /></button>
        <button onClick={onRemove} className="text-red-400 hover:text-red-600"><Trash2 size={12} /></button>
      </div>

      {aberto && (
        <div className="space-y-1.5 border-t px-3 py-2 text-xs text-gray-600">
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-1">{t('form.value')}
              <input value={f.value === undefined ? '' : String(f.value)}
                onChange={e => onChange({ ...f, value: e.target.value === '' ? undefined : e.target.value })}
                className="w-40 rounded border bg-white px-1 py-0.5" />
            </label>
            {/* Só o Console pré-preenche — medido: webchat e página web montam o
                input SEM `value`, e WhatsApp/SMS não têm a noção. Dizer isso aqui
                evita que alguém conte com ele num formulário voltado ao cliente. */}
            <span className="text-[11px] text-gray-400">{t('form.valueHint')}</span>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-1">{t('field.maskedType')}
              <select value={f.masked === true ? 'opaque' : (typeof f.masked === 'string' ? f.masked : '')}
                onChange={e => onChange({ ...f, masked: e.target.value || undefined })}
                className="rounded border bg-white px-1 py-0.5">
                <option value="">{t('field.maskedNone')}</option>
                {maskedTypes.map(mt => (
                  <option key={mt.id} value={mt.id}>{(mt.icon ? mt.icon + ' ' : '') + (mt.label ?? mt.id)}</option>
                ))}
              </select>
            </label>
            <label className="flex min-w-[180px] flex-1 items-center gap-1">{t('field.format')}
              <select value={f.validation?.format ?? ''} disabled={!!derivado}
                onChange={e => onChange({ ...f, validation: { ...f.validation, format: e.target.value || undefined } })}
                className="flex-1 rounded border bg-white px-1 py-0.5 disabled:bg-gray-100">
                <option value="">{t('field.formatNone')}</option>
                {formats.map(fm => <option key={fm.id} value={fm.id}>{formatLabel(fm, locale)}</option>)}
              </select>
            </label>
          </div>

          {derivado && !conflito && (
            <p className="text-[11px] text-blue-700">
              {t('field.formatDerived', { format: formatLabel(derivado, locale), tipo: String(f.masked) })}
            </p>
          )}
          {conflito && (
            <p className="flex items-center gap-1 text-[11px] text-red-700">
              <AlertTriangle className="h-3 w-3" />
              {t('field.formatConflict', { declarado: f.validation?.format ?? '', derivado: conflito })}
            </p>
          )}

          {/* População ZERO hoje: nenhum campo publicado usa `select`. As opções
              sobrevivem ao round-trip e a tela as ANUNCIA, em vez de fingir que
              não existem — o widget entra quando houver quem use. */}
          {f.options?.length ? (
            <p className="text-[11px] text-gray-500">{t('form.optionsCount', { n: f.options.length })}</p>
          ) : f.type === 'select' ? (
            <p className="text-[11px] text-amber-700">{t('form.selectNoOptions')}</p>
          ) : null}
        </div>
      )}
    </div>
  )
}

export default FormBlockEditor
