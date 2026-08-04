/**
 * assignment-filter.ts
 * Targeted-assignment filter for the Agent Assist WebSocket.
 *
 * `conversation.assigned` is published to the pool-wide channel
 * `pool:events:{poolId}`, so EVERY agent connected to a pool receives it. But a
 * contact is routed to exactly ONE instance (registerHumanAgent registers the
 * human as `human-{userId}` and the Routing Engine allocates that instance into
 * the event's `instance_id`). Without filtering, two agents in the same pool
 * (e.g. admin + operator) would both see — and serve — the same contact.
 *
 * `shouldDropAssignment` decides whether the WS connection must DROP an event
 * because it targets a different agent. It is intentionally conservative
 * (backward-compatible): it never drops anything it is not sure about.
 */

/**
 * @param eventType          the event `type` field
 * @param eventInstanceId    the event `instance_id` field (the routed target)
 * @param expectedInstanceId this connection's own instance ("human-{userId}"), or
 *                           "" for legacy clients that did not send a user_id
 * @returns true when the event must NOT be forwarded to this connection.
 *
 * Never drops when:
 *   - expectedInstanceId is "" (legacy/unknown identity) → preserve old behaviour;
 *   - the event is not a conversation.assigned;
 *   - the event carries no target instance_id (defensive — never over-filter).
 */
export function shouldDropAssignment(
  eventType:          unknown,
  eventInstanceId:    unknown,
  expectedInstanceId: string,
): boolean {
  if (!expectedInstanceId) return false
  if (eventType !== "conversation.assigned") return false
  const target = typeof eventInstanceId === "string" ? eventInstanceId : ""
  return target !== "" && target !== expectedInstanceId
}

// ── Fase D (D5) — a TELA não é fonte de posse ────────────────────────────────

/** Veredicto do árbitro, na forma que este módulo precisa (ver lib/work-queue). */
export interface PossessionVerdict {
  found:        boolean
  instance_id?: string | undefined
  via?:         string | undefined
  in_queue:     boolean
}

export interface DropDecision {
  drop:   boolean
  /** Sempre preenchido — inclusive quando `drop=false`. Um descarte (ou uma
   *  entrega) sem motivo legível é o que torna este caminho indepurável. */
  reason: string
}

/**
 * Decide se um `conversation.assigned` REPLICADO na reconexão deve ser entregue,
 * conferindo a POSSE contra o árbitro em vez de confiar no evento.
 *
 * Existe porque `pool:pending_assignment:{pool}` sobrevive ao F5 e é reentregue
 * ao agente que reconecta. Duas guardas já cobriam "sessão fechada" e "assignment
 * de outro agente" — e nenhuma cobre o caso do item de fila pull devolvido: a
 * workflow segue SUSPENSA (não há `session:closed`) e o `instance_id` BATE (é o
 * mesmo agente). O resultado era o formulário órfão na tela sobre um item que já
 * estava de volta na fila.
 *
 * Quatro ramos, os mesmos do submit (Fase A) e do drop (Fase B) — a regra de
 * posse existe num formato só, em três lugares:
 *
 * | Estado do árbitro                 | Decisão |
 * |---|---|
 * | detido por mim                    | entrega |
 * | detido por outro                  | DESCARTA |
 * | ninguém detém **e** está na fila  | **DESCARTA** (o que a Fase D fecha) |
 * | ninguém detém e fora da fila      | entrega — ausência honesta (push, encerrado, legado) |
 * | árbitro sem resposta (`null`)     | entrega, com aviso — falha de rede não recusa reconexão |
 *
 * Conservador como o `shouldDropAssignment`: `expectedInstanceId` vazio (cliente
 * legado, sem identidade) nunca descarta por posse — não há com o que comparar, e
 * inventar uma comparação seria pior que não fazê-la.
 */
export function shouldDropOnPossession(
  holder:             PossessionVerdict | null,
  expectedInstanceId: string,
): DropDecision {
  if (holder === null) {
    return { drop: false, reason: "arbiter_unreachable" }
  }
  if (holder.found) {
    if (!expectedInstanceId) return { drop: false, reason: "legacy_client_no_identity" }
    if (holder.instance_id && holder.instance_id !== expectedInstanceId) {
      return { drop: true, reason: `held_by_other:${holder.instance_id}` }
    }
    return { drop: false, reason: `held_by_me:${holder.via ?? "?"}` }
  }
  if (holder.in_queue) {
    return { drop: true, reason: "back_in_queue" }
  }
  return { drop: false, reason: "no_work_item" }
}
