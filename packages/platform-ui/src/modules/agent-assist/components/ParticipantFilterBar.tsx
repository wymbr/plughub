/**
 * ParticipantFilterBar
 *
 * Horizontal chip row rendered above the message list.
 * One chip per conference participant: customer, human agent, each AI agent.
 *
 * Clicking a chip sets the filter key — ChatArea receives filtered messages.
 * "Todos" chip resets the filter (show all).
 *
 * For supervisors/admins: an expandable ⚙ "Supervisão" chip reveals inline
 * intervention actions (inject context, force-complete) without leaving the chat.
 */

import React, { useRef, useState } from "react";
import { AiParticipantInfo, ChatMessage, CopilotSuggestions } from "../types";

// ── Filter key helpers ────────────────────────────────────────────────────────

/** Opaque filter key — null = show all */
export type FilterKey =
  | null
  | "customer"
  | "agent_human"
  | `agent_ai:${string}`;

export function makeAiKey(agentTypeId: string): FilterKey {
  return `agent_ai:${agentTypeId}` as FilterKey;
}

/**
 * Returns true if a message matches the given filter key.
 * null key → always matches.
 */
export function messageMatchesFilter(
  msg: ChatMessage,
  key: FilterKey
): boolean {
  if (key === null) return true;
  if (key === "customer")     return msg.author === "customer";
  if (key === "agent_human")  return msg.author === "agent_human";
  if (key.startsWith("agent_ai:")) {
    const typeId = key.slice("agent_ai:".length);
    return msg.author === "agent_ai" && msg.agentTypeId === typeId;
  }
  return true;
}

// ── Derived participant list ──────────────────────────────────────────────────

interface Participant {
  key:       FilterKey;
  label:     string;
  icon:      string;
  count:     number;
  /** true if still actively listed in ai_participants */
  active:    boolean;
  /** role label for tooltip */
  role?:     string;
  /** instance_id for terminate action */
  instanceId?: string;
  agentTypeId?: string;
}

function buildParticipants(
  messages: ChatMessage[],
  aiParticipants: AiParticipantInfo[]
): Participant[] {
  const result: Participant[] = [];

  // Count messages per author key
  const counts = new Map<FilterKey, number>();
  for (const msg of messages) {
    let key: FilterKey = null;
    if (msg.author === "customer")    key = "customer";
    else if (msg.author === "agent_human") key = "agent_human";
    else if (msg.author === "agent_ai" && msg.agentTypeId)
      key = makeAiKey(msg.agentTypeId);
    else continue; // system messages not shown as participant chips

    counts.set(key, (counts.get(key) ?? 0) + 1);
  }

  // Customer
  if (counts.has("customer")) {
    result.push({ key: "customer", label: "Cliente", icon: "👤",
      count: counts.get("customer") ?? 0, active: true });
  }

  // Human agent
  if (counts.has("agent_human")) {
    result.push({ key: "agent_human", label: "Agente", icon: "🧑",
      count: counts.get("agent_human") ?? 0, active: true });
  }

  // AI participants from supervisor_state (authoritative for active)
  const activeTypeIds = new Set(aiParticipants.map(p => p.agent_type_id));

  // First add from ai_participants (with state info)
  for (const ap of aiParticipants) {
    const key = makeAiKey(ap.agent_type_id);
    const shortLabel = ap.agent_type_id
      .replace(/^agente_/, "")
      .replace(/_v\d+$/, "")
      .replace(/_/g, " ")
      .replace(/\b\w/g, c => c.toUpperCase());
    result.push({
      key,
      label:       shortLabel,
      icon:        "🤖",
      count:       counts.get(key) ?? 0,
      active:      true,
      role:        ap.role,
      instanceId:  ap.instance_id,
      agentTypeId: ap.agent_type_id,
    });
  }

  // Also add AI agents seen in messages that are NOT in ai_participants (already left)
  for (const [key, count] of counts) {
    if (!key || !key.startsWith("agent_ai:")) continue;
    const typeId = key.slice("agent_ai:".length);
    if (activeTypeIds.has(typeId)) continue; // already added above
    const shortLabel = typeId
      .replace(/^agente_/, "")
      .replace(/_v\d+$/, "")
      .replace(/_/g, " ")
      .replace(/\b\w/g, c => c.toUpperCase());
    result.push({
      key,
      label:       shortLabel,
      icon:        "🤖",
      count,
      active:      false, // left the conference
      agentTypeId: typeId,
    });
  }

  return result;
}

