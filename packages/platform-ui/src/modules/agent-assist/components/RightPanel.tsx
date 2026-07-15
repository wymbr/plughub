/**
 * RightPanel — console-acoes-tab (Arc 11 refactor)
 * Tab content for Ações / Contexto / Histórico.
 * The tab bar is rendered in the shared sub-header row of AgentAssistPage.
 *
 * "Agentes" tab renamed to "Ações" (AcoesTab) — unifies agent invite/delegate
 * and process invocation under a single toggle surface.
 */

import React from "react";
import { useTranslation } from "react-i18next";
import { MousePointerClick } from "lucide-react";
import {
  ActiveTab,
  ChatMessage,
  MentionableAgent,
  SupervisorState,
} from "../types";
import { AcoesTab }    from "./tabs/AcoesTab";
import { ContextoTab }  from "./tabs/ContextoTab";
import { HistoricoTab } from "./tabs/HistoricoTab";
import { useAuth }      from "../../../auth/useAuth";

interface RightPanelProps {
  activeTab:                ActiveTab;
  supervisorState:          SupervisorState | null;
  customerId:               string | null;
  tenantId?:                string | null;
  /** Session ID forwarded to ContextoTab for manual tag writes */
  sessionId?:               string | null;
  sessionMessages?:         ChatMessage[];
  // ── Ações tab props ──
  agentName:                string;
  substitutionMode:         boolean;
  onToggleSubstitutionMode: () => void;
  mentionableAgents:        MentionableAgent[];
  onAddSpecialist:          (alias: string, instruction: string, visibility: "all" | "agents_only") => void;
  sessionClosed:            boolean;
  onTerminateSegment?:      (instanceId: string) => void;
}

export const RightPanel: React.FC<RightPanelProps> = ({
  activeTab,
  supervisorState,
  customerId,
  tenantId,
  sessionId,
  sessionMessages = [],
  agentName,
  substitutionMode,
  onToggleSubstitutionMode,
  mentionableAgents,
  onAddSpecialist,
  sessionClosed,
  onTerminateSegment,
}) => {
  const { t } = useTranslation('agentAssist');
  const { currentUser } = useAuth();
  const viewerRole = currentUser?.role ?? "operator";
  const context = supervisorState?.customer_context ?? null;

  if (!sessionId) {
    return (
      <div className="flex flex-col h-full items-center justify-center gap-3 text-muted-light select-none px-4">
        <MousePointerClick className="w-7 h-7" aria-hidden="true" />
        <p className="text-xs text-center leading-snug">
          {t('rightPanel.selectContact')}
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full bg-slate-50 overflow-hidden">
      {/* min-h-0 é obrigatório: sem ele o flex-1 cresce com o conteúdo (min-height:auto)
          e o overflow-y-auto das abas (ex.: Contexto com muitos campos) não rola. */}
      <div className="flex-1 min-h-0 overflow-hidden relative">

        {activeTab === "acoes" && (
          <AcoesTab
            agentName={agentName}
            supervisorState={supervisorState}
            sessionMessages={sessionMessages}
            onTerminateSegment={onTerminateSegment}
            substitutionMode={substitutionMode}
            onToggleSubstitutionMode={onToggleSubstitutionMode}
            mentionableAgents={mentionableAgents}
            onAddSpecialist={onAddSpecialist}
            sessionClosed={sessionClosed}
            hasContact={!!sessionId}
          />
        )}

        {activeTab === "contexto" && (
          <ContextoTab
            context={context}
            sessionId={sessionId}
            supervisorState={supervisorState}
            viewerRole={viewerRole}
          />
        )}

        {activeTab === "historico" && (
          <HistoricoTab
            customerId={customerId}
            tenantId={tenantId}
          />
        )}

      </div>
    </div>
  );
};
