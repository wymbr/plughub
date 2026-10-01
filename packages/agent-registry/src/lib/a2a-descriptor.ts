/**
 * lib/a2a-descriptor.ts — o canal `a2a` e o descritor do pool andam JUNTOS (AAS-01;
 * adr-a2a-server-binding D3).
 *
 * Um predicado só, chamado pelo POST e pelo PUT sobre o ESTADO RESULTANTE, pela mesma razão do
 * `media_policy` (VOZ-10): validar só o corpo deixaria um PUT que adiciona `a2a` a
 * `channel_types` passar sem contrato, e um que limpa o descritor deixar o pool exposto sem ele.
 *
 *   - pool de CONTATO com `a2a` em `channel_types` e SEM descritor → recusa: pool exposto sem
 *     contrato é o defeito que o descritor existe para impedir (o AgentCard é projeção dele, D2).
 *   - descritor SEM `a2a` em `channel_types` → recusa: seria a segunda casa de "está exposto"
 *     (o `exposed: boolean` da v1 saiu por isso, rev. 2). Vale para qualquer `purpose`.
 *
 * Por que só `purpose: contact` exige: o espelho de fila interna (`-int`) herda os canais do pai,
 * mas é trabalho do operador, sem chamador externo — exigir ali forçaria um contrato inventado
 * (mesmo critério do `media_policy`). Consequência para quem projeta o card (AAS-03): ele lista
 * SÓ pools de contato.
 */

export function a2aDescriptorViolation(
  channelTypes: readonly string[] | null | undefined,
  purpose:      string | null | undefined,
  descriptor:   unknown,
): { error: string; details: Record<string, unknown> } | null {
  const hasChannel = (channelTypes ?? []).includes("a2a")
  const isContact  = (purpose ?? "contact") === "contact"
  if (descriptor != null && !hasChannel) {
    return {
      error:
        "`a2a` (descritor) sem `a2a` em channel_types — o canal é o opt-in e o descritor é o " +
        "contrato; um sem o outro é a segunda casa de \"está exposto\". Adicione o canal ou " +
        "limpe o descritor (`a2a: null`).",
      details: { field: "a2a", channel_types: channelTypes },
    }
  }
  if (hasChannel && isContact && descriptor == null) {
    return {
      error:
        "pool de contato com `a2a` em channel_types exige o descritor `a2a` — display_name, " +
        "description, input_schema, output_schema, skills e principal_kinds. O AgentCard é " +
        "projeção dele; pool exposto sem contrato é o que esta regra impede.",
      details: { field: "a2a", channel_types: channelTypes, purpose: purpose ?? "contact" },
    }
  }
  return null
}
