/**
 * LoginPage.tsx
 * Real login form — calls auth-api via useAuth().login(email, password).
 *
 * Arc 7a: Replaces the mock credential form with email + password
 * backed by auth-api JWT authentication.
 */
import React, { useState } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { useAuth } from './useAuth'
import { AuthApiError } from '@/api/auth'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'

const LoginPage: React.FC = () => {
  const navigate       = useNavigate()
  const location       = useLocation()
  const { login }      = useAuth()

  const [email, setEmail]       = useState('')
  const [password, setPassword] = useState('')
  const [error, setError]       = useState('')
  const [isLoading, setIsLoading] = useState(false)

  // Redirect to the page the user was trying to access, or home
  const from = (location.state as { from?: { pathname: string } })?.from?.pathname ?? '/'

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')

    if (!email.trim() || !password) {
      setError('Email and password are required.')
      return
    }

    setIsLoading(true)
    try {
      await login(email.trim().toLowerCase(), password)
      navigate(from, { replace: true })
    } catch (err) {
      if (err instanceof AuthApiError) {
        if (err.status === 401) {
          setError('Invalid email or password.')
        } else if (err.status === 403) {
          setError('Your account is inactive. Contact your administrator.')
        } else {
          setError(`Authentication failed (${err.status}). Please try again.`)
        }
      } else {
        setError('Could not reach the authentication server. Check your connection.')
      }
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-primary to-secondary flex items-center justify-center p-4">
      <div className="bg-white rounded-lg shadow-xl p-8 w-full max-w-md">
        {/* Logo / branding */}
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold text-primary">PlugHub</h1>
          <p className="text-gray-500 text-sm mt-1">Enterprise Orchestration Platform</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-5">
          <Input
            id="email"
            label="Email"
            type="email"
            placeholder="you@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoComplete="email"
            autoFocus
          />

          <Input
            id="password"
            label="Password"
            type="password"
            placeholder="••••••••"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            autoComplete="current-password"
          />

          {error && (
            <div
              role="alert"
              className="bg-red-50 border border-red-400 text-red-700 px-4 py-3 rounded text-sm"
            >
              {error}
            </div>
          )}

          <Button
            type="submit"
            variant="primary"
            className="w-full"
            disabled={isLoading}
          >
            {isLoading ? 'Signing in…' : 'Sign In'}
          </Button>
        </form>

        <p className="text-gray-400 text-xs text-center mt-6">
          Default admin: <span className="font-mono">admin@plughub.local</span>
        </p>
      </div>
    </div>
  )
}

export default LoginPage
