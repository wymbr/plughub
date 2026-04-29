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
  ReactNode,
} from 'react'
import { ModuleConfig, Session, UserRole } from '@/types'
import { apiLogin, apiRefresh, apiLogout, AuthApiError } from '@/api/auth'

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
  userId:         string
  name:           string
  email:          string
  tenantId:       string
  roles:          string[]
  accessiblePools: string[]
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
}

export const AuthContext = createContext<AuthContextType | undefined>(undefined)

// ── Provider ──────────────────────────────────────────────────────────────────

export const AuthProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [session, setSession]           = useState<Session | null>(null)
  const [isInitializing, setIsInitializing] = useState(true)
  const refreshTimerRef                 = useRef<ReturnType<typeof setTimeout> | null>(null)
  const refreshingRef                   = useRef<Promise<Session | null> | null>(null)

  // ── Build Session from token response ───────────────────────────────────────

  const buildSession = useCallback((
    accessToken:  string,
    refreshToken: string,
    expiresIn:    number,   // seconds
    user: {
      id: string; email: string; name: string
      roles: string[]; tenant_id: string; accessible_pools: string[]
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      module_config?: Record<string, any>
    },
  ): Session => {
    return {
      userId:          user.id,
      email:           user.email,
      name:            user.name,
      role:            primaryRole(user.roles),
      roles:           user.roles,
      tenantId:        user.tenant_id,
      accessiblePools: user.accessible_pools,
      moduleConfig:    (user.module_config ?? {}) as ModuleConfig,
      installationId:  'default',
      locale:          'pt-BR',
      accessToken,
      refreshToken,
      expiresAt:       Date.now() + expiresIn * 1000,
    }
  }, [])

  // ── Persist / clear storage ──────────────────────────────────────────────────

  const persistSession = useCallback((s: Session) => {
    localStorage.setItem(REFRESH_TOKEN_KEY, s.refreshToken)
    const meta: SessionMeta = {
      userId:         s.userId,
      name:           s.name,
      email:          s.email,
      tenantId:       s.tenantId,
      roles:          s.roles,
      accessiblePools: s.accessiblePools,
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
        setSession(renewed)
        persistSession(renewed)
        scheduleRefresh(renewed)
      } catch {
        // Refresh failed — session expired
        setSession(null)
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
    setSession(s)
    persistSession(s)
    scheduleRefresh(s)
  }, [buildSession, persistSession, scheduleRefresh])

  // ── logout ──────────────────────────────────────────────────────────────────

  const logout = useCallback(async (): Promise<void> => {
    if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current)

    const current = session
    setSession(null)
    clearStorage()

    if (current) {
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
          setSession(renewed)
          persistSession(renewed)
          scheduleRefresh(renewed)
          return renewed
        })
        .catch(() => {
          setSession(null)
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

  // ── Context value ────────────────────────────────────────────────────────────

  return (
    <AuthContext.Provider value={{
      session,
      isAuthenticated: session !== null,
      isInitializing,
      login,
      logout,
      getAccessToken,
    }}>
      {children}
    </AuthContext.Provider>
  )
}
