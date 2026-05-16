/**
 * RightPanel — Arc 11 Fase 2 (Fase D)
 * Tab content for Agentes / Contexto / Histórico.
 * The tab bar is rendered in the shared sub-header row of AgentAssistPage.
 *
 * Fase C: "Estado" tab replaced by "Agentes" tab (AgentesTab).
 * Fase D: ContextoTab enriched:
 *   - IntentFlagsCard — intent.current + flags[] (migrated from EstadoTab)
 *   - ManualTagForm — human agent writes/edits ContextStore tags inline
 *   - sessionId threaded here from AgentAssistPage → ContextoTab
 */

import React from "react";
import {
  ActiveTab,
  ChatMessage,
  MentionableAgent,
  SupervisorState,
} from "../types";
import { AgentesTab }   from "./tabs/AgentesTab";
import { ContextoTab }  from "./tabs/ContextoTab";
import { HistoricoTab } from "./tabs/HistoricoTab";

interface RightPanelProps {
  activeTab:                ActiveTab;
  supervisorState:          SupervisorState | null;
  customerId:               string | null;
  tenantId?:                string | null;
  /** Session ID forwarded to ContextoTab for manual tag writes */
  sessionId?:               string | null;
  sessionMessages?:         ChatMessage[];
  // ── Agentes tab props ──
  agentName:                string;
  substitutionMode:         boolean;
  onToggleSubstitutionMode: () => void;
  mentionableAgents:        MentionableAgent[];
  onAddSpecialist:          (alias: string, context: string) => void;
  onDelegar:                () => void;
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
  onDelegar,
  sessionClosed,
  onTerminateSegment,
}) => {
  const context = supervisorState?.customer_context ?? null;

  return (
    <div className="flex flex-col h-full bg-slate-50 overflow-hidden">
      <div className="flex-1 overflow-hidden relative">

        {activeTab === "agentes" && (
          <AgentesTab
            agentName={agentName}
            supervisorState={supervisorState}
            sessionMessages={sessionMessages}
            onTerminateSegment={onTerminateSegment}
            substitutionMode={substitutionMode}
            onToggleSubstitutionMode={onToggleSubstitutionMode}
            mentionableAgents={mentionableAgents}
            onAddSpecialist={onAddSpecialist}
            onDelegar={onDelegar}
            sessionClosed={sessionClosed}
          />
        )}

        {activeTab === "contexto" && (
          <ContextoTab
            context={context}
            sessionId={sessionId}
            supervisorState={supervisorState}
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
