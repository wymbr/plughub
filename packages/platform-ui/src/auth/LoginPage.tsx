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
      setError('E-mail e senha são obrigatórios.')
      return
    }

    setIsLoading(true)
    try {
      await login(email.trim().toLowerCase(), password)
      navigate(from, { replace: true })
    } catch (err) {
      if (err instanceof AuthApiError) {
        if (err.status === 401) {
          setError('E-mail ou senha incorretos.')
        } else if (err.status === 403) {
          setError('Sua conta está inativa. Entre em contato com o administrador.')
        } else {
          setError(`Falha na autenticação (${err.status}). Tente novamente.`)
        }
      } else {
        setError('Não foi possível conectar ao servidor. Verifique sua conexão.')
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
          <p className="text-muted text-sm mt-1">Plataforma de Orquestração Empresarial</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-5" noValidate>
          <Input
            id="email"
            label="E-mail"
            type="email"
            placeholder="voce@empresa.com.br"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoComplete="email"
            autoFocus
          />

          <Input
            id="password"
            label="Senha"
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
              aria-live="assertive"
              className="bg-red-light border border-red text-red-text px-4 py-3 rounded-lg text-sm"
            >
              {error}
            </div>
          )}

          <Button
            type="submit"
            variant="primary"
            className="w-full"
            loading={isLoading}
          >
            {isLoading ? 'Entrando…' : 'Entrar'}
          </Button>
        </form>
      </div>
    </div>
  )
}

export default LoginPage
