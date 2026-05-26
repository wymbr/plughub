import React from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '@/auth/useAuth'
import { useTranslation } from 'react-i18next'
import Badge from '@/components/ui/Badge'
import ContextSwitcher from './ContextSwitcher'

const LANGUAGES = [
  { code: 'pt-BR', label: 'PT' },
  { code: 'en',    label: 'EN' },
] as const

const TopBar: React.FC = () => {
  const navigate = useNavigate()
  const { session, logout } = useAuth()
  const { t, i18n } = useTranslation('shell')

  const handleLogout = async () => {
    await logout()
    navigate('/login')
  }

  return (
    <header className="h-14 bg-white border-b border-border flex items-center justify-between px-6 shadow-card flex-shrink-0">
      <div className="flex items-center gap-6">
        {/* Skip-navigation landmark: screen readers can jump to main content */}
        <a
          href="#main-content"
          className="sr-only focus-visible:not-sr-only focus-visible:absolute focus-visible:top-2 focus-visible:left-2
            focus-visible:z-tooltip focus-visible:rounded focus-visible:bg-primary focus-visible:text-white
            focus-visible:px-3 focus-visible:py-1 focus-visible:text-sm"
        >
          Ir para o conteúdo principal
        </a>
        <span className="text-xl font-bold text-primary" aria-label="PlugHub">PlugHub</span>
        <ContextSwitcher />
      </div>

      <div className="flex items-center gap-6">
        <select
          value={i18n.language}
          onChange={e => i18n.changeLanguage(e.target.value)}
          aria-label={t('topbar.language')}
          className="w-12 text-sm text-muted hover:text-dark transition-colors font-medium
            bg-transparent border-none outline-none cursor-pointer appearance-none text-center"
        >
          {LANGUAGES.map(lang => (
            <option key={lang.code} value={lang.code}>{lang.label}</option>
          ))}
        </select>

        {session && (
          <>
            <div className="flex items-center gap-2" aria-label={`Usuário: ${session.name}, perfil ${session.role}`}>
              <div className="text-right">
                <p className="text-sm font-semibold text-dark">{session.name}</p>
                <p className="text-xs text-muted">{session.email}</p>
              </div>
              <Badge variant="default">{session.role}</Badge>
            </div>

            <button
              onClick={handleLogout}
              className="text-sm text-muted hover:text-red transition-colors font-medium"
            >
              {t('topbar.logout')}
            </button>
          </>
        )}
      </div>
    </header>
  )
}

export default TopBar
