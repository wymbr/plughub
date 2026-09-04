/**
 * DialogJsonPanel.tsx
 * Importador · editor · visualizador do JSON de uma DialogForm.
 *
 * ── Por que ele existe (2026-09-04) ─────────────────────────────────────────
 *
 * O editor de widgets não sabe autorar `fields[]` — a lista que a interação
 * `form` usa. Medido: `grep fields DialogFormsPage.tsx` = ZERO, enquanto o
 * runtime a suporta inteira (`buildRender` → `menu` → adapters). Escolher
 * `form` no dropdown produzia um nó morto, e nada ficava vermelho.
 *
 * A alternativa era um editor de widgets aninhado no 5º nível (bloco → nó →
 * pergunta → campo → opção/validação), com ~8 atributos por campo, dois deles
 * compostos. A decisão do dono foi outra, e é melhor: a forma **é** JSON — a
 * dialog-api persiste `nodes` como `list[dict[str, Any]]`, opaco por decisão —,
 * então a superfície de poder edita o JSON e o widget continua dono do que já
 * cobre bem (statement, texto, botões, instrumento).
 *
 * ── O veredicto é do SERVIDOR ───────────────────────────────────────────────
 *
 * Mesma razão do dry-run do editor de skill-flow (`adr-skill-flow-editor-
 * validation`, D2): `@plughub/schemas` não é importável no browser (sem
 * workspaces, o Dockerfile copia só este pacote, risco de dual-instance de
 * Zod). Então o editor PERGUNTA (`POST /api/dialog/preview`) e o servidor
 * responde rodando a MESMA função que o `form_get` roda.
 *
 * ⚠️ **Degradação é ALTA**: endpoint fora do ar ⇒ "não verificado", nunca
 * verde. Painel vazio é indistinguível de "sem erros", e é assim que um
 * verificador vira decoração.
 *
 * ⚠️ O dry-run **não cobre** o conflito `format` × `masked` (§D8) — aquela
 * regra precisa do catálogo de formatos (config-api) e continua no publish da
 * dialog-api. A tela DIZ isso: validador que insinua completude é como se
 * compra o *"o editor disse que estava bom e o save recusou"*.
 *
 * ── O preview mostra o `render`, não uma maquete de canal ───────────────────
 *
 * A mesma forma é renderizada hoje por TRÊS superfícies que já divergem
 * (Console, webchat, página web). Uma quarta, inventada para esta tela, seria a
 * divergência do `evaluateAskWhen` outra vez — com o agravante de ser aquela em
 * que o autor confia. O que se mostra aqui é o bloco `render` que o runner
 * recebe: fiel por construção, porque vem da função de produção.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Editor from '@monaco-editor/react'
import { AlertTriangle, Check, Download, Upload, X, Loader2, HelpCircle } from 'lucide-react'
import { apiFetch } from '@/api/apiFetch'
import type { DialogForm } from '@/api/dialog-hooks'

// ── Contrato do dry-run (espelha `@plughub/schemas/dialog-render`) ────────────
// Espelhado, não importado — ver o cabeçalho. O que impede a divergência de doer
// é que este lado só LÊ o veredicto; quem decide é o servidor.

export interface DialogFormIssue {
  path:    string
  message: string
  code:    'schema' | 'duplicate_node_id' | 'ask_when_forward_ref'
}

interface RenderField {
  id: string; label: string; type: string; required: boolean
  masked: boolean | string
  value?: string | number | boolean
  options?: Array<{ id: string; label: string }>
  validation?: unknown
}

interface DialogRender {
  interaction:     string
  prompt:          string
  options:         Array<{ id: string; label: string }>
  output_key:      string
  timeout_s:       number
  menu_prompt:     string
  fields:          RenderField[]
  statement_after: string
  questions:       unknown[]
  by_node:         Record<string, string>
  [k: string]: unknown
}

type Verdict =
  | { kind: 'idle' }
  | { kind: 'json_error';  reason: string }
  | { kind: 'checking' }
  | { kind: 'ok';          render: DialogRender }
  | { kind: 'invalid';     errors: DialogFormIssue[] }
  | { kind: 'unavailable'; reason: string }

/**
 * Pergunta ao servidor. Debounce de 700 ms como o dry-run do skill-flow — o
 * mesmo número, de propósito: são a mesma afordância e não devem parecer duas.
 */
