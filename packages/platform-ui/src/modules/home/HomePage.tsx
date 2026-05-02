import React from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '@/auth/useAuth'
import { useTranslation } from 'react-i18next'
import Card from '@/components/ui/Card'
import Badge from '@/components/ui/Badge'

const HomePage: React.FC = () => {
  const { session } = useAuth()
  const { t } = useTranslation('shell')

  return (
    <div>
      <h1 className="text-3xl font-bold text-dark mb-2">
        {session && `Bem-vindo, ${session.name}`}
      </h1>
      <p className="text-gray mb-8">Enterprise Orchestration Platform</p>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-8">
        <Card title="Profile Information">
          <div className="space-y-3">
            <div>
              <p className="text-xs text-gray uppercase font-semibold">User ID</p>
              <p className="text-sm text-dark font-semibold">{session?.userId}</p>
            </div>
            <div>
              <p className="text-xs text-gray uppercase font-semibold">Role</p>
              <div className="mt-1">
                <Badge variant="default">{session?.role}</Badge>
              </div>
            </div>
            <div>
              <p className="text-xs text-gray uppercase font-semibold">Tenant</p>
              <p className="text-sm text-dark font-semibold">{session?.tenantId}</p>
            </div>
            <div>
              <p className="text-xs text-gray uppercase font-semibold">Installation</p>
              <p className="text-sm text-dark font-semibold">{session?.installationId}</p>
            </div>
          </div>
        </Card>

        <Card title="Quick Links">
          <div className="space-y-2">
            {session?.role === 'admin' && (
              <Link
                to="/config/recursos"
                className="block p-3 rounded border border-lightGray hover:bg-tableAlt transition-colors"
              >
                <p className="font-semibold text-dark">{t('nav.recursos')}</p>
                <p className="text-xs text-gray">Manage pools, agents, and skills</p>
              </Link>
            )}

            {(session?.role === 'operator' || session?.role === 'supervisor') && (
              <>
                <Link
                  to="/monitor"
                  className="block p-3 rounded border border-lightGray hover:bg-tableAlt transition-colors"
                >
                  <p className="font-semibold text-dark">{t('nav.monitor')}</p>
                  <p className="text-xs text-gray">Monitor active sessions</p>
                </Link>
              </>
            )}

            <div className="p-3 rounded border border-lightGray bg-tableAlt">
              <p className="font-semibold text-dark text-sm">Modules under construction</p>
              <p className="text-xs text-gray mt-1">More features coming soon</p>
            </div>
          </div>
        </Card>
      </div>

      <Card title="Platform Features">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="text-center">
            <p className="text-2xl mb-2">🎯</p>
            <p className="font-semibold text-dark text-sm">Routing Engine</p>
            <p className="text-xs text-gray">Smart agent allocation</p>
          </div>
          <div className="text-center">
            <p className="text-2xl mb-2">⚙️</p>
            <p className="font-semibold text-dark text-sm">Skill Flows</p>
            <p className="text-xs text-gray">Process automation</p>
          </div>
          <div className="text-center">
            <p className="text-2xl mb-2">📊</p>
            <p className="font-semibold text-dark text-sm">Analytics</p>
            <p className="text-xs text-gray">Real-time insights</p>
          </div>
        </div>
      </Card>
    </div>
  )
}

export default HomePage
