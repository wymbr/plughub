/**
 * ctx-audit.ts — o resolvedor de plateia. **APLICA desde a F3 (2026-09-04).**
 *
 * Nasceu em modo auditoria (CTX-02): calculava o que o filtro faria e logava sem
 * aplicar. A F3 (CTX-04) o fez substituir o valor. Ver
 * `docs/adr/adr-context-read-audience-policy.md` §4.
 *
 * ⚠️ **O nome do arquivo e o prefixo `[ctx-audit]` do log FICARAM**, e a escolha é
 * deliberada: os dois são identidade greppável em gate, runbook e histórico, e
 * renomeá-los quebraria a busca de quem investiga um contato antigo sem mudar uma
 * linha de comportamento. O que precisava dizer a verdade é o TEXTO do log, e ele
 * diz — `APLICADO` × `NÃO aplicado`, com o motivo do segundo.
 *
 * ── UM leitor, que SUBSTITUI (§D1) ──────────────────────────────────────────
 * Não existe `getMasked()` ao lado do `get()`. Duas portas para o mesmo dado e só
 * uma trancada é o achado do `/sessions/{id}/stream`, e quem escreve template
 * escolheria a que funciona — que é sempre a permissiva.
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
 * `deriveAudience` e `maskForSite` vêm de `@plughub/schemas`, os mesmos que
 * o censo usa. Uma cópia aqui poderia dizer que está tudo bem enquanto a outra
 * mede outra coisa — e é literalmente o defeito que este arco existe para
 * corrigir.
 */
