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
import { DEFAULT_DATA_TYPE_CATALOG, type DataTypeCatalog } from "./audit"

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
 * O que a plateia pode ver.
 *
 *   plain     — valor inteiro
 *   masked    — parcial, conforme o tipo
 *   none      — não sai
 *   undecided — a plateia `model` (§D5), que ainda não tem política declarada.
 *               É valor PRÓPRIO e não um sinônimo de `plain`: chamar de `plain`
 *               transformaria uma decisão pendente numa permissão, que é como
 *               omissões viram política nesta casa.
 *   unknown   — o tipo não está no catálogo. Não é decidido aqui (§D4).
 */
export const EchoPolicySchema = z.enum(["plain", "masked", "none", "undecided", "unknown"])
export type EchoPolicy = z.infer<typeof EchoPolicySchema>

/**
 * resolveEchoPolicy — o TIPO decide o quê; a plateia só escolhe a coluna (§D6).
 *
 * Um template não pode afrouxar: ele só pode pedir EXCEÇÃO, que é declarada,
 * greppável e auditada (§D3). É isso que mantém uma única política de CPF no
 * tenant, editável por quem responde por conformidade.
 */
export function resolveEchoPolicy(
  typeId:   string | undefined,
  audience: CtxAudience,
  catalog:  DataTypeCatalog = DEFAULT_DATA_TYPE_CATALOG,
): EchoPolicy {
  if (audience === "none")   return "plain"      // não é renderizado; nada a filtrar
  if (audience === "system") return "plain"      // o gate do `invoke` é o AuditPolicy da tool
  if (audience === "model")  return "undecided"  // §D5
  if (!typeId) return "unknown"

  const t = catalog.types.find(x => x.id === typeId)
  if (!t) return "unknown"

  const d = t.mascara?.display
  const bruto = audience === "customer" ? d?.echo_to_customer : d?.echo_to_operator
  if (bruto === "none" || bruto === "masked" || bruto === "plain") return bruto
  // Tipo declarado que não declara eco: `unknown` e não `plain`. A diferença
  // importa — `plain` afirmaria uma permissão que ninguém escreveu.
  return "unknown"
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
