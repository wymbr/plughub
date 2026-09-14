/**
 * lib/media-policy.ts — pool de CONTATO com `webrtc` declara as mídias que oferece (VOZ-10).
 *
 * Um predicado só, chamado pelo POST e pelo PUT sobre o ESTADO RESULTANTE (campo novo ou
 * existente), pela mesma razão do `queue_config ⇒ agent_kind human`: validar só o corpo
 * deixaria um PUT que adiciona `webrtc` a `channel_types` passar sem política.
 *
 * Por que recusar em vez de assumir um default: no modelo antigo a ausência virava
 * `text` em silêncio, e um default aqui seria a mesma coisa com outro valor. Pool que
 * atende por WebRTC só com texto declara as duas listas VAZIAS — explícito.
 *
 * Por que só `purpose: contact`: o espelho de fila interna (`-int`) herda os canais do
 * pai, mas é trabalho do operador (pós-atendimento), sem cliente na sala — não há mídia
 * a oferecer, e exigir ali forçaria um valor inventado.
 */

export function mediaPolicyViolation(
  channelTypes: readonly string[] | null | undefined,
  purpose:      string | null | undefined,
  mediaPolicy:  unknown,
): { error: string; details: Record<string, unknown> } | null {
  const isWebrtc  = (channelTypes ?? []).includes("webrtc")
  const isContact = (purpose ?? "contact") === "contact"
  if (!isWebrtc || !isContact || mediaPolicy != null) return null
  return {
    error:
      "pool de contato com `webrtc` em channel_types exige `media_policy` — declare o que o " +
      "cliente (`customer_publish`) e o atendente (`agent_publish`) podem publicar; listas " +
      "VAZIAS significam só texto. Não há default: a ausência virava `text` em silêncio.",
    details: { field: "media_policy", channel_types: channelTypes, purpose: purpose ?? "contact" },
  }
}
