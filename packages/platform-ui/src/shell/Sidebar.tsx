import React, { useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { useAuth } from '@/auth/useAuth'
import { useTranslation } from 'react-i18next'
import {
  Home, Monitor, Radio, GitBranch, ClipboardCheck, BarChart2, Settings, Search,
  FileText, List, Waves, Zap, PenLine, Rocket, FileCheck, BookOpen,
  Archive, Ruler, LayoutDashboard, Package, Tv2, Calendar, ShieldOff, CreditCard,
  Lock, Users, Globe,
} from 'lucide-react'

type LucideIcon = React.FC<{ className?: string }>

interface NavItem {
  navKey?: string
  label: string
  href: string
  icon: LucideIcon
  roles?: string[]
  // strict: quando true, NÃO há bypass de admin/supervisor — o item é gateado pelo
  // grant ABAC mesmo para admin (usar quando a API correspondente enforça ABAC sem
  // admin token, ex.: curadoria/`curar`). Default (false) mantém o bypass de admin.
  // anyOf: visível se o usuário tiver QUALQUER um dos campos (OR); senão usa `field`.
  abac?: { module: string; field?: string; anyOf?: string[]; strict?: boolean }
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
      icon: Home,
      roles: ['operator', 'supervisor', 'admin', 'developer', 'business']
    },

    // ── Console ────────────────────────────────────────────────────
    {
      label: t('nav.console'),
      href:  '/console',
      icon:  Monitor,
      roles: ['operator', 'supervisor', 'admin'],
      abac:  { module: 'contacts', field: 'operacao' },
    },

    // ── Monitor ────────────────────────────────────────────────────
    {
      navKey: 'monitor',
      label: t('nav.monitor'),
      href: '#',
      icon: Radio,
      roles: ['operator', 'supervisor', 'admin'],
      children: [
        { label: t('nav.monitor.sessions'),  href: '/flow/monitor',      icon: FileText,  abac: { module: 'contacts',   field: 'operacao' } },
        { label: t('nav.monitor.agents'),    href: '/contacts/agents',   icon: Users,     abac: { module: 'contacts',   field: 'operacao' } },
        { label: t('nav.monitor.pools'),     href: '/contacts/pools',    icon: Waves,     abac: { module: 'contacts',   field: 'operacao' } },
        { label: t('nav.monitor.events'),    href: '/contacts/events',   icon: Zap,       abac: { module: 'contacts',   field: 'operacao' } },
      ]
    },

    // ── Fluxo ──────────────────────────────────────────────────────
    {
      navKey: 'flow',
      label: t('nav.flow'),
      href: '#',
      icon: GitBranch,
      roles: ['admin', 'developer', 'business', 'supervisor'],
      children: [
        { label: t('nav.flow.editor'), href: '/agent-flow/editor', icon: PenLine, abac: { module: 'skill_flows', field: 'operacao' } },
        { label: t('nav.flow.deploy'), href: '/agent-flow/deploy', icon: Rocket,  abac: { module: 'skill_flows', field: 'operacao' } },
      ]
    },

    // ── Avaliação ──────────────────────────────────────────────────
    {
      navKey: 'quality',
      label: t('nav.quality'),
      href: '#',
      icon: ClipboardCheck,
      roles: ['operator', 'supervisor', 'admin', 'business'],
      // Quality nav é grant-first (strict-ABAC): cada item é gateado pelo grant ABAC,
      // sem `roles` e sem bypass de admin/supervisor — o menu reflete exatamente as
      // permissões concedidas pela tela de Acesso. Exceção: Knowledge (sem campo ABAC
      // próprio — KB é admin-only por role).
      children: [
        { label: t('nav.eval.forms'),       href: '/evaluation/forms',       icon: FileCheck,    abac: { module: 'evaluation', field: 'formularios', strict: true } },
        { label: t('nav.eval.campaigns'),   href: '/evaluation/campaigns',   icon: List,         abac: { module: 'evaluation', field: 'formularios', strict: true } },
        { label: t('nav.eval.knowledge'),   href: '/evaluation/knowledge',   icon: BookOpen,     abac: { module: 'evaluation', field: 'formularios', strict: true } },
        { label: t('nav.eval.evaluations'), href: '/evaluation/evaluations', icon: Archive,      abac: { module: 'evaluation', anyOf: ['report', 'revisar', 'contestar'], strict: true } },
        { label: t('nav.eval.calibration'), href: '/evaluation/calibration', icon: Ruler,        abac: { module: 'evaluation', anyOf: ['curar', 'report'], strict: true } },
        { label: t('nav.eval.curadoria'),   href: '/evaluation/curadoria',   icon: Search,       abac: { module: 'evaluation', field: 'curar', strict: true } },
        { label: t('nav.eval.rubric'),      href: '/evaluation/rubric',      icon: FileCheck,    abac: { module: 'evaluation', field: 'gerir_rubrica', strict: true } },
      ]
    },

    // ── Analytics ──────────────────────────────────────────────────
    {
      navKey: 'analise',
      label: t('nav.analise'),
      href: '#',
      icon: BarChart2,
      roles: ['supervisor', 'admin', 'business'],
      children: [
        { label: t('nav.analise.sessions'),  href: '/analise/sessions',  icon: FileText,      abac: { module: 'contacts',   field: 'visualizar' } },
        { label: t('nav.analise.agents'),    href: '/analise/agents',    icon: Users,         abac: { module: 'contacts',   field: 'visualizar' } },
        { label: t('nav.analise.pools'),     href: '/analise/pools',     icon: Package,       abac: { module: 'contacts',   field: 'visualizar' } },
        { label: t('nav.analise.events'),    href: '/analise/events',    icon: Zap,           abac: { module: 'contacts',   field: 'visualizar' } },
        { label: t('nav.analise.quality'),   href: '/analise/quality',   icon: ClipboardCheck, abac: { module: 'evaluation', field: 'report'     } },
      ]
    },

    // ── Auditoria LGPD ────────────────────────────────────────────
    {
      label: t('nav.audit'),
      href:  '/audit',
      icon:  Search,
      roles: ['admin', 'supervisor'],
      abac:  { module: 'audit', field: 'sessions' },
    },

    // ── Configuração ───────────────────────────────────────────────
    {
      navKey: 'config',
      label: t('nav.config'),
      href: '#',
      icon: Settings,
      roles: ['admin', 'business'],
      children: [
        { label: t('nav.dashboards'),    href: '/dashboards',           icon: LayoutDashboard, abac: { module: 'config', field: 'platform'  } },
        { label: t('nav.resources'),     href: '/config/resources',     icon: Package,         abac: { module: 'config', field: 'resources' } },
        { label: t('nav.platform'),      href: '/config/platform',      icon: Tv2,             abac: { module: 'config', field: 'platform'  } },
        { label: t('nav.channels'),      href: '/config/channels',      icon: Radio,           abac: { module: 'config', field: 'platform'  } },
        { label: t('nav.calendars'),     href: '/config/calendars',     icon: Calendar,        abac: { module: 'config', field: 'platform'  } },
        { label: t('nav.masking'),       href: '/config/masking',       icon: ShieldOff,       abac: { module: 'config', field: 'masking'   } },
        { label: t('nav.billing'),       href: '/config/billing',       icon: CreditCard,      roles: ['admin', 'business'] },
        { label: t('nav.access'),        href: '/config/access',        icon: Lock,            abac: { module: 'config', field: 'users'     } },
        { label: t('nav.groups'),        href: '/config/groups',        icon: Users,           abac: { module: 'config', field: 'users'     } },
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
    const strict = item.abac.strict === true
    // Degradação graciosa (config vazio/legado → libera) e bypass de admin/supervisor
    // valem APENAS para itens NÃO-strict. Itens strict são grant-first: exigem o grant
    // mesmo com config vazio e mesmo para admin (igual à API que enforça ABAC).
    if (!strict) {
      if (!session?.moduleConfig || Object.keys(session.moduleConfig).length === 0) return true
      if (['admin', 'supervisor'].includes(session?.role ?? '')) return true
    }
    const { module, field, anyOf } = item.abac
    if (anyOf && anyOf.length > 0) return anyOf.some(f => perms.can(module, f))
    return field ? perms.can(module, field) : true
  }

  const childVisible = (child: NavItem) =>
    (!child.roles || child.roles.includes(session?.role || '')) && passesAbac(child)
  const visibleChildrenOf = (item: NavItem) => item.children?.filter(childVisible) ?? []

  const filteredItems = navItems.filter(item => {
    if (!((!item.roles || item.roles.includes(session?.role || '')) && passesAbac(item))) return false
    // grupo sem nenhum filho visível → esconde o cabeçalho do grupo
    if (item.children && item.children.length > 0) return visibleChildrenOf(item).length > 0
    return true
  })

  // ── Collapsed: icon-only strip ─────────────────────────────────────────────
  if (collapsed) {
    return (
      <aside className="w-11 bg-primary flex flex-col overflow-hidden flex-shrink-0 transition-all duration-200">
        <nav aria-label="Navegação principal" className="flex-1 py-3 flex flex-col items-center gap-1">
          {filteredItems.map(item => {
            const key = item.navKey ?? item.href
            const href = item.href === '#'
              ? (visibleChildrenOf(item)[0]?.href ?? '#')
              : item.href
            const active = item.href === '#'
              ? item.children?.some(c => isActive(c.href))
              : isActive(item.href)

            return (
              <Link
                key={key}
                to={href}
                aria-label={item.label}
                aria-current={active ? 'page' : undefined}
                className={`w-9 h-9 flex items-center justify-center rounded-lg
                  transition-colors
                  ${active
                    ? 'bg-white/15 text-white'
                    : 'text-white/60 hover:text-white hover:bg-white/10'
                  }`}
              >
                <item.icon className="w-4 h-4" aria-hidden="true" />
              </Link>
            )
          })}
        </nav>

        <button
          onClick={() => setCollapsed(false)}
          aria-label="Expandir menu"
          className="w-full py-3 flex items-center justify-center text-white/50 hover:text-white
            transition-colors border-t border-white/10 text-sm"
        >
          <span aria-hidden="true">›</span>
        </button>
      </aside>
    )
  }

  // ── Expanded: full sidebar ─────────────────────────────────────────────────
  const renderNavItem = (item: NavItem, depth: number = 0) => {
    const hasChildren = item.children && item.children.length > 0

    if (hasChildren) {
      const shownChildren = visibleChildrenOf(item)
      if (shownChildren.length === 0) return null
      const groupKey   = item.navKey ?? item.href
      const isExpanded = expandedGroups.includes(groupKey)
      const panelId    = `nav-panel-${groupKey}`

      return (
        <div key={groupKey}>
          <button
            onClick={() => toggleGroup(groupKey)}
            aria-expanded={isExpanded}
            aria-controls={panelId}
            className="w-full flex items-center gap-2 px-4 py-2 text-white/70 hover:text-white transition-colors text-sm group"
          >
            <item.icon className="w-4 h-4 flex-shrink-0" aria-hidden="true" />
            <span className="flex-1 text-left">{item.label}</span>
            <svg
              aria-hidden="true"
              className={`w-4 h-4 transition-transform motion-safe:transition-transform ${isExpanded ? 'rotate-180' : ''}`}
              fill="none" stroke="currentColor" viewBox="0 0 24 24"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M19 14l-7-7m0 0L5 14m7-7v12" />
            </svg>
          </button>

          <div
            id={panelId}
            hidden={!isExpanded}
            className={isExpanded ? "border-t border-white/10 mt-1 pt-1" : ""}
          >
            {shownChildren.map(child => renderNavItem(child, depth + 1))}
          </div>
        </div>
      )
    }

    const active = isActive(item.href)
    return (
      <Link
        key={item.label}
        to={item.href}
        aria-current={active ? 'page' : undefined}
        className={`flex items-center gap-2 px-4 py-2 text-sm transition-colors ${
          active
            ? 'bg-white/10 text-white font-semibold'
            : 'text-white/70 hover:text-white'
        } ${depth > 0 ? 'pl-8' : ''}`}
      >
        <item.icon className="w-4 h-4 flex-shrink-0" aria-hidden="true" />
        <span>{item.label}</span>
      </Link>
    )
  }

  return (
    <aside className="w-56 bg-primary flex flex-col overflow-y-auto flex-shrink-0 transition-all duration-200">
      <nav aria-label="Navegação principal" className="flex-1 py-4 space-y-1">
        {filteredItems.map(item => renderNavItem(item))}
      </nav>

      <div className="border-t border-white/10 p-3 flex items-center gap-2">
        <div className="flex-1 text-xs text-white/40">
          <p>v1.0.0</p>
        </div>
        <button
          onClick={() => setCollapsed(true)}
          aria-label="Recolher menu"
          className="text-white/40 hover:text-white/80 transition-colors text-xs px-1"
        >
          <span aria-hidden="true">‹</span>
        </button>
      </div>
    </aside>
  )
}

export default Sidebar
