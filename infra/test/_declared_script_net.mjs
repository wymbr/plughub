/**
 * _declared_script_net.mjs — roda a REDE de texto livre sobre textos de roteiro.
 *
 * Lê um JSON `[{form_id, node_id, texto}, …]` no stdin e imprime uma linha TSV por
 * texto que a rede ALTERARIA:  form_id \t node_id \t original \t alterado
 *
 * ⚠️ **Importa a rede, nunca a reescreve.** `maskFreeText` vem de `@plughub/schemas`,
 * a MESMA função que o engine chama em voo. Uma cópia de regex aqui poderia dizer que
 * está tudo bem enquanto o produto mascara — que é literalmente o defeito que este
 * probe existe para pegar.
 *
 * Sai 2 se a rede não carregar: instrumento morto não é medição verde.
 */
import { readFileSync } from "node:fs"

let maskFreeText, freeTextNetSize
try {
  // O pacote é CJS: dependendo da versão do Node os nomeados vêm na raiz do
  // namespace ou sob `default`. Aceitar os dois evita um INCONCLUSIVO que não é
  // sobre o produto.
  const m = await import("@plughub/schemas")
  const ns = typeof m.maskFreeText === "function" ? m : (m.default ?? m)
  maskFreeText   = ns.maskFreeText
  freeTextNetSize = ns.freeTextNetSize
} catch (e) {
  console.error(`REDE_INDISPONIVEL ${String(e)}`)
  process.exit(2)
}

if (typeof maskFreeText !== "function" || freeTextNetSize() === 0) {
  console.error("REDE_VAZIA nenhuma regra compilou")
  process.exit(2)
}

let entrada
try {
  entrada = JSON.parse(readFileSync(0, "utf8"))
} catch (e) {
  console.error(`ENTRADA_INVALIDA ${String(e)}`)
  process.exit(2)
}

const limpa = (s) => String(s).replace(/\t/g, " ").trim()

/**
 * ⚠️ Compara LINHA a LINHA, e isso é requisito do consumidor, não estilo.
 *
 * A linha alterada vira a AGULHA de uma busca em `messages` (ClickHouse). Um texto de
 * roteiro é multi-linha; devolvê-lo inteiro obrigaria a normalizar quebras dos dois
 * lados, e uma normalização que erre por um caractere devolve "zero ocorrências" —
 * um zero plausível, que é exatamente o que este probe não pode produzir.
 */
for (const item of Array.isArray(entrada) ? entrada : []) {
  const texto = item?.texto
  if (typeof texto !== "string" || texto === "") continue
  for (const linha of texto.split(/\r?\n/)) {
    const bruta = limpa(linha)
    if (bruta.length < 4) continue
    const r = maskFreeText(bruta)
    if (r.categories.length === 0) continue
    console.log([item.form_id ?? "?", item.node_id ?? "?", bruta, limpa(r.value)].join("\t"))
  }
}
