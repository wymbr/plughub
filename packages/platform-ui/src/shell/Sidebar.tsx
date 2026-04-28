import React, { useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { useAuth } from '@/auth/useAuth'
import { useTranslation } from 'react-i18next'

interface NavItem {
  label: string
  href: string
  icon: string
  roles?: string[]
  children?: NavItem[]
}

const Sidebar: React.FC = () => {
  const { session } = useAuth()
  const { t } = useTranslation('shell')
  const location = useLocation()
  const [expandedGroups, setExpandedGroups] = useState<string[]>(['home'])

  const toggleGroup = (key: string) => {
    setExpandedGroups(prev =>
      prev.includes(key) ? prev.filter(g => g !== key) : [...prev, key]
    )
  }

  const navItems: NavItem[] = [
    {
      label: t('nav.home'),
      href: '/',
      icon: '🏠',
      roles: ['operator', 'supervisor', 'admin', 'developer', 'business']
    },
    {
      label: t('nav.atendimento'),
      href: '#',
      icon: '📞',
      roles: ['operator', 'supervisor', 'admin'],
      children: [
        { label: t('nav.monitor'), href: '/monitor', icon: '📊' },
        { label: t('nav.agentAssist'), href: '/agent-assist', icon: '🤖' }
      ]
    },
    {
      label: t('nav.workflows'),
      href: '/workflows',
      icon: '⚙️',
      roles: ['operator', 'supervisor', 'admin']
    },
    {
      label: t('nav.avaliacao'),
      href: '#',
      icon: '✓',
      roles: ['supervisor', 'admin'],
      children: []
    },
    {
      label: t('nav.analytics'),
      href: '#',
      icon: '📈',
      roles: ['operator', 'supervisor', 'admin', 'business'],
      children: [
        { label: t('nav.dashboards'),  href: '/dashboards', icon: '📊' },
        { label: t('nav.relatorios'),  href: '/reports',    icon: '📄' },
        { label: t('nav.campanhas'),   href: '/campaigns',  icon: '📣' }
      ]
    },
    {
      label: t('nav.skillFlows'),
      href: '/skill-flows',
      icon: '🔄',
      roles: ['admin', 'developer']
    },
    {
      label: t('nav.configuracao'),
      href: '#',
      icon: '⚙️',
      roles: ['admin'],
      children: [
        { label: t('nav.recursos'), href: '/config/recursos', icon: '📦' },
        { label: t('nav.plataforma'), href: '/config/platform', icon: '🖥️' },
        { label: t('nav.mascaramento'), href: '/config/masking', icon: '🔒' },
        { label: t('nav.faturamento'), href: '/config/billing', icon: '💳' },
        { label: t('nav.acesso'), href: '/config/access', icon: '🔐' }
      ]
    },
    {
      label: t('nav.developer'),
      href: '/developer',
      icon: '👨‍💻',
      roles: ['developer', 'admin']
    },
    {
      label: t('nav.business'),
      href: '/business',
      icon: '💼',
      roles: ['business']
    }
  ]

  const isActive = (href: string) => location.pathname === href || location.pathname.startsWith(href + '/')

  const filteredItems = navItems.filter(item => !item.roles || item.roles.includes(session?.role || ''))

  const renderNavItem = (item: NavItem, depth: number = 0) => {
    const hasChildren = item.children && item.children.length > 0

    if (hasChildren) {
      const isExpanded = expandedGroups.includes(item.label)

      return (
        <div key={item.label}>
          <button
            onClick={() => toggleGroup(item.label)}
            className="w-full flex items-center gap-2 px-4 py-2 text-white/70 hover:text-white transition-colors text-sm group"
          >
            <span className="text-lg">{item.icon}</span>
            <span className="flex-1 text-left">{item.label}</span>
            <svg
              className={`w-4 h-4 transition-transform ${isExpanded ? 'rotate-180' : ''}`}
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 14l-7-7m0 0L5 14m7-7v12" />
            </svg>
          </button>

          {isExpanded && (
            <div className="border-t border-white/10 mt-1 pt-1">
              {item.children?.map(child => renderNavItem(child, depth + 1))}
            </div>
          )}
        </div>
      )
    }

    return (
      <Link
        key={item.label}
        to={item.href}
        className={`flex items-center gap-2 px-4 py-2 text-sm transition-colors ${
          isActive(item.href)
            ? 'bg-white/10 text-white font-semibold'
            : 'text-white/70 hover:text-white'
        } ${depth > 0 ? 'pl-8' : ''}`}
      >
        <span className="text-lg">{item.icon}</span>
        <span>{item.label}</span>
      </Link>
    )
  }

  return (
    <aside className="w-56 bg-primary flex flex-col overflow-y-auto">
      <nav className="flex-1 py-4 space-y-1">
        {filteredItems.map(item => renderNavItem(item))}
      </nav>

      <div className="border-t border-white/10 p-4 text-xs text-white/60">
        <p>v1.0.0</p>
        <p>© 2026 PlugHub</p>
      </div>
    </aside>
  )
}

export default Sidebar
