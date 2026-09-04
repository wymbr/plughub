/**
 * q_ctx_read_audience_census.ts — o censo da F0 (CTX-01).
 *
 * Responde: **de cada interpolação, para quem o valor vai, e o que o tipo diz
 * que aquela plateia pode ver?**
 *
 * ── Por que TypeScript, e não um script Python ───────────────────────────────
 * Porque a regra de derivação (`sítio → plateia`) é a MESMA que o engine vai
 * usar no runtime, e ela mora em `@plughub/schemas`. Um censo em Python teria
 * de reimplementá-la — e a cópia estática diria que está tudo bem enquanto a de
 * runtime deixa passar. É o defeito que este arco existe para corrigir; comê-lo
 * no próprio instrumento seria a pior forma de começar.
 *
 * ── Por que ele parseia YAML em vez de usar regex ────────────────────────────
 * Regex sobre o arquivo inteiro acha as interpolações mas **não sabe em que
 * step cada uma está** — e o step é justamente o que decide a plateia. Um censo
 * por regex mediria a proposição errada: *"quantas tags sensíveis existem"* em
 * vez de *"quantas chegam a um cliente"*.
 */
import { readFileSync, readdirSync } from "node:fs"
import { join } from "node:path"
import YAML from "/w/packages/agent-registry/node_modules/yaml/dist/index.js"
import {
  deriveAudience, maskForSite, maskChangesValue, flattenContextMap,
  type CtxAudience, type CtxReadMask,
} from "/w/packages/schemas/dist/index.js"

const SKILLS = "/w/packages/skill-flow-engine/skills"
const MAPA   = process.env.CTX_MAP_JSON   ?? "/t/cm.json"
const TIPOS  = process.env.CTX_TYPES_JSON ?? "/t/tipos.json"

/**
 * LEGÍTIMOS — exceções conhecidas, DECLARADAS e datadas.
 *
 * Cada linha é um valor que chega ao cliente **por desenho**, apesar de o tipo
 * dizer `none`. Existe como tabela, e não como regra ("todo credential passa"),
 * porque uma regra abrangeria o próximo `credential` que NÃO for legítimo.
 *
 * ⚠️ Esta tabela é o INSUMO da F2: cada linha aqui vira uma exceção declarada no
 * template. Enquanto ela existir, o censo mede o que a F2 ainda deve declarar —
 * e ela tem de ENCOLHER. O ramo D reprova uma linha que não se aplica mais.
 */
const LEGITIMOS: Array<{ skill: string; tag: string; porque: string }> = [
  // VAZIA, e isso é resultado — não pendência.
  //
  // Ela nasceu com seis linhas, escritas a partir de um censo por REGEX que
  // encontrou 20 pontos `@ctx.*` resolvendo para tipo com `echo_to_customer:
  // "none"`, dos quais 10 eram `credential`. A leitura foi: *"são tokens em
  // links que o cliente precisa receber"*.
  //
  // ⚠️ Aquele censo errou DUAS vezes, e só a primeira foi corrigida no mesmo
  // dia. A segunda: `echo_to_customer` é ADVISORY e trata do valor que o
  // cliente DIGITOU (`audit.ts:342`) — não do quanto de um valor armazenado a
  // plataforma mostra. O eixo desta medição hoje é `mascara.by_role`, com o
  // resolvedor canônico que já existia.
  //
  // **A medição estrutural refutou isso.** Aqueles `credential` estão em steps
  // `invoke` (`tool: workflow_resume`, `input.resume_token`) — plateia
  // `system`, argumento de tool. Nenhum deles chega a cliente nenhum. A regex
  // dizia que a tag APARECE no arquivo; a pergunta era se ela CHEGA a alguém.
  //
  // O que sobra depois de perguntar direito: de 88 interpolações, 7 alcançam o
  // cliente e **2** seriam bloqueadas — as duas de `skill_limite_retorno_v1`
  // que o teste do dono exibiu na tela.
  //
  // A conclusão do ADR (a plateia vem do SÍTIO, nunca da tag) sobrevive e sai
  // mais forte: uma regra por tag continuaria errada, mas por quebrar o
  // `workflow_resume` — não por comer o link.
]

