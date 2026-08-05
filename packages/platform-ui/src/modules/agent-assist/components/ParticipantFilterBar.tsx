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
import { useTranslation } from "react-i18next";
import { User, Bot, Settings2 } from "lucide-react";
import { AiParticipantInfo, ChatMessage, CopilotSuggestions } from "../types";
import { getAccessToken } from "@/auth/token-store";

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

type ParticipantIconComponent = React.FC<{ className?: string }>

interface Participant {
  key:       FilterKey;
  label:     string;
  Icon:      ParticipantIconComponent;
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
  aiParticipants: AiParticipantInfo[],
  t: (key: string) => string,
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
    result.push({ key: "customer", label: t('filterBar.customer'), Icon: User,
      count: counts.get("customer") ?? 0, active: true });
  }

  // Human agent
  if (counts.has("agent_human")) {
    result.push({ key: "agent_human", label: t('filterBar.agent'), Icon: User,
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
      Icon:        Bot,
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
      Icon:        Bot,
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
  const { t } = useTranslation('agentAssist');
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
        setMsg({ text: t('filterBar.injected'), ok: true });
        setContextText("");
        onRefresh();
      } else {
        setMsg({ text: t('filterBar.failed'), ok: false });
      }
    } catch {
      setMsg({ text: t('filterBar.networkError'), ok: false });
    } finally {
      setBusy(null);
    }
  }

  // Ver OrchestrationTab.handleConfirm: esta chamada também ia sem `Authorization`
  // (401 garantido), e 404 × 501 são conselhos OPOSTOS ao supervisor — colapsá-los
  // em "Falha." fazia o mesmo texto significar "não precisava" e "tente mais tarde".
  async function handleForce() {
    if (!window.confirm(t('filterBar.forceComplete'))) return;
    setBusy("force");
    setMsg(null);
    try {
      const token = getAccessToken();
      const res = await fetch(`${mcpBase}/api/force-complete/${sessionId}`, {
        method:  "POST",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (res.ok) {
        setMsg({ text: t('filterBar.completed'), ok: true });
        onRefresh();
      } else if (res.status === 404) {
        setMsg({ text: t('filterBar.nothingToComplete'), ok: false });
      } else if (res.status === 501) {
        setMsg({ text: t('filterBar.abortNotSupported'), ok: false });
      } else if (res.status === 401 || res.status === 403) {
        setMsg({ text: t('filterBar.forbidden'), ok: false });
      } else {
        setMsg({ text: t('filterBar.failed'), ok: false });
      }
    } catch {
      setMsg({ text: t('filterBar.networkError'), ok: false });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="absolute right-0 top-full mt-1 z-50 bg-white border border-border
      rounded-xl shadow-lg w-72 p-3 space-y-2.5">
      <div className="flex items-center justify-between">
        <span className="text-2xs font-bold text-muted-light uppercase tracking-wide">
          {t('filterBar.interventions')}
        </span>
        <button
          onClick={onClose}
          className="text-muted-light hover:text-muted text-xs leading-none"
          aria-label={t('aiCard.closeLabel')}
        >✕</button>
      </div>

      {/* Inject context */}
      <div>
        <label className="block text-2xs font-semibold text-muted mb-1">
          {t('filterBar.injectContext')}
        </label>
        <textarea
          rows={2}
          value={contextText}
          onChange={e => setContextText(e.target.value)}
          placeholder={t('filterBar.contextPlaceholder')}
          className="w-full text-xs border border-border-strong rounded-lg px-2 py-1.5
            focus:outline-none focus:ring-2 focus:ring-primary/40 resize-none
            text-dark placeholder-muted-light"
        />
        <button
          onClick={handleInject}
          disabled={!contextText.trim() || busy !== null}
          className="mt-1.5 w-full py-1 text-xs font-semibold text-white
            bg-primary hover:bg-primary-dark rounded-lg transition-colors
            disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {busy === "inject" ? t('filterBar.injecting') : t('filterBar.inject')}
        </button>
      </div>

      {/* Force complete */}
      <div>
        <button
          onClick={handleForce}
          disabled={busy !== null}
          className="w-full py-1.5 text-xs font-semibold text-contested-text
            bg-contested-light border border-contested/30 hover:bg-contested/10
            rounded-lg transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {busy === "force" ? t('filterBar.executing') : t('filterBar.forceComplete')}
        </button>
      </div>

      {/* Status message */}
      {msg && (
        <p className={`text-2xs font-medium text-center rounded px-1.5 py-0.5
          ${msg.ok ? "text-green-text bg-green-light" : "text-red-text bg-red-light"}`}>
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
  const { t } = useTranslation('agentAssist');
  const [supervisionOpen, setSupervisionOpen] = useState(false);
  const supervisionRef = useRef<HTMLDivElement>(null);
  const participants = buildParticipants(messages, aiParticipants, t);

  // Hide the bar when there are no participants at all
  if (participants.length === 0) return null;

  const total = messages.filter(m => m.author !== "system").length;

  return (
    <div className="flex items-center gap-1.5 px-3 py-1.5 bg-surface-muted border-b border-border
      flex-shrink-0 overflow-x-auto min-h-9">

      {/* ── "Todos" chip ── */}
      <button
        onClick={() => onFilterChange(null)}
        className={[
          "flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium",
          "border transition-colors flex-shrink-0",
          filterKey === null
            ? "bg-primary text-white border-primary"
            : "bg-white text-muted border-border-strong hover:border-primary/40 hover:text-primary",
        ].join(" ")}
        title={t('filterBar.showAll')}
      >
        {t('filterBar.all')}
        <span className={[
          "text-micro px-1 py-0.5 rounded-full leading-none",
          filterKey === null
            ? "bg-primary-dark text-white"
            : "bg-border text-muted",
        ].join(" ")}>
          {total}
        </span>
      </button>

      {/* Divider */}
      <div className="w-px h-4 bg-border flex-shrink-0" />

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
              isDimmed ? t('filterBar.leftConference') : "",
            ].filter(Boolean).join(" ")}
            className={[
              "flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium",
              "border transition-colors flex-shrink-0",
              isSelected
                ? "bg-primary text-white border-primary"
                : isDimmed
                  ? "bg-surface-muted text-muted-light border-border hover:border-border-strong"
                  : "bg-white text-muted border-border-strong hover:border-primary/40 hover:text-primary",
            ].join(" ")}
          >
            <p.Icon className="w-3 h-3 flex-shrink-0" aria-hidden="true" />
            <span className="truncate max-w-20">{p.label}</span>
            {p.count > 0 && (
              <span className={[
                "text-micro px-1 py-0.5 rounded-full leading-none flex-shrink-0",
                isSelected
                  ? "bg-primary-dark text-white"
                  : "bg-border text-muted",
              ].join(" ")}>
                {p.count}
              </span>
            )}
            {isDimmed && (
              <span className="text-micro text-border-strong leading-none" title="Saiu da conferência">●</span>
            )}
          </button>
        );
      })}

      {/* ── Supervisor interventions chip ── */}
      {isSupervisor && (
        <div className="relative flex-shrink-0 ml-auto" ref={supervisionRef}>
          <button
            onClick={() => setSupervisionOpen(o => !o)}
            title={t('filterBar.interventions')}
            className={[
              "flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium",
              "border transition-colors",
              supervisionOpen
                ? "bg-primary-light text-primary border-primary/30"
                : "bg-white text-muted border-border-strong hover:border-primary/30 hover:text-primary",
            ].join(" ")}
          >
            <Settings2 className="w-3.5 h-3.5" aria-hidden="true" /> {t('filterBar.supervision')}
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