// ── Supervisor interventions panel ────────────────────────────────────────────

interface InterventionsProps {
  sessionId: string;
  mcpBase:   string;
  onRefresh: () => void;
  onClose:   () => void;
}

const InterventionsPanel: React.FC<InterventionsProps> = ({
  sessionId, mcpBase, onRefresh, onClose,
}) => {
  const [contextText, setContextText] = useState("");
  const [busy, setBusy] = useState<"inject" | "force" | null>(null);
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null);

  async function handleInject() {
    if (!contextText.trim()) return;
    setBusy("inject");
    setMsg(null);
    try {
      const res = await fetch(`${mcpBase}/api/inject-context/${sessionId}`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ context: contextText.trim() }),
      });
      if (res.ok) {
        setMsg({ text: "Contexto injetado.", ok: true });
        setContextText("");
        onRefresh();
      } else {
        setMsg({ text: "Falha ao injetar.", ok: false });
      }
    } catch {
      setMsg({ text: "Erro de rede.", ok: false });
    } finally {
      setBusy(null);
    }
  }

  async function handleForce() {
    if (!window.confirm("Forçar conclusão do Skill-Flow ativo?")) return;
    setBusy("force");
    setMsg(null);
    try {
      const res = await fetch(`${mcpBase}/api/force-complete/${sessionId}`, {
        method: "POST",
      });
      setMsg(res.ok
        ? { text: "Completado.", ok: true }
        : { text: "Falha.", ok: false });
      if (res.ok) onRefresh();
    } catch {
      setMsg({ text: "Erro de rede.", ok: false });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="absolute right-0 top-full mt-1 z-50 bg-white border border-gray-200
      rounded-xl shadow-lg w-72 p-3 space-y-2.5">
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-bold text-gray-400 uppercase tracking-wide">
          Intervenções
        </span>
        <button
          onClick={onClose}
          className="text-gray-400 hover:text-gray-600 text-xs leading-none"
        >✕</button>
      </div>

      {/* Inject context */}
      <div>
        <label className="block text-[10px] font-semibold text-gray-500 mb-1">
          Injetar contexto
        </label>
        <textarea
          rows={2}
          value={contextText}
          onChange={e => setContextText(e.target.value)}
          placeholder="Instrução ou contexto adicional para o agente AI…"
          className="w-full text-xs border border-gray-300 rounded-lg px-2 py-1.5
            focus:outline-none focus:ring-2 focus:ring-indigo-400 resize-none
            text-gray-700 placeholder-gray-400"
        />
        <button
          onClick={handleInject}
          disabled={!contextText.trim() || busy !== null}
          className="mt-1.5 w-full py-1 text-xs font-semibold text-white
            bg-indigo-600 hover:bg-indigo-700 rounded-lg transition-colors
            disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {busy === "inject" ? "Injetando…" : "Injetar"}
        </button>
      </div>

      {/* Force complete */}
      <div>
        <button
          onClick={handleForce}
          disabled={busy !== null}
          className="w-full py-1.5 text-xs font-semibold text-orange-700
            bg-orange-50 border border-orange-200 hover:bg-orange-100
            rounded-lg transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {busy === "force" ? "Executando…" : "⚡ Forçar conclusão do flow"}
        </button>
      </div>

      {/* Status message */}
      {msg && (
        <p className={`text-[10px] font-medium text-center rounded px-1.5 py-0.5
          ${msg.ok ? "text-green-700 bg-green-50" : "text-red-700 bg-red-50"}`}>
          {msg.text}
        </p>
      )}
    </div>
  );
};

// ── Props ─────────────────────────────────────────────────────────────────────

export interface ParticipantFilterBarProps {
  messages:         ChatMessage[];
  aiParticipants:   AiParticipantInfo[];
  filterKey:        FilterKey;
  onFilterChange:   (key: FilterKey) => void;
  /** true for supervisor/admin — adds the ⚙ interventions chip */
  isSupervisor?:    boolean;
  sessionId:        string;
  mcpBase?:         string;
  onRefreshState?:  () => void;
  onTerminateSegment?: (instanceId: string) => void;
}

