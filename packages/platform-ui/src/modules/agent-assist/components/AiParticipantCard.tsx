/**
 * AiParticipantCard  (Arc 11 Fase A — F1)
 *
 * Displays a single AI agent participant active in the session.
 * Shows agent_type_id, role badge, Skill-Flow step status, and time in segment.
 *
 * Click to open a drawer with:
 *   - Last 5 messages from this agent
 *   - Current step detail
 *   - "Encerrar segmento" button (sends @{instance_id} terminate_self via WS)
 */

import React, { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Bot, X, Settings2, Clock, Check, AlertCircle } from "lucide-react";
import { AiParticipantInfo, ChatMessage } from "../types";

// ── Helpers ────────────────────────────────────────────────────────────────────

function formatAgentTypeId(id: string): string {
  // "agente_retencao_v1" → "Agente Retencao v1"
  return id.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
}

function formatDuration(ms: number): string {
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return `${m}m ${rem}s`;
}

function formatStepId(stepId: string | null): string {
  if (!stepId) return "—";
  // "reason_classificar_intent" → "reason: classificar intent"
  const parts = stepId.split("_");
  if (parts.length <= 1) return stepId;
  const type = parts[0];
  const rest = parts.slice(1).join(" ");
  return rest ? `${type}: ${rest}` : type;
}

// ── Status badge ───────────────────────────────────────────────────────────────

type StatusIconComponent = React.FC<{ className?: string }>

type StatusConfig = Record<string, {
  Icon: StatusIconComponent
  labelKey: string
  classes: string
  animate: string
}>

const STATUS_CONFIG: StatusConfig = {
  running: {
    Icon: Settings2,
    labelKey: "aiCard.status.running",
    classes: "bg-ai-light text-ai-text border-ai/30",
    animate: "animate-spin",
  },
  waiting: {
    Icon: Clock,
    labelKey: "aiCard.status.waiting",
    classes: "bg-warning-light text-warning-text border-warning/30",
    animate: "",
  },
  done: {
    Icon: Check,
    labelKey: "aiCard.status.done",
    classes: "bg-green-light text-green-text border-green/30",
    animate: "",
  },
  error: {
    Icon: AlertCircle,
    labelKey: "aiCard.status.error",
    classes: "bg-red-light text-red-text border-red/30",
    animate: "",
  },
}

type StepStatus = "running" | "waiting" | "done" | "error";

const ROLE_CLASSES: Record<string, string> = {
  primary:    "bg-primary-light text-primary",
  specialist: "bg-ai-light text-ai-text",
  supervisor: "bg-surface-alt text-dark",
};

// ── Card ───────────────────────────────────────────────────────────────────────

interface AiParticipantCardProps {
  participant:           AiParticipantInfo;
  sessionMessages:       ChatMessage[];
  onTerminateSegment?:   (instanceId: string) => void;
}

