/**
 * ctx-audit.ts — CTX-02: o resolvedor de plateia em MODO AUDITORIA.
 *
 * Ele calcula o que o filtro FARIA e **loga sem aplicar**. Ver
 * `docs/adr/adr-context-read-audience-policy.md` §4 (F1).
 *
 * ── Por que auditar em runtime, se já existe o censo estático ────────────────
 * O censo (`q_ctx_read_audience_census.ts`) lê os YAMLs do disco. **O bridge não
 * executa o YAML** — executa o snapshot do slot `current` do pool. As duas
 * coisas divergem por construção (seed-if-absent), e foi exatamente essa
 * divergência que deixou o wrap-up gravando nada por três dias em setembro.
 *
 * O runtime também vê o que o estático não pode: `visibility` que chega como ref
 * `$.`/`@ctx.`, e caminhos que nenhum YAML no disco descreve.
 *
 * ── A regra é IMPORTADA, não reescrita ──────────────────────────────────────
 * `deriveAudience` e `resolveEchoPolicy` vêm de `@plughub/schemas`, os mesmos que
 * o censo usa. Uma cópia aqui poderia dizer que está tudo bem enquanto a outra
 * mede outra coisa — e é literalmente o defeito que este arco existe para
 * corrigir.
 */
import {
  deriveAudience, resolveEchoPolicy, flattenContextMap,
  type CtxAudience, type EchoPolicy, type DataTypeCatalog,
} from "@plughub/schemas"

/** O sítio de onde a interpolação parte. É ele que decide a plateia (§D2). */
export interface SitioInterpolacao {
  stepType:    string
  visibility?: unknown
  stepId?:     string
}

const CONFIG_URL = process.env.CONFIG_API_URL ?? ""
const TTL_MS     = 5 * 60_000

interface Catalogos { mapa: Map<string, string>; tipos: DataTypeCatalog; em: number }
let cache: Catalogos | null = null
let carregando: Promise<Catalogos | null> | null = null
let avisouIndisponivel = false

async function buscar(tenantId: string): Promise<Catalogos | null> {
  if (!CONFIG_URL) {
    if (!avisouIndisponivel) {
      avisouIndisponivel = true
      // ⚠️ NOMEIA o que deixa de valer. "using default values" é a frase que
      // ninguém leu por meses no bridge, segundo o próprio CLAUDE.md — um aviso
      // que não diz qual capacidade caiu é ruído com cara de diligência.
      console.warn(
        "[ctx-audit] CONFIG_API_URL ausente — a AUDITORIA DE PLATEIA não roda. " +
        "Nenhuma leitura de contexto será avaliada contra `masking.types`, e o " +
        "censo de runtime da CTX-02 sairá VAZIO (o que não é o mesmo que zero achados)."
      )
    }
    return null
  }
  try {
    const url = (ns: string, k: string) =>
      `${CONFIG_URL}/config/${ns}/${k}?tenant_id=${encodeURIComponent(tenantId)}`
    const [rm, rt] = await Promise.all([
      fetch(url("masking", "context_map")),
      fetch(url("masking", "types")),
    ])
    if (!rm.ok || !rt.ok) throw new Error(`HTTP ${rm.status}/${rt.status}`)
    const jm = (await rm.json()) as { value?: unknown }
    const jt = (await rt.json()) as { value?: unknown }
    return {
      mapa:  flattenContextMap(jm.value),
      tipos: (jt.value ?? { types: [] }) as DataTypeCatalog,
      em:    Date.now(),
    }
  } catch (e) {
    if (!avisouIndisponivel) {
      avisouIndisponivel = true
      console.warn(
        `[ctx-audit] catálogos indisponíveis (${String(e)}) — a AUDITORIA DE PLATEIA ` +
        "não roda nesta instância. O censo de runtime sairá VAZIO, e vazio aqui " +
        "significa 'não medimos', nunca 'nada a corrigir'."
      )
    }
    return null
  }
}

async function catalogos(tenantId: string): Promise<Catalogos | null> {
  if (cache && Date.now() - cache.em < TTL_MS) return cache
  if (!carregando) {
    carregando = buscar(tenantId).then(c => { if (c) cache = c; carregando = null; return c })
  }
  return carregando
}

// Uma linha por combinação distinta, por processo. Sem isto um contato de dez
// turnos imprimiria a mesma constatação dez vezes, e o volume esconderia o
// achado — que é o inverso do que a auditoria existe para fazer.
const jaLogado = new Set<string>()

/**
 * Audita UMA leitura de contexto. Não altera nada e nunca lança: a auditoria não
 * pode ser a causa de um atendimento cair.
 *
 * Só registra o que MUDARIA (`none` / `masked`) e o que não sabe decidir
 * (`unknown` / `undecided`). Logar os `plain` seria imprimir 72 linhas do censo
 * para dizer que está tudo certo.
 */
export async function auditarLeituraCtx(
  tag:      string,
  sitio:    SitioInterpolacao,
  tenantId: string,
): Promise<void> {
  try {
    const c = await catalogos(tenantId)
    if (!c) return

    const plateia: CtxAudience  = deriveAudience(sitio.stepType, sitio.visibility)
    const tipo                  = c.mapa.get(tag)
    const politica: EchoPolicy  = resolveEchoPolicy(tipo, plateia, c.tipos)

    if (politica === "plain") return

    const chave = `${sitio.stepId ?? "?"}|${tag}|${plateia}|${politica}`
    if (jaLogado.has(chave)) return
    jaLogado.add(chave)

    // "AUDITORIA" no texto de propósito: quem grep-ar o log precisa distinguir
    // o que o filtro FARIA do que ele FEZ — e na F1 ele ainda não faz nada.
    console.warn(
      `[ctx-audit] AUDITORIA (não aplicado): tag=${tag} tipo=${tipo ?? "NÃO DECLARADA"} ` +
      `step=${sitio.stepId ?? "?"}:${sitio.stepType} plateia=${plateia} → política=${politica}`
    )
  } catch {
    // Silêncio aqui é a única degradação muda aceitável do arquivo, e a razão
    // está no contrato: auditar é observação. Um erro dela derrubar o turno
    // trocaria um problema de conformidade por um de disponibilidade.
  }
}

/** Estado da auditoria, para o gate distinguir "não achou" de "não rodou". */
export function estadoAuditoriaCtx(): {
  configurado: boolean; catalogos_carregados: boolean; achados: number
} {
  return {
    configurado: !!CONFIG_URL,
    catalogos_carregados: !!cache,
    achados: jaLogado.size,
  }
}
