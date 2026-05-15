/**
 * RightPanel
 * Tab content for Estado / Contexto / Histórico.
 * The tab bar is rendered in the shared sub-header row of AgentAssistPage.
 *
 * Capacidades tab removed — copilot moved to CopilotBanner inline in chat.
 * Orquestração tab removed — AI participant cards moved to ParticipantFilterBar.
 * Histórico tab added — previously lived in the center column as a CenterTab.
 */

import React from "react";
import {
  ActiveTab,
  ChatMessage,
  SupervisorState,
} from "../types";
import { EstadoTab }    from "./tabs/EstadoTab";
import { ContextoTab }  from "./tabs/ContextoTab";
import { HistoricoTab } from "./tabs/HistoricoTab";

interface RightPanelProps {
  activeTab:           ActiveTab;
  supervisorState:     SupervisorState | null;
  customerId:          string | null;
  tenantId?:           string | null;
  /** Arc 11 — current session messages for AI participant last-5 drawer. */
  sessionMessages?:    ChatMessage[];
  /** Arc 11 — callback to send @{instanceId} terminate_self. */
  onTerminateSegment?: (instanceId: string) => void;
}

export const RightPanel: React.FC<RightPanelProps> = ({
  activeTab,
  supervisorState,
  customerId,
  tenantId,
  sessionMessages = [],
  onTerminateSegment,
}) => {
  const context = supervisorState?.customer_context ?? null;

  return (
    <div className="flex flex-col h-full bg-slate-50 overflow-hidden">
      <div className="flex-1 overflow-hidden relative">
        {activeTab === "estado" && (
          <EstadoTab
            state={supervisorState}
            sessionMessages={sessionMessages}
            onTerminateSegment={onTerminateSegment}
          />
        )}
        {activeTab === "contexto" && (
          <ContextoTab context={context} />
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
