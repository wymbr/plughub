import React from 'react'
import { RouteObject, Navigate } from 'react-router-dom'
import Shell from '@/shell/Shell'
import LoginPage from '@/auth/LoginPage'
import { ProtectedRoute } from '@/auth/ProtectedRoute'
import { RequireEvalAccess } from '@/auth/RequireEvalAccess'
import HomePage from '@/modules/home/HomePage'
import ConfigRecursosIndex from '@/modules/config-recursos'
import ConfigPlataformaPage from '@/modules/config-plataforma/ConfigPlataformaPage'
import MaskingPage from '@/modules/masking/MaskingPage'
// Workflow / Fluxo
import WorkflowEditorPage   from '@/modules/workflows/WorkflowEditorPage'
import WorkflowCalendarPage from '@/modules/workflows/WorkflowCalendarPage'
import CalendarsPage from '@/modules/calendars/CalendarsPage'
import SchedulesPage from '@/modules/schedules/SchedulesPage'
import SchedulesMonitorPage from '@/modules/schedules/SchedulesMonitorPage'
import OutboundPage from '@/modules/outbound/OutboundPage'
import AgentFlowEditorPage  from '@/modules/agent-flow/AgentFlowEditorPage'
import AgentFlowDeployPage  from '@/modules/agent-flow/AgentFlowDeployPage'
import FlowMonitorPage      from '@/modules/agent-flow/FlowMonitorPage'
import ProcessosPage        from '@/modules/agent-flow/ProcessosPage'
import BillingPage from '@/modules/billing/BillingPage'
import FormsPage from '@/modules/evaluation/FormsPage'
import EvalCampaignsPage from '@/modules/evaluation/CampaignsPage'
import KnowledgePage from '@/modules/evaluation/KnowledgePage'
import AvaliacoesPage from '@/modules/evaluation/AvaliacoesPage'
import EvaluationDetailPage from '@/modules/evaluation/EvaluationDetailPage'
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
import AnaliseJourneysPage  from '@/modules/analise/AnaliseJourneysPage'
import AnaliseQualidadePage from '@/modules/analise/AnaliseQualidadePage'
import AnaliseClientesPage  from '@/modules/analise/AnaliseClientesPage'
import CustomerVoicePage    from '@/modules/analise/CustomerVoicePage'
import AnaliseSurveysPage   from '@/modules/analise/AnaliseSurveysPage'
import DashboardsPage from '@/modules/dashboards/DashboardsPage'
import ConfigChannelsIndex from '@/modules/config-channels'
import DialogFormsPage from '@/modules/dialog-forms/DialogFormsPage'

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
      // Monitor/Agendas → live schedules + dispatch ledger (Scheduler Fase 3)
      { path: 'monitor/schedules', element: <SchedulesMonitorPage /> },
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
      // Analytics/Processos → Journey view (J2): agrupa sessions por root_session_id
      // (proveniência), drill journey → sessão-membro → transcrição.
      { path: 'analise/processos', element: <AnaliseJourneysPage /> },
      { path: 'analise/quality',   element: <AnaliseQualidadePage /> },
      // Customer History H5 — lente Analytics do Cliente 360 (busca + 360 + histórico).
      { path: 'analise/customers', element: <AnaliseClientesPage /> },
      { path: 'analise/customer-voice', element: <CustomerVoicePage /> },
      // Survey response navigator (S8) — per-response list from PG survey_response.
      { path: 'analise/surveys', element: <AnaliseSurveysPage /> },
      // Legacy redirects
      { path: 'analise/contatos',  element: <Navigate to="/analise/sessions" replace /> },
      { path: 'analise/agentes',   element: <Navigate to="/analise/agents"   replace /> },
      { path: 'analise/qualidade', element: <Navigate to="/analise/quality"  replace /> },

      // ── Dashboards ────────────────────────────────────────────────
      { path: 'dashboards', element: <DashboardsPage /> },
      // Legacy redirects
      { path: 'reports',    element: <Navigate to="/analise/sessions" replace /> },

      // ── Evaluation ────────────────────────────────────────────────
      // Grant-first ABAC route guards mirror Sidebar.tsx (strict: no admin bypass).
      { path: 'evaluation/forms',        element: <RequireEvalAccess field="formularios"><FormsPage /></RequireEvalAccess> },
      { path: 'evaluation/campaigns',    element: <RequireEvalAccess field="formularios"><EvalCampaignsPage /></RequireEvalAccess> },
      { path: 'evaluation/knowledge',    element: <RequireEvalAccess field="formularios"><KnowledgePage /></RequireEvalAccess> },
      { path: 'evaluation/evaluations',  element: <RequireEvalAccess anyOf={['report', 'revisar', 'contestar']}><AvaliacoesPage /></RequireEvalAccess> },
      { path: 'evaluation/evaluations/:campaignId/:resultId', element: <RequireEvalAccess anyOf={['report', 'revisar', 'contestar']}><EvaluationDetailPage /></RequireEvalAccess> },
      { path: 'evaluation/avaliacoes',   element: <Navigate to="/evaluation/evaluations" replace /> },
      { path: 'evaluation/reports',      element: <EvalReportsPage /> },
      { path: 'evaluation/calibration', element: <RequireEvalAccess anyOf={['curar', 'report']}><CalibrationDashboard /></RequireEvalAccess> },
      { path: 'evaluation/curadoria',   element: <RequireEvalAccess field="curar"><CuradoriaPage /></RequireEvalAccess> },
      { path: 'evaluation/rubric',      element: <RequireEvalAccess field="gerir_rubrica"><RubricPage /></RequireEvalAccess> },

      // ── Configuration ─────────────────────────────────────────────
      { path: 'config/resources',  element: <ConfigRecursosIndex /> },
      { path: 'config/recursos',   element: <Navigate to="/config/resources" replace /> },
      { path: 'config/platform',   element: <ConfigPlataformaPage /> },
      { path: 'config/channels',   element: <ConfigChannelsIndex /> },
      { path: 'config/canais',     element: <Navigate to="/config/channels" replace /> },
      { path: 'config/masking',    element: <MaskingPage /> },
      { path: 'config/dialog-forms', element: <DialogFormsPage /> },
      { path: 'config/billing',    element: <BillingPage /> },
      { path: 'config/agent-reports', element: <Navigate to="/analise/agents"    replace /> },
      { path: 'config/access',     element: <AccessPage /> },
      { path: 'config/groups',     element: <GroupsPage /> },
      { path: 'audit',             element: <AuditPage /> },
      { path: 'config/calendars',  element: <CalendarsPage /> },
      { path: 'config/schedules',  element: <SchedulesPage /> },
      { path: 'config/outbound',   element: <OutboundPage /> },
      { path: 'workflow/triggers', element: <WorkflowCalendarPage /> },

      { path: 'business',  element: <Navigate to="/" replace /> },
    ]
  }
]
