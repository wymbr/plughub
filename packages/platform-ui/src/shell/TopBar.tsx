import React from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '@/auth/useAuth'
import { useTranslation } from 'react-i18next'
import Badge from '@/components/ui/Badge'
import ContextSwitcher from './ContextSwitcher'

const TopBar: React.FC = () => {
  const navigate = useNavigate()
  const { session, logout } = useAuth()
  const { t, i18n } = useTranslation('shell')

  const handleLogout = async () => {
    await logout()
    navigate('/login')
  }

  const toggleLanguage = () => {
    const newLang = i18n.language === 'pt-BR' ? 'en' : 'pt-BR'
    i18n.changeLanguage(newLang)
  }

  return (
    <div className="h-14 bg-white border-b border-lightGray flex items-center justify-between px-6 shadow-sm">
      <div className="flex items-center gap-6">
        <h1 className="text-xl font-bold text-primary">PlugHub</h1>
        <ContextSwitcher />
      </div>

      <div className="flex items-center gap-6">
        <button
          onClick={toggleLanguage}
          className="text-sm text-gray hover:text-dark transition-colors font-medium"
        >
          {i18n.language === 'pt-BR' ? 'EN' : 'PT'}
        </button>

        {session && (
          <>
            <div className="flex items-center gap-2">
              <div className="text-right">
                <p className="text-sm font-semibold text-dark">{session.name}</p>
                <p className="text-xs text-gray">{session.email}</p>
              </div>
              <Badge variant="default">{session.role}</Badge>
            </div>

            <button
              onClick={handleLogout}
              className="text-sm text-gray hover:text-red transition-colors font-medium"
            >
              {t('topbar.logout')}
            </button>
          </>
        )}
      </div>
    </div>
  )
}

export default TopBar