const ehLegitimo = (skill: string, tag: string): string | undefined =>
  LEGITIMOS.find(l => l.skill === skill && l.tag === tag)?.porque

// ── varredura ────────────────────────────────────────────────────────────────

const REF = /@ctx\.([a-zA-Z0-9_.]+)/g
const REF_PS = /\$\.pipeline_state\.([a-zA-Z0-9_.]+)/g

interface Achado {
  skill: string; step: string; tipoStep: string
  tag: string; tipo: string | undefined
  plateia: CtxAudience; mascara: CtxReadMask
  legitimo: string | undefined
}

function textosDoStep(s: Record<string, unknown>): string {
  // Só o que vira TEXTO ou argumento — não o step inteiro, senão `on_success` e
  // afins entrariam na conta.
  const partes: unknown[] = [s.prompt, s.message, s.input, s.options, s.fields, s.reprompt]
  return partes.filter(x => x !== undefined).map(x => JSON.stringify(x)).join("\n")
}

const mapa  = flattenContextMap(JSON.parse(readFileSync(MAPA, "utf-8")).value)
const tipos = JSON.parse(readFileSync(TIPOS, "utf-8")).value as { types: Array<{ id: string }> }
const catalogo = tipos as unknown as Parameters<typeof maskForSite>[2]

const achados: Achado[] = []
let totalRefs = 0

/**
 * F5 — `$.pipeline_state.*`, contado pela MESMA derivação de plateia.
 *
 * O ADR cita **225** para esta população, e o número veio de regex. A CTX-01 já
 * mostrou o que isso mede: *"a chave APARECE no arquivo"*, não *"a chave CHEGA a
 * alguém"*. Aqui a diferença volta a ser grande, e por isso ela é contada.
 *
 * ⚠️ **Sem veredicto, de propósito.** Não há política para esta população — o mapa
 * tipa tag de ContextStore, não chave de `pipeline_state`, então toda chave aqui é
 * indeclarável hoje. Um ramo que reprovasse estaria exigindo o que a F5 ainda não
 * construiu; um que aprovasse afirmaria segurança que ninguém verificou. O número
 * é publicado para ser LIDO por quem desenhar o carimbo de proveniência.
 */
const psPorPlateia: Record<string, number> = {}
const psAoCliente: Array<{ skill: string; step: string; chave: string }> = []
let psTotal = 0

for (const f of readdirSync(SKILLS).filter(x => x.endsWith(".yaml")).sort()) {
  const doc = YAML.parse(readFileSync(join(SKILLS, f), "utf-8")) as Record<string, unknown>
  const steps = ((doc.flow as Record<string, unknown> | undefined)?.steps
              ?? doc.steps ?? []) as Array<Record<string, unknown>>
  for (const s of steps) {
    const tipoStep = String(s.type ?? "")
    const plateia  = deriveAudience(tipoStep, s.visibility)
    const texto    = textosDoStep(s)
    for (const m of texto.matchAll(REF_PS)) {
      psTotal++
      psPorPlateia[plateia] = (psPorPlateia[plateia] ?? 0) + 1
      if (plateia === "customer") {
        psAoCliente.push({ skill: f, step: String(s.id ?? "?"), chave: m[1].replace(/\.$/, "") })
      }
    }
    for (const m of texto.matchAll(REF)) {
      totalRefs++
      const tag = m[1].replace(/\.$/, "")
      const tipo = mapa.get(tag)
      achados.push({
        skill: f, step: String(s.id ?? "?"), tipoStep, tag, tipo,
        plateia, mascara: maskForSite(tipo, plateia, catalogo),
        legitimo: ehLegitimo(f, tag),
      })
    }
  }
}

// ── veredicto ────────────────────────────────────────────────────────────────

const porPlateia = (p: CtxAudience) => achados.filter(a => a.plateia === p).length

