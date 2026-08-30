import { useState, useRef, useEffect, useMemo } from 'react'

/**
 * ContextVisibilitySelect — seletor sobre os nós do MAPA do ContextStore (D6 do
 * `adr-contextstore-allowlist`, fase V5).
 *
 * ── Por que um seletor, e não um campo de texto melhor ───────────────────────
 *
 * `context_visibility` era texto livre separado por vírgula, e foi isso que deixou
 * a UI prometer namespaces que não existem. Medido em 2026-08-29, no mesmo bloco de
 * tela: o **placeholder** dizia `service, journey, session` (o `journey` nunca
 * esteve no default, e o conserto de 08-26 corrigiu a dica ao lado mas não ele), e
 * a dica cita `service` e `history` como namespaces PII — os dois com **zero
 * produtores** em todo o repositório e zero ocorrências no ContextStore vivo.
 *
 * O seletor **é** o mecanismo: a lista não é escrita, é derivada do mapa. Não há
 * como escolher um namespace que não existe, e nenhuma prosa precisa ser mantida em
 * sincronia com o código.
 *
 * ── O valor LEGADO não pode sumir em silêncio ───────────────────────────────
 *
 * Um pool salvo antes desta tela pode declarar algo fora do mapa (`service` é o caso
 * real). Um seletor que só soubesse expressar as opções DESCARTARIA esse valor no
 * primeiro save — mudança de política silenciosa, e na direção que ninguém percebe:
 * o operador deixa de ver um campo e não abre chamado sobre o que não apareceu.
 *
 * Por isso o valor desconhecido é renderizado como chip próprio, marcado, e só sai
 * se alguém o remover. É a mesma regra do `hidden_count` da V1 — contar, nunca
 * omitir.
 */

export interface SelectOption {
  value:  string
  /** Rótulo curto à direita (ex.: "escopo", "legado", tipo do campo). */
  badge?: string
  /** Contexto no hover — canônica de um alias, nº de campos de um namespace. */
  hint?:  string
  /** Opção de forma antiga: some quando a migração terminar. */
  legacy?: boolean
}

export function ContextVisibilitySelect({
  options, value, onChange, placeholder, emptyLabel, searchLabel,
  unknownBadge, unknownTitle, disabled, searchable,
}: {
  options:      SelectOption[]
  value:        string[]
  onChange:     (v: string[]) => void
  placeholder:  string
  emptyLabel:   string
  searchLabel:  string
  unknownBadge: string
  unknownTitle: string
  disabled?:    boolean
  searchable?:  boolean
}) {
  const [open, setOpen]     = useState(false)
  const [query, setQuery]   = useState('')
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  const known = useMemo(() => new Set(options.map(o => o.value)), [options])
  // Selecionado que o mapa não conhece. É informação, não erro: pode ser um campo
  // que o tenant declarou noutro lugar, ou um resíduo como `service`.
  const unknownSelected = value.filter(v => !known.has(v))

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return q ? options.filter(o => o.value.toLowerCase().includes(q)) : options
  }, [options, query])

  const toggle = (v: string) =>
    onChange(value.includes(v) ? value.filter(x => x !== v) : [...value, v])

  return (
    <div ref={ref} className="relative">
      {/* Chips do que está selecionado */}
      <div
        className={`w-full min-h-[2.25rem] border border-border rounded px-2 py-1.5 flex flex-wrap items-center gap-1 ${
          disabled ? 'bg-surface-muted' : 'cursor-pointer'
        }`}
        onClick={() => !disabled && setOpen(o => !o)}
      >
        {value.length === 0 && <span className="text-sm text-muted-light">{placeholder}</span>}
        {value.map(v => {
          const opt = options.find(o => o.value === v)
          const isUnknown = !known.has(v)
          return (
            <span
              key={v}
              title={isUnknown ? unknownTitle : (opt?.hint ?? v)}
              className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-mono ${
                isUnknown
                  ? 'bg-warning/10 text-warning border border-warning/30'
                  : opt?.legacy
                    ? 'bg-surface-muted text-gray border border-border'
                    : 'bg-primary/10 text-primary border border-primary/20'
              }`}
            >
              {v}
              {isUnknown && <span className="not-italic">⚠</span>}
              <button
                type="button"
                aria-label={`remover ${v}`}
                className="opacity-60 hover:opacity-100"
                onClick={e => { e.stopPropagation(); onChange(value.filter(x => x !== v)) }}
              >×</button>
            </span>
          )
        })}
        <span className="ml-auto text-border-strong flex-shrink-0">▾</span>
      </div>

      {/* Aviso fora do dropdown: tem de ser visível SEM abrir o seletor. */}
      {unknownSelected.length > 0 && (
        <p className="text-xs text-warning mt-1">⚠ {unknownTitle}</p>
      )}

      {open && !disabled && (
        <div className="absolute z-30 mt-1 w-full max-h-72 overflow-auto bg-white border border-border rounded shadow-lg py-1">
          {searchable && (
            <div className="px-2 pb-1 sticky top-0 bg-white">
              <input
                type="text"
                autoFocus
                placeholder={searchLabel}
                value={query}
                onChange={e => setQuery(e.target.value)}
                onClick={e => e.stopPropagation()}
                className="w-full border border-border rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-primary"
              />
            </div>
          )}
          {filtered.length === 0 ? (
            <div className="px-3 py-2 text-xs text-muted-light">{emptyLabel}</div>
          ) : filtered.map(o => (
            <label
              key={o.value}
              title={o.hint}
              className="flex items-center gap-2 px-3 py-1.5 text-xs hover:bg-surface-muted cursor-pointer"
            >
              <input
                type="checkbox"
                checked={value.includes(o.value)}
                onChange={() => toggle(o.value)}
                className="accent-primary flex-shrink-0"
              />
              <span className="font-mono truncate flex-1">{o.value}</span>
              {o.badge && (
                <span className={`text-[10px] px-1 rounded flex-shrink-0 ${
                  o.legacy ? 'bg-surface-muted text-gray' : 'bg-primary/10 text-primary'
                }`}>{o.badge}</span>
              )}
            </label>
          ))}
          {/* Os desconhecidos aparecem no fim, selecionáveis para REMOÇÃO — sem isto,
              um valor legado só sairia pelo × do chip, que é fácil de não achar. */}
          {unknownSelected.length > 0 && !query && (
            <div className="border-t border-border mt-1 pt-1">
              {unknownSelected.map(v => (
                <label key={v} title={unknownTitle}
                  className="flex items-center gap-2 px-3 py-1.5 text-xs hover:bg-surface-muted cursor-pointer">
                  <input type="checkbox" checked onChange={() => toggle(v)} className="accent-warning flex-shrink-0" />
                  <span className="font-mono truncate flex-1 text-warning">{v}</span>
                  <span className="text-[10px] px-1 rounded bg-warning/10 text-warning flex-shrink-0">{unknownBadge}</span>
                </label>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
