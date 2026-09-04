/**
 * dialog-format.ts
 * Catálogo de FORMATOS DE ENTRADA do DialogForm — a metade que faltava do campo
 * de coleta. Ver `docs/adr/adr-dialog-input-format-catalog.md`.
 *
 * `validation.format` NOMEIA uma entrada daqui, e a entrada carrega as duas
 * metades que antes não tinham casa:
 *
 *   afordância — máscara de digitação, `inputmode`, `maxlength`, placeholder.
 *                Guia quem digita. NÃO autoriza (D7).
 *   veredicto  — DOIS níveis (D4): `shape` (regex ancorada) e `semantic`
 *                (função nomeada de um conjunto FECHADO). Os dois níveis existem
 *                porque `^\d{2}/\d{2}/\d{4}$` aceita `31/02/2026`, e calendário
 *                não é linguagem regular.
 *
 * ── Por que o catálogo é DADO e não código ───────────────────────────────────
 * São três superfícies que precisam do mesmo veredicto — engine (Node), Console
 * (React) e a página de survey (`<script>` inline servido por Python) — e não
 * existe import que atravesse as três. `evaluateAskWhen` é função PURA neste
 * mesmo pacote e mesmo assim tem três implementações, por topologia e não por
 * desleixo. Política em código herdaria isso.
 *
 * ⚠️ **A honestidade da afirmação acima tem um limite, e ele está declarado:** o
 * `shape` é dado (string de regex) e atravessa qualquer runtime; a função
 * `semantic` é CÓDIGO e cada runtime precisa da sua. O que impede isso de virar
 * o `askWhen` de novo são duas coisas — o conjunto é **fechado**
 * (`DialogSemanticCheckSchema`, hoje 5 nomes), e cada entrada carrega os seus
 * **vetores de conformidade** (`vectors`), de modo que um runtime que
 * implemente `cpf_checkdigit` errado REPROVA contra a mesma tabela em vez de
 * divergir em silêncio. A política (qual formato usa qual checagem) segue sendo
 * dado; só a primitiva é código.
 */

import { z } from "zod"
import { LocalizedTextSchema, type LocalizedText } from "./dialog"
import { DEFAULT_DATA_TYPE_CATALOG, type DataTypeCatalog } from "./audit"

// ─────────────────────────────────────────────
// Afordância — guia a digitação, nunca autoriza
// ─────────────────────────────────────────────

/**
 * Teclado sugerido ao cliente. Espelha os valores do atributo HTML `inputmode`
 * porque é o vocabulário que as duas superfícies web já falam; o adapter de
 * canal traduz para o que o canal dele suporta (voz ignora, WhatsApp ignora).
 * É SUGESTÃO — nenhum canal é obrigado a honrar, e nenhum veredicto depende.
 */
export const DialogInputModeSchema = z.enum([
  "text",
  "numeric",
  "decimal",
  "tel",
  "email",
])
export type DialogInputMode = z.infer<typeof DialogInputModeSchema>

export const DialogFormatAffordanceSchema = z.object({
  /**
   * Máscara de digitação, `#` = um dígito (ex.: `##/##/####`). Mesmo vocabulário
   * de `DataTypeFormat.display`, de propósito — é a mesma grandeza vista de dois
   * lados (digitar × redigir), e usar dois vocabulários faria a herança da D3
   * precisar de tradução.
   *
   * ⚠️ AUSENTE em entrada com `from_masked_type`: lá a máscara é HERDADA, e
   * declarar as duas é a duplicação que a D3 existe para impedir. O oráculo
   * reprova.
   */
  mask:        z.string().optional(),
  inputmode:   DialogInputModeSchema.optional(),
  maxlength:   z.number().int().positive().optional(),
  /** Exemplo de forma mostrado ao cliente (ex.: "dd/mm/aaaa"). */
  placeholder: LocalizedTextSchema.optional(),
})
export type DialogFormatAffordance = z.infer<typeof DialogFormatAffordanceSchema>

// ─────────────────────────────────────────────
// Veredicto — dois níveis (D4)
// ─────────────────────────────────────────────

