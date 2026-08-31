/**
 * AuthContext.tsx
 * Real JWT auth flow — integrates with auth-api (port 3200).
 *
 * Token storage strategy:
 *   access_token  → in-memory only (React state) — not in localStorage
 *   refresh_token → localStorage ('plughub_refresh_token') — survives page reload
 *   session meta  → localStorage ('plughub_session_meta') — userId, name, role, etc.
 *                   Does NOT store tokens; safe for persistence.
 *
 * Auto-refresh:
 *   A setTimeout fires 60 s before the access token expires and calls apiRefresh().
 *   On failure the user is logged out.
 *
 * On mount:
 *   If localStorage has a refresh_token, attempts silent re-authentication via apiRefresh().
 *   On success the user appears logged in without re-entering credentials.
 *   On failure (expired, revoked) clears storage and shows login.
 */
import React, {
  createContext,
  useState,
  useEffect,
  useCallback,
  useRef,
  useMemo,
  ReactNode,
} from 'react'
import { ModuleConfig, Session, UserRole } from '@/types'
import { apiLogin, apiRefresh, apiLogout, AuthApiError } from '@/api/auth'
import { makePermissions, Permissions } from '@/lib/permissions'
import { setAccessToken } from '@/auth/token-store'

// ── Helpers ───────────────────────────────────────────────────────────────────

const REFRESH_TOKEN_KEY = 'plughub_refresh_token'
const SESSION_META_KEY  = 'plughub_session_meta'

/** Map a roles[] array to the highest-privilege single UserRole for the UI.
 *  Priority: admin > developer > supervisor > operator > business
 *  Admin is placed first so admin+developer users unlock all admin nav items.
 *  Developer-only users still see Skill Flows and Developer Tools.
 */
function primaryRole(roles: string[]): UserRole {
  const priority: UserRole[] = ['admin', 'developer', 'supervisor', 'operator', 'business']
  for (const r of priority) {
    if (roles.includes(r)) return r
  }
  return 'operator'
}

interface SessionMeta {
  userId:                 string
  name:                   string
  email:                  string
  tenantId:               string
  roles:                  string[]
  accessiblePools:        string[]
  maxConcurrentSessions?: number
}

// ── Stable current-user object ────────────────────────────────────────────────

/** Flat projection of the logged-in user — the same data as `session` but
 *  as a plain, stable object (no token fields). Safe to pass as props or
 *  spread across modules without re-rendering on token refresh. */
export interface CurrentUser {
  userId:                 string
  name:                   string
  email:                  string
  tenantId:               string
  role:                   UserRole        // highest-privilege role
  roles:                  string[]
  accessiblePools:        string[]       // [] = all pools
  supervisedAgentTypes:   string[]       // [] = unrestricted (admin); non-empty = Arc 9 scope
  maxConcurrentSessions:  number
}

// ── Context types ─────────────────────────────────────────────────────────────

interface AuthContextType {
  session:         Session | null
  isAuthenticated: boolean
  isInitializing:  boolean   // true while silent re-auth is in progress on mount
  login:           (email: string, password: string) => Promise<void>
  logout:          () => Promise<void>
  /** Returns a valid access token, refreshing if needed. Used by API clients. */
  getAccessToken:  () => Promise<string | null>

  // ── Convenience derivations — stable across token refreshes ──────────────
  /** Tenant ID from JWT. Falls back to VITE_TENANT_ID env var when not authenticated. */
  tenantId:        string
  /** ABAC permission checker built from the JWT module_config. Never null — graceful for unauthenticated state. */
  perms:           Permissions
  /** Flat current-user object without token fields. Null when not authenticated. */
  currentUser:     CurrentUser | null
}

export const AuthContext = createContext<AuthContextType | undefined>(undefined)

// ── Provider ──────────────────────────────────────────────────────────────────