// ── ParticipantFilterBar ──────────────────────────────────────────────────────

export const ParticipantFilterBar: React.FC<ParticipantFilterBarProps> = ({
  messages,
  aiParticipants,
  filterKey,
  onFilterChange,
  isSupervisor = false,
  sessionId,
  mcpBase = "",
  onRefreshState,
  onTerminateSegment,
}) => {
  const [supervisionOpen, setSupervisionOpen] = useState(false);
  const supervisionRef = useRef<HTMLDivElement>(null);
  const participants = buildParticipants(messages, aiParticipants);

  // Hide the bar when there are no participants at all
  if (participants.length === 0) return null;

  const total = messages.filter(m => m.author !== "system").length;

  return (
    <div className="flex items-center gap-1.5 px-3 py-1.5 bg-gray-50 border-b border-gray-200
      flex-shrink-0 overflow-x-auto min-h-[36px]">

      {/* ── "Todos" chip ── */}
      <button
        onClick={() => onFilterChange(null)}
        className={[
          "flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium",
          "border transition-colors flex-shrink-0",
          filterKey === null
            ? "bg-indigo-600 text-white border-indigo-600"
            : "bg-white text-gray-600 border-gray-300 hover:border-indigo-400 hover:text-indigo-600",
        ].join(" ")}
        title="Mostrar todas as mensagens"
      >
        Todos
        <span className={[
          "text-[9px] px-1 py-0.5 rounded-full leading-none",
          filterKey === null
            ? "bg-indigo-500 text-white"
            : "bg-gray-200 text-gray-500",
        ].join(" ")}>
          {total}
        </span>
      </button>

      {/* Divider */}
      <div className="w-px h-4 bg-gray-200 flex-shrink-0" />

      {/* ── Participant chips ── */}
      {participants.map(p => {
        const isSelected = filterKey === p.key;
        const isDimmed   = !p.active;
        return (
          <button
            key={String(p.key)}
            onClick={() => onFilterChange(isSelected ? null : p.key)}
            title={[
              p.label,
              p.role ? `(${p.role})` : "",
              isDimmed ? "— já saiu da conferência" : "",
            ].filter(Boolean).join(" ")}
            className={[
              "flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium",
              "border transition-colors flex-shrink-0",
              isSelected
                ? "bg-indigo-600 text-white border-indigo-600"
                : isDimmed
                  ? "bg-gray-50 text-gray-400 border-gray-200 hover:border-gray-300"
                  : "bg-white text-gray-600 border-gray-300 hover:border-indigo-400 hover:text-indigo-600",
            ].join(" ")}
          >
            <span className="text-[10px] leading-none">{p.icon}</span>
            <span className="truncate max-w-[80px]">{p.label}</span>
            {p.count > 0 && (
              <span className={[
                "text-[9px] px-1 py-0.5 rounded-full leading-none flex-shrink-0",
                isSelected
                  ? "bg-indigo-500 text-white"
                  : "bg-gray-200 text-gray-500",
              ].join(" ")}>
                {p.count}
              </span>
            )}
            {isDimmed && (
              <span className="text-[9px] text-gray-300 leading-none" title="Saiu da conferência">●</span>
            )}
          </button>
        );
      })}

      {/* ── Supervisor interventions chip ── */}
      {isSupervisor && (
        <div className="relative flex-shrink-0 ml-auto" ref={supervisionRef}>
          <button
            onClick={() => setSupervisionOpen(o => !o)}
            title="Intervenções de supervisor"
            className={[
              "flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium",
              "border transition-colors",
              supervisionOpen
                ? "bg-indigo-100 text-indigo-700 border-indigo-300"
                : "bg-white text-gray-500 border-gray-300 hover:border-indigo-300 hover:text-indigo-600",
            ].join(" ")}
          >
            ⚙️ Supervisão
          </button>

          {supervisionOpen && (
            <InterventionsPanel
              sessionId={sessionId}
              mcpBase={mcpBase}
              onRefresh={() => { onRefreshState?.(); }}
              onClose={() => setSupervisionOpen(false)}
            />
          )}
        </div>
      )}
    </div>
  );
};
