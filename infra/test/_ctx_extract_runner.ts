/**
 * Metade TS do gate de paridade do EXTRATOR (V4/F2).
 *
 * Lê a fixture única e imprime uma linha por escrita coletada, em ordem estável:
 *
 *     <tag>\t<superficie>\t<step|->\t<dyn|lit>
 *
 * O gêmeo Python (`extrair_de_doc`, no censo) imprime exatamente o mesmo formato, e
 * `probe_context_tag_extractor_parity.sh` compara com `diff`. Nomes de superfície são
 * CONTRATO, não rótulo — ver o comentário em `context-map.ts`.
 *
 * Roda via esbuild `--bundle` sobre o FONTE de `packages/schemas` (nunca sobre `dist/`,
 * que pode estar atrasado). O caminho da fixture vem por argv porque o `__dirname` do
 * bundle resolve para `/tmp`, onde o esbuild o escreve — presumir o caminho aqui já
 * custou um diagnóstico neste arco.
 */
import { readFileSync } from "node:fs"
import { collectContextTagWrites } from "../../packages/schemas/src/context-map"

const caminho = process.argv[2]
if (!caminho) {
  process.stderr.write("uso: runner <fixture.json>\n")
  process.exit(2)
}

const doc = JSON.parse(readFileSync(caminho, "utf-8")) as unknown

const linhas = collectContextTagWrites(doc)
  // A coluna do STEP sai como `-` nos DOIS lados: o extrator do censo agrega por
  // ARQUIVO, não por step, e comparar uma coluna que um dos lados não tem seria comparar
  // a implementação em vez da proposição. O coletor TS a produz de qualquer forma —
  // quem consome o veredicto (a mensagem de publish) precisa dela.
  .map(w => `${w.tag}\t${w.surface}\t-\t${w.dynamic ? "dyn" : "lit"}`)
  .sort()

// `\n` explícito: em bancada Windows o `console.log` pode emitir CRLF, e o `diff` do
// gate acusaria TODAS as linhas por um byte — a divergência real sumiria no ruído.
process.stdout.write(linhas.join("\n") + (linhas.length ? "\n" : ""))
