import React, { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from './useAuth'
import { Session, UserRole } from '@/types'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'
import Select from '@/components/ui/Select'

const LoginPage: React.FC = () => {
  const navigate = useNavigate()
  const { login } = useAuth()
  const [userId, setUserId] = useState('')
  const [name, setName] = useState('')
  const [role, setRole] = useState<UserRole>('operator')
  const [tenantId, setTenantId] = useState('tenant_default')
  const [error, setError] = useState('')
  const [isLoading, setIsLoading] = useState(false)

  const roles: UserRole[] = ['operator', 'supervisor', 'admin', 'developer', 'business']

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')

    if (!userId.trim() || !name.trim()) {
      setError('User ID and Name are required')
      return
    }

    setIsLoading(true)
    try {
      const session: Session = {
        userId: userId.trim(),
        name: name.trim(),
        role,
        tenantId: tenantId.trim() || 'tenant_default',
        installationId: 'installation_default',
        locale: 'pt-BR'
      }

      login(session)
      navigate('/')
    } catch (err) {
      setError('Login failed. Please try again.')
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-primary to-secondary flex items-center justify-center p-4">
      <div className="bg-white rounded-lg shadow-xl p-8 w-full max-w-md">
        <h1 className="text-3xl font-bold text-primary mb-2 text-center">PlugHub</h1>
        <p className="text-gray text-center mb-8">Enterprise Orchestration Platform</p>

        <form onSubmit={handleSubmit} className="space-y-4">
          <Input
            id="userId"
            label="User ID"
            type="text"
            placeholder="user@example.com"
            value={userId}
            onChange={(e) => setUserId(e.target.value)}
            required
          />

          <Input
            id="name"
            label="Full Name"
            type="text"
            placeholder="John Doe"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />

          <Select
            id="role"
            label="Role"
            value={role}
            onChange={(e) => setRole(e.target.value as UserRole)}
            options={roles.map(r => ({ value: r, label: r.charAt(0).toUpperCase() + r.slice(1) }))}
          />

          <Input
            id="tenantId"
            label="Tenant ID"
            type="text"
            placeholder="tenant_default"
            value={tenantId}
            onChange={(e) => setTenantId(e.target.value)}
          />

          {error && (
            <div className="bg-red/10 border border-red text-red px-4 py-2 rounded">
              {error}
            </div>
          )}

          <Button
            type="submit"
            variant="primary"
            className="w-full"
            disabled={isLoading}
          >
            {isLoading ? 'Signing in...' : 'Sign In'}
          </Button>
        </form>

        <p className="text-gray text-xs text-center mt-6">
          Demo credentials: Any non-empty User ID and Name will work
        </p>
      </div>
    </div>
  )
}

export default LoginPage
