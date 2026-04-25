/**
 * RightPanel
 * Tab container for Estado / Capacidades / Contexto / Histórico.
 */

import React from "react";
import {
  ActiveTab,
  CustomerContext,
  SupervisorCapabilities,
  SupervisorState,
} from "../types";
import { EstadoTab } from "./tabs/EstadoTab";
import { CapacidadesTab } from "./tabs/CapacidadesTab";
import { ContextoTab } from "./tabs/ContextoTab";
import { HistoricoTab } from "./tabs/HistoricoTab";

interface RightPanelProps {
  activeTab: ActiveTab;
  onTabChange: (tab: ActiveTab) => void;
  supervisorState: SupervisorState | null;
  capabilities: SupervisorCapabilities | null;
  customerId: string | null;
  onInviteAgent?: (agentTypeId: string) => void;
  onEscalate?: (poolId: string) => void;
}

const TABS: Array<{ id: ActiveTab; label: string }> = [
  { id: "estado",       label: "Estado"      },
  { id: "capacidades",  label: "Capacidades" },
  { id: "contexto",     label: "Contexto"    },
  { id: "historico",    label: "Histórico"   },
];

export const RightPanel: React.FC<RightPanelProps> = ({
  activeTab,
  onTabChange,
  supervisorState,
  capabilities,
  customerId,
  onInviteAgent,
  onEscalate,
}) => {
  const context: CustomerContext | null =
    supervisorState?.customer_context ?? null;

  return (
    <div className="flex flex-col h-full bg-white border-l border-gray-200">
      {/* Tab bar */}
      <div className="flex border-b border-gray-200 flex-shrink-0">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => onTabChange(tab.id)}
            className={`flex-1 py-2.5 text-xs font-medium transition-colors ${
              activeTab === tab.id
                ? "border-b-2 border-indigo-600 text-indigo-600"
                : "text-gray-500 hover:text-gray-700"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-hidden relative">
        {activeTab === "estado" && (
          <EstadoTab state={supervisorState} />
        )}
        {activeTab === "capacidades" && (
          <CapacidadesTab
            capabilities={capabilities}
            onInviteAgent={onInviteAgent}
            onEscalate={onEscalate}
          />
        )}
        {activeTab === "contexto" && (
          <ContextoTab context={context} />
        )}
        {activeTab === "historico" && (
          <HistoricoTab customerId={customerId} />
        )}
      </div>
    </div>
  );
};
