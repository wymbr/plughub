/**
 * Runner TypeScript da fixture de paridade da APLICACAO de mascara.
 * Par: `_mask_runner.py`. Ver o cabecalho de `_stamp_runner.ts`.
 */
import * as fs   from "fs"
import * as path from "path"
import { applyMaskingTypeToValue } from "../../packages/mcp-server-plughub/src/lib/context-masking"
import type { ContextMaskingType } from "@plughub/schemas"

const FIXTURE = process.argv[2]
  ?? path.join(__dirname, "fixtures", "masking_apply_cases.json")

function estavel(v: unknown): string {
  if (v === null || typeof v !== "object") return JSON.stringify(v)
  if (Array.isArray(v)) return `[${v.map(estavel).join(",")}]`
  const o = v as Record<string, unknown>
  return `{${Object.keys(o).sort().map(k => `${JSON.stringify(k)}:${estavel(o[k])}`).join(",")}}`
}

interface Fx { cases: Array<{ name: string; mask: string; raw: string }> }

const fx = JSON.parse(fs.readFileSync(FIXTURE, "utf-8")) as Fx
for (const c of fx.cases) {
  const out = applyMaskingTypeToValue(c.raw, c.mask as ContextMaskingType)
  process.stdout.write(estavel({ name: c.name, out }) + String.fromCharCode(10))
}
