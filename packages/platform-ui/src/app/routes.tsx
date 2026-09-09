import React from 'react'
import { RouteObject, Navigate, useLocation } from 'react-router-dom'
import Shell from '@/shell/Shell'
import LoginPage from '@/auth/LoginPage'
import { ProtectedRoute } from '@/auth/ProtectedRoute'
import { RequireEvalAccess, RequireAbac } from '@/auth/RequireEvalAccess'
import HomePage from '@/modules/home/HomePage'
import ConfigRecursosIndex from '@/modules/config-recursos'
import ConfigPlataformaPage from '@/modules/config-plataforma/ConfigPlataformaPage'
import MaskingPage from '@/modules/masking/MaskingPage'
import ContextMapPage from '@/modules/context-map/ContextMapPage'
import CalendarsPage from '@/modules/calendars/CalendarsPage'
import SchedulesPage from '@/modules/schedules/SchedulesPage'
import SchedulesMonitorPage from '@/modules/schedules/SchedulesMonitorPage'
import WorkItemsPage from '@/modules/work-items/WorkItemsPage'
import OutboundPage from '@/modules/outbound/OutboundPage'
import AgentFlowEditorPage  from '@/modules/agent-flow/AgentFlowEditorPage'
import AgentFlowDeployPage  from '@/modules/agent-flow/AgentFlowDeployPage'
import FlowMonitorPage      from '@/modules/agent-flow/FlowMonitorPage'
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
// (F4 — `AnaliseContatosPage` e `ContactsPage` REMOVIDAS. A primeira ainda era
//  importada aqui e não era usada por rota nenhuma desde que `/analise/contatos`
//  virou `Navigate`; a segunda não era sequer importada. Import órfão não é
//  inofensivo: mantém a página compilando e viva no bundle, e sugere ao próximo
//  leitor que existe um caminho até ela.)
// (F0 do ADR de relatórios, 2026-08-28 — REMOVIDAS pela mesma razão, agora com gate:
//  `AnaliseComparacaoPage` (órfã) + `MetricSelector` (seu único consumidor),
//  `AgentReportsPage` (órfã), `AnaliseAgentesPage` (rota `/analise/agents-legacy`,
//  fora do menu, superseded pela mesa) e `ProcessosPage` (rota `/flow/processos`,
//  fora do menu; contradiz a D2 de adr-historico-unificado-duas-visoes, e sua aba
//  default agrega sobre `workflow_events`, VAZIA desde a deprecação do workflow-api).
//  O gate `infra/test/probe_report_surface.sh` reprova a próxima órfã.)
import ResourcesPage        from '@/modules/analise/ResourcesPage'
import AnaliseQualidadePage from '@/modules/analise/AnaliseQualidadePage'
import AnaliseClientesPage  from '@/modules/analise/AnaliseClientesPage'
import CustomerVoicePage    from '@/modules/analise/CustomerVoicePage'
import DashboardsPage from '@/modules/dashboards/DashboardsPage'
import ConfigChannelsIndex from '@/modules/config-channels'
import DialogFormsPage from '@/modules/dialog-forms/DialogFormsPage'

/** `<Navigate>` que carrega a query string junto. O `Navigate` puro a DESCARTA, e um
 *  redirect que perde `?journey=…` não erra: ele leva a uma tela plausível (a lista
 *  de contatos) em vez da pedida — o modo de falha que passa despercebido. */
