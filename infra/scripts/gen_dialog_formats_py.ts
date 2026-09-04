/**
 * gen_dialog_formats_py.ts — gera o gêmeo Python do catálogo de formatos.
 *
 * A AUTORIDADE é `@plughub/schemas/dialog-format.ts`. Este script emite
 * `packages/py-contextstore/src/plughub_contextstore/dialog_formats.py` para que
 * os consumidores Python (config-api no seed, channel-gateway na página de
 * survey) leiam a MESMA tabela — não uma cópia digitada.
 *
 * Mesma forma do `default_map.py`: o número de casas não muda (TS + Python), e
 * a divergência não é confiada à disciplina — `infra/test/probe_dialog_formats_parity.sh`
 * regenera e compara.
 *
 * Uso:
 *   docker run --rm -v $PWD:/w -w /w/packages/schemas node:20-alpine \
 *     node_modules/.bin/vite-node ../../infra/scripts/gen_dialog_formats_py.ts
 */
import { writeFileSync } from "node:fs"
import { DEFAULT_DIALOG_FORMAT_CATALOG } from "../../packages/schemas/src/dialog-format"

const DESTINO =
  new URL("../../packages/py-contextstore/src/plughub_contextstore/dialog_formats.py", import.meta.url)

const CABECALHO = `# -*- coding: utf-8 -*-
"""DIALOG_FORMAT_CATALOG — o espelho Python do catálogo de formatos de entrada.

⚠️ **GERADO, nunca digitado à mão.** Autoridade:
\`packages/schemas/src/dialog-format.ts\`. Regenerar com
\`infra/scripts/gen_dialog_formats_py.ts\`; a divergência é reprovada por
\`infra/test/probe_dialog_formats_parity.sh\`.

Ele mora aqui — e não no \`seed.py\` do config-api — pelo mesmo motivo do
\`default_map.py\`: são DOIS consumidores Python (o seed do config-api e o
interpretador da página de survey no channel-gateway), e uma cópia por
consumidor divergiria justamente na hora em que as duas superfícies
precisassem concordar sobre o mesmo veredicto.

⚠️ **Fonte de verdade em runtime é o STORE** (config-api, namespace \`dialog\`,
chave \`formats\`). O seed é seed-if-absent: editar aqui depois de a base estar
semeada é NO-OP. Este valor serve para (a) semear base vazia e (b) ser o
fallback de quem não conseguiu falar com o config-api.

Ver \`docs/adr/adr-dialog-input-format-catalog.md\`.
"""
from __future__ import annotations

from typing import Any

`

function py(v: unknown, ind: number): string {
  const pad = " ".repeat(ind)
  const pad2 = " ".repeat(ind + 4)
  if (v === null || v === undefined) return "None"
  if (typeof v === "boolean") return v ? "True" : "False"
  if (typeof v === "number") return String(v)
  if (typeof v === "string") return JSON.stringify(v)
  if (Array.isArray(v)) {
    if (!v.length) return "[]"
    return "[\n" + v.map(x => pad2 + py(x, ind + 4)).join(",\n") + `,\n${pad}]`
  }
  const e = Object.entries(v as Record<string, unknown>)
  if (!e.length) return "{}"
  return "{\n" + e.map(([k, x]) => `${pad2}${JSON.stringify(k)}: ${py(x, ind + 4)}`).join(",\n")
       + `,\n${pad}}`
}

const corpo =
  "DIALOG_FORMAT_CATALOG: dict[str, Any] = " + py(DEFAULT_DIALOG_FORMAT_CATALOG, 0) + "\n"

writeFileSync(DESTINO, CABECALHO + corpo, "utf-8")
console.log(
  `dialog_formats.py gerado — ${DEFAULT_DIALOG_FORMAT_CATALOG.formats.length} formatos`,
)
