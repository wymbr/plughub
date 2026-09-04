/**
 * ctx-audience.ts — a derivação da PLATEIA a partir do SÍTIO, e a política que
 * o tipo declara para ela. Ver `docs/adr/adr-context-read-audience-policy.md`.
 *
 * ── Por que isto é uma função pura em `@plughub/schemas` ─────────────────────
 * Dois consumidores precisam da MESMA regra e nasceriam em linguagens
 * diferentes: o censo estático sobre os YAMLs (CTX-01) e o resolvedor de runtime
 * no engine (CTX-02). Escrever a regra duas vezes é exatamente o defeito que
 * este arco existe para corrigir — e teria a agravante de a cópia estática
 * dizer que está tudo bem enquanto a de runtime deixa passar.
 *
 * Por isso o censo é TypeScript e importa daqui, em vez de ser um script Python
 * que reimplementa a derivação.
 *
 * ── O que esta casa NÃO decide ───────────────────────────────────────────────
 * Nada sobre tag DESCONHECIDA (§D4 — é a V4 do ADR da allowlist) e nada sobre
 * detecção em texto livre (§D8). Aqui só se responde: *dado um sítio, para quem
 * o valor vai?* e *dado um tipo e uma plateia, o que ela pode ver?*
 */

import { z } from "zod"
import {
  DEFAULT_DATA_TYPE_CATALOG,
  DEFAULT_MASKING_RULES,
  type DataTypeCatalog, type DataType, type ContextMaskingType,
  type DataCategory as ContextMaskingCategory,
} from "./audit"

// ─────────────────────────────────────────────
// Plateia
// ─────────────────────────────────────────────

/**
 * Para quem o valor interpolado vai.
 *
 *   customer — texto que chega ao cliente
 *   operator — texto que chega só a agentes/supervisores
 *   system   — argumento de tool (o CRM PRECISA do valor inteiro)
 *   model    — prompt de LLM. Plateia PRÓPRIA (§D5): o prompt SAI da
 *              plataforma, e mandar um CPF ao provedor de modelo é outro fato
 *              que mostrá-lo a um operador logado. Dobrá-lo em `operator`
 *              escolheria a política mais frouxa por conveniência de tabela.
 *   none     — o valor não é renderizado para ninguém (`choice`, `complete`…);
 *              não há eco a filtrar.
 */
export const CtxAudienceSchema = z.enum(["customer", "operator", "system", "model", "none"])
export type CtxAudience = z.infer<typeof CtxAudienceSchema>

/**
 * deriveAudience — a plateia sai do SÍTIO, nunca da tag.
 *
 * Declarar a plateia em cada interpolação seriam ~392 declarações e ~392
 * chances de esquecer, e a que faltasse degradaria para o permissivo. O sítio já
 * carrega a informação: `visibility` é resolvido antes de todo envio.
 *
 * ⚠️ **`visibility` ausente é `"all"`**, que é CLIENTE. O default do produto é o
 * permissivo, e é por isso que a ausência precisa ser tratada explicitamente
 * aqui — ler `undefined` como "não sei" faria a maioria dos sítios escapar do
 * filtro justamente por não terem declarado nada.
 *
 * Corolário medido: a mesma tag num skill de WORKFLOW e num de AGENTE tem
 * plateias diferentes. Em 2026-09-04, 4 dos 20 pontos sensíveis estavam em
 * `skill_limite_processo_v1`, que tem ZERO steps `notify`/`menu` — não há eco a
 * filtrar ali, e uma regra por tag os teria reprovado.
 */
export function deriveAudience(stepType: string, visibility?: unknown): CtxAudience {
  switch (stepType) {
    case "notify":
    case "menu": {
      if (visibility === undefined || visibility === null || visibility === "all") return "customer"
      if (visibility === "agents_only") return "operator"
      // Array explícito de participant_ids: sem resolver os ids não dá para
      // saber se o cliente está dentro. RECUSA ALTO — trata como cliente, que é
      // a política mais estrita. Assumir "operador" faria um array contendo o
      // cliente escapar do filtro, e esse é o caso que a lista existe para
      // construir (visibilidade dirigida ao cliente no NPS, por exemplo).
      if (Array.isArray(visibility)) return "customer"
      // Ref `$.`/`@ctx.` não resolvida em análise estática — mesma postura.
      return "customer"
    }
    case "invoke":  return "system"
    case "reason":  return "model"
    default:        return "none"
  }
}