function RedirectPreservingQuery(
  { to, add, rename }: { to: string; add?: Record<string, string>; rename?: Record<string, string> },
) {
  const { search } = useLocation()
  // `add` existe para o redirect de `/analise/agents`: aquele endereço ERA a mesa, e
  // a mesa virou o MODO comparar da Superfície B (F3 · D6). Sem carimbar
  // `mode=compare`, o link antigo cairia no modo evoluir — tecnicamente na tela
  // certa, e mostrando outra coisa, que é pior que um 404 porque parece funcionar.
  //
  // `rename` existe pelo mesmo caso: o toggle Diário↔Versão da lente de deploy viajava
  // em `?mode=`, nome que na superfície nova significa evoluir↔comparar. Sem traduzir,
  // um link antigo `?mode=epoch` viraria "modo desconhecido" e cairia em evoluir,
  // levando a pessoa a outra tela. Renomear ANTES de `add` é o que faz o link de
  // ontem chegar onde ele apontava.
  if (!add && !rename) return <Navigate to={`${to}${search}`} replace />
  const qs = new URLSearchParams(search)
  for (const [de, para] of Object.entries(rename ?? {})) {
    const v = qs.get(de)
    if (v !== null) { qs.delete(de); qs.set(para, v) }
  }
  for (const [k, v] of Object.entries(add ?? {})) qs.set(k, v)
  return <Navigate to={`${to}?${qs.toString()}`} replace />
}

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
      { path: 'contacts/agents',   element: <RequireAbac module="contacts" field="monitorar"><AgentsPage /></RequireAbac> },
      { path: 'contacts/pools',    element: <RequireAbac module="contacts" field="monitorar"><PoolsPage /></RequireAbac> },
      // F0 (ADR relatórios, D7) — `/contacts/events` renderizava o MESMO componente
      // que `/analise/events`, com entrada de menu nos DOIS grupos. A página consulta
      // o stream ARMAZENADO (investigação por session_id), não o "agora" do Monitor:
      // vive em Analytics, e o endereço histórico vira redirect.
      { path: 'contacts/events',   element: <Navigate to="/analise/events" replace /> },
      { path: 'flow/monitor',      element: <RequireAbac module="contacts" field="monitorar"><FlowMonitorPage /></RequireAbac> },
      // Monitor/Agendas → live schedules + dispatch ledger (Scheduler Fase 3)
      { path: 'monitor/schedules', element: <RequireAbac module="scheduler" field="operacao"><SchedulesMonitorPage /></RequireAbac> },
      // Monitor/Pendências → wrap-ups pendentes AGORA (I5 / ADR § D7b, fatia 1).
      // Só o vivo: o histórico do período é query sobre `segments` no Analytics.
      { path: 'monitor/work-items', element: <RequireAbac module="agent_assist" field="atender"><WorkItemsPage /></RequireAbac> },
      // Legacy redirects
      { path: 'contacts',          element: <Navigate to="/analise/sessions"  replace /> },
      { path: 'contacts/sessions', element: <Navigate to="/analise/sessions"  replace /> },
      { path: 'monitor',           element: <Navigate to="/flow/monitor"      replace /> },

      // ── Fluxo ──────────────────────────────────────────────────────
      { path: 'agent-flow/editor',  element: <RequireAbac module="skill_flows" field="operacao"><AgentFlowEditorPage /></RequireAbac> },
      { path: 'agent-flow/deploy',  element: <RequireAbac module="skill_flows" field="operacao"><AgentFlowDeployPage /></RequireAbac> },
      // Legacy redirects
      { path: 'agent-flow/monitor', element: <Navigate to="/flow/monitor"      replace /> },
      { path: 'agent-flow/report',  element: <Navigate to="/analise/sessions"  replace /> },
      { path: 'skill-flows',        element: <Navigate to="/agent-flow/editor" replace /> },

      // ── Workflow routes (still accessible directly) ─────────────
      // MOD-11 (2026-09-08): as duas telas de workflow foram REMOVIDAS, e o
      // endereco vira redirect em vez de sumir -- ausencia honesta vira presenca
      // declarada (D6 do ADR de webhook). O `WorkflowEditorPage` era um
      // disparador manual que endereçava SKILL (contra o invariante "o POOL e a
      // unidade enderecavel") e cujo proxy respondia 404 desde a Fase E; o editor
      // vivo e `/agent-flow/editor`. A `WebhooksTab` administrava
      // `workflow.webhooks`, medida em ZERO linhas, enquanto o registro que
      // resolve endereco e o `ChannelEndpoint` de `/config/channels`.
      { path: 'workflow/editor',   element: <Navigate to="/agent-flow/editor" replace /> },
      { path: 'workflow/calendar', element: <Navigate to="/config/channels"   replace /> },
      // Redirects
      { path: 'workflow/monitor',  element: <Navigate to="/flow/monitor"     replace /> },
      { path: 'workflow/report',   element: <Navigate to="/analise/sessions" replace /> },
      { path: 'workflows',         element: <Navigate to="/flow/monitor"     replace /> },
      { path: 'campaigns',         element: <Navigate to="/analise/sessions" replace /> },

      // ── Analytics (historical views) ──────────────────────────────
      { path: 'analise/sessions',  element: <RequireAbac module="contacts" field="visualizar"><SessionsPage /></RequireAbac> },
      // F3 — **Superfície B · Recursos**. Absorve os dois endereços abaixo: os painéis
      // de `/analise/pools` viram as lentes do modo EVOLUIR, e a mesa de
      // `/analise/agents` vira o modo COMPARAR (D6: a mesa é modo, não página).
      { path: 'analise/resources', element: <RequireAbac module="contacts" field="visualizar"><ResourcesPage /></RequireAbac> },
      // Os redirects PRESERVAM a query: `/analise/agents?lens=deploy&sel=…` é link
      // compartilhável e aparece em deep-links; perder os parâmetros levaria a uma
      // tela genérica em vez do que a pessoa pediu. `mode=compare` é acrescentado ao
      // de `/analise/agents` porque aquele endereço ERA a mesa.
      { path: 'analise/agents',    element: <RedirectPreservingQuery to="/analise/resources" rename={{ mode: 'deploy' }} add={{ mode: 'compare' }} /> },
      { path: 'analise/pools',     element: <RedirectPreservingQuery to="/analise/resources" /> },
      { path: 'analise/events',    element: <RequireAbac module="contacts" field="visualizar"><EventsPage /></RequireAbac> },
      // F3.3 — `/analise/processos` foi ABSORVIDO por `/analise/sessions`. O processo
      // é nível 2 daquela rota (`?journey=…`), alcançado pelo chip da linha de contato;
      // a lista livre de processos deixou de existir por decisão (D2: processo é pivô,
      // nunca navegação). O redirect PRESERVA a query — há 4 deep-links vivos
      // (`HistoricoTab` ×3, `AnaliseSurveysPage`), e perder o `?journey=` os levaria
      // a uma lista de contatos em vez do processo pedido.
      { path: 'analise/processos', element: <RedirectPreservingQuery to="/analise/sessions" /> },
      // AUT-37: a rota exigia `contacts.visualizar` e o MENU exige `evaluation.report`.
      // Duas casas, respostas diferentes, e a rota era a mais PERMISSIVA: `operator` e
      // `devops` tem `contacts.visualizar` no preset e nao tem `evaluation.report`, entao
      // alcancavam por URL uma tela que o menu lhes esconde. Alinhado ao menu, que e o
      // campo semanticamente certo ("Visualizar relatorios de qualidade").
      { path: 'analise/quality',   element: <RequireAbac module="evaluation" field="report"><AnaliseQualidadePage /></RequireAbac> },
      // Customer History H5 — lente Analytics do Cliente 360 (busca + 360 + histórico).
      { path: 'analise/customers', element: <RequireAbac module="contacts" field="visualizar"><AnaliseClientesPage /></RequireAbac> },
      { path: 'analise/customer-voice', element: <RequireAbac module="contacts" field="visualizar"><CustomerVoicePage /></RequireAbac> },
      // F4 — `/analise/surveys` (navegador de respostas, S8) foi ABSORVIDO como o nível
      // de RESPOSTAS da Voz do Cliente (D7). O redirect carimba `view=responses` porque
      // aquele endereço ERA a lista: sem isso o link antigo cairia no agregado —
      // tecnicamente na tela certa, mostrando outra coisa.
      { path: 'analise/surveys', element: <RedirectPreservingQuery to="/analise/customer-voice" add={{ view: 'responses' }} /> },
      // F2 — `/analise/wrapup` foi ABSORVIDO por `/analise/sessions` como a lente de
      // **disposição** (D7: "endereço morre, componente é re-hospedado"). A regra que
      // o matou é a de sobrevivência: a unidade de análise é o CONTATO, logo é lente.
      // O componente continua vivo, hospedado no nível 1 daquela rota, e passa a
      // honrar o intervalo da barra de filtro em vez do próprio seletor de período.
      { path: 'analise/wrapup', element: <Navigate to="/analise/sessions?lens=disposition" replace /> },
      // Legacy redirects
      { path: 'analise/contatos',  element: <Navigate to="/analise/sessions" replace /> },
      { path: 'analise/agentes',   element: <Navigate to="/analise/agents"   replace /> },
      { path: 'analise/qualidade', element: <Navigate to="/analise/quality"  replace /> },

      // ── Dashboards ────────────────────────────────────────────────
      // ── AUT-37: as 14 rotas que o menu escondia e a URL NAO barrava ──────────
      //
      // Medido em 2026-09-09, a partir de uma pergunta do dono: das 31 entradas de
      // menu com regra ABAC, 11 tinham rota gateada, 6 gateavam por DENTRO
      // (`perms.can` no proprio componente) e **14 nao tinham guarda nenhuma** —
      // alcancaveis por URL por qualquer autenticado. Mesma forma do item (b) da
      // MOD-11: o menu esconde, a rota nao barra.
      //
      // ⚠️ A ficha que registrava isto (AUT-37) apontava para `analise/*`, e estava
      // VENCIDA: aquelas 6 rotas ganharam `RequireAbac` entre 27 e 29/08/2026, e a
      // ficha so foi movida para o `pending.md` em 05/09, carregada verbatim sem
      // remedir. O buraco estava no VIZINHO que ela nao olhou.
      //
      // ⚠️ O par (modulo, campo) de cada guarda e COPIADO da regra que a entrada de
      // menu declara — nunca reescolhido aqui. Duas casas decidindo a mesma coisa
      // divergem, e a divergencia seria o proprio defeito de volta. Gate:
      // `probe_nav_route_guard_agreement.sh`.
      //
      // ⚠️ Isto NAO e a fronteira de autorizacao (ela e o backend). E coerencia de
      // UX + defesa em profundidade: a rota passa a responder o MESMO que o menu,
      // porque as duas chamam `passesAbacRule`.
      { path: 'dashboards', element: <RequireAbac module="config" field="dashboards"><DashboardsPage /></RequireAbac> },
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
      { path: 'config/resources',  element: <RequireAbac module="config" field="resources"><ConfigRecursosIndex /></RequireAbac> },
      { path: 'config/recursos',   element: <Navigate to="/config/resources" replace /> },
      { path: 'config/platform',   element: <RequireAbac module="config" field="platform"><ConfigPlataformaPage /></RequireAbac> },
      { path: 'config/channels',   element: <RequireAbac module="config" field="channels"><ConfigChannelsIndex /></RequireAbac> },
      { path: 'config/canais',     element: <Navigate to="/config/channels" replace /> },
      { path: 'config/masking',    element: <RequireAbac module="config" field="masking"><MaskingPage /></RequireAbac> },
      { path: 'config/context-map', element: <RequireAbac module="config" field="context_map"><ContextMapPage /></RequireAbac> },
      { path: 'config/dialog-forms', element: <RequireAbac module="config" field="dialog_forms"><DialogFormsPage /></RequireAbac> },
      { path: 'config/billing',    element: <RequireAbac module="billing" field="visualizar"><BillingPage /></RequireAbac> },
      { path: 'config/agent-reports', element: <Navigate to="/analise/agents"    replace /> },
      { path: 'config/access',     element: <RequireAbac module="config" field="users"><AccessPage /></RequireAbac> },
      { path: 'config/groups',     element: <RequireAbac module="config" field="users"><GroupsPage /></RequireAbac> },
      { path: 'audit',             element: <AuditPage /> },
      { path: 'config/calendars',  element: <RequireAbac module="config" field="calendars"><CalendarsPage /></RequireAbac> },
      { path: 'config/schedules',  element: <RequireAbac module="scheduler" field="configurar"><SchedulesPage /></RequireAbac> },
      { path: 'config/outbound',   element: <RequireAbac module="outbound" field="configurar"><OutboundPage /></RequireAbac> },
      { path: 'workflow/triggers', element: <Navigate to="/config/channels" replace /> },

      { path: 'business',  element: <Navigate to="/" replace /> },
    ]
  }
]
