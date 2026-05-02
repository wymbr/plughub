/**
 * ProtectedRoute.tsx
 * Wraps a route requiring authentication.
 *
 * - Shows a full-screen spinner while silent re-auth is in progress (isInitializing=true).
 *   Without this, users who have a valid refresh_token would see a flash of the login page
 *   before being redirected to the app.
 * - Saves the attempted URL in location.state so LoginPage can redirect back after login.
 */
import React from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from './useAuth'
import Spinner from '@/components/ui/Spinner'

interface ProtectedRouteProps {
  children: React.ReactNode
}

export const ProtectedRoute: React.FC<ProtectedRouteProps> = ({ children }) => {
  const { isAuthenticated, isInitializing } = useAuth()
  const location = useLocation()

  if (isInitializing) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <Spinner size="lg" />
      </div>
    )
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />
  }

  return <>{children}</>
}
