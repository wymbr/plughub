/**
 * CapacidadesTab
 * Shows:
 *  1. 🤖 Co-pilot suggestions (response suggestion, risk flags, recommended actions)
 *     Written by AI Gateway after each customer message; pulled from ContextStore.
 *  2. Suggested AI agents (supervisor_capabilities)
 *  3. Escalation options (supervisor_capabilities)
 */

import React from "react";
import { useTranslation } from "react-i18next";
import { Bot } from "lucide-react";
import { CopilotSuggestions, SupervisorCapabilities } from "../../types";

interface CapacidadesTabProps {
  capabilities: SupervisorCapabilities | null;
  copilotSuggestions?: CopilotSuggestions | null;
  onInviteAgent?: (agentTypeId: string) => void;
  onEscalate?: (poolId: string) => void;
}

const RELEVANCE_BADGE: Record<string, string> = {
  high:   "bg-green-light text-green-text",
  medium: "bg-warning-light text-warning-text",
  low:    "bg-surface-alt text-muted",
};

const CIRCUIT_BADGE: Record<string, string> = {
  closed:    "bg-green",
  half_open: "bg-warning",
  open:      "bg-red",
};

const RISK_FLAG_BADGE: Record<string, string> = {
  sentimento_negativo:      "bg-red-light text-red-text",
  intencao_cancelamento:    "bg-red-light text-red-text",
  sla_em_risco:             "bg-contested-light text-contested-text",
  frustracao_alta:          "bg-contested-light text-contested-text",
  escalacao_necessaria:     "bg-contested-light text-contested-text",
  protocolo_nao_seguido:    "bg-warning-light text-warning-text",
  dados_sensiveis:          "bg-ai-light text-ai-text",
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
  const { t } = useTranslation('agentAssist');
  const hasContent =
    copilot.sugestao_resposta ||
    copilot.flags_risco.length > 0 ||
    copilot.acoes_recomendadas.length > 0;

  if (!hasContent) return null;

  return (
    <section className="border border-revised/30 rounded-lg bg-revised-light overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 bg-revised-light border-b border-revised/30">
        <div className="flex items-center gap-1.5">
          <Bot className="w-4 h-4 text-revised-text" aria-hidden="true" />
          <span className="text-xs font-semibold text-revised-text">Co-pilot</span>
        </div>
        {copilot.last_analysis && (
          <span className="text-2xs text-revised">
            {formatTime(copilot.last_analysis)}
          </span>
        )}
      </div>

      <div className="p-3 flex flex-col gap-3">
        {/* Risk flags */}
        {copilot.flags_risco.length > 0 && (
          <div>
            <p className="text-2xs font-semibold text-revised-text uppercase tracking-wide mb-1">
              {t('capacidades.riskFlags')}
            </p>
            <div className="flex flex-wrap gap-1">
              {copilot.flags_risco.map((flag) => (
                <span
                  key={flag}
                  className={`text-2xs px-1.5 py-0.5 rounded-full font-medium ${
                    RISK_FLAG_BADGE[flag] ?? "bg-surface-alt text-muted"
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
            <p className="text-2xs font-semibold text-revised-text uppercase tracking-wide mb-1">
              {t('capacidades.responseSuggestion')}
            </p>
            <blockquote className="text-xs text-dark leading-relaxed bg-white border border-revised/30 rounded p-2 italic">
              "{copilot.sugestao_resposta}"
            </blockquote>
          </div>
        )}

        {/* Recommended actions */}
        {copilot.acoes_recomendadas.length > 0 && (
          <div>
            <p className="text-2xs font-semibold text-revised-text uppercase tracking-wide mb-1">
              {t('capacidades.recommendedActions')}
            </p>
            <ul className="flex flex-col gap-1">
              {copilot.acoes_recomendadas.map((acao, i) => (
                <li key={i} className="flex items-start gap-1.5 text-xs text-dark">
                  <span className="text-revised mt-0.5 flex-shrink-0">▸</span>
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
  const { t } = useTranslation('agentAssist');
  const hasCopilot =
    copilotSuggestions &&
    (copilotSuggestions.sugestao_resposta ||
      copilotSuggestions.flags_risco.length > 0 ||
      copilotSuggestions.acoes_recomendadas.length > 0);

  if (!capabilities && !hasCopilot) {
    return (
      <div className="flex-1 flex items-center justify-center text-sm text-muted-light">
        {t('capacidades.waiting')}
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
        <h3 className="text-xs font-semibold text-muted uppercase tracking-wide mb-2">
          {t('capacidades.suggestedAgents')}
        </h3>
        {suggested_agents.length === 0 ? (
          <p className="text-xs text-muted-light">{t('capacidades.noSuggestedAgents')}</p>
        ) : (
          <div className="flex flex-col gap-2">
            {suggested_agents.map((agent) => (
              <div
                key={agent.agent_type_id}
                className="border border-border rounded-lg p-2.5 bg-white"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span className="text-sm font-medium text-dark truncate">
                        {agent.agent_type_id}
                      </span>
                      <span
                        className={`text-2xs px-1.5 py-0.5 rounded-full font-medium ${
                          RELEVANCE_BADGE[agent.relevance] ?? "bg-surface-alt text-muted"
                        }`}
                      >
                        {agent.relevance}
                      </span>
                      <span className="flex items-center gap-0.5 text-2xs text-muted">
                        <span
                          className={`w-1.5 h-1.5 rounded-full inline-block ${
                            CIRCUIT_BADGE[agent.circuit_breaker] ?? "bg-muted-light"
                          }`}
                        />
                        {agent.circuit_breaker}
                      </span>
                    </div>
                    <p className="text-xs text-muted mt-0.5 leading-tight">
                      {agent.reason}
                    </p>
                    <div className="flex gap-2 mt-1 text-2xs text-muted-light">
                      <span>{agent.interaction_model}</span>
                      <span>·</span>
                      <span>{agent.available_instances} {t('capacidades.available')}</span>
                    </div>
                  </div>

                  {onInviteAgent && agent.circuit_breaker !== "open" && (
                    <button
                      onClick={() => onInviteAgent(agent.agent_type_id)}
                      className="flex-shrink-0 text-xs px-2 py-1 rounded-md bg-ai-light text-ai-text hover:bg-ai/10 transition-colors"
                    >
                      {agent.interaction_model === "conference"
                        ? t('capacidades.conference')
                        : t('capacidades.delegate')}
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
        <h3 className="text-xs font-semibold text-muted uppercase tracking-wide mb-2">
          {t('capacidades.escalations')}
        </h3>
        {escalations.length === 0 ? (
          <p className="text-xs text-muted-light">{t('capacidades.noEscalations')}</p>
        ) : (
          <div className="flex flex-col gap-2">
            {escalations.map((esc) => (
              <div
                key={esc.pool_id}
                className={`border rounded-lg p-2.5 ${
                  esc.recommended
                    ? "border-primary/30 bg-primary-light"
                    : "border-border bg-white"
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span className="text-sm font-medium text-dark">
                        {esc.pool_id}
                      </span>
                      {esc.recommended && (
                        <span className="text-2xs px-1.5 py-0.5 bg-primary text-white rounded-full font-medium">
                          {t('capacidades.recommended')}
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-muted mt-0.5 leading-tight">
                      {esc.reason}
                    </p>
                    <p className="text-2xs text-muted-light mt-0.5">
                      {t('capacidades.estimatedWait', { wait: formatWait(esc.estimated_wait_s) })}
                    </p>
                  </div>

                  {onEscalate && (
                    <button
                      onClick={() => onEscalate(esc.pool_id)}
                      className="flex-shrink-0 text-xs px-2 py-1 rounded-md bg-contested-light text-contested-text hover:bg-contested/10 transition-colors"
                    >
                      {t('capacidades.escalate')}
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
