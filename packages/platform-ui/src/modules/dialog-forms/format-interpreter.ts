/**
 * format-interpreter.ts — o interpretador do catálogo de formatos no Console.
 *
 * É a segunda das três implementações previstas pela §D2 do
 * `adr-dialog-input-format-catalog`, e a duplicação é DECLARADA, não acidental:
 * as três superfícies (engine em Node, este app React, e o `<script>` inline
 * servido por Python na página de survey) não compartilham import — foi por
 * isso que `evaluateAskWhen` acabou com três cópias, por topologia e não por
 * desleixo.
 *
 * O que impede isto de virar aquilo:
 *
 *   · a POLÍTICA é dado — `shape`, `mask`, `inputmode`, `maxlength` e a mensagem
 *     de erro vêm todos da entrada de catálogo, lida do config-api. Só as
 *     PRIMITIVAS semânticas são código, e são cinco, num conjunto fechado.
 *   · cada entrada carrega seus VETORES de conformidade, e o gate das três
 *     superfícies roda os mesmos vetores contra as três implementações. Uma
 *     `cpf_checkdigit` errada aqui REPROVA, em vez de divergir em silêncio.
 *
 * ⚠️ Este arquivo não importa React de propósito: precisa rodar em Node, sem
 * DOM, para o gate poder interrogá-lo.
 */
import type { FormatEntry } from './catalog-hooks'

export type RejeicaoFormato = 'unknown_format' | 'shape' | 'semantic'

export interface VeredictoFormato {
  ok:      boolean
  reason?: RejeicaoFormato
}

// ── primitivas semânticas — conjunto FECHADO, espelho de dialog-format.ts ────

function ehDataCalendario(s: string): boolean {
  const m = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(s)
  if (!m) return false
  const d = Number(m[1]), mo = Number(m[2]), y = Number(m[3])
  if (mo < 1 || mo > 12 || d < 1) return false
  return d <= new Date(y, mo, 0).getDate()
}

