import React, { KeyboardEvent, ReactNode, useId } from 'react'

export interface TabItem {
  /** Unique key used as the tab value */
  key: string
  /** Visible tab label */
  label: ReactNode
  /** Badge count shown next to the label (e.g. unread items) */
  count?: number
  /** When true, the tab is rendered but non-interactive */
  disabled?: boolean
}

type TabsVariant = 'underline' | 'pill'

interface TabsProps {
  tabs: TabItem[]
  activeTab: string
  onChange: (key: string) => void
  /** Visual style. Default: 'underline' */
  variant?: TabsVariant
  /** Additional className on the tab list container */
  className?: string
  /** When provided, renders the corresponding panel (matched by tab key) */
  panels?: Record<string, ReactNode>
  /** Accessible label for the tab list */
  'aria-label'?: string
}

// ── Style maps ───────────────────────────────────────────────────────────────

const containerVariant: Record<TabsVariant, string> = {
  underline: 'border-b border-border',
  pill:      'bg-surface-alt rounded-lg p-1',
}

const tabBase: Record<TabsVariant, string> = {
  underline: 'relative pb-3 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary rounded-t',
  pill:      'px-3 py-1.5 text-sm font-medium rounded-md transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary',
}

const tabActive: Record<TabsVariant, string> = {
  underline: 'text-primary after:absolute after:bottom-0 after:inset-x-0 after:h-0.5 after:bg-primary after:rounded-t',
  pill:      'bg-surface text-primary shadow-card',
}

const tabInactive: Record<TabsVariant, string> = {
  underline: 'text-muted hover:text-dark',
  pill:      'text-muted hover:text-dark hover:bg-surface/60',
}

const tabDisabled = 'opacity-40 cursor-not-allowed pointer-events-none'

// ── Component ────────────────────────────────────────────────────────────────

const Tabs: React.FC<TabsProps> = ({
  tabs,
  activeTab,
  onChange,
  variant = 'underline',
  className = '',
  panels,
  'aria-label': ariaLabel,
}) => {
  const baseId = useId()

  const tabId   = (key: string) => `${baseId}-tab-${key}`
  const panelId = (key: string) => `${baseId}-panel-${key}`

  const enabledTabs = tabs.filter(t => !t.disabled)

  const handleKeyDown = (e: KeyboardEvent<HTMLButtonElement>, currentKey: string) => {
    const idx = enabledTabs.findIndex(t => t.key === currentKey)
    if (idx === -1) return

    let next: TabItem | undefined

    if (e.key === 'ArrowRight') {
      next = enabledTabs[(idx + 1) % enabledTabs.length]
    } else if (e.key === 'ArrowLeft') {
      next = enabledTabs[(idx - 1 + enabledTabs.length) % enabledTabs.length]
    } else if (e.key === 'Home') {
      next = enabledTabs[0]
    } else if (e.key === 'End') {
      next = enabledTabs[enabledTabs.length - 1]
    }

    if (next) {
      e.preventDefault()
      onChange(next.key)
      // Move DOM focus to the newly active tab
      document.getElementById(tabId(next.key))?.focus()
    }
  }

  return (
    <div>
      {/* Tab list */}
      <div
        role="tablist"
        aria-label={ariaLabel}
        className={`flex gap-1 ${containerVariant[variant]} ${className}`}
      >
        {tabs.map(tab => {
          const isActive   = tab.key === activeTab
          const isDisabled = !!tab.disabled

          return (
            <button
              key={tab.key}
              id={tabId(tab.key)}
              role="tab"
              aria-selected={isActive}
              aria-controls={panels ? panelId(tab.key) : undefined}
              aria-disabled={isDisabled || undefined}
              tabIndex={isActive ? 0 : -1}
              onClick={() => !isDisabled && onChange(tab.key)}
              onKeyDown={e => handleKeyDown(e, tab.key)}
              className={`
                inline-flex items-center gap-1.5
                ${tabBase[variant]}
                ${isActive ? tabActive[variant] : tabInactive[variant]}
                ${isDisabled ? tabDisabled : ''}
              `}
            >
              {tab.label}
              {tab.count !== undefined && (
                <span
                  className={`
                    inline-flex items-center justify-center min-w-[1.25rem] h-5 px-1
                    rounded-full text-xs font-semibold
                    ${isActive ? 'bg-primary-light text-primary' : 'bg-border text-muted'}
                  `}
                  aria-label={`${tab.count} itens`}
                >
                  {tab.count}
                </span>
              )}
            </button>
          )
        })}
      </div>

      {/* Tab panels (optional) */}
      {panels && tabs.map(tab => (
        <div
          key={tab.key}
          id={panelId(tab.key)}
          role="tabpanel"
          aria-labelledby={tabId(tab.key)}
          hidden={tab.key !== activeTab}
          tabIndex={0}
          className="focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary rounded"
        >
          {panels[tab.key]}
        </div>
      ))}
    </div>
  )
}

export default Tabs
