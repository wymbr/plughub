import React from 'react'
import { useAuth } from '@/auth/useAuth'
import { useTranslation } from 'react-i18next'

const ContextSwitcher: React.FC = () => {
  const { session } = useAuth()
  const { t } = useTranslation('shell')

  if (!session) return null

  return (
    <div className="flex items-center gap-2">
      <div className="flex items-center gap-1 px-2 py-1 bg-white/10 rounded text-sm text-white">
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4" />
        </svg>
        <span className="whitespace-nowrap">{t('contextSwitcher.installation')}</span>
      </div>

      <div className="text-white/50">|</div>

      <div className="flex items-center gap-1 px-2 py-1 bg-white/10 rounded text-sm text-white">
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
        </svg>
        <span className="whitespace-nowrap">{t('contextSwitcher.tenant')}</span>
      </div>
    </div>
  )
}

export default ContextSwitcher
