/**
 * OptionTreeEditor.tsx
 * Autoria da TAXONOMIA — opções em árvore (F5 do `adr-dialog-tree-options`).
 *
 * ── O desenho, e por que este e não outro ───────────────────────────────────
 *
 * É a MESMA tabela de opções que já existia, com um chevron por linha. Descer um
 * nível é abrir a linha, não mudar de tela. Não há drag-and-drop porque não há
 * lib de DnD no projeto, e a ordem se resolve com ↑↓ como no resto do editor.
 *
 * O renderer do operador (Console) desenha COLUNAS MILLER, e isso não é
 * incoerência: quem AUTORA precisa ver a árvore inteira de uma vez; quem RESPONDE
 * precisa descer rápido sem se perder. Duas perguntas, dois desenhos.
 *
 * ── As três regras que a tela precisa impor ─────────────────────────────────
 *
 * **D2 — pasta × folha é DERIVADO.** Selecionável ⟺ sem filhos. Nada a marcar.
 * Daí a armadilha que esta tela AVISA: apagar o último filho converte a pasta em
 * folha selecionável, e o rótulo da pasta vira resposta. O aviso vem ANTES, com o
 * nome da pasta — perda de significado consentida, nunca silenciosa.
 *
 * **D6 — `id` é IMUTÁVEL; `label` é livre.** O `id` compõe a categoria do Arc 12,
 * e a série é append-only: se `cobranca` significar coisas diferentes na v7 e na
 * v9, a agregação por prefixo funde as duas em silêncio. Por isso o campo do `id`
 * só é editável na opção que NÃO veio do documento carregado — o que está no
 * store tem série atrás; o que é novo ainda não tem, e pode ser corrigido até
 * salvar. Comparar com o carregado é o que torna a regra estável através das
 * reescritas imutáveis da árvore (marcar linha a linha não sobrevive a elas).
 * Mudou de conceito ⇒ folha nova, e a antiga sai da oferta com `active: false`.
 *
 * **D6 — aposentar ≠ apagar.** O botão de aposentadoria tira a folha da OFERTA e
 * a mantém no documento, para o dado histórico continuar explicável. Apagar é
 * outra ação, e continua existindo para quem nunca publicou aquela folha.
 */
import React from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronDown, ChevronRight, EyeOff, Eye, Plus, Trash2, ArrowUp, ArrowDown } from 'lucide-react'
import type { DialogOption, LocalizedText } from '@/api/dialog-hooks'

// ── LocalizedText (mesma semântica dos outros editores deste módulo) ─────────

function ltToStr(t: LocalizedText | undefined, locale: string, defaultLocale: string): string {
  if (t == null) return ''
  if (typeof t === 'string') return locale === defaultLocale ? t : ''
  return t[locale] ?? ''
}
function setLt(
  cur: LocalizedText | undefined, locale: string, v: string, defaultLocale: string,
): LocalizedText {
  if (typeof cur === 'string' || cur == null) {
    if (locale === defaultLocale) return v
    return { ...(cur ? { [defaultLocale]: cur } : {}), [locale]: v }
  }
  return { ...cur, [locale]: v }
}

// ── forma da árvore ──────────────────────────────────────────────────────────

export const OPT_SEP = '.'
const filhosDe = (o: DialogOption) => o.options ?? []
export const ehPasta = (o: DialogOption) => filhosDe(o).length > 0
export const DIALOG_OPTION_MAX_DEPTH = 5

/** Profundidade da árvore a partir desta lista (1 = só folhas). */
export function profundidade(opts: DialogOption[] | undefined, d = 1): number {
  let max = d
  for (const o of opts ?? []) if (ehPasta(o)) max = Math.max(max, profundidade(o.options, d + 1))
  return max
}

/** Substitui a lista no caminho dado, devolvendo a árvore NOVA (imutável). */
function comLista(
  raiz: DialogOption[], caminho: number[], f: (l: DialogOption[]) => DialogOption[],
): DialogOption[] {
  if (!caminho.length) return f(raiz)
  const [i, ...resto] = caminho
  const copia = raiz.slice()
  const alvo = copia[i]
  if (!alvo) return raiz
  copia[i] = { ...alvo, options: comLista(alvo.options ?? [], resto, f) }
  // Pasta que ficou sem filho volta a ser FOLHA: a chave sai, em vez de virar
  // `options: []` — que o validador canônico recusa, porque lê-se de dois jeitos.
  if (!copia[i]!.options?.length) { const { options: _fora, ...semFilhos } = copia[i]!; copia[i] = semFilhos }
  return copia
}

export interface OptionTreeEditorProps {
  options:       DialogOption[]
  onChange:      (o: DialogOption[]) => void
  locale:        string
  defaultLocale: string
  /**
   * Todos os caminhos de `id` que vieram do DOCUMENTO CARREGADO. O `id` é
   * editável só fora deste conjunto — ou seja, enquanto a opção é nova e nunca
   * foi salva. Comparar com o carregado (e não marcar linha por linha) é o que
   * torna a regra estável através das reescritas imutáveis da árvore.
   */
  idsSalvos:     Set<string>
}

