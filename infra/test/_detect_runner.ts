/**
 * Runner TypeScript da paridade do validador de DETECÇÃO (CTX-12).
 * Par: `_detect_runner.py`. Os dois têm de imprimir exatamente as mesmas linhas.
 *
 * Importa do FONTE do schemas (o esbuild empacota a fonte), nunca do `dist/` — um build
 * atrasado mediria a casa errada, pela mesma razão do `_mask_runner.ts`.
 */
import * as fs   from "fs"
import * as path from "path"
import { passesDetectValidator } from "../../packages/schemas/src/audit"
import { maskFreeText }          from "../../packages/schemas/src/ctx-audience"

const FIXTURE = process.argv[2] ?? path.join(__dirname, "fixtures", "detect_validator_cases.json")

interface Fx {
  validator_cases: Array<{ name: string; validator: string | null; match: string }>
  text_cases:      Array<{ name: string; text: string }>
}

function linha(o: Record<string, unknown>): void {
  const chaves = Object.keys(o).sort()
  process.stdout.write(`{${chaves.map(k => `${JSON.stringify(k)}:${JSON.stringify(o[k])}`).join(",")}}` +
                       String.fromCharCode(10))
}

const fx = JSON.parse(fs.readFileSync(FIXTURE, "utf-8")) as Fx
for (const c of fx.validator_cases) {
  linha({ name: c.name, passa: passesDetectValidator(c.validator ?? undefined, c.match) })
}
// A CATEGORIA é o que se compara no texto. O display foi unificado na MSK-05 (2026-09-25:
// `detectedDisplay`, o by_role do catálogo); a paridade dele é do probe_masking_display_parity.
for (const c of fx.text_cases) {
  const r = maskFreeText(c.text)
  linha({ name: c.name, categorias: [...new Set(r.categories)].sort() })
}
