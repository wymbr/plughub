/**
 * Runner TypeScript da fixture de paridade da APLICACAO de mascara.
 * Par: `_mask_runner.py`. Ver o cabecalho de `_stamp_runner.ts`.
 */
import * as fs   from "fs"
import * as path from "path"
// As DUAS metades vêm do FONTE do schemas, e não do `dist/` — o bundle do esbuild
// puxa a fonte junto, então o runner nunca mede um build atrasado.
//
// ⚠️ `applyMaskingTypeToValue` era importada de
// `mcp-server-plughub/src/lib/context-masking` até 2026-09-04 (CTX-07), quando o
// corpo mudou de casa. O mcp-server ainda a reexporta, e importar de lá continuaria
// COMPILANDO e passando — medindo a casa que repassa em vez da que decide. Um
// gêmeo divergente introduzido no schemas ficaria verde.
import {
  applyMaskingTypeToValue, resolveMaskForAudience,
} from "../../packages/schemas/src/ctx-audience"
import type { ContextMaskingType, DataType } from "@plughub/schemas"

const FIXTURE = process.argv[2]
  ?? path.join(__dirname, "fixtures", "masking_apply_cases.json")

function estavel(v: unknown): string {
  if (v === null || typeof v !== "object") return JSON.stringify(v)
  if (Array.isArray(v)) return `[${v.map(estavel).join(",")}]`
  const o = v as Record<string, unknown>
  return `{${Object.keys(o).sort().map(k => `${JSON.stringify(k)}:${estavel(o[k])}`).join(",")}}`
}

interface Fx {
  cases: Array<{ name: string; mask: string; raw: string }>
  resolve_cases?: Array<{ name: string; audiencia: string; tipo: unknown }>
}

const fx = JSON.parse(fs.readFileSync(FIXTURE, "utf-8")) as Fx
for (const c of fx.cases) {
  const out = applyMaskingTypeToValue(c.raw, c.mask as ContextMaskingType)
  process.stdout.write(estavel({ name: c.name, out }) + String.fromCharCode(10))
}
// Segunda metade: o RESOLVEDOR. A fixture entrega a entrada de tipo CRUA, como a
// config a serve — passar por um parser Zod aqui mediria o parser, não o gêmeo.
for (const c of fx.resolve_cases ?? []) {
  const out = resolveMaskForAudience(
    (c.tipo ?? undefined) as DataType | undefined, c.audiencia)
  process.stdout.write(estavel({ name: c.name, out }) + String.fromCharCode(10))
}
