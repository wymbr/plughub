import React from 'react'
import { useAuth } from '@/auth/useAuth'
import { useTranslation } from 'react-i18next'
import DashboardView from '@/modules/dashboards/DashboardView'

const HomePage: React.FC = () => {
  const { session } = useAuth()
  const { t } = useTranslation('home')

  return (
    <div>
      <h1 className="text-2xl font-bold text-dark mb-1">
        {session && t('welcome', { name: session.name })}
      </h1>
      <p className="text-gray mb-6">{t('subtitle')}</p>

      {/* Personalised dashboard (view-only). Builder lives in Config → Dashboards. */}
      <DashboardView />
    </div>
  )
}

export default HomePage
