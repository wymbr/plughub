import React, { useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { useAuth } from '@/auth/useAuth'
import { useTranslation } from 'react-i18next'

interface NavItem {
  navKey?: string
  label: string
  href: string
  icon: string
  roles?: string[]
  abac?: { module: string; field: string }
  children?: NavItem[]
}

const Sidebar: React.FC = () => {
  const { session, perms } = useAuth()
  const { t } = useTranslation('shell')
  const location = useLocation()
  const [expandedGroups, setExpandedGroups] = useState<string[]>([])
  const [collapsed, setCollapsed]           = useState(false)

  const toggleGroup = (key: string) => {
    if (collapsed) { setCollapsed(false); return }
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

    // ── Atendimento ────────────────────────────────────────────────
    {
      navKey: 'service',
      label: t('nav.service'),
      href: '#',
      icon: '📞',
      roles: ['operator', 'supervisor', 'admin', 'business'],
      children: [
        {
          label: t('nav.service.sessions'),
          href:  '/contacts/sessions',
          icon:  '📋',
        },
        {
          label: t('nav.service.agents'),
          href:  '/contacts/agents',
          icon:  '👥',
          abac:  { module: 'contacts', field: 'operacao' },
        },
        {
          label: t('nav.service.pools'),
          href:  '/contacts/pools',
          icon:  '🏊',
          abac:  { module: 'contacts', field: 'operacao' },
        },
        {
          label: t('nav.service.events'),
          href:  '/contacts/events',
          icon:  '📡',
          abac:  { module: 'contacts', field: 'operacao' },
        },
        {
          label: t('nav.agentAssist'),
          href:  '/agent-assist',
          icon:  '🤖',
          abac:  { module: 'contacts', field: 'operacao' },
        },
      ]
    },

    // ── Fluxo ──────────────────────────────────────────────────────
    {
      navKey: 'flow',
      label: t('nav.flow'),
      href: '#',
      icon: '🔄',
      roles: ['admin', 'developer', 'business', 'supervisor'],
      children: [
        { label: t('nav.flow.editor'),   href: '/agent-flow/editor',   icon: '✏️', abac: { module: 'skill_flows', field: 'operacao' } },
        { label: t('nav.flow.deploy'),   href: '/agent-flow/deploy',   icon: '🚀', abac: { module: 'skill_flows', field: 'operacao' } },
        { label: t('nav.flow.monitor'),  href: '/flow/monitor',        icon: '📡', abac: { module: 'skill_flows', field: 'operacao' } },
        { label: t('nav.flow.processos'),href: '/flow/processos',      icon: '⚙️', abac: { module: 'workflows',   field: 'operacao' } },
      ]
    },

    // ── Avaliação ──────────────────────────────────────────────────
    {
      navKey: 'quality',
      label: t('nav.quality'),
      href: '#',
      icon: '✓',
      roles: ['operator', 'supervisor', 'admin', 'business'],
      children: [
        { label: t('nav.eval.forms'),       href: '/evaluation/forms',       icon: '📝', roles: ['admin'],                           abac: { module: 'evaluation', field: 'formularios' } },
        { label: t('nav.eval.campaigns'),   href: '/evaluation/campaigns',   icon: '📋', roles: ['supervisor', 'admin'],             abac: { module: 'evaluation', field: 'formularios' } },
        { label: t('nav.eval.knowledge'),   href: '/evaluation/knowledge',   icon: '📚', roles: ['admin'] },
        { label: t('nav.eval.evaluations'), href: '/evaluation/evaluations', icon: '🗂️', roles: ['operator', 'supervisor', 'admin'] },
        { label: t('nav.eval.reports'),     href: '/evaluation/reports',     icon: '📊', roles: ['supervisor', 'admin', 'business'], abac: { module: 'evaluation', field: 'report' } },
      ]
    },

    // ── Análise ────────────────────────────────────────────────────
    {
      navKey: 'analise',
      label: t('nav.analise'),
      href: '#',
      icon: '📊',
      roles: ['supervisor', 'admin', 'business'],
      children: [
        { label: t('nav.analise.contacts'), href: '/analise/contatos',  icon: '📋', abac: { module: 'contacts', field: 'visualizar' } },
        { label: t('nav.analise.agents'),   href: '/analise/agentes',   icon: '👥', abac: { module: 'contacts', field: 'visualizar' } },
        { label: t('nav.analise.processos'),href: '/analise/processos', icon: '⚙️', abac: { module: 'workflows', field: 'operacao'  } },
        { label: t('nav.analise.quality'),  href: '/analise/qualidade', icon: '✓',  abac: { module: 'evaluation', field: 'report'   } },
      ]
    },

    // ── Configuração ───────────────────────────────────────────────
    {
      navKey: 'config',
      label: t('nav.config'),
      href: '#',
      icon: '⚙️',
      roles: ['admin', 'business'],
      children: [
        { label: t('nav.dashboards'),    href: '/dashboards',           icon: '📊', abac: { module: 'config', field: 'platform'  } },
        { label: t('nav.resources'),     href: '/config/resources',     icon: '📦', abac: { module: 'config', field: 'resources' } },
        { label: t('nav.platform'),      href: '/config/platform',      icon: '🖥️', abac: { module: 'config', field: 'platform'  } },
        { label: t('nav.channels'),      href: '/config/channels',      icon: '📡', abac: { module: 'config', field: 'platform'  } },
        { label: t('nav.calendars'),     href: '/config/calendars',     icon: '📅', abac: { module: 'config', field: 'platform'  } },
        { label: t('nav.masking'),       href: '/config/masking',       icon: '🔒', abac: { module: 'config', field: 'masking'   } },
        { label: t('nav.billing'),       href: '/config/billing',       icon: '💳', roles: ['admin', 'business'] },
        { label: t('nav.access'),        href: '/config/access',        icon: '🔐', abac: { module: 'config', field: 'users'     } },
      ]
    },

  ]

  const isActive = (href: string) => {
    const qIdx = href.indexOf('?')
    if (qIdx >= 0) {
      const hrefPath   = href.slice(0, qIdx)
      const hrefSearch = href.slice(qIdx + 1)
      return location.pathname === hrefPath && location.search.includes(hrefSearch)
    }
    return location.pathname === href || location.pathname.startsWith(href + '/')
  }

  function passesAbac(item: NavItem): boolean {
    if (!item.abac) return true
    if (!session?.moduleConfig || Object.keys(session.moduleConfig).length === 0) return true
    // admin / supervisor bypass ABAC
    if (['admin', 'supervisor'].includes(session?.role ?? '')) return true
    return perms.can(item.abac.module, item.abac.field)
  }

  const filteredItems = navItems.filter(item =>
    (!item.roles || item.roles.includes(session?.role || '')) && passesAbac(item)
  )

  // ── Collapsed: icon-only strip ─────────────────────────────────────────────
  if (collapsed) {
    return (
      <aside className="w-11 bg-primary flex flex-col overflow-hidden flex-shrink-0 transition-all duration-200">
        <nav className="flex-1 py-3 flex flex-col items-center gap-1">
          {filteredItems.map(item => {
            const key = item.navKey ?? item.href
            const href = item.href === '#'
              ? (item.children?.[0]?.href ?? '#')
              : item.href
            const active = item.href === '#'
              ? item.children?.some(c => isActive(c.href))
              : isActive(item.href)

            return (
              <Link
                key={key}
                to={href}
                title={item.label}
                className={`w-9 h-9 flex items-center justify-center rounded-lg text-lg
                  transition-colors
                  ${active
                    ? 'bg-white/15 text-white'
                    : 'text-white/60 hover:text-white hover:bg-white/10'
                  }`}
              >
                {item.icon}
              </Link>
            )
          })}
        </nav>

        <button
          onClick={() => setCollapsed(false)}
          title="Expandir menu"
          className="w-full py-3 flex items-center justify-center text-white/50 hover:text-white
            transition-colors border-t border-white/10 text-sm"
        >
          ›
        </button>
      </aside>
    )
  }

  // ── Expanded: full sidebar ─────────────────────────────────────────────────
  const renderNavItem = (item: NavItem, depth: number = 0) => {
    const hasChildren = item.children && item.children.length > 0

    if (hasChildren) {
      const groupKey  = item.navKey ?? item.href
      const isExpanded = expandedGroups.includes(groupKey)

      return (
        <div key={groupKey}>
          <button
            onClick={() => toggleGroup(groupKey)}
            className="w-full flex items-center gap-2 px-4 py-2 text-white/70 hover:text-white transition-colors text-sm group"
          >
            <span className="text-lg">{item.icon}</span>
            <span className="flex-1 text-left">{item.label}</span>
            <svg
              className={`w-4 h-4 transition-transform ${isExpanded ? 'rotate-180' : ''}`}
              fill="none" stroke="currentColor" viewBox="0 0 24 24"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M19 14l-7-7m0 0L5 14m7-7v12" />
            </svg>
          </button>

          {isExpanded && (
            <div className="border-t border-white/10 mt-1 pt-1">
              {item.children
                ?.filter(child =>
                  (!child.roles || child.roles.includes(session?.role || '')) &&
                  passesAbac(child)
                )
                .map(child => renderNavItem(child, depth + 1))}
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
    <aside className="w-56 bg-primary flex flex-col overflow-y-auto flex-shrink-0 transition-all duration-200">
      <nav className="flex-1 py-4 space-y-1">
        {filteredItems.map(item => renderNavItem(item))}
      </nav>

      <div className="border-t border-white/10 p-3 flex items-center gap-2">
        <div className="flex-1 text-xs text-white/40">
          <p>v1.0.0</p>
        </div>
        <button
          onClick={() => setCollapsed(true)}
          title="Recolher menu"
          className="text-white/40 hover:text-white/80 transition-colors text-xs px-1"
        >
          ‹
        </button>
      </div>
    </aside>
  )
}

export default Sidebar
