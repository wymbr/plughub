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
  deriveAudience, resolveEchoPolicy, flattenContextMap,
  type CtxAudience, type EchoPolicy,
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

interface Achado {
  skill: string; step: string; tipoStep: string
  tag: string; tipo: string | undefined
  plateia: CtxAudience; politica: EchoPolicy
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
const catalogo = tipos as unknown as Parameters<typeof resolveEchoPolicy>[2]

const achados: Achado[] = []
let totalRefs = 0

for (const f of readdirSync(SKILLS).filter(x => x.endsWith(".yaml")).sort()) {
  const doc = YAML.parse(readFileSync(join(SKILLS, f), "utf-8")) as Record<string, unknown>
  const steps = ((doc.flow as Record<string, unknown> | undefined)?.steps
              ?? doc.steps ?? []) as Array<Record<string, unknown>>
  for (const s of steps) {
    const tipoStep = String(s.type ?? "")
    const plateia  = deriveAudience(tipoStep, s.visibility)
    const texto    = textosDoStep(s)
    for (const m of texto.matchAll(REF)) {
      totalRefs++
      const tag = m[1].replace(/\.$/, "")
      const tipo = mapa.get(tag)
      achados.push({
        skill: f, step: String(s.id ?? "?"), tipoStep, tag, tipo,
        plateia, politica: resolveEchoPolicy(tipo, plateia, catalogo),
        legitimo: ehLegitimo(f, tag),
      })
    }
  }
}

// ── veredicto ────────────────────────────────────────────────────────────────

const porPlateia = (p: CtxAudience) => achados.filter(a => a.plateia === p).length
const bloquearia = achados.filter(a => a.politica === "none")
const semDeclarar = bloquearia.filter(a => !a.legitimo)
const declaradosOrfaos = LEGITIMOS.filter(
  l => !bloquearia.some(a => a.skill === l.skill && a.tag === l.tag))

console.log(JSON.stringify({
  refs: totalRefs,
  por_plateia: {
    customer: porPlateia("customer"), operator: porPlateia("operator"),
    system: porPlateia("system"), model: porPlateia("model"), none: porPlateia("none"),
  },
  por_politica: {
    none:      achados.filter(a => a.politica === "none").length,
    masked:    achados.filter(a => a.politica === "masked").length,
    plain:     achados.filter(a => a.politica === "plain").length,
    undecided: achados.filter(a => a.politica === "undecided").length,
    unknown:   achados.filter(a => a.politica === "unknown").length,
  },
  // A tag fora do mapa é CONTADA e não decidida (§D4) — evidência para a V4 da
  // allowlist, nunca autorização para ela.
  tags_nao_declaradas: [...new Set(achados.filter(a => !a.tipo).map(a => a.tag))].sort(),
  bloquearia: bloquearia.length,
  legitimos_declarados: bloquearia.filter(a => a.legitimo).length,
  a_declarar: semDeclarar.map(a => ({ skill: a.skill, step: a.step, tag: a.tag, tipo: a.tipo })),
  legitimos_orfaos: declaradosOrfaos.map(l => `${l.skill}:${l.tag}`),
}, null, 2))
