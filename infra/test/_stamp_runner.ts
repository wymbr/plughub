/**
 * Runner TypeScript da fixture de paridade do carimbo — ALW-02 passo 2.
 *
 * Lê `fixtures/context_stamp_cases.json`, roda `stampContextEntry` em cada caso e imprime
 * uma linha JSON por caso, com as chaves ORDENADAS.
 *
 * Chaves ordenadas de propósito: a ordem de inserção não faz parte do contrato (ninguém
 * compara bytes de uma entrada do Redis — todo leitor desserializa), então compará-la
 * transformaria uma diferença cosmética num vermelho que ensina a ignorar o gate. O que o
 * gate mede é CONTEÚDO.
 *
 * Par: `_stamp_runner.py`. Os dois têm de imprimir exatamente as mesmas linhas.
 */
import * as fs   from "fs"
import * as path from "path"
import {
  buildContextTagIndex,
  stampContextEntry,
  ContextMapSchema,
  type ContextTagIndex,
} from "../../packages/schemas/src/context-map"

/**
 * Caminho por ARGUMENTO, com default co-localizado. O gate passa explicitamente porque
 * este runner roda BUNDLADO (esbuild) e o `__dirname` do bundle é o diretório de saída,
 * não o do fonte — e um default que só funciona por acidente de layout é a espécie de
 * fragilidade que fica verde até o dia em que não fica.
 */
const FIXTURE = process.argv[2]
  ?? path.join(__dirname, "fixtures", "context_stamp_cases.json")

/**
 * Idêntica à do runner Python. Existe para a testemunha de PASSAGEM — "o carimbo não toca
 * nos campos do escritor" —, medida DENTRO do runner e reportada como booleano.
 *
 * ⚠️ Por que booleano, e não a entrada inteira na comparação: a primeira versão deste gate
 * emitia o objeto completo e acusou divergência nos 15 casos — `"confidence":1.0` em Python
 * contra `"confidence":1` aqui, porque JS não separa inteiro de float. Os 15 `atributo`
 * eram byte a byte iguais. Era o INSTRUMENTO divergindo, não o produto.
 */
const ENTRADA: Record<string, unknown> = {
  value:      "123.456.789-00",
  confidence: 1.0,
  source:     "parity",
  visibility: "agents_only",
  updated_at: "2026-09-02T00:00:00.000Z",
}

/** `JSON.stringify` com chaves ordenadas em qualquer profundidade. */
function estavel(v: unknown): string {
  if (v === null || typeof v !== "object") return JSON.stringify(v)
  if (Array.isArray(v)) return `[${v.map(estavel).join(",")}]`
  const o = v as Record<string, unknown>
  return `{${Object.keys(o).sort().map(k => `${JSON.stringify(k)}:${estavel(o[k])}`).join(",")}}`
}

interface Fixture {
  maps:  Record<string, unknown>
  cases: Array<{ name: string; map: string; tag: string; fallback: boolean }>
}

function main(): void {
  const fx = JSON.parse(fs.readFileSync(FIXTURE, "utf-8")) as Fixture

  const indices: Record<string, ContextTagIndex> = {}
  for (const [nome, m] of Object.entries(fx.maps)) {
    // Passa pelo `ContextMapSchema` — o MESMO caminho de produção (`getContextMap` faz
    // `safeParse`). É daqui que sai o `.default()` de `dynamic_prefixes`, e é por isso
    // que o runner não pode aplicá-lo à mão: um runner que remenda o default compensaria
    // a falha em vez de medi-la, e o caso `prefixos_dinamicos_AUSENTES_usam_o_default`
    // ficaria verde mesmo se alguém removesse o `.default()` do schema.
    indices[nome] = buildContextTagIndex(ContextMapSchema.parse(m))
  }

  for (const caso of fx.cases) {
    const out = stampContextEntry(ENTRADA, caso.tag, indices[caso.map]!, Boolean(caso.fallback))
    // Igualdade NATIVA, dentro do runner — nunca atravessa JSON.
    const passagem = Object.entries(ENTRADA).every(([k, v]) => out[k] === v)
    const intacta  = !("atributo" in ENTRADA)
    process.stdout.write(estavel({
      name:            caso.name,
      atributo:        out["atributo"],
      passagem_ok:     passagem,
      entrada_intacta: intacta,
    }) + String.fromCharCode(10))
  }
}

main()
