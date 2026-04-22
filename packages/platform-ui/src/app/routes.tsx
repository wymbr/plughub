import React, { lazy, Suspense } from 'react'
import { RouteObject } from 'react-router-dom'
import Shell from '@/shell/Shell'
import LoginPage from '@/auth/LoginPage'
import { ProtectedRoute } from '@/auth/ProtectedRoute'
import HomePage from '@/modules/home/HomePage'
import ConfigRecursosIndex from '@/modules/config-recursos'
import MonitorPage from '@/modules/atendimento/MonitorPage'
import ConfigPlataformaPage from '@/modules/config-plataforma/ConfigPlataformaPage'
import PlaceholderPage from '@/modules/_placeholder/PlaceholderPage'

const LoadingFallback = () => <div className="flex justify-center items-center h-screen">Loading...</div>

export const routes: RouteObject[] = [
  {
    path: '/login',
    element: <LoginPage />
  },
  {
    path: '/',
    element: (
      <ProtectedRoute>
        <Shell />
      </ProtectedRoute>
    ),
    children: [
      {
        index: true,
        element: <HomePage />
      },
      {
        path: 'config/recursos',
        element: <ConfigRecursosIndex />
      },
      {
        path: 'monitor',
        element: <MonitorPage />
      },
      {
        path: 'agent-assist',
        element: <PlaceholderPage module="Agent Assist" phase="Arc 2" />
      },
      {
        path: 'workflows',
        element: <PlaceholderPage module="Workflows" phase="Arc 4" />
      },
      {
        path: 'dashboards',
        element: <PlaceholderPage module="Dashboards" phase="Arc 3" />
      },
      {
        path: 'reports',
        element: <PlaceholderPage module="Reports" phase="Arc 3" />
      },
      {
        path: 'skill-flows',
        element: <PlaceholderPage module="Skill Flows" phase="Arc 2" />
      },
      {
        path: 'config/platform',
        element: <ConfigPlataformaPage />
      },
      {
        path: 'config/access',
        element: <PlaceholderPage module="Access Control" phase="Arc 2" />
      },
      {
        path: 'developer',
        element: <PlaceholderPage module="Developer Tools" phase="Arc 4" />
      },
      {
        path: 'business',
        element: <PlaceholderPage module="Business Analytics" phase="Arc 3" />
      }
    ]
  }
]
