/**
 * RightPanel
 * Tab content for Estado / Capacidades / Contexto.
 * The tab bar is rendered in the shared sub-header row of AgentAssistPage.
 */

import React from "react";
import {
  ActiveTab,
  CopilotSuggestions,
  CustomerContext,
  SupervisorCapabilities,
  SupervisorState,
} from "../types";
import { EstadoTab }     from "./tabs/EstadoTab";
import { CapacidadesTab } from "./tabs/CapacidadesTab";
import { ContextoTab }   from "./tabs/ContextoTab";

interface RightPanelProps {
  activeTab: ActiveTab;
  supervisorState: SupervisorState | null;
  capabilities: SupervisorCapabilities | null;
  copilotSuggestions?: CopilotSuggestions | null;
  customerId: string | null;
  onInviteAgent?: (agentTypeId: string) => void;
  onEscalate?: (poolId: string) => void;
}

export const RightPanel: React.FC<RightPanelProps> = ({
  activeTab,
  supervisorState,
  capabilities,
  copilotSuggestions,
  customerId,
  onInviteAgent,
  onEscalate,
}) => {
  const context: CustomerContext | null =
    supervisorState?.customer_context ?? null;

  return (
    <div className="flex flex-col h-full bg-white overflow-hidden">
      {/* Tab content — tab bar lives in the parent's shared sub-header row */}
      <div className="flex-1 overflow-hidden relative">
        {activeTab === "estado" && (
          <EstadoTab state={supervisorState} />
        )}
        {activeTab === "capacidades" && (
          <CapacidadesTab
            capabilities={capabilities}
            copilotSuggestions={copilotSuggestions}
            onInviteAgent={onInviteAgent}
            onEscalate={onEscalate}
          />
        )}
        {activeTab === "contexto" && (
          <ContextoTab context={context} />
        )}
      </div>
    </div>
  );
};