// ─────────────────────────────────────────────
// Política do tipo para a plateia
// ─────────────────────────────────────────────

/**
 * ⚠️ **O EIXO desta seção foi TROCADO em 2026-09-04, horas depois de escrita.**
 *
 * A primeira versão leu `mascara.display.echo_to_customer` / `echo_to_operator`
 * e devolveu um modo (`none` | `masked` | `plain`). Ao medir para começar a F3,
 * apareceu que **aquele campo responde outra pergunta** — e o próprio schema já
 * dizia (`audit.ts:342`): *"`echo_to_customer` é ADVISORY: o cliente digitou o
 * valor, já o conhece"*.
 *
 *   `echo_to_*`  — o valor que o cliente DIGITOU volta no evento de interação?
 *                  É sobre entrada mascarada, e é medida contra quem olha por
 *                  cima do ombro — não contra o titular do dado.
 *   este arco    — quanto de um valor ARMAZENADO a plataforma mostra ao
 *                  renderizá-lo num template?
 *
 * O eixo certo para a segunda pergunta já existia, com as três peças prontas:
 *
 *   · `mascara.by_role` diz QUAL máscara (`last_4`, `email_domain`, `hidden`),
 *     enquanto `echo_to_*` só diz que há alguma — e para RENDERIZAR `masked` a
 *     `by_role` seria necessária de qualquer forma;
 *   · o resolvedor canônico é `resolve_mask_for_audience`
 *     (`py-contextstore/masking.py:87`), e o docstring dele **antecipa este
 *     arco**: *"quando o eixo `customer` existir, o segundo ramo o pega sem
 *     mudar nada aqui"*;
 *   · já há consumidor com a audiência `customer` VIVA —
 *     `_build_pending_preview` (`channel-gateway/adapters/webhook.py:2466`),
 *     que é o `***4444` medido na tela do dono.
 *
 * Insistir no `echo_to_*` teria criado um TERCEIRO vocabulário para a mesma
 * decisão — e o comentário daquele consumidor existe justamente para impedir
 * isso: ele nomeia o TIPO, e não a máscara, para não virar *"o quarto motor de
 * máscara do repositório"*.
 *
 * O que sobrevive da primeira versão é `deriveAudience`: o resolvedor canônico
 * recebe a audiência PRONTA e nunca soube derivá-la de um sítio.
 */

/**
 * resolveMaskForAudience — gêmeo TS FIEL de `resolve_mask_for_audience`.
 *
 * Três ramos, na ordem do original:
 *
 *     by_role VAZIO            → "plain"   (tipo de FINALIDADE, aberto por decisão)
 *     audiência declarada      → a dela
 *     audiência não declarada  → a do `operator`
 *
 * ⚠️ `by_role: {}` significa ABERTO **declarado**, não "esqueceram de
 * preencher" — é a marca dos tipos de finalidade (medido: 3 de 14). Tratá-lo
 * como ausência e cair no `operator` esconderia do cliente o que ele declarou.
 *
 * ⚠️ O fallback para `operator` é **declarado, não é ordenação de severidade**.
 * Hoje `operator` é a única audiência que o catálogo preenche (11 de 14, e
 * `customer` em nenhum). Inventar um "mais restritivo" exigiria ordenar as nove
 * máscaras por força, que é opinião; usar a única declarada é fato.
 *
 * Tipo ausente do catálogo → `"full"`. Recusa alta: tipo que a config não
 * conhece não pode virar `plain` por omissão.
 *
 * ⚠️ **Fidelidade é requisito, não estilo** — divergir aqui reabriria as duas
 * respostas para *"quanto do valor aparece"* que o `_build_pending_preview`
 * existe para não criar.
 */