export const AuthProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [session, setSession]           = useState<Session | null>(null)
  const [isInitializing, setIsInitializing] = useState(true)
  const refreshTimerRef                 = useRef<ReturnType<typeof setTimeout> | null>(null)
  const refreshingRef                   = useRef<Promise<Session | null> | null>(null)

  // G-PROBE platform-wide: espelha o access token num holder de módulo p/ módulos
  // não-React (ex.: api/registry.ts, api/apiFetch.ts) mandarem Authorization: Bearer
  // fora de hook.
  //
  // ⚠️ **ISTO ERA UM `useEffect`, e por isso a primeira leitura de TODO reload saía
  // sem `Authorization`** (medido 2026-08-25). Não era corrida de rede — era ordem de
  // efeitos, que no React é determinística: **efeito de filho roda ANTES do efeito do
  // pai**. No commit em que a re-auth silenciosa termina, o `ProtectedRoute` para de
  // mostrar o spinner e a árvore inteira monta; o efeito da página (`ListaTab.load`)
  // dispara o `fetch` antes de o efeito daqui ter escrito o token.
  //
  // A consequência não é cosmética: sem o Bearer o backend degrada para
  // `accessible_pools = None` — **irrestrito** —, que é a degradação documentada no
  // `apiFetch`. Um operador com escopo de pools restrito via, por alguns segundos,
  // contatos de fora do domínio dele. O sintoma na tela era a MESMA URL devolver
  // contagens diferentes conforme se chegasse pelo menu ou por F5.
  //
  // O conserto é escrever no store **durante o render**, que acontece antes de
  // qualquer efeito de qualquer nível. A guarda por `ref` torna a escrita idempotente
  // (StrictMode renderiza duas vezes) e evita trabalho em re-render sem troca de
  // sessão. Não pode ser `useLayoutEffect`: layout effects também rodam filho-primeiro.
  const mirroredTokenRef = useRef<string | null>(null)
  const currentToken = session?.accessToken ?? null
  if (mirroredTokenRef.current !== currentToken) {
    mirroredTokenRef.current = currentToken
    setAccessToken(currentToken)
  }

  // ── Build Session from token response ───────────────────────────────────────

  const buildSession = useCallback((
    accessToken:  string,
    refreshToken: string,
    expiresIn:    number,   // seconds
    user: {
      id: string; email: string; name: string
      roles: string[]; tenant_id: string; accessible_pools: string[]
      /** Declaração explícita de "sem recorte" — porta larga do menu grant-first. */
      supervised_agent_types?: string[]
      max_concurrent_sessions?: number
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      module_config?: Record<string, any>
    },
  ): Session => {
    return {
      userId:                user.id,
      email:                 user.email,
      name:                  user.name,
      role:                  primaryRole(user.roles),
      roles:                 user.roles,
      tenantId:              user.tenant_id,
      accessiblePools:       user.accessible_pools,
      supervisedAgentTypes:  user.supervised_agent_types ?? [],
      maxConcurrentSessions: user.max_concurrent_sessions ?? 3,
      moduleConfig:          (user.module_config ?? {}) as ModuleConfig,
      installationId:        'default',
      locale:                'pt-BR',
      accessToken,
      refreshToken,
      expiresAt:             Date.now() + expiresIn * 1000,
    }
  }, [])

  // ── Persist / clear storage ──────────────────────────────────────────────────

  const persistSession = useCallback((s: Session) => {
    localStorage.setItem(REFRESH_TOKEN_KEY, s.refreshToken)
    const meta: SessionMeta = {
      userId:                s.userId,
      name:                  s.name,
      email:                 s.email,
      tenantId:              s.tenantId,
      roles:                 s.roles,
      accessiblePools:       s.accessiblePools,
      maxConcurrentSessions: s.maxConcurrentSessions,
    }
    localStorage.setItem(SESSION_META_KEY, JSON.stringify(meta))
  }, [])

  const clearStorage = useCallback(() => {
    localStorage.removeItem(REFRESH_TOKEN_KEY)
    localStorage.removeItem(SESSION_META_KEY)
  }, [])

  // ── Auto-refresh scheduler ──────────────────────────────────────────────────

  const scheduleRefresh = useCallback((s: Session) => {
    if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current)

    const msUntilExpiry = s.expiresAt - Date.now()
    const refreshIn     = Math.max(msUntilExpiry - 60_000, 5_000)  // 60s before expiry, min 5s

    refreshTimerRef.current = setTimeout(async () => {
      try {
        const data    = await apiRefresh(s.refreshToken)
        const renewed = buildSession(data.access_token, data.refresh_token, data.expires_in, data.user)
        setAccessToken(renewed.accessToken); setSession(renewed)
        persistSession(renewed)
        scheduleRefresh(renewed)
      } catch {
        // Refresh failed — session expired
        setAccessToken(null); setSession(null)
        clearStorage()
      }
    }, refreshIn)
  }, [buildSession, persistSession, clearStorage])

  // ── Silent re-auth on mount ──────────────────────────────────────────────────

  useEffect(() => {
    const storedToken = localStorage.getItem(REFRESH_TOKEN_KEY)

    if (!storedToken) {
      setIsInitializing(false)
      return
    }

    apiRefresh(storedToken)
      .then((data) => {
        const s = buildSession(data.access_token, data.refresh_token, data.expires_in, data.user)
        setSession(s)
        persistSession(s)
        scheduleRefresh(s)
      })
      .catch(() => {
        clearStorage()
      })
      .finally(() => {
        setIsInitializing(false)
      })

    return () => {
      if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ── login ───────────────────────────────────────────────────────────────────

  const login = useCallback(async (email: string, password: string): Promise<void> => {
    const data = await apiLogin(email, password)
    const s    = buildSession(data.access_token, data.refresh_token, data.expires_in, data.user)
    setAccessToken(s.accessToken); setSession(s)
    persistSession(s)
    scheduleRefresh(s)
  }, [buildSession, persistSession, scheduleRefresh])

  // ── logout ──────────────────────────────────────────────────────────────────

  const logout = useCallback(async (): Promise<void> => {
    if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current)

    const current = session
    setAccessToken(null); setSession(null)
    clearStorage()

    if (current) {
      // Clear any durable agent pause marker on explicit logout (end of shift) so
      // the next login starts ready. No-op if the user is not a paused agent;
      // navigation/crash never reach this path. Best-effort, non-blocking.
      try {
        await fetch(`/api/agent-clear-pause`, {
          method:  "POST",
          headers: { Authorization: `Bearer ${current.accessToken}` },
        })
      } catch {
        // best-effort
      }
      try {
        await apiLogout(current.refreshToken, current.accessToken)
      } catch {
        // Best-effort — already cleared locally
      }
    }
  }, [session, clearStorage])

  // ── getAccessToken — for API clients ─────────────────────────────────────────

  const getAccessToken = useCallback(async (): Promise<string | null> => {
    if (!session) return null

    // Token still valid (with 10s margin)?
    if (session.expiresAt - Date.now() > 10_000) {
      return session.accessToken
    }

    // Token about to expire — refresh now (deduplicate concurrent calls)
    if (!refreshingRef.current) {
      refreshingRef.current = apiRefresh(session.refreshToken)
        .then((data) => {
          const renewed = buildSession(data.access_token, data.refresh_token, data.expires_in, data.user)
          setAccessToken(renewed.accessToken); setSession(renewed)
          persistSession(renewed)
          scheduleRefresh(renewed)
          return renewed
        })
        .catch(() => {
          setAccessToken(null); setSession(null)
          clearStorage()
          return null
        })
        .finally(() => {
          refreshingRef.current = null
        })
    }

    const renewed = await refreshingRef.current
    return renewed?.accessToken ?? null
  }, [session, buildSession, persistSession, scheduleRefresh, clearStorage])

  // ── Derived stable values — recomputed only when session identity changes ───

  /** tenantId from JWT; falls back to env var so non-authenticated code still works */
  const tenantId = session?.tenantId ?? (import.meta.env.VITE_TENANT_ID as string | undefined) ?? ''

  /** ABAC permission checker — recomputed only when moduleConfig object changes */
  const perms = useMemo(
    () => makePermissions(session?.moduleConfig),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [session?.moduleConfig]
  )

  /** Flat current-user object — stable across token refreshes because it omits token fields */
  const currentUser = useMemo<CurrentUser | null>(() => {
    if (!session) return null
    return {
      userId:                session.userId,
      name:                  session.name,
      email:                 session.email,
      tenantId:              session.tenantId,
      role:                  session.role,
      roles:                 session.roles,
      accessiblePools:       session.accessiblePools,
      supervisedAgentTypes:  session.supervisedAgentTypes,
      maxConcurrentSessions: session.maxConcurrentSessions,
    }
  }, [
    session?.userId, session?.name, session?.email, session?.tenantId,
    session?.role, session?.roles, session?.accessiblePools,
    session?.supervisedAgentTypes, session?.maxConcurrentSessions,
  ])

  // ── Context value ────────────────────────────────────────────────────────────

  return (
    <AuthContext.Provider value={{
      session,
      isAuthenticated: session !== null,
      isInitializing,
      login,
      logout,
      getAccessToken,
      tenantId,
      perms,
      currentUser,
    }}>
      {children}
    </AuthContext.Provider>
  )
}
