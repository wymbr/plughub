import { useState, useRef, useEffect } from 'react'

/**
 * PoolMultiSelect — combo multi-selecionável de pools (checkbox dropdown).
 *
 * Arco de segurança (2026-07-23): o filtro de pool nas telas de Analytics deve oferecer
 * o DOMÍNIO do usuário (`listPools ∩ session.accessiblePools`), não texto livre.
 *
 * ⚠️ `pools` chega JÁ filtrado pelo chamador — este componente não vê `accessiblePools` e
 * por isso não sabe distinguir "o tenant não tem pool" de "eu não alcanço nenhum". Quem
 * tem essa informação é quem o usa; o aviso da AUT-10 (`EmptyScopeNotice`) mora lá.
 *
 * `value` vazio = nenhum filtro aplicado (todo o domínio), que NÃO é "todos os pools" —
 * o backend SEMPRE reintersecta com o domínio. A UI é conveniência, nunca a fronteira.
 */
export function PoolMultiSelect({ pools, value, onChange, placeholder, allLabel, countLabel }: {
  pools:       string[]
  value:       string[]
  onChange:    (ids: string[]) => void
  placeholder: string
  allLabel:    string
  countLabel:  (n: number) => string
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  const toggle = (p: string) =>
    onChange(value.includes(p) ? value.filter(x => x !== p) : [...value, p])

  const label = value.length === 0 ? allLabel
    : value.length === 1 ? value[0]
    : countLabel(value.length)

  return (
    <div ref={ref} className="relative">
      <button type="button" onClick={() => setOpen(o => !o)}
        className="text-xs border border-border-strong rounded px-2 py-1 min-w-[9rem] text-left flex items-center justify-between gap-2 focus:outline-none focus:ring-1 focus:ring-primary/40">
        <span className={value.length ? 'text-dark truncate' : 'text-muted-light'}>{label}</span>
        <span className="text-border-strong flex-shrink-0">▾</span>
      </button>
      {open && (
        <div className="absolute z-20 mt-1 w-56 max-h-64 overflow-auto bg-white border border-border rounded shadow-lg py-1">
          {pools.length === 0 ? (
            <div className="px-3 py-2 text-xs text-muted-light">{placeholder}</div>
          ) : pools.map(p => (
            <label key={p} className="flex items-center gap-2 px-3 py-1.5 text-xs hover:bg-surface-muted cursor-pointer">
              <input type="checkbox" checked={value.includes(p)} onChange={() => toggle(p)} className="accent-primary" />
              <span className="font-mono truncate" title={p}>{p}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  )
}
