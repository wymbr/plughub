/**
 * required-config.ts — deploy de skill que DECLARA parâmetro obrigatório sem o
 * pool o ter preenchido.
 *
 * Irmão de `masked-deploy.ts` e `profile-steps.ts`, e de propósito: mesma
 * pergunta ("este par (skill, slot) pode rodar?"), mesmo momento (set-next e
 * promote), mesma forma de veredicto. Rollback fica isento, como nos outros dois.
 *
 * ── Por que a recusa existe ─────────────────────────────────────────────────
 *
 * `config_params` é a declaração do skill; `PoolSkillSlot.config_json` é a
 * resposta do deploy; o bridge injeta o config_json como `$.config.*` no launch.
 * Quando o skill exige `form_id` e o slot não o tem, `$.config.form_id` não
 * resolve — e o modo de falha NÃO é uma queda: é o pool subir saudável, aceitar
 * contato e o primeiro step falhar, mandando todo mundo para o `on_failure`.
 * No `skill_navegacao_v1` isso significa um pool que existe, parece deployado, e
 * escala 100% dos contatos ao humano. Nada fica vermelho.
 *
 * A tela marca `required`, mas validação só de UI é promessa sem mecanismo — a
 * mesma família que a `DLG-33` registrou. Quem decide o que roda é o promote.
 *
 * ── O que NÃO é consultado, e é decisão ─────────────────────────────────────
 *
 * O `default` do descritor. Quem escreve o `config_json` é que aplica (ou não) o
 * default; o runtime lê o `config_json`, então é ELE que decide o que roda —
 * consultar o default aqui aprovaria um slot cujo valor ninguém gravou.
 * Medido em 2026-09-08: dos 3 skills que declaram parâmetro no registry, nenhum
 * tem `required: true` com `default`, então a regra nasce sem caso ambíguo.
 *
 * ── `required: false` nunca reprova ─────────────────────────────────────────
 *
 * O `grain` do `skill_survey_outbound_v1` é opcional de propósito. A regra é
 * sobre obrigatórios, e alargá-la transformaria descritor em formulário.
 */

/** Forma mínima do descritor; o schema canônico é `SkillConfigParamSchema`. */
export type ConfigParamLike = {
  key:       string
  label?:    string
  required?: boolean
}

export type RequiredConfigVerdict =
  | { kind: "ok" }
  | { kind: "block"; error: string; message: string }

/**
 * VAZIO é mais largo que ausente, e cada caso foi escolhido:
 *   ausente/`null`/`undefined` — nunca gravado;
 *   string só de espaço        — o operador "preencheu" sem preencher;
 *   `[]` e `{}`                — o `channel_policy` vazio é tão inútil quanto
 *                                ausente, e um `type: object` chega assim quando
 *                                alguém abre o editor de JSON e salva.
 * `0` e `false` NÃO são vazios: são valores legítimos de `number`/`boolean`, e
 * tratá-los como ausência é o defeito de truthiness que este repositório já
 * cataloga (`if not x` × `is None`).
 */
function vazio(v: unknown): boolean {
  if (v === undefined || v === null) return true
  if (typeof v === "string") return v.trim() === ""
  if (Array.isArray(v)) return v.length === 0
  if (typeof v === "object") return Object.keys(v as object).length === 0
  return false
}

/**
 * Julga um par (parâmetros declarados pelo skill, config_json do slot).
 *
 * ⚠️ **Skill sem `config_params` é `ok`** — a esmagadora maioria (medido: 30 de
 * 31 slots `current` em 2026-09-08). Não declarar é o estado normal, não uma
 * omissão a punir; a regra só vale para quem declarou.
 */
export function judgeRequiredConfig(
  configParams: unknown,
  configJson:   unknown,
  ctx:          { poolId: string; skillId: string },
): RequiredConfigVerdict {
  if (!Array.isArray(configParams) || configParams.length === 0) return { kind: "ok" }

  const cfg = (configJson && typeof configJson === "object" && !Array.isArray(configJson))
    ? (configJson as Record<string, unknown>)
    : {}

  const faltando = (configParams as ConfigParamLike[])
    .filter(p => p && typeof p.key === "string" && p.required === true)
    .filter(p => vazio(cfg[p.key]))

  if (faltando.length === 0) return { kind: "ok" }

  const nomes = faltando
    .map(p => (p.label ? `${p.key} ("${p.label}")` : p.key))
    .join(", ")

  return {
    kind:  "block",
    error: "config_obrigatoria_ausente",
    message:
      `O skill '${ctx.skillId}' declara parâmetro(s) de deploy obrigatório(s) que o pool ` +
      `'${ctx.poolId}' não preencheu: ${nomes}. ` +
      `O bridge injeta o \`config_json\` do slot como \`$.config.*\`; sem esse valor a ` +
      `referência não resolve e o step que a usa falha no primeiro contato — o pool sobe, ` +
      `parece deployado e não faz o trabalho dele. Preencha em Flow › Deploy antes de promover.`,
  }
}