export function resolveMaskForAudience(
  tipoEntry: DataType | undefined,
  audiencia: string,
): ContextMaskingType {
  if (!tipoEntry) return "full"
  const byRole = tipoEntry.mascara?.by_role
  if (!byRole || typeof byRole !== "object") return "full"
  if (Object.keys(byRole).length === 0) return "plain"
  // ⚠️ `||`, e NUNCA `??`. O original é `by_role.get(audiencia) or
  // by_role.get("operator")`, e `or` cai no fallback para QUALQUER valor falsy —
  // inclusive a string vazia. Com `??` o par divergiria em
  // `{ customer: "", operator: "last_4" }`: Python devolve `last_4`, o `??`
  // devolveria `full`. É a Mudança 35 do `CLAUDE.md` (`??` × truthiness) na
  // versão mais cara possível — dentro de um gêmeo cuja única obrigação é não
  // divergir. Há vetor de paridade para exatamente este caso.
  const m = byRole[audiencia] || byRole["operator"]
  return (typeof m === "string" && m) ? (m as ContextMaskingType) : "full"
}

/**
 * O que a PLATEIA derivada do sítio recebe.
 *
 * Dois valores que NÃO são máscara, e são distintos de propósito:
 *
 *   `"undecided"` — plateia `model` (§D5), que ainda não tem política.
 *   `"unknown"`   — a tag não está no `context_map`. Este arco **não decide** o
 *                   default de tag desconhecida (§D4): isso é a V4 da allowlist.
 *                   Ele CONTA, e conta em balde próprio.
 *
 * Os dois ficam no tipo de retorno — e não colapsados em `"plain"` — para que o
 * compilador force o chamador a tratá-los; chamá-los de `plain` transformaria
 * uma decisão pendente numa permissão, que é como omissão vira política aqui.
 */
export type CtxReadMask = ContextMaskingType | "undecided" | "unknown"

/**
 * maskForSite — junta os dois eixos: o SÍTIO já virou plateia
 * (`deriveAudience`); aqui o TIPO decide quanto ela vê.
 *
 * As três plateias que o catálogo não conhece são decididas aqui, cada uma por
 * um motivo diferente:
 *
 *   system — o valor sai INTEIRO. O CRM precisa do número, e o portão daquele
 *            caminho é o `AuditPolicy.data_categories` da tool, não este.
 *   none   — não é renderizado para ninguém; não há o que filtrar.
 *   model  — `undecided` (§D5): um prompt SAI da plataforma, e mandar um CPF ao
 *            provedor de modelo é outro fato que mostrá-lo a um operador logado.
 */
export function maskForSite(
  tipoId:   string | undefined,
  plateia:  CtxAudience,
  catalog:  DataTypeCatalog = DEFAULT_DATA_TYPE_CATALOG,
): CtxReadMask {
  if (plateia === "system" || plateia === "none") return "plain"
  if (plateia === "model") return "undecided"
  if (!tipoId) return "unknown"   // §D4 — contada, não decidida
  return resolveMaskForAudience(catalog.types.find(x => x.id === tipoId), plateia)
}

// ─────────────────────────────────────────────
// Aplicação de máscara por TIPO
// ─────────────────────────────────────────────

/**
 * applyMaskingTypeToValue — aplica UMA máscara do catálogo a um valor.
 *
 * ── Por que mora AQUI e não no `mcp-server` (CTX-07, 2026-09-04) ─────────────
 *
 * Ela nasceu em `mcp-server-plughub/src/lib/context-masking.ts` e ficou lá
 * enquanto os consumidores eram daquele pacote. A CTX-01 quebrou isso: o
 * `skill-flow-engine` passou a **decidir** qual máscara aplicar
 * (`resolveMaskForAudience`) e não conseguia **aplicá-la** — o engine não importa
 * o mcp-server, e nunca deve (a dependência é na direção contrária).
 *
 * As saídas eram duas, e uma delas é proibida: copiar para o engine faria a QUARTA
 * casa de mascaramento do repositório, que é exatamente o que o comentário do
 * `_build_pending_preview` existe para impedir. Então ela **mudou de casa** para a
 * base que todo mundo já importa, e o `mcp-server` passou a REEXPORTAR — nunca a
 * redefinir, pela regra do `CLAUDE.md`.
 *
 * ⚠️ **O gêmeo Python (`apply_masking_type_to_value`) NÃO se mexeu**, e a paridade
 * entre os dois é o portão `infra/test/probe_masking_apply_parity.sh` (30 vetores).
 * Mudar de casa sem mudar de comportamento é o requisito, e o gate é quem afirma.
 *
 * ⚠️ `hidden` devolve **string vazia**, que é SINAL para o chamador omitir o campo —
 * não é "o valor é vazio". O gêmeo Python faz o mesmo, e manter a convenção é o que
 * permite comparar as duas saídas.
 *
 * Cada tipo é apresentação VISUAL pura — nenhuma semântica de tipo de dado.
 */