/**
 * Checagens semânticas — conjunto FECHADO. Fechado e não string livre porque
 * um nome livre que nenhum runtime implementa não teria como ser recusado no
 * catálogo, e o interpretador cairia no ramo *"não sei checar"*, que é sempre
 * um fail-open disfarçado.
 *
 * `none` é explícito, e não a ausência: uma entrada que só checa forma
 * DECLARA isso, para que a ausência de checagem não se pareça com aprovação.
 */
export const DialogSemanticCheckSchema = z.enum([
  "none",
  /** dd/mm/aaaa que EXISTE no calendário (rejeita 31/02, 29/02 em não-bissexto). */
  "calendar_date",
  /** hh:mm[:ss] com faixas válidas. */
  "clock_time",
  /** CPF: dois dígitos verificadores + rejeita repetição trivial (000…, 111…). */
  "cpf_checkdigit",
  /** Luhn — cartão de crédito. */
  "luhn",
  /** MM/AA com mês em 01..12. NÃO checa se está vencido: vencimento é REGRA DE
   *  NEGÓCIO (depende de relógio), e formato não depende de quando é executado. */
  "month_year",
])
export type DialogSemanticCheck = z.infer<typeof DialogSemanticCheckSchema>

export const DialogFormatVerdictSchema = z.object({
  /**
   * Regex de FORMA, obrigatoriamente ANCORADA (`^…$`). O oráculo reprova sem
   * âncora — sem ela a regex é um *finder* e não um validador, e é exatamente
   * assim que `detect_pattern` do catálogo de mascaramento aceitaria
   * `meu cpf é 111.222.333-44 obrigado`.
   */
  shape:    z.string().optional(),
  semantic: DialogSemanticCheckSchema.default("none"),
  /** Mensagem ao cliente quando o veredicto recusa. */
  error:    LocalizedTextSchema.optional(),
})
export type DialogFormatVerdict = z.infer<typeof DialogFormatVerdictSchema>

// ─────────────────────────────────────────────
// Entrada e catálogo
// ─────────────────────────────────────────────

/**
 * Vetores de conformidade — o mecanismo que impede a primitiva `semantic` de
 * divergir entre runtimes. Cada superfície roda os mesmos vetores contra a sua
 * implementação; divergência REPROVA nomeando o formato e o valor.
 *
 * Vivem na ENTRADA e não no teste porque um teste que carrega os próprios
 * vetores testa a si mesmo: só há conformidade se os três lerem a MESMA tabela.
 */
export const DialogFormatVectorsSchema = z.object({
  valid:   z.array(z.string()).default([]),
  invalid: z.array(z.string()).default([]),
})
export type DialogFormatVectors = z.infer<typeof DialogFormatVectorsSchema>

export const DialogFormatEntrySchema = z.object({
  /** Id nomeado por `validation.format`, snake_case. */
  id:    z.string().regex(/^[a-z0-9_]+$/, { message: "format id must be snake_case (a-z0-9_)" }),
  label: LocalizedTextSchema.optional(),
  /**
   * Vínculo com o catálogo de MASCARAMENTO (`masking.types`), quando o formato
   * tem contraparte lá. Duas consequências, e são a D3 e a D8:
   *   · a máscara é HERDADA de `masking.types.<id>.formato.display` (D3);
   *   · `masked: "<id>"` num campo DERIVA este formato (D8), sem o autor
   *     declarar duas vezes.
   *
   * ⚠️ Os ids NÃO coincidem sempre — o tipo mascarado é `email_addr` e o
   * formato é `email`. É por isso que o vínculo é este campo e não a igualdade
   * de nome: igualdade de nome é convenção, e convenção quebra sem ficar
   * vermelha.
   */
  from_masked_type: z.string().optional(),
  affordance:       DialogFormatAffordanceSchema.default({}),
  verdict:          DialogFormatVerdictSchema.default({ semantic: "none" }),
  vectors:          DialogFormatVectorsSchema.default({ valid: [], invalid: [] }),
})
export type DialogFormatEntry = z.infer<typeof DialogFormatEntrySchema>

