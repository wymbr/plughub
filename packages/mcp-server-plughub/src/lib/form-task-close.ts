/**
 * form-task-close.ts — encerrar uma TAREFA DE FORMULÁRIO pendente é devolvê-la à
 * fila, nunca fechá-la (PUL-07).
 *
 * O defeito, medido em 2026-09-11: `5120fe90` e `4841c60d` eram wrap-ups
 * reivindicados cujo formulário abriu VAZIO (CNS-24). Sem as tags no snapshot, o
 * Console não reconheceu a tarefa, mostrou a barra de atendimento COM "Encerrar",
 * e o fechamento invalidou o `resume_token` (`cancel_pending_resumes`, RET-03) —
 * a tabulação dos dois contatos está perdida e é irrecuperável.
 *
 * ⚠️ **Por que a decisão NÃO pode morar na tela.** O Console já troca a barra por
 * "Return to queue" quando `isFormFillSnapshot` dá verdadeiro; foi essa checagem
 * que falhou, porque ela depende do snapshot renderizado — exatamente a coisa que
 * pode faltar. Perguntar ao mesmo dado que falhou é repetir o defeito com outro
 * nome. O fato que decide é do SERVIDOR: existe item parqueado no ledger
 * (`{t}:work_task:{sid}`) com token vivo? É a mesma regra do D5 ("a tela não é
 * fonte de posse"), aplicada ao fechamento.
 *
 * Pura, pelo mesmo motivo de `shouldDropOnPossession` e `decideLedgerRehydration`.
 */

export interface FormTaskState {
  /** Existe `{t}:work_task:{sid}` — a sessão é uma tarefa PARQUEADA. */
  ledgerPresent:  boolean
  /** O `resume_token` do ledger ainda está em `{t}:resume_tokens`?
   *  `null` = não conferido (leitura falhou / ledger sem token). */
  tokenAlive:     boolean | null
  /** Quem detém o item, por lease ou registro durável. `null` = ninguém. */
  holderInstance: string | null
}

export type CloseAction = "close" | "return_to_queue" | "refuse"

export interface CloseVerdict {
  action: CloseAction
  /** Sempre preenchido — inclusive no `close`, que é o caminho normal. */
  reason: string
}

/**
 * @param state          o que o servidor leu do ledger para ESTA sessão
 * @param callerInstance a instância de quem está fechando (`human-{userId}`)
 *
 * | Estado                                   | Ação |
 * |---|---|
 * | sem item no ledger                       | `close` — contato normal, nada muda |
 * | item, token CANCELADO                    | `close` — não há o que preservar (item morto da PUL-06) |
 * | item vivo, detido por MIM                | `return_to_queue` — o trabalho continua existindo |
 * | item vivo, detido por OUTRO              | `refuse` — matar o token alheio é o defeito, com outro dono |
 * | item vivo, sem dono                      | `refuse` — devolver exige posse; encerrar é do supervisor |
 * | item vivo, chamador sem identidade       | `refuse` — sem instância não há release possível |
 *
 * ⚠️ Token NÃO conferido conta como VIVO: desconhecido não é morto, e o custo dos
 * dois erros é assimétrico — devolver à fila por engano é reversível (basta
 * reivindicar de novo); fechar por engano apaga o token e a tabulação.
 */
export function decideFormTaskClose(
  state:          FormTaskState,
  callerInstance: string,
): CloseVerdict {
  if (!state.ledgerPresent)      return { action: "close", reason: "no_work_item" }
  if (state.tokenAlive === false) return { action: "close", reason: "token_gone" }
  if (!callerInstance)            return { action: "refuse", reason: "caller_unknown" }
  if (!state.holderInstance)      return { action: "refuse", reason: "no_holder" }
  if (state.holderInstance !== callerInstance) {
    return { action: "refuse", reason: `held_by_other:${state.holderInstance}` }
  }
  return {
    action: "return_to_queue",
    reason: state.tokenAlive === null ? "form_task_pending:token_unverified" : "form_task_pending",
  }
}