/**
 * `mudaria` substituiu `bloquearia`, e a troca é do EIXO, não de palavra.
 *
 * O `echo_to_*` respondia *"o valor volta?"* — ternário, e o balde que importava
 * era o `none`. O `by_role` responde *"quanto do valor aparece?"* e devolve a
 * máscara NOMEADA, então o balde é *"alguma máscara se aplicaria"* e o relatório
 * pode dizer QUAL. `hidden` continua sendo o caso que remove o campo, e por isso
 * é contado à parte.
 */
const mudaria     = achados.filter(a => maskChangesValue(a.mascara))
const omitiria    = achados.filter(a => a.mascara === "hidden")
const semDeclarar = mudaria.filter(a => !a.legitimo)
const declaradosOrfaos = LEGITIMOS.filter(
  l => !mudaria.some(a => a.skill === l.skill && a.tag === l.tag))

const porMascara: Record<string, number> = {}
for (const a of achados) porMascara[a.mascara] = (porMascara[a.mascara] ?? 0) + 1

/**
 * F4 — a população que a §D5 teria de decidir, e ela precisa ser contada à parte.
 *
 * `maskForSite` devolve `undecided` para a plateia `model`, então esses pontos
 * NUNCA entram em `mudaria` — e um censo que só olhasse `mudaria` concluiria que
 * não há nada em risco num prompt. É a proposição adjacente de novo.
 *
 * A pergunta certa é outra: **das interpolações que vão ao MODELO, quantas
 * carregam tipo que mascara para alguém?** Se for zero, a D5 estaria decidindo
 * política contra população zero — o erro que este repositório já nomeou. Se
 * deixar de ser zero, alguém começou a mandar PII para fora da plataforma, e é
 * disso que a F4 precisa saber ANTES de escolher.
 */
const tipoMascaraAlguem = (id: string | undefined): boolean => {
  if (!id) return false
  const e = tipos.types.find((x: { id: string }) => x.id === id) as
    { mascara?: { by_role?: Record<string, unknown> } } | undefined
  return Object.keys(e?.mascara?.by_role ?? {}).length > 0
}
const aoModelo = achados.filter(a => a.plateia === "model")
const modeloSensivel = aoModelo.filter(a => tipoMascaraAlguem(a.tipo))

console.log(JSON.stringify({
  refs: totalRefs,
  por_plateia: {
    customer: porPlateia("customer"), operator: porPlateia("operator"),
    system: porPlateia("system"), model: porPlateia("model"), none: porPlateia("none"),
  },
  por_mascara: porMascara,
  // A tag fora do mapa é CONTADA e não decidida (§D4) — evidência para a V4 da
  // allowlist, nunca autorização para ela.
  tags_nao_declaradas: [...new Set(achados.filter(a => !a.tipo).map(a => a.tag))].sort(),
  // F5 — publicado, não julgado (ver o comentário na declaração).
  pipeline_state: {
    total: psTotal,
    por_plateia: psPorPlateia,
    chaves_ao_cliente: [...new Set(psAoCliente.map(x => x.chave))].sort(),
  },
  mudaria: mudaria.length,
  ao_modelo: aoModelo.length,
  // F4/§D5 — tem de ficar em ZERO enquanto a plateia `model` não tiver política.
  modelo_com_tipo_que_mascara: modeloSensivel.map(
    a => ({ skill: a.skill, step: a.step, tag: a.tag, tipo: a.tipo })),
  modelo_tags_nao_declaradas: [...new Set(
    aoModelo.filter(a => !a.tipo).map(a => a.tag))].sort(),
  omitiria: omitiria.length,
  legitimos_declarados: mudaria.filter(a => a.legitimo).length,
  a_declarar: semDeclarar.map(a => ({
    skill: a.skill, step: a.step, tag: a.tag, tipo: a.tipo, mascara: a.mascara,
  })),
  legitimos_orfaos: declaradosOrfaos.map(l => `${l.skill}:${l.tag}`),
}, null, 2))
