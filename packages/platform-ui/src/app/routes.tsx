import React from 'react'
import { RouteObject, Navigate } from 'react-router-dom'
import Shell from '@/shell/Shell'
import LoginPage from '@/auth/LoginPage'
import { ProtectedRoute } from '@/auth/ProtectedRoute'
import HomePage from '@/modules/home/HomePage'
import ConfigRecursosIndex from '@/modules/config-recursos'
import ConfigPlataformaPage from '@/modules/config-plataforma/ConfigPlataformaPage'
import MaskingPage from '@/modules/masking/MaskingPage'
// Workflow group
import WorkflowEditorPage   from '@/modules/workflows/WorkflowEditorPage'
import WorkflowMonitorPage  from '@/modules/workflows/WorkflowMonitorPage'
import WorkflowReportPage   from '@/modules/workflows/WorkflowReportPage'
import WorkflowCalendarPage from '@/modules/workflows/WorkflowCalendarPage'
// AgentFlow group
import AgentFlowEditorPage  from '@/modules/agent-flow/AgentFlowEditorPage'
import AgentFlowMonitorPage from '@/modules/agent-flow/AgentFlowMonitorPage'
import AgentFlowReportPage  from '@/modules/agent-flow/AgentFlowReportPage'
import AgentFlowDeployPage  from '@/modules/agent-flow/AgentFlowDeployPage'
import BillingPage from '@/modules/billing/BillingPage'
import FormsPage from '@/modules/evaluation/FormsPage'
import EvalCampaignsPage from '@/modules/evaluation/CampaignsPage'
import KnowledgePage from '@/modules/evaluation/KnowledgePage'
import AvaliacoesPage from '@/modules/evaluation/AvaliacoesPage'
import EvalReportsPage from '@/modules/evaluation/ReportsPage'
import { AgentAssistPage } from '@/modules/agent-assist/AgentAssistPage'
import PlaceholderPage from '@/modules/_placeholder/PlaceholderPage'
import AccessPage from '@/modules/access/AccessPage'
import ContactsPage from '@/modules/contacts/ContactsPage'
import DashboardsPage from '@/modules/dashboards/DashboardsPage'

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
      { index: true, element: <HomePage /> },

      // ── Service ────────────────────────────────────────────────
      { path: 'contacts',     element: <ContactsPage /> },
      { path: 'agent-assist', element: <AgentAssistPage /> },
      // Legacy redirect: /monitor → /contacts?tab=monitor
      { path: 'monitor',      element: <Navigate to="/contacts?tab=monitor" replace /> },

      // ── Workflow group ─────────────────────────────────────────
      { path: 'workflow/editor',   element: <WorkflowEditorPage /> },
      { path: 'workflow/monitor',  element: <WorkflowMonitorPage /> },
      { path: 'workflow/report',   element: <WorkflowReportPage /> },
      { path: 'workflow/calendar', element: <WorkflowCalendarPage /> },
      // Legacy redirects
      { path: 'workflows',         element: <Navigate to="/workflow/monitor" replace /> },
      { path: 'campaigns',         element: <Navigate to="/workflow/report"  replace /> },
      { path: 'config/calendars',  element: <Navigate to="/workflow/calendar" replace /> },

      // ── AgentFlow group ────────────────────────────────────────
      { path: 'agent-flow/editor',  element: <AgentFlowEditorPage /> },
      { path: 'agent-flow/monitor', element: <AgentFlowMonitorPage /> },
      { path: 'agent-flow/report',  element: <AgentFlowReportPage /> },
      { path: 'agent-flow/deploy',  element: <AgentFlowDeployPage /> },
      // Legacy redirect
      { path: 'skill-flows',        element: <Navigate to="/agent-flow/editor" replace /> },

      // ── Analytics ─────────────────────────────────────────────
      { path: 'dashboards', element: <DashboardsPage /> },
      { path: 'reports',    element: <PlaceholderPage module="Reports" phase="Arc 3" /> },

      // ── Evaluation ────────────────────────────────────────────
      { path: 'evaluation/forms',      element: <FormsPage /> },
      { path: 'evaluation/campaigns',  element: <EvalCampaignsPage /> },
      { path: 'evaluation/knowledge',  element: <KnowledgePage /> },
      { path: 'evaluation/avaliacoes', element: <AvaliacoesPage /> },
      { path: 'evaluation/reports',    element: <EvalReportsPage /> },

      // ── Configuration ─────────────────────────────────────────
      { path: 'config/recursos',  element: <ConfigRecursosIndex /> },
      { path: 'config/platform',  element: <ConfigPlataformaPage /> },
      { path: 'config/masking',   element: <MaskingPage /> },
      { path: 'config/billing',   element: <BillingPage /> },
      { path: 'config/access',    element: <AccessPage /> },

      // ── Developer / Business ───────────────────────────────────
      { path: 'developer', element: <PlaceholderPage module="Developer Tools"    phase="Arc 4" /> },
      { path: 'business',  element: <PlaceholderPage module="Business Analytics" phase="Arc 3" /> },
    ]
  }
]