/**
 * DialogFormatCatalog — armazenado no Config API, namespace `dialog`, chave
 * `formats`. Fonte de verdade é o config-api; o seed apenas semeia base vazia,
 * mesma regra do `masking.types` (seed-if-absent / DB-owned).
 */
export const DialogFormatCatalogSchema = z.object({
  formats: z.array(DialogFormatEntrySchema).default([]),
})
export type DialogFormatCatalog = z.infer<typeof DialogFormatCatalogSchema>

// ─────────────────────────────────────────────
// O catálogo semeado
// ─────────────────────────────────────────────

/**
 * DEFAULT_DIALOG_FORMAT_CATALOG — o catálogo global semeado.
 *
 * **Contém apenas o que tem razão de existir HOJE**, pela mesma disciplina que
 * expulsou `iban`/`passport` do catálogo de mascaramento: formato que nenhum
 * caso pede é fantasma, não item de backlog. As entradas aqui saem de duas
 * fontes, e nenhuma é invenção:
 *
 *   · o pedido de produto — domínio (numérico/alfanumérico/texto), data, hora e
 *     comprimento;
 *   · contraparte existente em `masking.types` — `cpf`, `credit_card`, `phone`,
 *     `card_expiry`, `email_addr`, que já carregam a máscara.
 *
 * `cep`, `cnpj` e afins ficam de FORA até alguém precisar. Acrescentar é uma
 * linha; remover depois de virar contrato, não.
 */