function useDryRun(text: string, locale: string, tenantId: string): Verdict {
  const [state, setState] = useState<Verdict>({ kind: 'idle' })

  useEffect(() => {
    if (!text.trim()) { setState({ kind: 'idle' }); return }
    let vivo = true
    const timer = setTimeout(async () => {
      let doc: unknown
      try {
        doc = JSON.parse(text)
      } catch (e) {
        // Sintaxe quebrada NÃO é veredicto de contrato: misturar os dois manda o
        // autor procurar no lugar errado.
        if (vivo) setState({ kind: 'json_error', reason: e instanceof Error ? e.message : String(e) })
        return
      }
      if (vivo) setState({ kind: 'checking' })
      try {
        const res = await apiFetch('/api/dialog/preview', {
          method:  'POST',
          headers: { 'Content-Type': 'application/json', 'X-Tenant-ID': tenantId },
          body:    JSON.stringify({ form: doc, locale }),
        })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const body = await res.json() as { valid: boolean; errors?: DialogFormIssue[]; render?: DialogRender }
        if (!vivo) return
        setState(
          body.valid && body.render
            ? { kind: 'ok', render: body.render }
            : { kind: 'invalid', errors: body.errors ?? [] },
        )
      } catch (e) {
        if (vivo) setState({ kind: 'unavailable', reason: e instanceof Error ? e.message : 'erro' })
      }
    }, 700)
    return () => { vivo = false; clearTimeout(timer) }
  }, [text, locale, tenantId])

  return state
}

// ── Painel ────────────────────────────────────────────────────────────────────

interface Props {
  /** Documento atual (draft + blocos já achatados). */
  doc:      DialogForm
  locale:   string
  tenantId: string
  /** `form_id` imutável (forma já existente); mudar quebraria o PUT com 400. */
  lockedFormId?: string
  readOnly?: boolean
  onApply:  (doc: DialogForm) => void
  onClose:  () => void
}

