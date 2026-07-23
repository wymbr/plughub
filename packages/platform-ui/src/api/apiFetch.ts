import { getAccessToken } from '@/auth/token-store'

/**
 * apiFetch — `fetch` que anexa `Authorization: Bearer` do token em memória (quando há e
 * ainda não foi setado). USAR em TODA chamada de relatório/leitura gateada por ABAC:
 * `/reports/*`, `/v1/evaluation/*`, `/analytics/*`.
 *
 * Motivo (arco de segurança, 2026-07-23): sem o token, o backend degrada para
 * `accessible_pools=None` = irrestrito (vê todos os pools). Anexar o Bearer é o que
 * ativa o pool-scoping por domínio do usuário. Não é monkey-patch do fetch global (que
 * vazaria token em auth/refresh/CDN) — é explícito, consistente com o `bearer()` do
 * `api/registry.ts`.
 */
export function apiFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const token = getAccessToken()
  const headers = new Headers(init.headers)
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`)
  }
  return fetch(input, { ...init, headers })
}