export function applyMaskingTypeToValue(raw: string, type: ContextMaskingType): string {
  const digits = raw.replace(/\D/g, "")
  switch (type) {
    case "plain":
      return raw
    case "hidden":
      return ""   // sinal: o chamador omite o campo
    case "full":
      return "***"
    case "last_2":
      return digits.length >= 2
        ? `***${digits.slice(-2)}`
        : "***"
    case "last_4":
      return digits.length >= 4
        ? `***${digits.slice(-4)}`
        : digits.length > 0 ? `***${digits}` : "***"
    case "first_1":
      return raw.length > 0 ? `${raw[0]}***` : "***"
    case "first_word": {
      const word = raw.split(/\s+/)[0] ?? ""
      return word.length > 0 ? `${word} ***` : "***"
    }
    case "email_domain": {
      const atIdx = raw.indexOf("@")
      if (atIdx > 0) {
        const local  = raw.slice(0, atIdx)
        const domain = raw.slice(atIdx) // inclui o "@"
        return `${local[0] ?? "*"}***${domain}`
      }
      return raw.length > 0 ? `${raw[0]}***` : "***"
    }
    case "financial":
      return "R$ ****,**"
    default:
      return "***"
  }
}

/** `hidden` é o sinal de OMITIR o campo — o masker devolve string vazia para ele. */
export function maskOmitsField(m: CtxReadMask): boolean {
  return m === "hidden"
}

/**
 * A máscara mudaria o valor?
 *
 * `plain` não muda. `undecided` e `unknown` **não são máscaras** e por isso
 * também respondem `false`: a F3 só pode aplicar o que está decidido, e
 * responder `true` aqui faria o aplicador escolher por um arco que se absteve.
 *
 * ⚠️ **É um type predicate, e isso é load-bearing.** Como `boolean`, ele exprimia
 * a intenção e não a provava: o aplicador passava um `CtxReadMask` a
 * `applyMaskingTypeToValue`, que só aceita `ContextMaskingType`, e nada impedia
 * `undecided` de chegar lá — onde cairia no `default` e viraria `***`, aplicando
 * justamente a decisão de que este ADR se absteve (§D5). Foi o compilador quem
 * apontou, na F3. Estreitar para `ContextMaskingType` é sadio: o que sobra depois
 * dos três excluídos é exatamente esse conjunto.
 */
export function maskChangesValue(m: CtxReadMask): m is ContextMaskingType {
  return m !== "plain" && m !== "undecided" && m !== "unknown"
}

// ─────────────────────────────────────────────
// A REDE — detecção de PII em texto livre (§D12)
// ─────────────────────────────────────────────

/**
 * ⚠️ **MITIGAÇÃO, nunca controle.** Leia a §D12 antes de confiar nisto.
 *
 * A garantia vem de DECLARAR o campo (`DialogForm` → tipo → §D6), e a regra de produto
 * é **nunca capturar dado em texto livre quando um `DialogForm` puder fazê-lo**. Esta
 * rede existe para reduzir dano onde a captura já aconteceu — apresentá-la como
 * cobertura faria alguém relaxar sobre a captura, que é o anestésico mais caro que este
 * arco pode produzir.
 *
 * Limites medidos em 2026-09-04:
 *
 *   · **4 de 15 tipos** têm `detect_pattern` — `cpf`, `credit_card`, `phone`,
 *     `email_addr`. Os 11 restantes não são detectáveis, e vários POR DECISÃO (o
 *     `card_expiry` não tem padrão porque `\d{2}/\d{2}` casaria qualquer data);
 *   · pega o que é reconhecível por FORMA, nada do que é sensível por CONTEXTO;
 *   · nem com LLM o acerto é 100% — LLM melhoraria a estimativa, não a transformaria
 *     em garantia. E ficaria FORA daqui: esta função é pura e síncrona, e é chamada
 *     por interpolação, dentro do turno.
 *
 * **Idempotente**, e é isso que a torna segura sobre valor que já passou por máscara:
 * os `replacement` não contêm padrão de PII, então uma segunda passada é no-op.
 * Medido nos quatro tipos — `***4444`, `***.***.***.--`, `(##) ****-4321` e
 * `m***@exemplo.com` não casam nada.
 *
 * ⚠️ **Mudou de casa em 2026-09-04 (F5).** O corpo vivia em
 * `sdk/src/mcp-interceptor.ts` (R7a). O engine precisou dela e não importa o sdk — e
 * copiar faria a enésima casa de mascaramento. O sdk agora reexporta.
 */
