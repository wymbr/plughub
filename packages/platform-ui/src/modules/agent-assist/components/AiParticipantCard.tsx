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

const STATUS_CONFIG = {
  running: {
    icon: "⚙",
    label: "Rodando",
    classes: "bg-indigo-100 text-indigo-700 border-indigo-300",
    animate: "animate-spin inline-block",
  },
  waiting: {
    icon: "⏳",
    label: "Aguardando",
    classes: "bg-amber-50 text-amber-700 border-amber-300",
    animate: "",
  },
  done: {
    icon: "✓",
    label: "Concluído",
    classes: "bg-green-50 text-green-700 border-green-300",
    animate: "",
  },
  error: {
    icon: "✕",
    label: "Erro",
    classes: "bg-red-50 text-red-700 border-red-300",
    animate: "",
  },
} as const;

type StepStatus = keyof typeof STATUS_CONFIG;

const ROLE_CLASSES: Record<string, string> = {
  primary:    "bg-blue-100 text-blue-800",
  specialist: "bg-purple-100 text-purple-800",
  supervisor: "bg-gray-100 text-gray-700",
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
  const [drawerOpen, setDrawerOpen] = useState(false);

  const { instance_id, agent_type_id, role, ai_state } = participant;
  const statusKey = (ai_state.step_status ?? "running") as StepStatus;
  const cfg       = STATUS_CONFIG[statusKey] ?? STATUS_CONFIG.running;

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
        className="w-full text-left rounded-lg border border-gray-200 bg-white hover:bg-gray-50
                   transition-colors p-2.5 focus:outline-none focus:ring-2 focus:ring-indigo-400"
        onClick={() => setDrawerOpen(true)}
        title="Clique para detalhes"
      >
        {/* Top row: icon + name + role badge */}
        <div className="flex items-center gap-1.5 mb-1.5">
          <span className="text-base">🤖</span>
          <span className="text-xs font-semibold text-gray-800 truncate flex-1">
            {formatAgentTypeId(agent_type_id)}
          </span>
          <span
            className={`text-[10px] font-medium px-1.5 py-0.5 rounded-full ${ROLE_CLASSES[role] ?? "bg-gray-100 text-gray-600"}`}
          >
            {role}
          </span>
        </div>

        {/* Step row */}
        <div className="flex items-center gap-1.5">
          <span
            className={`text-[10px] font-semibold border rounded px-1.5 py-0.5 ${cfg.classes}`}
          >
            <span className={cfg.animate}>{cfg.icon}</span>
            {" "}{cfg.label}
          </span>
          <span className="text-[10px] text-gray-500 truncate flex-1">
            {formatStepId(ai_state.current_step)}
          </span>
          <span className="text-[10px] text-gray-400 flex-shrink-0">
            {formatDuration(ai_state.since_ms)}
          </span>
        </div>

        {/* Waiting-for pill */}
        {ai_state.waiting_for && (
          <div className="mt-1 text-[10px] text-amber-700 bg-amber-50 rounded px-1.5 py-0.5 inline-block">
            aguardando: {ai_state.waiting_for}
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
            <div className="flex items-center gap-2 px-4 py-3 border-b border-gray-200">
              <span className="text-lg">🤖</span>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-semibold text-gray-800 truncate">
                  {formatAgentTypeId(agent_type_id)}
                </p>
                <p className="text-[11px] text-gray-400 font-mono truncate">{instance_id}</p>
              </div>
              <button
                onClick={() => setDrawerOpen(false)}
                className="text-gray-400 hover:text-gray-600 text-lg leading-none flex-shrink-0"
              >
                ✕
              </button>
            </div>

            {/* Step detail */}
            <div className="px-4 py-3 border-b border-gray-100 bg-gray-50">
              <div className="flex items-center gap-2 mb-1">
                <span
                  className={`text-[11px] font-semibold border rounded px-1.5 py-0.5 ${cfg.classes}`}
                >
                  {cfg.icon} {cfg.label}
                </span>
                <span className="text-[11px] text-gray-500">
                  {formatDuration(ai_state.since_ms)}
                </span>
              </div>
              <p className="text-xs text-gray-700 font-mono">
                {formatStepId(ai_state.current_step)}
              </p>
              {ai_state.waiting_for && (
                <p className="text-[11px] text-amber-700 mt-1">
                  aguardando: {ai_state.waiting_for}
                </p>
              )}
            </div>

            {/* Last messages from this agent */}
            <div className="flex-1 overflow-y-auto px-4 py-3">
              <h4 className="text-[11px] font-semibold text-gray-500 uppercase tracking-wide mb-2">
                Últimas mensagens
              </h4>
              {agentMessages.length === 0 ? (
                <p className="text-xs text-gray-400">Sem mensagens deste agente na sessão.</p>
              ) : (
                <div className="flex flex-col gap-2">
                  {agentMessages.map(msg => (
                    <div
                      key={msg.id}
                      className="text-xs bg-indigo-50 border border-indigo-100 rounded-lg px-2.5 py-2 text-gray-700"
                    >
                      <span className="text-[10px] text-indigo-400 block mb-0.5">
                        {new Date(msg.timestamp).toLocaleTimeString("pt-BR", {
                          hour: "2-digit",
                          minute: "2-digit",
                          second: "2-digit",
                        })}
                        {msg.visibility && msg.visibility !== "all" && (
                          <span className="ml-1 text-amber-500">(interno)</span>
                        )}
                      </span>
                      {msg.text}
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Actions */}
            <div className="px-4 py-3 border-t border-gray-200">
              <button
                onClick={() => {
                  onTerminateSegment?.(instance_id);
                  setDrawerOpen(false);
                }}
                disabled={!onTerminateSegment || ai_state.step_status === "done"}
                className="w-full py-2 px-3 text-sm font-medium text-red-600 border border-red-300
                           rounded-lg hover:bg-red-50 disabled:opacity-40 disabled:cursor-not-allowed
                           transition-colors"
              >
                Encerrar segmento
              </button>
              <p className="text-[10px] text-gray-400 text-center mt-1.5">
                Envia @{instance_id} terminate_self
              </p>
            </div>
          </div>
        </div>
      )}
    </>
  );
};
