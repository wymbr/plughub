import React from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import TopBar from './TopBar'
import Sidebar from './Sidebar'
import BreadcrumbBar from './BreadcrumbBar'
import { AgentAssistProvider } from '@/modules/agent-assist/AgentAssistContext'

// Routes that need full-bleed layout (no padding, overflow-hidden)
const FULL_BLEED_ROUTES = ['/console', '/monitor', '/agent-assist', '/config/platform', '/workflows', '/analise']

const Shell: React.FC = () => {
  const { pathname } = useLocation()
  const fullBleed    = FULL_BLEED_ROUTES.some(r => pathname === r || pathname.startsWith(r + '/'))

  return (
    <div className="flex h-screen bg-surface-muted">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <TopBar />
        <BreadcrumbBar />
        <AgentAssistProvider>
          {fullBleed ? (
            <main id="main-content" className="flex-1 overflow-hidden" tabIndex={-1}>
              <Outlet />
            </main>
          ) : (
            <main id="main-content" className="flex-1 overflow-auto" tabIndex={-1}>
              <div className="px-6 py-6">
                <Outlet />
              </div>
            </main>
          )}
        </AgentAssistProvider>
      </div>
    </div>
  )
}

export default Shell
