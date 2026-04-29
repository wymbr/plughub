import React, { lazy, Suspense } from 'react'
import { RouteObject } from 'react-router-dom'
import Shell from '@/shell/Shell'
import LoginPage from '@/auth/LoginPage'
import { ProtectedRoute } from '@/auth/ProtectedRoute'
import HomePage from '@/modules/home/HomePage'
import ConfigRecursosIndex from '@/modules/config-recursos'
import MonitorPage from '@/modules/atendimento/MonitorPage'
import ConfigPlataformaPage from '@/modules/config-plataforma/ConfigPlataformaPage'
import MaskingPage from '@/modules/masking/MaskingPage'
import WorkflowsPage from '@/modules/workflows/WorkflowsPage'
import CampaignsPage from '@/modules/campaigns/CampaignsPage'
import BillingPage from '@/modules/billing/BillingPage'
import FormsPage from '@/modules/evaluation/FormsPage'
import EvalCampaignsPage from '@/modules/evaluation/CampaignsPage'
import KnowledgePage from '@/modules/evaluation/KnowledgePage'
import AvaliacoesPage from '@/modules/evaluation/AvaliacoesPage'
import EvalReportsPage from '@/modules/evaluation/ReportsPage'
import SkillFlowsPage from '@/modules/skill-flows/SkillFlowsPage'
import { AgentAssistPage } from '@/modules/agent-assist/AgentAssistPage'
import PlaceholderPage from '@/modules/_placeholder/PlaceholderPage'
import AccessPage from '@/modules/access/AccessPage'
import ContactsPage from '@/modules/contacts/ContactsPage'
import DashboardsPage from '@/modules/dashboards/DashboardsPage'

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
        path: 'contacts',
        element: <ContactsPage />
      },
      {
        path: 'monitor',
        element: <MonitorPage />
      },
      {
        path: 'agent-assist',
        element: <AgentAssistPage />
      },
      {
        path: 'workflows',
        element: <WorkflowsPage />
      },
      {
        path: 'campaigns',
        element: <CampaignsPage />
      },
      {
        path: 'dashboards',
        element: <DashboardsPage />
      },
      {
        path: 'reports',
        element: <PlaceholderPage module="Reports" phase="Arc 3" />
      },
      {
        path: 'skill-flows',
        element: <SkillFlowsPage />
      },
      {
        path: 'config/platform',
        element: <ConfigPlataformaPage />
      },
      {
        path: 'config/masking',
        element: <MaskingPage />
      },
      {
        path: 'config/billing',
        element: <BillingPage />
      },
      {
        path: 'config/access',
        element: <AccessPage />
      },
      {
        path: 'evaluation/forms',
        element: <FormsPage />
      },
      {
        path: 'evaluation/campaigns',
        element: <EvalCampaignsPage />
      },
      {
        path: 'evaluation/knowledge',
        element: <KnowledgePage />
      },
      {
        path: 'evaluation/avaliacoes',
        element: <AvaliacoesPage />
      },
      {
        path: 'evaluation/reports',
        element: <EvalReportsPage />
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
