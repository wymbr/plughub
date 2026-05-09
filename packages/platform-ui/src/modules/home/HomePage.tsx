import React from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '@/auth/useAuth'
import { useTranslation } from 'react-i18next'
import Card from '@/components/ui/Card'
import Badge from '@/components/ui/Badge'

const HomePage: React.FC = () => {
  const { session } = useAuth()
  const { t } = useTranslation('home')

  return (
    <div>
      <h1 className="text-3xl font-bold text-dark mb-2">
        {session && t('welcome', { name: session.name })}
      </h1>
      <p className="text-gray mb-8">{t('subtitle')}</p>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-8">
        <Card title={t('profile.title')}>
          <div className="space-y-3">
            <div>
              <p className="text-xs text-gray uppercase font-semibold">{t('profile.userId')}</p>
              <p className="text-sm text-dark font-semibold">{session?.userId}</p>
            </div>
            <div>
              <p className="text-xs text-gray uppercase font-semibold">{t('profile.role')}</p>
              <div className="mt-1">
                <Badge variant="default">{session?.role}</Badge>
              </div>
            </div>
            <div>
              <p className="text-xs text-gray uppercase font-semibold">{t('profile.tenant')}</p>
              <p className="text-sm text-dark font-semibold">{session?.tenantId}</p>
            </div>
            <div>
              <p className="text-xs text-gray uppercase font-semibold">{t('profile.installation')}</p>
              <p className="text-sm text-dark font-semibold">{session?.installationId}</p>
            </div>
          </div>
        </Card>

        <Card title={t('quickLinks.title')}>
          <div className="space-y-2">
            {session?.role === 'admin' && (
              <Link
                to="/config/resources"
                className="block p-3 rounded border border-lightGray hover:bg-tableAlt transition-colors"
              >
                <p className="font-semibold text-dark">{t('quickLinks.resources')}</p>
                <p className="text-xs text-gray">{t('quickLinks.resourcesDesc')}</p>
              </Link>
            )}

            {(session?.role === 'operator' || session?.role === 'supervisor') && (
              <>
                <Link
                  to="/monitor"
                  className="block p-3 rounded border border-lightGray hover:bg-tableAlt transition-colors"
                >
                  <p className="font-semibold text-dark">{t('quickLinks.monitor')}</p>
                  <p className="text-xs text-gray">{t('quickLinks.monitorDesc')}</p>
                </Link>
              </>
            )}

            <div className="p-3 rounded border border-lightGray bg-tableAlt">
              <p className="font-semibold text-dark text-sm">{t('quickLinks.underConstruction')}</p>
              <p className="text-xs text-gray mt-1">{t('quickLinks.comingSoon')}</p>
            </div>
          </div>
        </Card>
      </div>

      <Card title={t('features.title')}>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="text-center">
            <p className="text-2xl mb-2">🎯</p>
            <p className="font-semibold text-dark text-sm">{t('features.routing')}</p>
            <p className="text-xs text-gray">{t('features.routingDesc')}</p>
          </div>
          <div className="text-center">
            <p className="text-2xl mb-2">⚙️</p>
            <p className="font-semibold text-dark text-sm">{t('features.skills')}</p>
            <p className="text-xs text-gray">{t('features.skillsDesc')}</p>
          </div>
          <div className="text-center">
            <p className="text-2xl mb-2">📊</p>
            <p className="font-semibold text-dark text-sm">{t('features.analytics')}</p>
            <p className="text-xs text-gray">{t('features.analyticsDesc')}</p>
          </div>
        </div>
      </Card>
    </div>
  )
}

export default HomePage
