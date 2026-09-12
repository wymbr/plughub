/**
 * ledger-rehydration.ts — o Console reencontra o item EM POSSE pelo LEDGER (PUL-05).
 *
 * Até aqui a reconexão do Console só tinha uma fonte: `pool:pending_assignment:{pool}`,
 * que o bridge grava na ativação com TTL de 300 s e que é UMA chave por pool
 * (last-write wins). Item reivindicado que não abrisse em 5 min — ou que perdesse a
 * vez para outro contato do mesmo pool — continuava EM POSSE (`claim_record`, A5):
 * ninguém mais o pegava e o próprio dono não o via. Medido em 2026-09-11 no
 * `0596f383` do operator@: token vivo, formulário suspenso, invisível no Console.
 *
 * A fonte que não expira em 5 min já existia: o ledger `{t}:work_task:{sid}`, o
 * mesmo que `/api/work_queue/pending` lê. Esta função decide, sobre os itens já
 * classificados por `listPendingWorkTasks`, quais REENTREGAR ao agente que conecta.
 *
 * Pura de propósito, como `shouldDropOnPossession`: a regra fica testável fora do
 * WebSocket, onde exercitá-la exigiria Redis, pub/sub e um socket.
 *
 * ⚠️ Posse vem da CLASSIFICAÇÃO, não de uma chamada ao árbitro: `listPendingWorkTasks`
 * lê lease → registro na MESMA ordem de `work_task_holder`, e é isso que o impede de
 * divergir dele. Um HTTP por item só repetiria a leitura.
 *
 * ⚠️ Os dois descartes da PUL-06 são o que torna esta reentrega segura: item com a
 * sessão FECHADA ou com o token CANCELADO continua no ledger, em posse, por até 25 h —
 * e nenhuma ação na tela o completa (o submit e o Close retomam pelo token). Entregá-lo
 * poria de volta no Console exatamente a tela que não tem saída.
 */

/** O recorte de `PendingWorkTask` que a decisão lê. */
export interface LedgerItemView {
  session_id:       string
  queue_session_id: string
  pool_id:          string
  state:            string
  claimed_by:       string | null
  claimed_at:       string | null
}

export interface LedgerCandidate {
  item: LedgerItemView
  /** O `resume_token` do ledger ainda está em `{t}:resume_tokens`?
   *  `null` = não foi possível conferir (leitura falhou, ledger sem token). */
  tokenAlive:   boolean | null
  /** Valor de `session:{queue_session_id}:closed`, ou `null` se ausente. */
  closedMarker: string | null
}

export interface RehydratedAssignment {
  type:        "conversation.assigned"
  session_id:  string
  pool_id:     string
  instance_id: string
  /** Âncora do relógio do Console: o CLAIM, que é quando o agente passou a dever. */
  assigned_at?: string
  /** Rastro: quem o Console está vendo foi reentregue pelo ledger, não pelo bridge. */
  source:      "work_ledger"
}

export interface RehydrationDecision {
  session_id: string
  deliver:    boolean
  /** Sempre preenchido — entrega ou descarte sem motivo legível é indepurável. */
  reason:     string
  event?:     RehydratedAssignment
}

/**
 * @param candidates         itens do ledger do pool desta conexão, já classificados
 * @param expectedInstanceId a instância desta conexão (`human-{userId}`), ou `""`
 * @param alreadyDelivered   `queue_session_id`s que a chave por pool já entregou
 */
export function decideLedgerRehydration(
  candidates:         readonly LedgerCandidate[],
  expectedInstanceId: string,
  alreadyDelivered:   ReadonlySet<string>,
): RehydrationDecision[] {
  return candidates.map(({ item, tokenAlive, closedMarker }) => {
    const sid = item.queue_session_id || item.session_id
    const skip = (reason: string): RehydrationDecision => ({ session_id: sid, deliver: false, reason })

    // Sem identidade não há com o que comparar a posse — e reentregar item de
    // ledger a um cliente anônimo seria inventar a comparação.
    if (!expectedInstanceId)                return skip("legacy_client_no_identity")
    // `unclaimed` mora na inbox da fila pull; `orphaned`/`not_queued` não são de ninguém.
    if (item.state !== "claimed")           return skip(`not_claimed:${item.state}`)
    if (item.claimed_by !== expectedInstanceId) {
      return skip(`held_by_other:${item.claimed_by ?? "?"}`)
    }
    if (alreadyDelivered.has(sid))          return skip("already_delivered")
    if (closedMarker)                       return skip(`session_closed:${closedMarker}`)
    if (tokenAlive === false)               return skip("token_gone")

    const event: RehydratedAssignment = {
      type:        "conversation.assigned",
      session_id:  sid,
      pool_id:     item.pool_id,
      instance_id: expectedInstanceId,
      ...(item.claimed_at ? { assigned_at: item.claimed_at } : {}),
      source:      "work_ledger",
    }
    // DESCONHECIDO ≠ morto: entrega, como `arbiter_unreachable` no D5. O A5 no
    // ingress de resume continua sendo quem decide o submit.
    return {
      session_id: sid,
      deliver:    true,
      reason:     tokenAlive === null ? "held_by_me:token_unverified" : "held_by_me",
      event,
    }
  })
}