import {
  deriveAudience, maskForSite, maskChangesValue, maskOmitsField,
  applyMaskingTypeToValue, maskFreeText, flattenContextMap,
  type CtxAudience, type CtxReadMask, type DataTypeCatalog,
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

/** Loga UMA vez por combinação distinta. Compartilhado pelas duas entradas. */
function registra(chave: string, emitir: () => void): void {
  if (jaLogado.has(chave)) return
  jaLogado.add(chave)
  emitir()
}

/**
 * filtrarLeituraCtx — o LEITOR. Devolve o valor que a plateia daquele sítio pode ver.
 *
 * Substitui o valor; nunca o duplica numa segunda porta (§D1). É a mesma decisão que
 * a auditoria da CTX-02 já calculava — e é a MESMA função, não uma segunda: duas
 * implementações da mesma regra é o defeito que este arco existe para corrigir, e
 * cometê-lo aqui seria cometê-lo no coração dele.
 *
 * ── O que NÃO é aplicado, e por quê ─────────────────────────────────────────
 *
 *   plain      → nada a fazer (inclui `system` e `none`, e o `by_role: {}` aberto)
 *   undecided  → plateia `model` (§D5): decisão própria, fase F4. Aplicar aqui
 *                seria escolher por um arco que se absteve.
 *   unknown    → tag fora do mapa (§D4): é a V4 da allowlist, irreversível e com
 *                população própria sendo contada. Este ADR CONTA, não decide.
 *
 * Os dois últimos **são logados** — é justamente deles que a próxima fase precisa.
 *
 * ── Catálogo indisponível ───────────────────────────────────────────────────
 *
 * Devolve o valor intacto, e isso **não é conveniência**: sem catálogo toda tag é
 * `unknown`, e `unknown` já está decidido acima como *contar, não aplicar*. O
 * carregador (`buscar`) é quem grita, NOMEANDO o que deixa de valer — mascarar tudo
 * faria toda mensagem do parque virar `***` por uma queda de config, e recusar o
 * passo trocaria um problema de conformidade por um de disponibilidade.
 *
 * ── Nunca lança ─────────────────────────────────────────────────────────────
 *
 * Um erro do filtro derrubando um atendimento seria pior que o vazamento que ele
 * evita. Em qualquer falha o valor sai como veio, e o `catch` **loga**.
 */
export async function filtrarLeituraCtx(
  valor:    unknown,
  tag:      string,
  sitio:    SitioInterpolacao,
  tenantId: string,
): Promise<unknown> {
  try {
    if (valor === undefined || valor === null || valor === "") return valor

    const c = await catalogos(tenantId)
    if (!c) return valor

    const plateia: CtxAudience = deriveAudience(sitio.stepType, sitio.visibility)
    const tipo                 = c.mapa.get(tag)
    const mascara: CtxReadMask = maskForSite(tipo, plateia, c.tipos)

    if (mascara === "plain") return valor

    const onde = `step=${sitio.stepId ?? "?"}:${sitio.stepType} plateia=${plateia}`
    const quem = `tag=${tag} tipo=${tipo ?? "NÃO DECLARADA"}`

    if (!maskChangesValue(mascara)) {
      // `undecided` / `unknown` — a fase que DECIDE não é esta, e continua não sendo:
      // o log fica. O que mudou na F5 é que o `unknown` deixa de sair sem nada — a
      // REDE (§D12, camada 3) passa por cima dele. Isso não decide a V4: a rede é
      // mitigação por FORMA e não declara tipo nenhum; a tag continua indeclarada e
      // continua sendo contada como tal.
      registra(`${sitio.stepId ?? "?"}|${tag}|${plateia}|${mascara}`, () => console.warn(
        `[ctx-audit] NÃO aplicado (${mascara === "undecided" ? "§D5, fase F4" : "§D4, é a V4 da allowlist"}): ` +
        `${quem} ${onde} → ${mascara}`))
      return mascara === "unknown"
        ? redeParaTextoLivre(valor, plateia, `${sitio.stepId ?? "?"}:${sitio.stepType} ${tag}`)
        : valor
    }

    const filtrado = applyMaskingTypeToValue(String(valor), mascara)
    registra(`${sitio.stepId ?? "?"}|${tag}|${plateia}|${mascara}`, () => console.info(
      `[ctx-audit] APLICADO: ${quem} ${onde} → máscara=${mascara}` +
      (maskOmitsField(mascara) ? " (campo OMITIDO — `hidden` devolve vazio)" : "")))
    return filtrado
  } catch (e) {
    // Degradação NUNCA silenciosa: aqui ela deixa passar um valor CRU, que é o fato
    // mais caro que este arquivo pode produzir. Sem esta linha o vazamento seria
    // indistinguível de um tipo `plain`.
    console.warn(
      `[ctx-audit] FALHA ao filtrar tag=${tag} step=${sitio.stepId ?? "?"} (${String(e)}) — ` +
      "o valor saiu SEM filtro. Isto não é 'nada a mascarar'."
    )
    return valor
  }
}

/**
 * redeParaTextoLivre — a camada 3 da §D12, e ela é MITIGAÇÃO.
 *
 * Roda onde não há tipo declarado: `$.pipeline_state.*` (que o mapa não alcança — ele
 * tipa tag de ContextStore, não chave de pipeline_state) e tag fora do mapa.
 *
 * ⚠️ **Não confundir com cobertura.** Ela pega 4 dos 15 tipos, só por FORMA. A garantia
 * é declarar o campo num `DialogForm`; esta função reduz dano onde a captura já
 * aconteceu em texto livre.
 *
 * ⚠️ **Só para plateia de gente.** `system` recebe o valor inteiro — o CRM precisa dele,
 * e é a mesma razão do `plain` da §D2. Rodar a rede ali quebraria o `invoke` sem que
 * nenhuma política pedisse.
 *
 * Segura sobre valor JÁ mascarado: os `replacement` não contêm padrão de PII, então a
 * segunda passada é no-op (§D12, medido). É o que permitiu à F5 dispensar o carimbo.
 */
function redeParaTextoLivre(
  valor:   unknown,
  plateia: CtxAudience,
  onde:    string,
): unknown {
  if (plateia !== "customer" && plateia !== "operator") return valor
  const r = maskFreeText(valor)
  if (r.categories.length === 0) return valor
  registra(`rede|${onde}|${[...new Set(r.categories)].sort().join(",")}`, () => console.info(
    `[ctx-audit] REDE (mitigação, §D12): ${onde} plateia=${plateia} — ` +
    `categorias=${[...new Set(r.categories)].sort().join(",")} em ${r.fields.length} campo(s). ` +
    "Isto é dado capturado em TEXTO LIVRE; o certo é declará-lo num DialogForm."))
  return r.value
}

/**
 * filtrarTextoLivre — a porta da F5, para o que NÃO tem tag.
 *
 * O `interpolate` a chama para `$.pipeline_state.*`. Não há tipo a consultar, então não
 * há catálogo a carregar e nada que possa falhar por rede — é regex puro e síncrono.
 */
export function filtrarTextoLivre(
  valor: unknown,
  sitio: SitioInterpolacao,
  ref:   string,
): unknown {
  try {
    if (valor === undefined || valor === null || valor === "") return valor
    const plateia = deriveAudience(sitio.stepType, sitio.visibility)
    return redeParaTextoLivre(valor, plateia, `${sitio.stepId ?? "?"}:${sitio.stepType} ${ref}`)
  } catch (e) {
    console.warn(
      `[ctx-audit] FALHA na rede para ${ref} step=${sitio.stepId ?? "?"} (${String(e)}) — ` +
      "o valor saiu SEM filtro. Isto não é 'nada a mascarar'.")
    return valor
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