export const DialogJsonPanel: React.FC<Props> = ({
  doc, locale, tenantId, lockedFormId, readOnly, onApply, onClose,
}) => {
  const { t } = useTranslation('dialogForms')
  const [text, setText]   = useState(() => JSON.stringify(doc, null, 2))
  const [tab,  setTab]    = useState<'json' | 'preview'>('json')
  const [erroLocal, setErroLocal] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const verdict = useDryRun(text, locale, tenantId)

  const aplicar = useCallback(() => {
    let parsed: unknown
    try {
      parsed = JSON.parse(text)
    } catch (e) {
      setErroLocal(e instanceof Error ? e.message : String(e)); return
    }
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
      setErroLocal(t('json.err.notObject')); return
    }
    const novo = parsed as DialogForm
    // O PUT casa `form_id` do corpo com o do caminho (400 no servidor). Recusar
    // AQUI, nomeando, evita que o autor receba um HTTP cru — e não corrige em
    // silêncio, que trocaria um erro visível por uma edição perdida.
    if (lockedFormId && novo.form_id !== lockedFormId) {
      setErroLocal(t('json.err.formIdLocked', { id: lockedFormId })); return
    }
    setErroLocal(null)
    onApply(novo)
  }, [text, lockedFormId, onApply, t])

  const importar = useCallback((file: File) => {
    const reader = new FileReader()
    reader.onload = () => {
      const raw = String(reader.result ?? '')
      try {
        // Reserializa: arquivo semeado vem com indentação variada, e o autor
        // deve ver o mesmo formato que a tela produz.
        setText(JSON.stringify(JSON.parse(raw), null, 2))
        setErroLocal(null)
        setTab('json')
      } catch (e) {
        setErroLocal(e instanceof Error ? e.message : String(e))
      }
    }
    reader.readAsText(file)
  }, [])

  const exportar = useCallback(() => {
    const blob = new Blob([text], { type: 'application/json' })
    const url  = URL.createObjectURL(blob)
    const a    = document.createElement('a')
    a.href = url
    a.download = `${doc.form_id || 'dialog_form'}.json`
    a.click()
    URL.revokeObjectURL(url)
  }, [text, doc.form_id])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-6"
         role="dialog" aria-modal="true">
      <div className="flex h-full w-full max-w-6xl flex-col rounded-lg bg-white shadow-xl">
        {/* header */}
        <div className="flex items-center gap-3 border-b px-4 py-2">
          <h2 className="text-sm font-semibold text-gray-700">{t('json.title')}</h2>
          <span className="font-mono text-[11px] text-gray-400">{doc.form_id}</span>
          <div className="flex-1" />
          <button onClick={() => setTab('json')}
            className={`rounded px-2 py-1 text-xs ${tab === 'json' ? 'bg-blue-50 text-blue-700' : 'text-gray-500 hover:bg-gray-50'}`}>
            {t('json.tab.json')}
          </button>
          <button onClick={() => setTab('preview')}
            className={`rounded px-2 py-1 text-xs ${tab === 'preview' ? 'bg-blue-50 text-blue-700' : 'text-gray-500 hover:bg-gray-50'}`}>
            {t('json.tab.preview')}
          </button>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-700"><X size={16} /></button>
        </div>

        {/* toolbar */}
        <div className="flex items-center gap-2 border-b bg-gray-50 px-4 py-1.5 text-xs">
          <input ref={fileRef} type="file" accept="application/json,.json" className="hidden"
            onChange={e => { const f = e.target.files?.[0]; if (f) importar(f); e.target.value = '' }} />
          <button disabled={readOnly} onClick={() => fileRef.current?.click()}
            className="flex items-center gap-1 rounded border px-2 py-0.5 hover:bg-white disabled:opacity-40">
            <Upload size={12} /> {t('json.import')}
          </button>
          <button onClick={exportar}
            className="flex items-center gap-1 rounded border px-2 py-0.5 hover:bg-white">
            <Download size={12} /> {t('json.export')}
          </button>
          <div className="flex-1" />
          <VerdictBadge state={verdict} />
        </div>

        {/* body */}
        <div className="min-h-0 flex-1">
          {tab === 'json' ? (
            <Editor
              height="100%"
              defaultLanguage="json"
              theme="vs"
              value={text}
              onChange={v => setText(v ?? '')}
              options={{
                fontSize:                13,
                minimap:                 { enabled: false },
                scrollBeyondLastLine:    false,
                wordWrap:                'on',
                tabSize:                 2,
                readOnly:                !!readOnly,
                bracketPairColorization: { enabled: true },
                padding:                 { top: 12 },
                fontFamily: "'JetBrains Mono', 'Fira Code', Consolas, monospace",
              }}
            />
          ) : (
            <RenderPreview state={verdict} />
          )}
        </div>

        {/* verdict detail */}
        <VerdictPanel state={verdict} />

        {/* footer */}
        <div className="flex items-center gap-3 border-t px-4 py-2">
          <button onClick={onClose} className="rounded border px-3 py-1.5 text-sm hover:bg-gray-50">
            {t('json.cancel')}
          </button>
          <button disabled={readOnly} onClick={aplicar}
            title={verdict.kind === 'invalid' ? t('json.applyAnywayHint') : undefined}
            className="rounded bg-blue-700 px-3 py-1.5 text-sm text-white hover:bg-blue-800 disabled:opacity-50">
            {verdict.kind === 'invalid' ? t('json.applyAnyway') : t('json.apply')}
          </button>
          <span className="text-[11px] text-gray-400">{t('json.applyHint')}</span>
          {erroLocal && (
            <span className="flex items-center gap-1 text-xs text-red-600">
              <AlertTriangle size={13} />{erroLocal}
            </span>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Veredicto ─────────────────────────────────────────────────────────────────

const VerdictBadge: React.FC<{ state: Verdict }> = ({ state }) => {
  const { t } = useTranslation('dialogForms')
  if (state.kind === 'idle') return null
  if (state.kind === 'checking')
    return <span className="flex items-center gap-1 text-gray-500"><Loader2 size={12} className="animate-spin" />{t('json.verdict.checking')}</span>
  if (state.kind === 'ok')
    return <span className="flex items-center gap-1 text-green-700"><Check size={12} />{t('json.verdict.ok')}</span>
  if (state.kind === 'json_error')
    return <span className="text-amber-700">{t('json.verdict.syntax')}</span>
  if (state.kind === 'unavailable')
    // NUNCA verde: "não verificado" é o que ele é.
    return <span className="flex items-center gap-1 text-amber-700"><HelpCircle size={12} />{t('json.verdict.unavailable')}</span>
  return <span className="flex items-center gap-1 text-red-600"><AlertTriangle size={12} />{t('json.verdict.invalid', { n: state.errors.length })}</span>
}

const VerdictPanel: React.FC<{ state: Verdict }> = ({ state }) => {
  const { t } = useTranslation('dialogForms')
  if (state.kind === 'idle' || state.kind === 'checking') return null

  const base = 'border-t px-4 py-2 text-xs max-h-40 overflow-auto'
  if (state.kind === 'json_error')
    return <div className={`${base} bg-amber-50 text-amber-900`}>{t('json.verdict.syntaxDetail', { reason: state.reason })}</div>
  if (state.kind === 'unavailable')
    return <div className={`${base} bg-amber-50 text-amber-900`}>{t('json.verdict.unavailableDetail', { reason: state.reason })}</div>
  if (state.kind === 'ok')
    return <div className={`${base} bg-green-50 text-green-900`}>{t('json.verdict.okDetail')}</div>

  return (
    <div className={`${base} bg-red-50`}>
      <ul className="space-y-0.5">
        {state.errors.map((e, i) => (
          <li key={i} className="flex gap-2 text-red-900">
            <span className="font-mono text-[11px] text-red-700">{e.path || '(raiz)'}</span>
            <span>{e.message}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

// ── Preview: o bloco `render` que o runner receberia ──────────────────────────

const RenderPreview: React.FC<{ state: Verdict }> = ({ state }) => {
  const { t } = useTranslation('dialogForms')
  if (state.kind !== 'ok')
    return (
      <div className="flex h-full items-center justify-center px-8 text-center text-xs text-gray-400">
        {state.kind === 'checking' ? t('json.verdict.checking') : t('json.preview.unavailable')}
      </div>
    )

  const r = state.render
  return (
    <div className="h-full overflow-auto p-4 text-xs">
      <p className="mb-3 rounded border border-blue-100 bg-blue-50 px-3 py-2 text-[11px] text-blue-900">
        {t('json.preview.disclaimer')}
      </p>

      <dl className="mb-4 grid grid-cols-[9rem_1fr] gap-x-3 gap-y-1">
        <Row k={t('json.preview.interaction')} v={<code className="font-mono">{r.interaction}</code>} />
        <Row k={t('json.preview.outputKey')}   v={<code className="font-mono">{r.output_key}</code>} />
        <Row k={t('json.preview.timeout')}     v={`${r.timeout_s}s`} />
        <Row k={t('json.preview.prompt')}      v={<span className="whitespace-pre-wrap">{r.prompt || '—'}</span>} />
        <Row k={t('json.preview.menuPrompt')}  v={<span className="whitespace-pre-wrap">{r.menu_prompt || '—'}</span>} />
        {r.statement_after && (
          <Row k={t('json.preview.statementAfter')} v={<span className="whitespace-pre-wrap">{r.statement_after}</span>} />
        )}
      </dl>

      {r.options.length > 0 && (
        <section className="mb-4">
          <h4 className="mb-1 font-semibold text-gray-600">{t('json.preview.options')}</h4>
          <div className="flex flex-wrap gap-1">
            {r.options.map(o => (
              <span key={o.id} className="rounded border bg-gray-50 px-2 py-0.5">
                {o.label} <span className="font-mono text-gray-400">({o.id})</span>
              </span>
            ))}
          </div>
        </section>
      )}

      <section className="mb-4">
        <h4 className="mb-1 font-semibold text-gray-600">
          {t('json.preview.fields', { n: r.fields.length })}
        </h4>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[42rem] border-collapse">
            <thead>
              <tr className="border-b text-left text-[11px] text-gray-500">
                <th className="py-1 pr-2">id</th>
                <th className="py-1 pr-2">{t('field.label')}</th>
                <th className="py-1 pr-2">{t('json.preview.type')}</th>
                <th className="py-1 pr-2">{t('json.preview.required')}</th>
                <th className="py-1 pr-2">{t('field.masked')}</th>
                <th className="py-1 pr-2">{t('json.preview.validation')}</th>
              </tr>
            </thead>
            <tbody>
              {r.fields.map(f => (
                <tr key={f.id} className="border-b last:border-0 align-top">
                  <td className="py-1 pr-2 font-mono text-[11px]">{f.id}</td>
                  <td className="py-1 pr-2">{f.label}</td>
                  <td className="py-1 pr-2">
                    <code className="font-mono">{f.type}</code>
                    {f.options && <span className="ml-1 text-gray-400">({f.options.length})</span>}
                  </td>
                  <td className="py-1 pr-2">{f.required ? '✓' : '—'}</td>
                  <td className="py-1 pr-2">
                    {f.masked === false ? <span className="text-gray-300">—</span>
                      : <code className="font-mono text-amber-700">🔒 {String(f.masked)}</code>}
                  </td>
                  <td className="py-1 pr-2 font-mono text-[11px] text-gray-500">
                    {f.validation ? JSON.stringify(f.validation) : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <details>
        <summary className="cursor-pointer text-[11px] text-gray-500">{t('json.preview.raw')}</summary>
        <pre className="mt-1 overflow-auto rounded bg-gray-50 p-2 font-mono text-[11px] leading-tight">
          {JSON.stringify(r, null, 2)}
        </pre>
      </details>
    </div>
  )
}

const Row: React.FC<{ k: string; v: React.ReactNode }> = ({ k, v }) => (
  <>
    <dt className="text-gray-500">{k}</dt>
    <dd className="text-gray-800">{v}</dd>
  </>
)

export default DialogJsonPanel
