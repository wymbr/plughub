/**
 * auth.ts
 * Auth API client — integrates with auth-api (port 3200).
 *
 * Endpoints used:
 *   POST /auth/login    → { access_token, refresh_token, expires_in, user }
 *   POST /auth/refresh  → { access_token, refresh_token, expires_in, user }
 *   POST /auth/logout   → 204
 *   GET  /auth/me       → { sub, email, name, roles, tenant_id, accessible_pools }
 *
 * All requests are proxied via Vite '^/auth' → http://localhost:3200.
 */

export interface AuthTokenResponse {
  access_token:  string
  refresh_token: string
  token_type:    string
  expires_in:    number   // seconds
  user: {
    id:               string
    email:            string
    name:             string
    roles:            string[]
    tenant_id:        string
    accessible_pools: string[]
  }
}

export interface MeResponse {
  sub:              string
  email:            string
  name:             string
  roles:            string[]
  tenant_id:        string
  accessible_pools: string[]
}

export class AuthApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
  ) {
    super(detail)
    this.name = 'AuthApiError'
  }
}

async function _post<T>(path: string, body: object, accessToken?: string): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (accessToken) headers['Authorization'] = `Bearer ${accessToken}`

  const res = await fetch(path, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  })

  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const json = await res.json()
      detail = json.detail ?? detail
    } catch { /* ignore */ }
    throw new AuthApiError(res.status, detail)
  }

  if (res.status === 204) return undefined as unknown as T
  return res.json() as Promise<T>
}

/** POST /auth/login — returns token pair + user info. */
export async function apiLogin(email: string, password: string): Promise<AuthTokenResponse> {
  return _post<AuthTokenResponse>('/auth/login', { email, password })
}

/** POST /auth/refresh — rotates the refresh token and returns a new pair. */
export async function apiRefresh(refreshToken: string): Promise<AuthTokenResponse> {
  return _post<AuthTokenResponse>('/auth/refresh', { refresh_token: refreshToken })
}

/** POST /auth/logout — invalidates the refresh token (idempotent). */
export async function apiLogout(refreshToken: string, accessToken: string): Promise<void> {
  return _post<void>('/auth/logout', { refresh_token: refreshToken }, accessToken)
}