export interface FreeTextMaskResult {
  value:      unknown
  /** Caminhos em notação de ponto onde houve substituição. */
  fields:     string[]
  categories: ContextMaskingCategory[]
}

const _REDE: { re: RegExp; category: ContextMaskingCategory; replacement: string }[] =
  DEFAULT_MASKING_RULES.flatMap(r => {
    try {
      return [{ re: new RegExp(r.pattern, "g"), category: r.category, replacement: r.replacement }]
    } catch {
      // Regra com regex inválida SAI da rede em vez de derrubar o processo. É
      // degradação, e ela é contável: `_REDE.length` < `DEFAULT_MASKING_RULES.length`.
      return []
    }
  })

/** Quantas regras da rede compilaram. Menor que o catálogo ⇒ alguma regex é inválida. */
export function freeTextNetSize(): number {
  return _REDE.length
}

/**
 * maskFreeText — anda o valor recursivamente e mascara PII nas folhas de string.
 *
 * Devolve a cópia mascarada, os caminhos onde houve substituição e as categorias
 * detectadas. Pura e síncrona — sem cofre, sem I/O. Conteúdo não-PII fica intacto.
 */
export function maskFreeText(value: unknown, path = ""): FreeTextMaskResult {
  if (typeof value === "string") {
    let masked = value
    const categories: ContextMaskingCategory[] = []
    for (const rule of _REDE) {
      rule.re.lastIndex = 0
      if (rule.re.test(masked)) {
        masked = masked.replace(rule.re, rule.replacement)
        categories.push(rule.category)
      }
    }
    return categories.length > 0
      ? { value: masked, fields: [path || "$"], categories }
      : { value, fields: [], categories: [] }
  }
  if (Array.isArray(value)) {
    const out: unknown[] = []
    const fields: string[] = []
    const categories: ContextMaskingCategory[] = []
    value.forEach((item, i) => {
      const r = maskFreeText(item, path ? `${path}[${i}]` : `[${i}]`)
      out.push(r.value); fields.push(...r.fields); categories.push(...r.categories)
    })
    return { value: out, fields, categories }
  }
  if (value !== null && typeof value === "object") {
    const out: Record<string, unknown> = {}
    const fields: string[] = []
    const categories: ContextMaskingCategory[] = []
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      const r = maskFreeText(v, path ? `${path}.${k}` : k)
      out[k] = r.value; fields.push(...r.fields); categories.push(...r.categories)
    }
    return { value: out, fields, categories }
  }
  return { value, fields: [], categories: [] }
}

// ─────────────────────────────────────────────
// Mapa tag → tipo
// ─────────────────────────────────────────────

/**
 * Achata o `masking.context_map` em `tag → tipo`, incluindo os ALIASES legados.
 *
 * Os aliases não são detalhe: a tag que os flows realmente interpolam é a
 * legada (`session.numero_cartao`), e a canônica (`session.cartao.numero`) é a
 * que carrega o tipo. Um índice que só lesse canônicas resolveria ZERO das
 * interpolações vivas e concluiria que não há nada a filtrar.
 */
export function flattenContextMap(mapa: unknown): Map<string, string> {
  const out = new Map<string, string>()
  const anda = (no: unknown, cam: string[]): void => {
    if (!no || typeof no !== "object") return
    const obj = no as Record<string, unknown>
    if (typeof obj.tipo === "string") {
      // A raiz `contexto` é do documento, não da tag.
      const canon = cam[0] === "contexto" ? cam.slice(1).join(".") : cam.join(".")
      out.set(canon, obj.tipo)
      for (const a of (obj.legado as string[] | undefined) ?? []) out.set(a, obj.tipo)
      return
    }
    for (const [k, v] of Object.entries(obj)) anda(v, [...cam, k])
  }
  anda(mapa, [])
  return out
}