export const DEFAULT_DIALOG_FORMAT_CATALOG: DialogFormatCatalog = {
  formats: [
    // ── autossuficientes (sem contraparte de mascaramento) ───────────────────
    {
      id: "text", label: "Texto livre",
      affordance: { inputmode: "text" },
      verdict: { semantic: "none" },
      vectors: { valid: ["qualquer coisa", "123"], invalid: [] },
    },
    {
      id: "digits", label: "Somente dígitos",
      affordance: { inputmode: "numeric" },
      verdict: {
        shape: "^[0-9]+$", semantic: "none",
        error: { "pt-BR": "Use somente números.", en: "Digits only." },
      },
      vectors: { valid: ["0", "123456"], invalid: ["12a", "1 2", "", "-1"] },
    },
    {
      id: "integer", label: "Número inteiro",
      affordance: { inputmode: "numeric" },
      verdict: {
        shape: "^-?[0-9]+$", semantic: "none",
        error: { "pt-BR": "Informe um número inteiro.", en: "Enter a whole number." },
      },
      vectors: { valid: ["0", "-7", "42"], invalid: ["1.5", "1,5", "abc", ""] },
    },
    {
      id: "decimal", label: "Número decimal",
      affordance: { inputmode: "decimal" },
      verdict: {
        // Vírgula E ponto: o separador decimal é fato de LOCALE e a normalização
        // é do consumidor. Recusar vírgula aqui reprovaria a metade lusófona dos
        // clientes por uma escolha de representação que não é do formato.
        shape: "^-?[0-9]+([.,][0-9]+)?$", semantic: "none",
        error: { "pt-BR": "Informe um número.", en: "Enter a number." },
      },
      vectors: { valid: ["0", "-7", "1.5", "1,5", "1234"], invalid: ["1.2.3", "R$ 10", ""] },
    },
    {
      id: "alphanumeric", label: "Letras e números",
      affordance: { inputmode: "text" },
      verdict: {
        shape: "^[A-Za-z0-9]+$", semantic: "none",
        error: { "pt-BR": "Use apenas letras e números, sem espaços.",
                 en: "Letters and digits only, no spaces." },
      },
      vectors: { valid: ["AB12", "abc", "999"], invalid: ["AB 12", "ab-12", "ção", ""] },
    },
    {
      id: "date_br", label: "Data (dd/mm/aaaa)",
      affordance: {
        mask: "##/##/####", inputmode: "numeric", maxlength: 10,
        placeholder: { "pt-BR": "dd/mm/aaaa", en: "dd/mm/yyyy" },
      },
      verdict: {
        shape: "^[0-9]{2}/[0-9]{2}/[0-9]{4}$", semantic: "calendar_date",
        error: { "pt-BR": "Informe uma data válida no formato dd/mm/aaaa.",
                 en: "Enter a valid date as dd/mm/yyyy." },
      },
      // `31/02/2026` e `29/02/2026` casam a FORMA e são recusados pela semântica —
      // é o par que prova que os dois níveis não são o mesmo nível.
      vectors: {
        valid:   ["01/01/2026", "29/02/2024", "31/12/1999"],
        invalid: ["31/02/2026", "29/02/2026", "00/01/2026", "01/13/2026", "1/1/2026", ""],
      },
    },
    {
      id: "time_hm", label: "Hora (hh:mm)",
      affordance: {
        mask: "##:##", inputmode: "numeric", maxlength: 5,
        placeholder: { "pt-BR": "hh:mm", en: "hh:mm" },
      },
      verdict: {
        shape: "^[0-9]{2}:[0-9]{2}$", semantic: "clock_time",
        error: { "pt-BR": "Informe um horário válido (hh:mm).",
                 en: "Enter a valid time (hh:mm)." },
      },
      vectors: { valid: ["00:00", "23:59"], invalid: ["24:00", "12:60", "9:30", ""] },
    },
    {
      id: "time_hms", label: "Hora com segundos (hh:mm:ss)",
      affordance: {
        mask: "##:##:##", inputmode: "numeric", maxlength: 8,
        placeholder: { "pt-BR": "hh:mm:ss", en: "hh:mm:ss" },
      },
      verdict: {
        shape: "^[0-9]{2}:[0-9]{2}:[0-9]{2}$", semantic: "clock_time",
        error: { "pt-BR": "Informe um horário válido (hh:mm:ss).",
                 en: "Enter a valid time (hh:mm:ss)." },
      },
      vectors: { valid: ["00:00:00", "23:59:59"], invalid: ["23:59:60", "24:00:00", ""] },
    },

    // ── com contraparte em `masking.types` — máscara HERDADA, nunca repetida ──
    {
      id: "cpf", label: "CPF", from_masked_type: "cpf",
      affordance: { inputmode: "numeric", maxlength: 14 },
      verdict: {
        shape: "^[0-9]{3}\\.[0-9]{3}\\.[0-9]{3}-[0-9]{2}$", semantic: "cpf_checkdigit",
        error: { "pt-BR": "CPF inválido.", en: "Invalid CPF." },
      },
      // `000.000.000-00` casa a forma e falha o dígito — o mesmo par do date_br.
      vectors: {
        valid:   ["529.982.247-25", "111.444.777-35"],
        invalid: ["000.000.000-00", "111.111.111-11", "529.982.247-26", "52998224725", ""],
      },
    },
    {
      id: "credit_card", label: "Cartão de crédito", from_masked_type: "credit_card",
      affordance: { inputmode: "numeric", maxlength: 19 },
      verdict: {
        shape: "^[0-9]{4} [0-9]{4} [0-9]{4} [0-9]{4}$", semantic: "luhn",
        error: { "pt-BR": "Número de cartão inválido.", en: "Invalid card number." },
      },
      vectors: {
        valid:   ["4539 1488 0343 6467", "5500 0055 5555 5559"],
        invalid: ["4539 1488 0343 6468", "1234 5678 9012 3456", ""],
      },
    },
    {
      id: "card_expiry", label: "Vencimento do cartão", from_masked_type: "card_expiry",
      affordance: { inputmode: "numeric", maxlength: 5,
                    placeholder: { "pt-BR": "mm/aa", en: "mm/yy" } },
      verdict: {
        shape: "^[0-9]{2}/[0-9]{2}$", semantic: "month_year",
        error: { "pt-BR": "Informe o vencimento como mm/aa.", en: "Enter expiry as mm/yy." },
      },
      vectors: { valid: ["01/26", "12/30"], invalid: ["13/26", "00/26", "1/26", ""] },
    },
    {
      // ⚠️ A máscara herdada (`(##) #####-####`) é de CELULAR, 11 dígitos. Fixo
      // tem 10 e não cabe nela. A limitação é do catálogo de mascaramento e é
      // herdada junto — está aqui declarada para não ser descoberta em campo.
      id: "phone_br", label: "Telefone (Brasil)", from_masked_type: "phone",
      affordance: { inputmode: "tel", maxlength: 15 },
      verdict: {
        shape: "^\\([0-9]{2}\\) [0-9]{4,5}-[0-9]{4}$", semantic: "none",
        error: { "pt-BR": "Informe o telefone com DDD.", en: "Enter the phone with area code." },
      },
      vectors: {
        valid:   ["(11) 98765-4321", "(11) 3456-7890"],
        invalid: ["11987654321", "(11) 987-654", ""],
      },
    },
    {
      id: "email", label: "E-mail", from_masked_type: "email_addr",
      affordance: { inputmode: "email" },
      verdict: {
        shape: "^[A-Za-z0-9._%+\\-]+@[A-Za-z0-9.\\-]+\\.[A-Za-z]{2,}$", semantic: "none",
        error: { "pt-BR": "Informe um e-mail válido.", en: "Enter a valid email." },
      },
      vectors: {
        valid:   ["a@b.co", "nome.sobrenome+tag@exemplo.com.br"],
        invalid: ["a@b", "sem-arroba.com", "a b@c.co", ""],
      },
    },
  ],
}

