import React, { useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { useAuth } from '@/auth/useAuth'
import { passesAbacRule, type AbacNavRule } from '@/lib/permissions'
import { useTranslation } from 'react-i18next'
import {
  Home, Monitor, Radio, GitBranch, ClipboardCheck, ClipboardList, BarChart2, Settings, Search,
  FileText, List, Waves, Zap, PenLine, Rocket, FileCheck, BookOpen,
  Archive, Ruler, LayoutDashboard, Package, Tv2, Calendar, ShieldOff, CreditCard,
  Lock, Users, Globe, MessageSquare, UserSearch, CalendarClock, Send, Inbox,
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
  // REFERENCIA o tipo compartilhado, nao uma copia: a copia anterior guardava a
  // flag `strict` que `AbacNavRule` ja tinha perdido — tipo copiado diverge na
  // primeira mudanca, igual a regra copiada.
  abac?: AbacNavRule
  children?: NavItem[]
}

const Sidebar: React.FC = () => {
  const { session } = useAuth()
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
    },

    // ── Console ────────────────────────────────────────────────────
    {
      label: t('nav.console'),
      href:  '/console',
      icon:  Monitor,
      abac:  { module: 'contacts', field: 'operacao' },
    },

    // ── Monitor ────────────────────────────────────────────────────
    {
      navKey: 'monitor',
      label: t('nav.monitor'),
      href: '#',
      icon: Radio,
      children: [
        { label: t('nav.monitor.sessions'),  href: '/flow/monitor',      icon: FileText,  abac: { module: 'contacts',   field: 'operacao' } },
        { label: t('nav.monitor.agents'),    href: '/contacts/agents',   icon: Users,     abac: { module: 'contacts',   field: 'operacao' } },
        { label: t('nav.monitor.pools'),     href: '/contacts/pools',    icon: Waves,     abac: { module: 'contacts',   field: 'operacao' } },
        { label: t('nav.monitor.events'),    href: '/contacts/events',   icon: Zap,       abac: { module: 'contacts',   field: 'operacao' } },
        // Scheduler Fase 3 — grant-first (strict): visível só com scheduler.operacao (D2).
        { label: t('nav.monitor.schedules'), href: '/monitor/schedules', icon: CalendarClock, abac: { module: 'scheduler', field: 'operacao' } },
        // I5 / ADR § D7b — pendências de wrap-up AGORA. Leitura sob o mesmo grant
        // do resto do Monitor; a AÇÃO de encerrar é mais estreita e é o endpoint
        // que a enforça (supervisor|admin).
        { label: t('nav.monitor.workItems'), href: '/monitor/work-items', icon: Inbox, abac: { module: 'contacts', field: 'operacao' } },
      ]
    },

    // ── Fluxo ──────────────────────────────────────────────────────
    {
      navKey: 'flow',
      label: t('nav.flow'),
      href: '#',
      icon: GitBranch,
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
      // Quality nav é grant-first (strict-ABAC): cada item é gateado pelo grant ABAC,
      // sem `roles` e sem bypass de admin/supervisor — o menu reflete exatamente as
      // permissões concedidas pela tela de Acesso. Exceção: Knowledge (sem campo ABAC
      // próprio — KB é admin-only por role).
      children: [
        { label: t('nav.eval.forms'),       href: '/evaluation/forms',       icon: FileCheck,    abac: { module: 'evaluation', field: 'formularios' } },
        { label: t('nav.eval.campaigns'),   href: '/evaluation/campaigns',   icon: List,         abac: { module: 'evaluation', field: 'formularios' } },
        { label: t('nav.eval.knowledge'),   href: '/evaluation/knowledge',   icon: BookOpen,     abac: { module: 'evaluation', field: 'formularios' } },
        { label: t('nav.eval.evaluations'), href: '/evaluation/evaluations', icon: Archive,      abac: { module: 'evaluation', anyOf: ['report', 'revisar', 'contestar'] } },
        { label: t('nav.eval.calibration'), href: '/evaluation/calibration', icon: Ruler,        abac: { module: 'evaluation', anyOf: ['curar', 'report'] } },
        { label: t('nav.eval.curadoria'),   href: '/evaluation/curadoria',   icon: Search,       abac: { module: 'evaluation', field: 'curar' } },
        { label: t('nav.eval.rubric'),      href: '/evaluation/rubric',      icon: FileCheck,    abac: { module: 'evaluation', field: 'gerir_rubrica' } },
      ]
    },

    // ── Analytics ──────────────────────────────────────────────────
    {
      navKey: 'analise',
      label: t('nav.analise'),
      href: '#',
      icon: BarChart2,
      // ⚠️ `roles: ['supervisor','admin','business']` REMOVIDO em 2026-08-27.
      // Era um portão de PAPEL a montante da ABAC, hardcoded e não editável pela tela
      // de Acesso — violava "Every config field is UI-editable" e esvaziava o
      // `module_config` justamente onde ele deveria decidir. O `operator` tem
      // `contacts.visualizar: read_only` no seed (medido nos 3 usuários) e mesmo assim
      // não alcançava o menu, porque o papel falhava ANTES de a ABAC ser consultada.
      // Agora quem decide é o grant de cada filho (`contacts.visualizar`).
      // Efeito colateral desejado: passa a existir usuário que combina *alcança o
      // Analytics* com *escopo de pool estreito* — a cobaia que faltava para validar
      // escopo pela TELA, e que até aqui só existia por remendo à mão.
      children: [
        { label: t('nav.analise.sessions'),  href: '/analise/sessions',  icon: FileText,      abac: { module: 'contacts',   field: 'visualizar' } },
        // F3.3 — «Processos» saiu do menu: processo é PIVÔ, não navegação livre (D2).
        // Chega-se a ele pelo chip da linha de contato em Análise › Sessões. Uma lista
        // livre reintroduziria o filtro por pool no nível errado — devolveria *journeys
        // que tocaram o pool* a quem pediu contatos.
        { label: t('nav.analise.agents'),    href: '/analise/agents',    icon: Users,         abac: { module: 'contacts',   field: 'visualizar' } },
        { label: t('nav.analise.pools'),     href: '/analise/pools',     icon: Package,       abac: { module: 'contacts',   field: 'visualizar' } },
        { label: t('nav.analise.events'),    href: '/analise/events',    icon: Zap,           abac: { module: 'contacts',   field: 'visualizar' } },
        { label: t('nav.analise.quality'),   href: '/analise/quality',   icon: ClipboardCheck, abac: { module: 'evaluation', field: 'report'     } },
        { label: t('nav.analise.customers'), href: '/analise/customers', icon: UserSearch,    abac: { module: 'contacts',   field: 'visualizar' } },
        { label: t('nav.analise.customerVoice'), href: '/analise/customer-voice', icon: MessageSquare, abac: { module: 'contacts', field: 'visualizar' } },
        { label: t('nav.analise.surveys'), href: '/analise/surveys', icon: ClipboardList, abac: { module: 'evaluation', field: 'report' } },
        // I5 / ADR § D7b fatia 2 — histórico de wrap-up. Par retrospectivo do
        // Monitor › Pendências; mesmo grant do resto do Analytics.
        { label: t('nav.analise.wrapup'), href: '/analise/wrapup', icon: Inbox, abac: { module: 'contacts', field: 'visualizar' } },
      ]
    },

    // ── Auditoria LGPD ────────────────────────────────────────────
    // grant-first e SEM `roles:` (2026-08-27). Medido: o menu mostrava esta
    // entrada a admin e supervisor pelo BYPASS DE PAPEL, e `GET /v1/audit/mcp-calls`
    // devolve 403 para os TRES usuarios do tenant — ninguem tem
    // `module_config.audit.sessions`. Ou seja, a UI oferecia uma tela que o backend
    // recusa; falha fechada, mas ainda assim uma tela que nao funciona.
    //
    // O `roles:` sai porque o modulo `audit` e do DPO/compliance e o CLAUDE.md o
    // declara ORTOGONAL as roles existentes: quem tem o grant ve, tenha o papel que
    // tiver. Manter o portao de papel aqui seria justamente o contrario do desenho.
    {
      label: t('nav.audit'),
      href:  '/audit',
      icon:  Search,
      abac:  { module: 'audit', field: 'sessions' },
    },

    // ── Configuração ───────────────────────────────────────────────
    {
      navKey: 'config',
      label: t('nav.config'),
      href: '#',
      icon: Settings,
      children: [
        { label: t('nav.dashboards'),    href: '/dashboards',           icon: LayoutDashboard, abac: { module: 'config', field: 'dashboards' } },
        { label: t('nav.resources'),     href: '/config/resources',     icon: Package,         abac: { module: 'config', field: 'resources' } },
        { label: t('nav.platform'),      href: '/config/platform',      icon: Tv2,             abac: { module: 'config', field: 'platform'  } },
        { label: t('nav.channels'),      href: '/config/channels',      icon: Radio,           abac: { module: 'config', field: 'channels'  } },
        // ⚠️ MENU-ONLY: o calendar-api nao tem portao nenhum — medido em 2026-08-27,
        // `POST /v1/calendars` SEM credencial devolveu 201 e criou o recurso. Este campo
        // decide quem VE a tela, nao quem pode escrever. Ver `TODO.md` § "Serviços de
        // config sem portão".
        { label: t('nav.calendars'),     href: '/config/calendars',     icon: Calendar,        abac: { module: 'config', field: 'calendars' } },
        // Scheduler Fase 3 — grant-first (strict): visível só com scheduler.configurar (D2).
        { label: t('nav.schedules'),     href: '/config/schedules',     icon: CalendarClock,   abac: { module: 'scheduler', field: 'configurar' } },
        // Outbound (fatia 1b) — grant-first (strict): visível só com outbound.configurar.
        { label: t('nav.outbound'),      href: '/config/outbound',      icon: Send,            abac: { module: 'outbound', field: 'configurar' } },
        { label: t('nav.masking'),       href: '/config/masking',       icon: ShieldOff,       abac: { module: 'config', field: 'masking'   } },
        // ⚠️ MENU-ONLY, mesma razao: `DIALOG_ADMIN_TOKEN` vazio no compose torna o
        // `_require_admin` do dialog-api inerte (`if expected and ...`) — criar E PUBLICAR
        // form sem credencial devolveu 200 nos dois.
        { label: t('nav.dialogForms'),   href: '/config/dialog-forms',  icon: MessageSquare,   abac: { module: 'config', field: 'dialog_forms' } },
        // Era a UNICA entrada gateada por PAPEL no nivel do item. O modulo `billing` ja
        // existia e o supervisor chegou a ter `billing.visualizar` concedido sem ver a
        // tela — grant e portao discordando, cada um em silencio.
        { label: t('nav.billing'),       href: '/config/billing',       icon: CreditCard,      abac: { module: 'billing', field: 'visualizar' } },
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

  // ⚠️ NÃO EXISTE MAIS PORTÃO DE PAPEL no menu (2026-08-27, passo 5). Os 7 `roles:`
  // saíram: 5 eram cabeçalhos de grupo — e cabeçalho já era DERIVADO ("grupo visível
  // ⟺ ao menos um filho visível", logo abaixo) —, 1 era o `nav.home` (qualquer um
  // logado vê) e 1 era o `nav.billing`, que virou grant. Enquanto existiam, conceder o
  // campo do filho não mudava o que a pessoa via: o papel decidia antes.
  //
  // A decisão vive em `lib/permissions.ts` (`passesAbacRule`) porque o GUARD DE ROTA
  // precisa da mesma resposta. Duas implementações da mesma regra é como a divergência
  // de masking nasceu — cada porta com teste próprio, nenhum comparando as portas.
  function passesAbac(item: NavItem): boolean {
    return passesAbacRule(item.abac, session?.moduleConfig, session?.role,
                          session?.unrestricted)
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
