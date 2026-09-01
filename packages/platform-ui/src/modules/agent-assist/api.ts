/**
 * api.ts — leituras REST do módulo Agent Assist.
 *
 * ── Por que o histórico tem UM carregador, e não três ─────────────────────────
 *
 * `GET /api/conversation_history/{sessionId}` tinha três chamadores
 * (`AgentAssistContext.fetchHistory`, o preview de fila em `AgentAssistPage` e o
 * briefing do `DialogFormRenderer`), e os três decidiam sozinhos o que fazer com
 * a falha — todos da mesma forma: `res.ok ? json : { messages: [] }`. Três cópias
 * da mesma decisão é como elas divergem; pior, esta em particular convertia
 * QUALQUER falha em **histórico vazio**, que é um valor plausível.
 *
 * Custou um diagnóstico inteiro em 2026-09-01: a rota passou a exigir credencial
 * (CAP-12) e o bundle servido era anterior à migração para `apiFetch` — então ela
 * respondia `401` a cada atribuição de contato e o Console mostrava
 * *"Awaiting messages…"*, indistinguível de um contato que de fato não tem
 * histórico. O 401 não aparecia em lugar nenhum: nem na tela, nem no console do
 * browser. O sintoma chegou ao operador como *"o Console não mostra histórico"*,
 * e a causa estava a três camadas de distância.
 *
 * ── O contrato ───────────────────────────────────────────────────────────────
 *
 * `error === null` significa que **a leitura aconteceu** — e aí `messages: []` é
 * um fato: este contato não tem histórico. `error !== null` significa que **não
 * se sabe** o que há no histórico; `messages` vem vazio porque não há o que
 * entregar, nunca porque o histórico está vazio. Quem consome tem de separar os
 * dois na tela, senão a ausência volta a ser muda.
 */

import { apiFetch } from '@/api/apiFetch'
import { ChatMessage } from './types'

export interface ConversationHistory {
  messages: ChatMessage[]
  /** `null` = leitura bem-sucedida. Não-nulo = motivo, já legível ao operador. */
  error: string | null
}

/**
 * Lê o histórico persistido da sessão. Nunca lança: o desfecho vem no `error`,
 * e a falha é LOGADA aqui (uma casa só) nomeando rota, sessão e motivo — para que
 * a próxima ocorrência apareça no console do browser sem depender do log do nginx.
 */
export async function loadConversationHistory(sessionId: string): Promise<ConversationHistory> {
  const url = `/api/conversation_history/${encodeURIComponent(sessionId)}`

  let res: Response
  try {
    res = await apiFetch(url)
  } catch (e) {
    const motivo = e instanceof Error ? e.message : String(e)
    console.error(`[agent-assist] histórico ILEGÍVEL (session=${sessionId}) — GET ${url} falhou: ${motivo}`)
    return { messages: [], error: motivo }
  }

  if (!res.ok) {
    console.error(`[agent-assist] histórico ILEGÍVEL (session=${sessionId}) — GET ${url} → HTTP ${res.status}`)
    return { messages: [], error: `HTTP ${res.status}` }
  }

  try {
    const data = await res.json() as { messages?: ChatMessage[] }
    return { messages: Array.isArray(data.messages) ? data.messages : [], error: null }
  } catch (e) {
    const motivo = e instanceof Error ? e.message : String(e)
    console.error(`[agent-assist] histórico ILEGÍVEL (session=${sessionId}) — corpo não é JSON: ${motivo}`)
    return { messages: [], error: motivo }
  }
}