// ─────────────────────────────────────────────
// Primitivas semânticas — o único código, e ele é fechado
// ─────────────────────────────────────────────

/** dd/mm/aaaa que existe no calendário. Assume a FORMA já validada. */
function isCalendarDate(s: string): boolean {
  const m = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(s)
  if (!m) return false
  const d = Number(m[1]), mo = Number(m[2]), y = Number(m[3])
  if (mo < 1 || mo > 12 || d < 1) return false
  // `new Date(y, mo, 0).getDate()` = último dia do mês `mo` (1-based), e cobre
  // bissexto sem tabela — o mês 0 do ano seguinte é dezembro, então o índice
  // fecha sozinho.
  return d <= new Date(y, mo, 0).getDate()
}

/** hh:mm ou hh:mm:ss com faixas válidas. */
function isClockTime(s: string): boolean {
  const m = /^(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(s)
  if (!m) return false
  const h = Number(m[1]), mi = Number(m[2]), se = m[3] === undefined ? 0 : Number(m[3])
  return h <= 23 && mi <= 59 && se <= 59
}

/** CPF: dois dígitos verificadores; repetição trivial é recusada. */
function isCpf(s: string): boolean {
  const d = s.replace(/\D/g, "")
  if (d.length !== 11) return false
  if (/^(\d)\1{10}$/.test(d)) return false
  const dv = (upTo: number): number => {
    let soma = 0
    for (let i = 0; i < upTo; i++) soma += Number(d[i]) * (upTo + 1 - i)
    const r = (soma * 10) % 11
    return r === 10 ? 0 : r
  }
  return dv(9) === Number(d[9]) && dv(10) === Number(d[10])
}

/** Luhn. */
function isLuhn(s: string): boolean {
  const d = s.replace(/\D/g, "")
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

/** MM/AA com mês em 01..12. Vencido ou não é regra de negócio, não formato. */
function isMonthYear(s: string): boolean {
  const m = /^(\d{2})\/(\d{2})$/.exec(s)
  if (!m) return false
  const mo = Number(m[1])
  return mo >= 1 && mo <= 12
}

const SEMANTIC_FNS: Record<DialogSemanticCheck, (s: string) => boolean> = {
  none:           () => true,
  calendar_date:  isCalendarDate,
  clock_time:     isClockTime,
  cpf_checkdigit: isCpf,
  luhn:           isLuhn,
  month_year:     isMonthYear,
}

// ─────────────────────────────────────────────
// Interpretador
// ─────────────────────────────────────────────

/** Por que o veredicto recusou. Nunca um booleano pelado: a recusa tem de saber
 *  dizer QUAL nível reprovou, senão a mensagem ao cliente é adivinhação. */
export type DialogFormatRejection =
  | "unknown_format"   // o nome não existe no catálogo — recusa ALTO, ver abaixo
  | "shape"
  | "semantic"

export interface DialogFormatResult {
  ok:      boolean
  reason?: DialogFormatRejection
  /** Mensagem declarada na entrada, não resolvida (o chamador conhece o locale). */
  error?:  LocalizedText
}

/** Índice id → entrada. */
export function indexDialogFormats(
  catalog: DialogFormatCatalog = DEFAULT_DIALOG_FORMAT_CATALOG,
): Map<string, DialogFormatEntry> {
  return new Map(catalog.formats.map(f => [f.id, f]))
}

/**
 * validateDialogFormat — o interpretador. Puro, determinístico, sem I/O.
 *
 * ⚠️ **Formato desconhecido RECUSA**, não passa. É a escolha oposta à do
 * `validateFormat` que este catálogo substitui, onde regex inválida caía num
 * `catch` e liberava tudo. Um nome que o catálogo não conhece significa forma
 * publicada contra catálogo que mudou — e nesse estado *"aceita qualquer
 * coisa"* é o pior desfecho possível para um campo que alguém restringiu de
 * propósito.
 */
export function validateDialogFormat(
  value:   string,
  formatId: string,
  catalog: DialogFormatCatalog = DEFAULT_DIALOG_FORMAT_CATALOG,
): DialogFormatResult {
  const entry = indexDialogFormats(catalog).get(formatId)
  if (!entry) return { ok: false, reason: "unknown_format" }

  const s = value ?? ""
  if (entry.verdict.shape) {
    let re: RegExp
    try {
      re = new RegExp(entry.verdict.shape)
    } catch {
      // Entrada de catálogo com regex quebrada é defeito de CATÁLOGO, e o
      // oráculo o pega antes de semear. Se chegou aqui, recusa — nunca libera.
      return { ok: false, reason: "shape", error: entry.verdict.error }
    }
    if (!re.test(s)) return { ok: false, reason: "shape", error: entry.verdict.error }
  }

  const fn = SEMANTIC_FNS[entry.verdict.semantic]
  if (!fn(s)) return { ok: false, reason: "semantic", error: entry.verdict.error }

  return { ok: true }
}

/**
 * resolveFormatMask — a HERANÇA da D3. Entrada com `from_masked_type` não
 * declara máscara própria; ela vem de `masking.types.<id>.formato.display`.
 *
 * Existir como função (e não como campo já resolvido no catálogo) é o que torna
 * *provável por construção* que a máscara com que o cliente digita o CPF seja a
 * mesma com que aquele CPF é redigido no histórico: há uma string, num lugar.
 */
export function resolveFormatMask(
  entry:        DialogFormatEntry,
  maskedTypes:  DataTypeCatalog = DEFAULT_DATA_TYPE_CATALOG,
): string | undefined {
  if (entry.affordance.mask) return entry.affordance.mask
  if (!entry.from_masked_type) return undefined
  return maskedTypes.types.find(t => t.id === entry.from_masked_type)?.formato.display
}

/**
 * formatForMaskedType — a derivação da D8, lida do outro lado do vínculo:
 * dado `masked: "cpf"` num campo, qual formato vale sem o autor declarar de novo?
 *
 * Devolve `undefined` quando o tipo mascarado não tem contraparte — e isso é um
 * desfecho LEGÍTIMO, não uma falha: `credential` e `opaque` mascaram sem
 * formatar. Mascarar e formatar são eixos ortogonais.
 */
export function formatForMaskedType(
  maskedTypeId: string,
  catalog:      DialogFormatCatalog = DEFAULT_DIALOG_FORMAT_CATALOG,
): DialogFormatEntry | undefined {
  return catalog.formats.find(f => f.from_masked_type === maskedTypeId)
}

// ─────────────────────────────────────────────
// Oráculo do catálogo
// ─────────────────────────────────────────────

/**
 * verifyDialogFormatCatalog — o ORÁCULO do gate da F1, exportado para não ser
 * reimplementado pelo teste (um gate que reconstrói a regra que julga testa a si
 * mesmo — mesma razão de `verifyDataTypeCatalog`).
 *
 * Devolve `declared` junto com as listas porque **listas vazias sobre catálogo
 * vazio não é aprovação**: sem a testemunha de presença, um catálogo apagado
 * passaria.
 */
export function verifyDialogFormatCatalog(
  catalog:     DialogFormatCatalog = DEFAULT_DIALOG_FORMAT_CATALOG,
  maskedTypes: DataTypeCatalog     = DEFAULT_DATA_TYPE_CATALOG,
): {
  declared: number
  /** `from_masked_type` que não existe em `masking.types`. */
  dangling_masked_ref: string[]
  /** Tipo mascarado com MAIS de um formato — a busca reversa da D8 fica ambígua. */
  ambiguous_masked_ref: string[]
  /** Declara `from_masked_type` E máscara própria — a duplicação que a D3 proíbe. */
  duplicated_mask: string[]
  /** `shape` sem âncora `^…$` — é finder, não validador (D4). */
  unanchored_shape: string[]
  /** `shape` que não compila. */
  invalid_shape: string[]
  /** Entrada sem afordância NEM veredicto — fantasma, a lição do `iban`. */
  inert_entry: string[]
  /** Entrada cujos próprios vetores reprovam contra o próprio veredicto. */
  vector_mismatch: string[]
  /** Ids repetidos. */
  duplicate_id: string[]
} {
  const dangling_masked_ref:  string[] = []
  const duplicated_mask:      string[] = []
  const unanchored_shape:     string[] = []
  const invalid_shape:        string[] = []
  const inert_entry:          string[] = []
  const vector_mismatch:      string[] = []

  const maskedIds = new Set(maskedTypes.types.map(t => t.id))
  const porMasked = new Map<string, number>()
  const vistos    = new Map<string, number>()

  for (const f of catalog.formats) {
    vistos.set(f.id, (vistos.get(f.id) ?? 0) + 1)

    if (f.from_masked_type) {
      porMasked.set(f.from_masked_type, (porMasked.get(f.from_masked_type) ?? 0) + 1)
      if (!maskedIds.has(f.from_masked_type)) dangling_masked_ref.push(f.id)
      if (f.affordance.mask) duplicated_mask.push(f.id)
    }

    if (f.verdict.shape) {
      if (!f.verdict.shape.startsWith("^") || !f.verdict.shape.endsWith("$")) {
        unanchored_shape.push(f.id)
      }
      try { new RegExp(f.verdict.shape) } catch { invalid_shape.push(f.id) }
    }

    // Fantasma: não guia, não julga, e não herda máscara de lugar nenhum.
    const guia  = !!(f.affordance.mask || f.affordance.inputmode || f.affordance.maxlength
                     || f.affordance.placeholder || f.from_masked_type)
    const julga = !!f.verdict.shape || f.verdict.semantic !== "none"
    if (!guia && !julga) inert_entry.push(f.id)

    // Os vetores da própria entrada têm de concordar com o próprio veredicto.
    // É o que impede vetor copiado de outra entrada de passar despercebido, e é
    // a base do gate das três superfícies.
    const cat1: DialogFormatCatalog = { formats: [f] }
    const okV = f.vectors.valid.every(v => validateDialogFormat(v, f.id, cat1).ok)
    const okI = f.vectors.invalid.every(v => !validateDialogFormat(v, f.id, cat1).ok)
    if (!okV || !okI) vector_mismatch.push(f.id)
  }

  return {
    declared: catalog.formats.length,
    dangling_masked_ref,
    ambiguous_masked_ref: [...porMasked.entries()].filter(([, n]) => n > 1).map(([k]) => k),
    duplicated_mask,
    unanchored_shape,
    invalid_shape,
    inert_entry,
    vector_mismatch,
    duplicate_id: [...vistos.entries()].filter(([, n]) => n > 1).map(([k]) => k),
  }
}
