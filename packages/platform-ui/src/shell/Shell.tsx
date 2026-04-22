import React from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import TopBar from './TopBar'
import Sidebar from './Sidebar'

// Routes that need full-bleed layout (no padding, overflow-hidden)
const FULL_BLEED_ROUTES = ['/monitor', '/agent-assist', '/config/platform']

const Shell: React.FC = () => {
  const { pathname } = useLocation()
  const fullBleed    = FULL_BLEED_ROUTES.some(r => pathname === r || pathname.startsWith(r + '/'))

  return (
    <div className="flex h-screen bg-gray-50">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <TopBar />
        {fullBleed ? (
          <main className="flex-1 overflow-hidden">
            <Outlet />
          </main>
        ) : (
          <main className="flex-1 overflow-auto">
            <div className="px-6 py-6">
              <Outlet />
            </div>
          </main>
        )}
      </div>
    </div>
  )
}

export default Shell