function ehHora(s: string): boolean {
  const m = /^(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(s)
  if (!m) return false
  const h = Number(m[1]), mi = Number(m[2]), se = m[3] === undefined ? 0 : Number(m[3])
  return h <= 23 && mi <= 59 && se <= 59
}

function ehCpf(s: string): boolean {
  const d = s.replace(/\D/g, '')
  if (d.length !== 11) return false
  if (/^(\d)\1{10}$/.test(d)) return false
  const dv = (ate: number): number => {
    let soma = 0
    for (let i = 0; i < ate; i++) soma += Number(d[i]) * (ate + 1 - i)
    const r = (soma * 10) % 11
    return r === 10 ? 0 : r
  }
  return dv(9) === Number(d[9]) && dv(10) === Number(d[10])
}

function ehLuhn(s: string): boolean {
  const d = s.replace(/\D/g, '')
  if (d.length < 12) return false
  let soma = 0, alt = false
  for (let i = d.length - 1; i >= 0; i--) {
    let n = Number(d[i])
    if (alt) { n *= 2; if (n > 9) n -= 9 }
    soma += n
    alt = !alt
  }
  return soma % 10 === 0
}

function ehMesAno(s: string): boolean {
  const m = /^(\d{2})\/(\d{2})$/.exec(s)
  if (!m) return false
  const mo = Number(m[1])
  return mo >= 1 && mo <= 12
}

const SEMANTICAS: Record<string, (s: string) => boolean> = {
  none:           () => true,
  calendar_date:  ehDataCalendario,
  clock_time:     ehHora,
  cpf_checkdigit: ehCpf,
  luhn:           ehLuhn,
  month_year:     ehMesAno,
}

// ── veredicto ────────────────────────────────────────────────────────────────

/**
 * Julga um valor contra uma entrada de catálogo.
 *
 * ⚠️ **Semântica desconhecida RECUSA.** Se o catálogo nomear uma checagem que
 * esta superfície não implementa, aceitar seria fingir que julgou — e a versão
 * "aceita o que não sei checar" é fail-open com a cara de tolerância.
 */
export function julgaFormato(valor: string, entry: FormatEntry | undefined): VeredictoFormato {
  if (!entry) return { ok: false, reason: 'unknown_format' }
  const s = valor ?? ''
  const shape = entry.verdict?.shape
  if (shape) {
    let re: RegExp
    try { re = new RegExp(shape) } catch { return { ok: false, reason: 'shape' } }
    if (!re.test(s)) return { ok: false, reason: 'shape' }
  }
  const nome = entry.verdict?.semantic ?? 'none'
  const fn = SEMANTICAS[nome]
  if (!fn) return { ok: false, reason: 'semantic' }
  if (!fn(s)) return { ok: false, reason: 'semantic' }
  return { ok: true }
}

/**
 * Julga um valor contra a declaração INTEIRA de um campo: o formato primeiro,
 * os campos estreitos depois. A ordem é a mesma do engine — um `max_length` da
 * pergunta APERTA o do formato, nunca o afrouxa.
 *
 * `formatId` ausente ⇒ só os campos estreitos. Nenhuma regra ⇒ passa: não
 * inventamos validação onde ninguém declarou.
 */
export function julgaDeclaracao(
  valor:   string,
  decl:    { format?: string; numeric?: boolean; min_length?: number; max_length?: number; min?: number; max?: number } | undefined,
  catalogo: FormatEntry[],
): VeredictoFormato {
  if (!decl) return { ok: true }
  const s = valor ?? ''
  if (decl.format) {
    const v = julgaFormato(s, catalogo.find(f => f.id === decl.format))
    if (!v.ok) return v
  }
  if (decl.numeric && (s.trim() === '' || Number.isNaN(Number(s)))) return { ok: false, reason: 'shape' }
  if (decl.min_length !== undefined && s.length < decl.min_length) return { ok: false, reason: 'shape' }
  if (decl.max_length !== undefined && s.length > decl.max_length) return { ok: false, reason: 'shape' }
  if (decl.min !== undefined || decl.max !== undefined) {
    const n = Number(s)
    if (Number.isNaN(n)) return { ok: false, reason: 'shape' }
    if (decl.min !== undefined && n < decl.min) return { ok: false, reason: 'shape' }
    if (decl.max !== undefined && n > decl.max) return { ok: false, reason: 'shape' }
  }
  return { ok: true }
}

// ── afordância ───────────────────────────────────────────────────────────────

/**
 * Aplica a máscara de digitação (`#` = um dígito) ao que a pessoa digitou.
 *
 * Ela GUIA, e por isso não pode brigar com quem digita: só os dígitos do valor
 * são consumidos, os separadores da máscara entram sozinhos, e o excedente é
 * descartado. Uma máscara que rejeitasse tecla viraria veredicto disfarçado —
 * e veredicto tem hora e lugar (o submit), com mensagem própria.
 */
export function aplicaMascara(valor: string, mask: string | undefined): string {
  if (!mask) return valor
  const digitos = (valor ?? '').replace(/\D/g, '')
  let saida = ''
  let i = 0
  for (const c of mask) {
    if (i >= digitos.length) break
    if (c === '#') { saida += digitos[i]; i++ } else { saida += c }
  }
  return saida
}

/** Comprimento máximo efetivo: o da declaração aperta o da máscara. */
export function maxEfetivo(entry: FormatEntry | undefined, declMax: number | undefined): number | undefined {
  const doCatalogo = entry?.affordance?.maxlength
  if (doCatalogo === undefined) return declMax
  if (declMax === undefined) return doCatalogo
  return Math.min(doCatalogo, declMax)
}

/**
 * Roda os vetores de conformidade de uma entrada contra ESTA implementação.
 * Exportado para o gate das três superfícies — é o que impede as primitivas de
 * divergirem entre runtimes sem nada ficar vermelho.
 */
export function conferemVetores(entry: FormatEntry & { vectors?: { valid?: string[]; invalid?: string[] } }): {
  ok: boolean
  falhas: string[]
} {
  const falhas: string[] = []
  for (const v of entry.vectors?.valid ?? []) {
    if (!julgaFormato(v, entry).ok) falhas.push(`valido recusado: ${JSON.stringify(v)}`)
  }
  for (const v of entry.vectors?.invalid ?? []) {
    if (julgaFormato(v, entry).ok) falhas.push(`invalido aceito: ${JSON.stringify(v)}`)
  }
  return { ok: falhas.length === 0, falhas }
}
