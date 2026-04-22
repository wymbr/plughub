import React, { createContext, useState, useCallback, ReactNode } from 'react'
import { Session } from '@/types'

interface AuthContextType {
  session: Session | null
  isAuthenticated: boolean
  login: (session: Session) => void
  logout: () => void
}

export const AuthContext = createContext<AuthContextType | undefined>(undefined)

interface AuthProviderProps {
  children: ReactNode
}

export const AuthProvider: React.FC<AuthProviderProps> = ({ children }) => {
  const [session, setSession] = useState<Session | null>(() => {
    const stored = localStorage.getItem('plughub_session')
    if (stored) {
      try {
        return JSON.parse(stored)
      } catch {
        return null
      }
    }
    return null
  })

  const login = useCallback((newSession: Session) => {
    setSession(newSession)
    localStorage.setItem('plughub_session', JSON.stringify(newSession))
  }, [])

  const logout = useCallback(() => {
    setSession(null)
    localStorage.removeItem('plughub_session')
  }, [])

  const value: AuthContextType = {
    session,
    isAuthenticated: session !== null,
    login,
    logout
  }

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  )
}
