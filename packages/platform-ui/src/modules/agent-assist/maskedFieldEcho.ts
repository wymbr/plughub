/**
 * maskedFieldEcho — o que aparece no lugar do valor de um campo mascarado.
 *
 * **Remove-se o VALOR, nunca o CAMPO** (decisão do dono, 2026-09-10), e preenchido
 * difere de vazio:
 *
 *     preenchido → `••••••`   ·   vazio → `""`
 *
 * ── Por que isto é um módulo PRÓPRIO, e não uma função dentro da página ──────
 *
 * A mesma regra vive em TRÊS casas: aqui (eco OTIMISTA do Console, que aparece
 * antes do round-trip), no `orchestrator-bridge` (eco ao vivo, que chega depois) e
 * no adapter de webchat do `channel-gateway` (histórico, que a MESMA tela relê num
 * F5). São três serviços em duas linguagens, e o Console **não importa**
 * `@plughub/schemas` por decisão declarada — logo a concordância não pode ser por
 * importação.
 *
 * Prometê-la em prosa é o defeito que este repositório cataloga; quem a impõe é
 * `infra/test/probe_masked_field_echo_parity.sh`, que COMPILA este arquivo e o roda
 * contra a mesma tabela de casos das duas implementações Python. Por isso ele não
 * importa nada, não toca no DOM e não depende de React: um módulo puro é o que
 * torna a paridade executável em vez de afirmada.
 *
 * ⚠️ Se as duas pontas do eco divergirem, o campo **pisca** — aparece no otimista e
 * some quando o bridge responde, ou o contrário. Foi por isso que a ALW-10 já pedia
 * que as duas concordassem; o que faltava era o mecanismo.
 *
 * ⚠️ **`0` e `false` NÃO são vazios.** `if (!v)` marcaria um `0` digitado como *"o
 * cliente não preencheu"* — o defeito de truthiness que o `CLAUDE.md` cataloga.
 */
export const MASKED_FIELD_PLACEHOLDER = "••••••"
export const EMPTY_FIELD = ""

export function maskedFieldEcho(value: unknown): string {
  if (value === null || value === undefined) return EMPTY_FIELD
  if (typeof value === "string") {
    return value.trim() === "" ? EMPTY_FIELD : MASKED_FIELD_PLACEHOLDER
  }
  if (Array.isArray(value)) {
    return value.length === 0 ? EMPTY_FIELD : MASKED_FIELD_PLACEHOLDER
  }
  if (typeof value === "object") {
    return Object.keys(value as Record<string, unknown>).length === 0
      ? EMPTY_FIELD
      : MASKED_FIELD_PLACEHOLDER
  }
  return MASKED_FIELD_PLACEHOLDER
}
