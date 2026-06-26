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
