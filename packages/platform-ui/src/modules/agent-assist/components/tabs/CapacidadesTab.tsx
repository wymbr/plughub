/**
 * CapacidadesTab
 * Shows:
 *  1. 🤖 Co-pilot suggestions (response suggestion, risk flags, recommended actions)
 *     Written by AI Gateway after each customer message; pulled from ContextStore.
 *  2. Suggested AI agents (supervisor_capabilities)
 *  3. Escalation options (supervisor_capabilities)
 */

import React from "react";
import { CopilotSuggestions, SupervisorCapabilities } from "../../types";

interface CapacidadesTabProps {
  capabilities: SupervisorCapabilities | null;
  copilotSuggestions?: CopilotSuggestions | null;
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

const RISK_FLAG_BADGE: Record<string, string> = {
  sentimento_negativo:      "bg-red-100 text-red-700",
  intencao_cancelamento:    "bg-red-100 text-red-700",
  sla_em_risco:             "bg-orange-100 text-orange-700",
  frustracao_alta:          "bg-orange-100 text-orange-700",
  escalacao_necessaria:     "bg-orange-100 text-orange-700",
  protocolo_nao_seguido:    "bg-yellow-100 text-yellow-700",
  dados_sensiveis:          "bg-purple-100 text-purple-700",
};

function flagLabel(flag: string): string {
  return flag
    .replace(/_/g, " ")
    .replace(/^\w/, c => c.toUpperCase());
}

function formatWait(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  return `${Math.round(seconds / 60)}min`;
}

function formatTime(iso: string | null): string {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return "";
  }
}

// ── Co-pilot suggestions section ──────────────────────────────────────────────

const CopilotSection: React.FC<{ copilot: CopilotSuggestions }> = ({ copilot }) => {
  const hasContent =
    copilot.sugestao_resposta ||
    copilot.flags_risco.length > 0 ||
    copilot.acoes_recomendadas.length > 0;

  if (!hasContent) return null;

  return (
    <section className="border border-teal-200 rounded-lg bg-teal-50 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 bg-teal-100 border-b border-teal-200">
        <div className="flex items-center gap-1.5">
          <span className="text-sm">🤖</span>
          <span className="text-xs font-semibold text-teal-800">Co-pilot</span>
        </div>
        {copilot.ultima_analise && (
          <span className="text-[10px] text-teal-600">
            {formatTime(copilot.ultima_analise)}
          </span>
        )}
      </div>

      <div className="p-3 flex flex-col gap-3">
        {/* Risk flags */}
        {copilot.flags_risco.length > 0 && (
          <div>
            <p className="text-[10px] font-semibold text-teal-700 uppercase tracking-wide mb-1">
              Flags de risco
            </p>
            <div className="flex flex-wrap gap-1">
              {copilot.flags_risco.map((flag) => (
                <span
                  key={flag}
                  className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${
                    RISK_FLAG_BADGE[flag] ?? "bg-gray-100 text-gray-600"
                  }`}
                >
                  {flagLabel(flag)}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Suggested response */}
        {copilot.sugestao_resposta && (
          <div>
            <p className="text-[10px] font-semibold text-teal-700 uppercase tracking-wide mb-1">
              Sugestão de resposta
            </p>
            <blockquote className="text-xs text-gray-700 leading-relaxed bg-white border border-teal-200 rounded p-2 italic">
              "{copilot.sugestao_resposta}"
            </blockquote>
          </div>
        )}

        {/* Recommended actions */}
        {copilot.acoes_recomendadas.length > 0 && (
          <div>
            <p className="text-[10px] font-semibold text-teal-700 uppercase tracking-wide mb-1">
              Ações recomendadas
            </p>
            <ul className="flex flex-col gap-1">
              {copilot.acoes_recomendadas.map((acao, i) => (
                <li key={i} className="flex items-start gap-1.5 text-xs text-gray-700">
                  <span className="text-teal-500 mt-0.5 flex-shrink-0">▸</span>
                  <span>{flagLabel(acao)}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
};

// ── Main component ─────────────────────────────────────────────────────────────

export const CapacidadesTab: React.FC<CapacidadesTabProps> = ({
  capabilities,
  copilotSuggestions,
  onInviteAgent,
  onEscalate,
}) => {
  const hasCopilot =
    copilotSuggestions &&
    (copilotSuggestions.sugestao_resposta ||
      copilotSuggestions.flags_risco.length > 0 ||
      copilotSuggestions.acoes_recomendadas.length > 0);

  if (!capabilities && !hasCopilot) {
    return (
      <div className="flex-1 flex items-center justify-center text-sm text-gray-400">
        Aguardando dados…
      </div>
    );
  }

  const suggested_agents = capabilities?.suggested_agents ?? [];
  const escalations      = capabilities?.escalations      ?? [];

  return (
    <div className="flex flex-col gap-4 p-3 overflow-y-auto h-full">
      {/* Co-pilot suggestions — always first */}
      {hasCopilot && copilotSuggestions && (
        <CopilotSection copilot={copilotSuggestions} />
      )}

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
