/**
 * Roda `maskedFieldEcho` do Console contra a MESMA tabela das duas casas Python.
 *
 * Compilado com o `tsc` de verdade e executado — não lido por regex. Um censo
 * textual diria que a função existe; só a execução diz o que ela devolve, e é a
 * devolução que precisa concordar.
 *
 * Uso (dentro do container):  node _masked_field_echo_ts.js < casos.json
 * Saída: uma linha por caso, `<indice>\t<saida>`.
 */
import { readFileSync } from "node:fs"
import { maskedFieldEcho } from "./maskedFieldEcho"

let casos: unknown[]
try {
  casos = JSON.parse(readFileSync(0, "utf8")) as unknown[]
} catch (e) {
  console.error(`CASOS_INVALIDOS ${String(e)}`)
  process.exit(2)
}

if (typeof maskedFieldEcho !== "function") {
  console.error("SEM_FUNCAO maskedFieldEcho")
  process.exit(2)
}

casos.forEach((caso, i) => {
  console.log(`${i}\t${maskedFieldEcho(caso)}`)
})