export const OptionTreeEditor: React.FC<OptionTreeEditorProps> = ({
  options, onChange, locale, defaultLocale, idsSalvos,
}) => {
  const { t } = useTranslation('dialogForms')
  const [abertos, setAbertos] = React.useState<Set<string>>(() => new Set())

  const idPath = (pais: DialogOption[], o: DialogOption) =>
    [...pais.map(p => p.value ?? p.id), o.value ?? o.id].filter(Boolean).join(OPT_SEP)

  const nivel = (lista: DialogOption[], idx: number[], pais: DialogOption[]): React.ReactNode =>
    lista.map((o, i) => {
      const caminho  = [...idx, i]
      const cid      = idPath(pais, o)
      const pasta    = ehPasta(o)
      const aberto   = abertos.has(cid)
      const novo     = !o.id || !idsSalvos.has(cid)
      const inativa  = o.active === false
      const podeDescer = profundidade(options) < DIALOG_OPTION_MAX_DEPTH || pasta

      const troca = (patch: Partial<DialogOption>) =>
        onChange(comLista(options, idx, l => {
          const c = l.slice(); c[i] = { ...c[i]!, ...patch }; return c
        }))

      return (
        <div key={i} className="flex flex-col gap-0.5">
          <div className={`flex items-center gap-2 ${inativa ? 'opacity-50' : ''}`}>
            {pasta ? (
              <button
                onClick={() => setAbertos(s => {
                  const n = new Set(s); n.has(cid) ? n.delete(cid) : n.add(cid); return n
                })}
                className="text-gray-500 hover:text-gray-800"
                aria-label={t('tree.expand')}
              >
                {aberto ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
              </button>
            ) : <span className="w-[13px]" />}

            <input
              value={o.id}
              placeholder="id"
              readOnly={!novo}
              title={novo ? undefined : t('tree.idLocked')}
              onChange={e => troca({ id: e.target.value })}
              className={`w-24 border rounded px-2 py-0.5 text-xs ${
                novo ? 'bg-white' : 'bg-gray-100 text-gray-500 cursor-not-allowed'}`}
            />
            <input
              value={ltToStr(o.label, locale, defaultLocale)}
              placeholder={t('field.label')}
              onChange={e => troca({ label: setLt(o.label, locale, e.target.value, defaultLocale) })}
              className={`flex-1 border rounded px-2 py-0.5 text-xs bg-white ${pasta ? 'font-medium' : ''}`}
            />
            {/* `value` é ignorado numa PASTA (ela não é resposta) — desabilitar diz
                isso na tela, em vez de deixar o autor preencher o que não vale. */}
            <input
              value={o.value ?? ''}
              placeholder={t('field.value')}
              disabled={pasta}
              onChange={e => troca({ value: e.target.value || undefined })}
              className="w-16 border rounded px-2 py-0.5 text-xs bg-white disabled:bg-gray-100"
            />
            <span className={`text-[10px] px-1.5 py-0.5 rounded ${
              pasta ? 'bg-gray-100 text-gray-600' : 'bg-green-50 text-green-700'}`}>
              {pasta ? t('tree.folder') : t('tree.leaf')}
            </span>

            <button
              onClick={() => {
                onChange(comLista(options, caminho, l => [...l, { id: '', label: '' }]))
                setAbertos(s => new Set(s).add(cid))
              }}
              disabled={!podeDescer}
              title={podeDescer ? t('tree.addChild') : t('tree.maxDepth', { n: DIALOG_OPTION_MAX_DEPTH })}
              className="text-[11px] border px-1.5 py-0.5 rounded hover:bg-white disabled:opacity-40"
            >
              <Plus size={11} className="inline" /> {t('field.option')}
            </button>

            <button
              onClick={() => troca({ active: inativa ? undefined : false })}
              title={inativa ? t('tree.reactivate') : t('tree.retire')}
              className="text-gray-400 hover:text-gray-700"
            >
              {inativa ? <Eye size={13} /> : <EyeOff size={13} />}
            </button>

            <button
              onClick={() => onChange(comLista(options, idx, l => {
                const c = l.slice(); const [x] = c.splice(i, 1)
                c.splice(Math.max(0, i - 1), 0, x!); return c
              }))}
              className="text-gray-400 hover:text-gray-700"><ArrowUp size={12} /></button>
            <button
              onClick={() => onChange(comLista(options, idx, l => {
                const c = l.slice(); const [x] = c.splice(i, 1)
                c.splice(Math.min(c.length, i + 1), 0, x!); return c
              }))}
              className="text-gray-400 hover:text-gray-700"><ArrowDown size={12} /></button>

            <button
              onClick={() => {
                // D2 — apagar o ÚLTIMO filho converte a pasta em folha
                // selecionável, e o rótulo dela vira resposta. Avisar ANTES, com o
                // nome: perda de significado consentida, nunca silenciosa.
                const pai = pais[pais.length - 1]
                if (pai && lista.length === 1) {
                  const nome = ltToStr(pai.label, locale, defaultLocale) || pai.id
                  if (!window.confirm(t('tree.confirmLastChild', { pasta: nome }))) return
                }
                onChange(comLista(options, idx, l => l.filter((_, k) => k !== i)))
              }}
              className="text-red-400 hover:text-red-600"><Trash2 size={13} /></button>
          </div>

          {pasta && aberto && (
            <div className="ml-4 pl-3 border-l border-gray-200 flex flex-col gap-0.5">
              {nivel(o.options ?? [], caminho, [...pais, o])}
            </div>
          )}
        </div>
      )
    })

  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium text-gray-600">{t('field.options')}</span>
        <span className="text-[10px] text-gray-400">
          {t('tree.depth', { d: profundidade(options), max: DIALOG_OPTION_MAX_DEPTH })}
        </span>
        <button
          onClick={() => onChange([...options, { id: '', label: '' }])}
          className="text-[11px] border px-1.5 py-0.5 rounded hover:bg-white"
        >
          + {t('field.option')}
        </button>
      </div>
      <div className="flex flex-col gap-0.5">{nivel(options, [], [])}</div>
      {options.length > 0 && (
        <div className="text-[10px] text-gray-400 leading-snug">{t('tree.hint')}</div>
      )}
    </div>
  )
}
