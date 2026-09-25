// _skill_yaml_strict.js — metade Node do probe_skill_yaml_strict.sh (SFE-07).
// Recebe um diretório de flows já convertidos para JSON (um arquivo por skill) e, para cada
// um, roda o SkillFlowSchema COMPILADO. Imprime uma linha por achado:
//   PARSE_FAIL <skill> <caminho> <mensagem>   — o schema recusa (inclui campo desconhecido
//                                               nos schemas `.strict()`)
//   LOST <skill> <step>:<caminho>             — o parse aceitou e DESCARTOU o campo
//   OK <n>                                    — total de flows que passaram limpos
// Não reimplementa a regra: quem decide é o schema que o registry usa.
const fs = require("fs")
const path = require("path")
const root = path.resolve(__dirname, "../..")
const { SkillFlowSchema } = require(path.join(root, "packages/schemas/dist/index.js"))

const dir = process.argv[2]
const lost = (a, b, p, out) => {
  if (a && typeof a === "object" && !Array.isArray(a)) {
    for (const k of Object.keys(a)) {
      if (b === null || b === undefined || typeof b !== "object" || !(k in b)) out.push(p + "." + k)
      else lost(a[k], b[k], p + "." + k, out)
    }
  } else if (Array.isArray(a) && Array.isArray(b)) {
    a.forEach((x, i) => lost(x, b[i], p + "[" + i + "]", out))
  }
  return out
}
let clean = 0
for (const f of fs.readdirSync(dir).sort()) {
  const skill = f.replace(/\.json$/, "")
  const raw = JSON.parse(fs.readFileSync(path.join(dir, f), "utf8"))
  const r = SkillFlowSchema.safeParse(raw)
  if (!r.success) {
    for (const i of r.error.issues.slice(0, 5)) {
      console.log(`PARSE_FAIL ${skill} ${i.path.join(".")} ${i.message}`)
    }
    continue
  }
  const found = []
  raw.steps.forEach((st, i) => {
    for (const p of lost(st, r.data.steps[i], "", [])) found.push(`${st.id}:${p.slice(1)}`)
  })
  if (found.length) for (const x of found) console.log(`LOST ${skill} ${x}`)
  else clean++
}
console.log(`OK ${clean}`)
