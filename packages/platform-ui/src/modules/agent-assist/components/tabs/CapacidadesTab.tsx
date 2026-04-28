/**
 * CapacidadesTab
 * Shows supervisor_capabilities: suggested AI agents + escalation options.
 */

import React from "react";
import { SupervisorCapabilities } from "../../types";

interface CapacidadesTabProps {
  capabilities: SupervisorCapabilities | null;
  onInviteAgent?: (agentTypeId: string) => void;
  onEscalate?: (poolId: string) => void;
}

const RELEVANCE_BADGE: Record<string, string> = {
  high:   "bg-green-100 text-green-700",
  medium: "bg-yellow-100 text-yellow-700",
  low:    "bg-gray-100 text-gray-500",
};

const CIRCUIT_BADGE: Record<string, string> = {
  closed:    "bg-green-500",
  half_open: "bg-yellow-500",
  open:      "bg-red-500",
};

function formatWait(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  return `${Math.round(seconds / 60)}min`;
}

export const CapacidadesTab: React.FC<CapacidadesTabProps> = ({
  capabilities,
  onInviteAgent,
  onEscalate,
}) => {
  if (!capabilities) {
    return (
      <div className="flex-1 flex items-center justify-center text-sm text-gray-400">
        Aguardando dados…
      </div>
    );
  }

  const { suggested_agents, escalations } = capabilities;

  return (
    <div className="flex flex-col gap-4 p-3 overflow-y-auto h-full">
      {/* Suggested agents */}
      <section>
        <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
          Agentes sugeridos
        </h3>
        {suggested_agents.length === 0 ? (
          <p className="text-xs text-gray-400">Nenhum agente sugerido.</p>
        ) : (
          <div className="flex flex-col gap-2">
            {suggested_agents.map((agent) => (
              <div
                key={agent.agent_type_id}
                className="border border-gray-200 rounded-lg p-2.5 bg-white"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span className="text-sm font-medium text-gray-800 truncate">
                        {agent.agent_type_id}
                      </span>
                      <span
                        className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${
                          RELEVANCE_BADGE[agent.relevance] ?? "bg-gray-100 text-gray-500"
                        }`}
                      >
                        {agent.relevance}
                      </span>
                      <span className="flex items-center gap-0.5 text-[10px] text-gray-500">
                        <span
                          className={`w-1.5 h-1.5 rounded-full inline-block ${
                            CIRCUIT_BADGE[agent.circuit_breaker] ?? "bg-gray-400"
                          }`}
                        />
                        {agent.circuit_breaker}
                      </span>
                    </div>
                    <p className="text-[11px] text-gray-500 mt-0.5 leading-tight">
                      {agent.reason}
                    </p>
                    <div className="flex gap-2 mt-1 text-[10px] text-gray-400">
                      <span>{agent.interaction_model}</span>
                      <span>·</span>
                      <span>{agent.available_instances} disponível(is)</span>
                    </div>
                  </div>

                  {onInviteAgent && agent.circuit_breaker !== "open" && (
                    <button
                      onClick={() => onInviteAgent(agent.agent_type_id)}
                      className="flex-shrink-0 text-xs px-2 py-1 rounded-md bg-indigo-50 text-indigo-700 hover:bg-indigo-100 transition-colors"
                    >
                      {agent.interaction_model === "conference"
                        ? "Conferência"
                        : "Delegar"}
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Escalations */}
      <section>
        <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
          Escalações
        </h3>
        {escalations.length === 0 ? (
          <p className="text-xs text-gray-400">Nenhuma escalação disponível.</p>
        ) : (
          <div className="flex flex-col gap-2">
            {escalations.map((esc) => (
              <div
                key={esc.pool_id}
                className={`border rounded-lg p-2.5 ${
                  esc.recommended
                    ? "border-indigo-300 bg-indigo-50"
                    : "border-gray-200 bg-white"
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span className="text-sm font-medium text-gray-800">
                        {esc.pool_id}
                      </span>
                      {esc.recommended && (
                        <span className="text-[10px] px-1.5 py-0.5 bg-indigo-600 text-white rounded-full font-medium">
                          Recomendado
                        </span>
                      )}
                    </div>
                    <p className="text-[11px] text-gray-500 mt-0.5 leading-tight">
                      {esc.reason}
                    </p>
                    <p className="text-[10px] text-gray-400 mt-0.5">
                      Espera estimada: {formatWait(esc.estimated_wait_s)}
                    </p>
                  </div>

                  {onEscalate && (
                    <button
                      onClick={() => onEscalate(esc.pool_id)}
                      className="flex-shrink-0 text-xs px-2 py-1 rounded-md bg-orange-50 text-orange-700 hover:bg-orange-100 transition-colors"
                    >
                      Escalar
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
};
