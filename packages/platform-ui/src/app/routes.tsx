import React from 'react'
import { RouteObject, Navigate } from 'react-router-dom'
import Shell from '@/shell/Shell'
import LoginPage from '@/auth/LoginPage'
import { ProtectedRoute } from '@/auth/ProtectedRoute'
import HomePage from '@/modules/home/HomePage'
import ConfigRecursosIndex from '@/modules/config-recursos'
import ConfigPlataformaPage from '@/modules/config-plataforma/ConfigPlataformaPage'
import MaskingPage from '@/modules/masking/MaskingPage'
// Workflow / Fluxo
import WorkflowEditorPage   from '@/modules/workflows/WorkflowEditorPage'
import WorkflowCalendarPage from '@/modules/workflows/WorkflowCalendarPage'
import CalendarsPage from '@/modules/calendars/CalendarsPage'
import AgentFlowEditorPage  from '@/modules/agent-flow/AgentFlowEditorPage'
import AgentFlowDeployPage  from '@/modules/agent-flow/AgentFlowDeployPage'
import FlowMonitorPage      from '@/modules/agent-flow/FlowMonitorPage'
import ProcessosPage        from '@/modules/agent-flow/ProcessosPage'
import BillingPage from '@/modules/billing/BillingPage'
import FormsPage from '@/modules/evaluation/FormsPage'
import EvalCampaignsPage from '@/modules/evaluation/CampaignsPage'
import KnowledgePage from '@/modules/evaluation/KnowledgePage'
import AvaliacoesPage from '@/modules/evaluation/AvaliacoesPage'
import EvalReportsPage from '@/modules/evaluation/ReportsPage'
import CalibrationDashboard from '@/modules/evaluation/CalibrationDashboard'
import CuradoriaPage from '@/modules/evaluation/CuradoriaPage'
import RubricPage from '@/modules/evaluation/RubricPage'
import { AgentAssistPage } from '@/modules/agent-assist/AgentAssistPage'
import AccessPage from '@/modules/access/AccessPage'
import GroupsPage from '@/modules/groups/GroupsPage'
import AuditPage from '@/modules/audit/AuditPage'
// Atendimento
import SessionsPage from '@/modules/contacts/SessionsPage'
import AgentsPage   from '@/modules/contacts/AgentsPage'
import PoolsPage    from '@/modules/contacts/PoolsPage'
import EventsPage   from '@/modules/contacts/EventsPage'
// Análise
import AnaliseContatosPage  from '@/modules/analise/AnaliseContatosPage'
import AnaliseAgentesPage   from '@/modules/analise/AnaliseAgentesPage'
import AgentsBenchPage      from '@/modules/analise/AgentsBenchPage'
import AnalisePoolsPage     from '@/modules/analise/AnalisePoolsPage'
import AnaliseProcessosPage from '@/modules/analise/AnaliseProcessosPage'
import AnaliseQualidadePage from '@/modules/analise/AnaliseQualidadePage'
import DashboardsPage from '@/modules/dashboards/DashboardsPage'
import ConfigChannelsIndex from '@/modules/config-channels'

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

      // ── Console ────────────────────────────────────────────────────
      { path: 'console',           element: <AgentAssistPage /> },
      { path: 'agent-assist',      element: <Navigate to="/console" replace /> },

      // ── Monitor (live operational views) ──────────────────────────
      // Monitor/Sessions → /flow/monitor (live pool view)
      { path: 'contacts/agents',   element: <AgentsPage   /> },
      { path: 'contacts/pools',    element: <PoolsPage    /> },
      { path: 'contacts/events',   element: <EventsPage   /> },
      { path: 'flow/monitor',      element: <FlowMonitorPage     /> },
      // Monitor/Processes → KPI dashboard (completion rates, failure analysis)
      { path: 'flow/processos',    element: <ProcessosPage      /> },
      // Legacy redirects
      { path: 'contacts',          element: <Navigate to="/analise/sessions"  replace /> },
      { path: 'contacts/sessions', element: <Navigate to="/analise/sessions"  replace /> },
      { path: 'monitor',           element: <Navigate to="/flow/monitor"      replace /> },

      // ── Fluxo ──────────────────────────────────────────────────────
      { path: 'agent-flow/editor',  element: <AgentFlowEditorPage /> },
      { path: 'agent-flow/deploy',  element: <AgentFlowDeployPage /> },
      // Legacy redirects
      { path: 'agent-flow/monitor', element: <Navigate to="/flow/monitor"      replace /> },
      { path: 'agent-flow/report',  element: <Navigate to="/analise/sessions"  replace /> },
      { path: 'skill-flows',        element: <Navigate to="/agent-flow/editor" replace /> },

      // ── Workflow routes (still accessible directly) ─────────────
      { path: 'workflow/editor',   element: <WorkflowEditorPage /> },
      { path: 'workflow/calendar', element: <WorkflowCalendarPage /> },
      // Redirects
      { path: 'workflow/monitor',  element: <Navigate to="/flow/monitor"     replace /> },
      { path: 'workflow/report',   element: <Navigate to="/analise/sessions" replace /> },
      { path: 'workflows',         element: <Navigate to="/flow/monitor"     replace /> },
      { path: 'campaigns',         element: <Navigate to="/analise/sessions" replace /> },

      // ── Analytics (historical views) ──────────────────────────────
      { path: 'analise/sessions',  element: <SessionsPage       /> },
      { path: 'analise/agents',    element: <AgentsBenchPage    /> },
      { path: 'analise/agents-legacy', element: <AnaliseAgentesPage /> },
      { path: 'analise/pools',     element: <AnalisePoolsPage   /> },
      { path: 'analise/events',    element: <EventsPage         /> },
      // Analytics/Processes → Workflow Instances list (drill-down to sessions)
      { path: 'analise/processos', element: <AnaliseProcessosPage /> },
      { path: 'analise/quality',   element: <AnaliseQualidadePage /> },
      // Legacy redirects
      { path: 'analise/contatos',  element: <Navigate to="/analise/sessions" replace /> },
      { path: 'analise/agentes',   element: <Navigate to="/analise/agents"   replace /> },
      { path: 'analise/qualidade', element: <Navigate to="/analise/quality"  replace /> },

      // ── Dashboards ────────────────────────────────────────────────
      { path: 'dashboards', element: <DashboardsPage /> },
      // Legacy redirects
      { path: 'reports',    element: <Navigate to="/analise/sessions" replace /> },

      // ── Evaluation ────────────────────────────────────────────────
      { path: 'evaluation/forms',        element: <FormsPage /> },
      { path: 'evaluation/campaigns',    element: <EvalCampaignsPage /> },
      { path: 'evaluation/knowledge',    element: <KnowledgePage /> },
      { path: 'evaluation/evaluations',  element: <AvaliacoesPage /> },
      { path: 'evaluation/avaliacoes',   element: <Navigate to="/evaluation/evaluations" replace /> },
      { path: 'evaluation/reports',      element: <EvalReportsPage /> },
      { path: 'evaluation/calibration', element: <CalibrationDashboard /> },
      { path: 'evaluation/curadoria',   element: <CuradoriaPage /> },
      { path: 'evaluation/rubric',      element: <RubricPage /> },

      // ── Configuration ─────────────────────────────────────────────
      { path: 'config/resources',  element: <ConfigRecursosIndex /> },
      { path: 'config/recursos',   element: <Navigate to="/config/resources" replace /> },
      { path: 'config/platform',   element: <ConfigPlataformaPage /> },
      { path: 'config/channels',   element: <ConfigChannelsIndex /> },
      { path: 'config/canais',     element: <Navigate to="/config/channels" replace /> },
      { path: 'config/masking',    element: <MaskingPage /> },
      { path: 'config/billing',    element: <BillingPage /> },
      { path: 'config/agent-reports', element: <Navigate to="/analise/agents"    replace /> },
      { path: 'config/access',     element: <AccessPage /> },
      { path: 'config/groups',     element: <GroupsPage /> },
      { path: 'audit',             element: <AuditPage /> },
      { path: 'config/calendars',  element: <CalendarsPage /> },
      { path: 'workflow/triggers', element: <WorkflowCalendarPage /> },

      { path: 'business',  element: <Navigate to="/" replace /> },
    ]
  }
]
