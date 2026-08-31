/**
 * token-store.ts
 * Holder em módulo do access token corrente, para módulos NÃO-React (ex.: api/registry.ts)
 * que precisam mandar `Authorization: Bearer` fora do contexto de hook. Atualizado pelo
 * AuthContext sempre que a sessão muda (login/refresh/logout). In-memory only (igual ao
 * access token da sessão — nunca em localStorage).
 */
let _accessToken: string | null = null

export function setAccessToken(token: string | null): void {
  _accessToken = token
}

export function getAccessToken(): string | null {
  return _accessToken
}

// ── Re-auth reativo (AUT-19, 2026-08-31) ──────────────────────────────────────
//
// O `apiFetch` precisa reagir a um 401 renovando a sessão, e ele é módulo — não vê o
// AuthContext. Mesma ponte que o `setAccessToken` já fazia para o token: o provider
// REGISTRA a função, o módulo a INVOCA.
//
// Por que isto nasceu junto com o 401 do lado servidor: até a AUT-19 as rotas
// operacionais nunca devolviam 401 (respondiam 200 com lista vazia), então uma sessão
// expirada virava "Monitor vazio" — e como o re-auth é por TIMER, ninguém a renovava
// até o timer disparar. Passar a devolver 401 sem este handler trocaria uma tela vazia
// por uma tela quebrada; é por isso que as duas metades são uma decisão só.
//
// ⚠️ O single-flight NÃO é otimização — é correção. O refresh token é ROTATIVO (o
// auth-api devolve um novo e invalida o anterior), e o Monitor dispara várias chamadas
// em paralelo. Sem a dedup, N respostas 401 simultâneas fariam N refreshes, cada um
// invalidando o anterior: a sessão morreria justamente quando tentasse se salvar.
type Reauthorizer = () => Promise<string | null>

let _reauthorizer: Reauthorizer | null = null
let _inFlight: Promise<string | null> | null = null

/** Chamado pelo AuthProvider. `null` no unmount/logout desarma o re-auth. */
export function setReauthorizer(fn: Reauthorizer | null): void {
  _reauthorizer = fn
}

/**
 * Renova a sessão e devolve o novo access token (ou `null` se não há como renovar —
 * sem provider registrado, sem refresh token, ou o refresh foi recusado).
 *
 * Chamadas concorrentes compartilham a MESMA promessa.
 */
export function reauthorize(): Promise<string | null> {
  if (!_reauthorizer) return Promise.resolve(null)
  if (!_inFlight) {
    const fn = _reauthorizer
    _inFlight = fn()
      .catch(() => null)
      .finally(() => { _inFlight = null })
  }
  return _inFlight
}
