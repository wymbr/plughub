/**
 * BreadcrumbBar — global navigation breadcrumb strip.
 *
 * Rendered by Shell between TopBar and the main content area on every route.
 * Uses the same i18n keys as Sidebar so labels stay in sync automatically.
 *
 * Layout:  [Section ›] Current page
 * The section label links to the first page in that group.
 */
import React from 'react'
import { Link, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ChevronRight } from 'lucide-react'

// ── Route → breadcrumb mapping ────────────────────────────────────────────────
// Keys are i18n paths in the 'shell' namespace (same as Sidebar).
// sectionHref: first meaningful page in the section (used as the section link).

interface BreadcrumbDef {
  /** i18n key for the parent section (e.g. 'nav.monitor'). Omit for top-level pages. */
  section?: string
  /** href for the section label link */
  sectionHref?: string
  /** i18n key for the current page label */
  page: string
}

const BREADCRUMBS: Record<string, BreadcrumbDef> = {
  // Top-level
  '/':                       { page: 'nav.home' },
  '/console':                { page: 'nav.console' },
  '/audit':                  { page: 'nav.audit' },
  '/dashboards':             { section: 'nav.config', sectionHref: '/dashboards',        page: 'nav.dashboards' },

  // ── Monitor ────────────────────────────────────────────────────────────────
  '/flow/monitor':           { section: 'nav.monitor', sectionHref: '/flow/monitor',    page: 'nav.monitor.sessions' },
  '/contacts/agents':        { section: 'nav.monitor', sectionHref: '/flow/monitor',    page: 'nav.monitor.agents' },
  '/contacts/pools':         { section: 'nav.monitor', sectionHref: '/flow/monitor',    page: 'nav.monitor.pools' },

  // ── Flow ───────────────────────────────────────────────────────────────────
  '/agent-flow/editor':      { section: 'nav.flow',    sectionHref: '/agent-flow/editor', page: 'nav.flow.editor' },
  '/agent-flow/deploy':      { section: 'nav.flow',    sectionHref: '/agent-flow/editor', page: 'nav.flow.deploy' },

  // ── Quality ────────────────────────────────────────────────────────────────
  '/evaluation/forms':       { section: 'nav.quality', sectionHref: '/evaluation/forms', page: 'nav.eval.forms' },
  '/evaluation/campaigns':   { section: 'nav.quality', sectionHref: '/evaluation/forms', page: 'nav.eval.campaigns' },
  '/evaluation/knowledge':   { section: 'nav.quality', sectionHref: '/evaluation/forms', page: 'nav.eval.knowledge' },
  '/evaluation/evaluations': { section: 'nav.quality', sectionHref: '/evaluation/forms', page: 'nav.eval.evaluations' },
  '/evaluation/reports':     { section: 'nav.quality', sectionHref: '/evaluation/forms', page: 'nav.eval.reports' },
  '/evaluation/calibration': { section: 'nav.quality', sectionHref: '/evaluation/forms', page: 'nav.eval.calibration' },
  '/evaluation/curadoria':   { section: 'nav.quality', sectionHref: '/evaluation/forms', page: 'nav.eval.curadoria' },

  // ── Analytics ──────────────────────────────────────────────────────────────
  '/analise/sessions':       { section: 'nav.analise', sectionHref: '/analise/sessions',  page: 'nav.analise.sessions' },
  '/analise/agents':         { section: 'nav.analise', sectionHref: '/analise/sessions',  page: 'nav.analise.agents' },
  '/analise/pools':          { section: 'nav.analise', sectionHref: '/analise/sessions',  page: 'nav.analise.pools' },
  '/analise/events':         { section: 'nav.analise', sectionHref: '/analise/sessions',  page: 'nav.analise.events' },
  '/analise/quality':        { section: 'nav.analise', sectionHref: '/analise/sessions',  page: 'nav.analise.quality' },

  // ── Configuration ──────────────────────────────────────────────────────────
  '/config/resources':       { section: 'nav.config',  sectionHref: '/config/resources',  page: 'nav.resources' },
  '/config/platform':        { section: 'nav.config',  sectionHref: '/config/resources',  page: 'nav.platform' },
  '/config/channels':        { section: 'nav.config',  sectionHref: '/config/resources',  page: 'nav.channels' },
  '/config/calendars':       { section: 'nav.config',  sectionHref: '/config/resources',  page: 'nav.calendars' },
  '/config/masking':         { section: 'nav.config',  sectionHref: '/config/resources',  page: 'nav.masking' },
  '/config/billing':         { section: 'nav.config',  sectionHref: '/config/resources',  page: 'nav.billing' },
  '/config/access':          { section: 'nav.config',  sectionHref: '/config/resources',  page: 'nav.access' },
  '/config/groups':          { section: 'nav.config',  sectionHref: '/config/resources',  page: 'nav.groups' },
}

// ── Component ─────────────────────────────────────────────────────────────────

const BreadcrumbBar: React.FC = () => {
  const { pathname } = useLocation()
  const { t } = useTranslation('shell')

  const def = BREADCRUMBS[pathname]
  if (!def) return null   // unknown / redirect routes — render nothing

  return (
    <nav
      aria-label="Breadcrumb"
      className="flex items-center gap-1 px-6 h-8 bg-surface-muted border-b border-border flex-shrink-0 text-xs"
    >
      {def.section && (
        <>
          {def.sectionHref ? (
            <Link
              to={def.sectionHref}
              className="text-muted hover:text-primary transition-colors font-medium"
            >
              {t(def.section)}
            </Link>
          ) : (
            <span className="text-muted font-medium">{t(def.section)}</span>
          )}
          <ChevronRight className="w-3 h-3 text-muted-light flex-shrink-0" aria-hidden="true" />
        </>
      )}
      <span className="text-dark font-semibold" aria-current="page">
        {t(def.page)}
      </span>
    </nav>
  )
}

export default BreadcrumbBar