export const AiParticipantCard: React.FC<AiParticipantCardProps> = ({
  participant,
  sessionMessages,
  onTerminateSegment,
}) => {
  const { t } = useTranslation('agentAssist');
  const [drawerOpen, setDrawerOpen] = useState(false);

  const { instance_id, agent_type_id, role, ai_state } = participant;
  const statusKey = (ai_state.step_status ?? "running") as StepStatus;
  const cfg       = STATUS_CONFIG[statusKey] ?? STATUS_CONFIG.running;
  const statusLabel = t(cfg.labelKey);

  // Last 5 messages from this AI agent in the current session
  const agentMessages = useMemo(
    () =>
      sessionMessages
        .filter(m => m.agentTypeId === agent_type_id || m.author === "agent_ai")
        .slice(-5),
    [sessionMessages, agent_type_id],
  );

  return (
    <>
      {/* ── Card ── */}
      <button
        className="w-full text-left rounded-lg border border-border bg-white hover:bg-surface-muted
                   transition-colors p-2.5 focus:outline-none focus:ring-2 focus:ring-ai/40"
        onClick={() => setDrawerOpen(true)}
        title={t('aiCard.clickDetails')}
      >
        {/* Top row: icon + name + role badge */}
        <div className="flex items-center gap-1.5 mb-1.5">
          <Bot className="w-4 h-4 text-ai flex-shrink-0" aria-hidden="true" />
          <span className="text-xs font-semibold text-dark truncate flex-1">
            {formatAgentTypeId(agent_type_id)}
          </span>
          <span
            className={`text-2xs font-medium px-1.5 py-0.5 rounded-full ${ROLE_CLASSES[role] ?? "bg-surface-alt text-muted"}`}
          >
            {role}
          </span>
        </div>

        {/* Step row */}
        <div className="flex items-center gap-1.5">
          <span
            className={`inline-flex items-center gap-1 text-2xs font-semibold border rounded px-1.5 py-0.5 ${cfg.classes}`}
          >
            <cfg.Icon className={`w-3 h-3 ${cfg.animate}`} aria-hidden="true" />
            {statusLabel}
          </span>
          <span className="text-2xs text-muted truncate flex-1">
            {formatStepId(ai_state.current_step)}
          </span>
          <span className="text-2xs text-muted-light flex-shrink-0">
            {formatDuration(ai_state.since_ms)}
          </span>
        </div>

        {/* Waiting-for pill */}
        {ai_state.waiting_for && (
          <div className="mt-1 text-2xs text-warning-text bg-warning-light rounded px-1.5 py-0.5 inline-block">
            {t('aiCard.waitingFor', { what: ai_state.waiting_for })}
          </div>
        )}
      </button>

      {/* ── Drawer overlay ── */}
      {drawerOpen && (
        <div
          className="fixed inset-0 z-50 flex"
          onClick={() => setDrawerOpen(false)}
        >
          {/* Backdrop */}
          <div className="flex-1 bg-black/20" />

          {/* Drawer panel */}
          <div
            className="w-80 bg-white shadow-xl flex flex-col overflow-hidden"
            onClick={e => e.stopPropagation()}
          >
            {/* Header */}
            <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
              <Bot className="w-5 h-5 text-ai flex-shrink-0" aria-hidden="true" />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-semibold text-dark truncate">
                  {formatAgentTypeId(agent_type_id)}
                </p>
                <p className="text-xs text-muted-light font-mono truncate">{instance_id}</p>
              </div>
              <button
                onClick={() => setDrawerOpen(false)}
                className="text-muted-light hover:text-muted leading-none flex-shrink-0 p-1"
                aria-label={t('aiCard.closeLabel')}
              >
                <X className="w-4 h-4" aria-hidden="true" />
              </button>
            </div>

            {/* Step detail */}
            <div className="px-4 py-3 border-b border-border bg-surface-muted">
              <div className="flex items-center gap-2 mb-1">
                <span
                  className={`inline-flex items-center gap-1 text-xs font-semibold border rounded px-1.5 py-0.5 ${cfg.classes}`}
                >
                  <cfg.Icon className={`w-3 h-3 ${cfg.animate}`} aria-hidden="true" />
                  {statusLabel}
                </span>
                <span className="text-xs text-muted">
                  {formatDuration(ai_state.since_ms)}
                </span>
              </div>
              <p className="text-xs text-dark font-mono">
                {formatStepId(ai_state.current_step)}
              </p>
              {ai_state.waiting_for && (
                <p className="text-xs text-warning-text mt-1">
                  {t('aiCard.waitingFor', { what: ai_state.waiting_for })}
                </p>
              )}
            </div>

            {/* Last messages from this agent */}
            <div className="flex-1 overflow-y-auto px-4 py-3">
              <h4 className="text-xs font-semibold text-muted uppercase tracking-wide mb-2">
                {t('aiCard.lastMessages')}
              </h4>
              {agentMessages.length === 0 ? (
                <p className="text-xs text-muted-light">{t('aiCard.noMessages')}</p>
              ) : (
                <div className="flex flex-col gap-2">
                  {agentMessages.map(msg => (
                    <div
                      key={msg.id}
                      className="text-xs bg-ai-light border border-ai/20 rounded-lg px-2.5 py-2 text-dark"
                    >
                      <span className="text-2xs text-ai block mb-0.5">
                        {new Date(msg.timestamp).toLocaleTimeString("pt-BR", {
                          hour: "2-digit",
                          minute: "2-digit",
                          second: "2-digit",
                        })}
                        {msg.visibility && msg.visibility !== "all" && (
                          <span className="ml-1 text-warning">{t('aiCard.internal')}</span>
                        )}
                      </span>
                      {msg.text}
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Actions */}
            <div className="px-4 py-3 border-t border-border">
              <button
                onClick={() => {
                  onTerminateSegment?.(instance_id);
                  setDrawerOpen(false);
                }}
                disabled={!onTerminateSegment || ai_state.step_status === "done"}
                className="w-full py-2 px-3 text-sm font-medium text-red-text border border-red/30
                           rounded-lg hover:bg-red-light disabled:opacity-40 disabled:cursor-not-allowed
                           transition-colors"
              >
                {t('aiCard.terminateSegment')}
              </button>
              <p className="text-2xs text-muted-light text-center mt-1.5">
                {t('aiCard.terminateInfo', { instanceId: instance_id })}
              </p>
            </div>
          </div>
        </div>
      )}
    </>
  );
};
