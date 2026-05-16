/**
 * AgentesTab — Arc 11 Fase 2 (Fase C)
 *
 * Substitui a aba State como superfície de orquestração de agentes.
 * Três seções verticais:
 *
 *   Seção A — Ativos na Sessão
 *     Agente humano (primary) + AI participants em cards.
 *     Card humano tem menu "..." com ação Substituir (migrada da ActionBar).
 *     Cards AI reutilizam AiParticipantCard (click abre drawer com Encerrar segmento).
 *
 *   Seção B — Adicionar Agente
 *     Lista de agentes mentionáveis do pool atual.
 *     Estado por agente: ⚪ Available → 🔄 Pending → 🟢 Active → ✅ Done.
 *     Convite via context textarea inline (expansível).
 *     Botão "Delegar Tarefa" abre DelegarTarefaDrawer existente.
 *
 *   Seção C — Pós-Atendimento (Arc 14 — não implementado)
 *     Oculto até Arc 14 Fase A estar implementado.
 */

import React, { useEffect, useState } from "react";
import {
  AiParticipantInfo,
  ChatMessage,
  MentionableAgent,
  SupervisorState,
} from "../../types";
import { AiParticipantCard } from "../AiParticipantCard";

// ── Helpers ────────────────────────────────────────────────────────────────────

function formatAgentName(id: string): string {
  return id.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
}

// ── Agent invite state ─────────────────────────────────────────────────────────

type InviteState = "available" | "pending" | "active" | "done";

function getInviteState(
  agent:         MentionableAgent,
  participants:  AiParticipantInfo[],
  pending:       Set<string>,
): InviteState {
  const p = participants.find(a => a.agent_type_id === agent.agent_type_id);
  if (p) return p.ai_state.step_status === "done" ? "done" : "active";
  if (pending.has(agent.alias)) return "pending";
  return "available";
}

const INVITE_STATE_CONFIG: Record<InviteState, {
  dot: string; btnLabel: string; btnCls: string; btnDisabled: boolean
}> = {
  available: {
    dot:         "bg-gray-300",
    btnLabel:    "Convidar",
    btnCls:      "text-indigo-700 bg-indigo-50 border-indigo-200 hover:bg-indigo-100",
    btnDisabled: false,
  },
  pending: {
    dot:         "bg-yellow-400 animate-pulse",
    btnLabel:    "Aguardando…",
    btnCls:      "text-gray-400 bg-gray-50 border-gray-200 cursor-not-allowed",
    btnDisabled: true,
  },
  active: {
    dot:         "bg-green-500",
    btnLabel:    "Ativo",
    btnCls:      "text-green-700 bg-green-50 border-green-200 cursor-default",
    btnDisabled: true,
  },
  done: {
    dot:         "bg-indigo-400",
    btnLabel:    "Convidar",
    btnCls:      "text-indigo-700 bg-indigo-50 border-indigo-200 hover:bg-indigo-100",
    btnDisabled: false,
  },
};

// ── HumanAgentCard ─────────────────────────────────────────────────────────────

interface HumanAgentCardProps {
  agentName:                string;
  sessionClosed:            boolean;
  substitutionMode:         boolean;
  onToggleSubstitutionMode: () => void;
}

const HumanAgentCard: React.FC<HumanAgentCardProps> = ({
  agentName,
  sessionClosed,
  substitutionMode,
  onToggleSubstitutionMode,
}) => {
  const [menuOpen, setMenuOpen] = useState(false);

  // Close menu on outside click
  useEffect(() => {
    if (!menuOpen) return;
    function handler(e: MouseEvent) {
      const t = e.target as HTMLElement;
      if (!t.closest("[data-human-card-menu]")) setMenuOpen(false);
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [menuOpen]);

  return (
    <div className="rounded-lg border border-blue-200 bg-blue-50 p-2.5 relative">
      {/* Top row */}
      <div className="flex items-center gap-1.5">
        <span className="text-base flex-shrink-0">👤</span>
        <span className="text-xs font-semibold text-gray-800 truncate flex-1">
          {agentName}
        </span>
        <span className="text-[10px] font-medium px-1.5 py-0.5 rounded-full bg-blue-100 text-blue-800 flex-shrink-0">
          primary
        </span>

        {/* "..." overflow menu */}
        <div data-human-card-menu className="relative flex-shrink-0">
          <button
            onClick={() => setMenuOpen(v => !v)}
            className="text-gray-400 hover:text-gray-600 w-6 h-6 flex items-center justify-center
              rounded hover:bg-white/60 text-sm leading-none"
            title="Opções"
          >
            ···
          </button>
          {menuOpen && (
            <div className="absolute right-0 top-full mt-1 z-10 bg-white border border-gray-200
              rounded-lg shadow-lg min-w-[150px] py-1 overflow-hidden">
              <button
                onClick={() => { onToggleSubstitutionMode(); setMenuOpen(false); }}
                disabled={sessionClosed}
                className={[
                  "w-full text-left px-3 py-1.5 text-xs transition-colors",
                  substitutionMode
                    ? "text-amber-800 hover:bg-amber-50"
                    : "text-amber-700 hover:bg-amber-50",
                  "disabled:opacity-40 disabled:cursor-not-allowed",
                ].join(" ")}
              >
                🔄 {substitutionMode ? "Parar substituição" : "Substituir"}
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Status row */}
      <div className="mt-1.5 flex items-center gap-1.5 flex-wrap">
        <span className={[
          "text-[10px] font-semibold border rounded px-1.5 py-0.5",
          sessionClosed
            ? "bg-gray-50 text-gray-500 border-gray-200"
            : "bg-green-50 text-green-700 border-green-300",
        ].join(" ")}>
          {sessionClosed ? "✓ Encerrado" : "● Atendendo"}
        </span>
        {substitutionMode && (
          <span className="text-[10px] bg-amber-100 text-amber-700 border border-amber-200 rounded px-1.5 py-0.5">
            substituindo
          </span>
        )}
      </div>
    </div>
  );
};

// ── AgentInviteRow ─────────────────────────────────────────────────────────────

interface AgentInviteRowProps {
  agent:        MentionableAgent;
  inviteState:  InviteState;
  disabled:     boolean;
  onInvite:     (alias: string, context: string) => void;
}

const AgentInviteRow: React.FC<AgentInviteRowProps> = ({
  agent,
  inviteState,
  disabled,
  onInvite,
}) => {
  const [expanded, setExpanded] = useState(false);
  const [context,  setContext]  = useState("");

  const cfg     = INVITE_STATE_CONFIG[inviteState];
  const canAct  = !disabled && !cfg.btnDisabled;

  function handleInvite() {
    if (!canAct) return;
    onInvite(agent.alias, context.trim());
    setExpanded(false);
    setContext("");
  }

  function handleBtnClick() {
    if (!canAct) return;
    setExpanded(v => !v);
  }

  return (
    <div className="rounded-lg border border-gray-200 bg-white overflow-hidden">
      {/* Main row */}
      <div className="flex items-center gap-2 p-2.5">
        <span className="text-base flex-shrink-0">🤖</span>

        <div className="flex-1 min-w-0">
          <div className="text-xs font-semibold text-gray-800 truncate">
            {formatAgentName(agent.agent_type_id)}
          </div>
          {agent.description && (
            <div className="text-[10px] text-gray-500 leading-snug mt-0.5 truncate">
              {agent.description}
            </div>
          )}
          <div className="text-[10px] text-purple-500 font-mono">@{agent.alias}</div>
        </div>

        {/* State dot */}
        <span className={`w-2 h-2 rounded-full flex-shrink-0 ${cfg.dot}`} />

        {/* Invite button */}
        <button
          onClick={handleBtnClick}
          disabled={!canAct}
          className={[
            "px-2 py-0.5 rounded border text-[10px] font-medium flex-shrink-0 transition-colors",
            cfg.btnCls,
            disabled ? "opacity-40 cursor-not-allowed" : "",
          ].join(" ")}
        >
          {cfg.btnLabel}
        </button>
      </div>

      {/* Inline context form */}
      {expanded && canAct && (
        <div className="px-2.5 pb-2.5 border-t border-gray-100 bg-gray-50">
          <textarea
            autoFocus
            value={context}
            onChange={e => setContext(e.target.value)}
            placeholder="Contexto para o agente (opcional)…"
            rows={2}
            className="w-full mt-2 text-xs border border-gray-300 rounded px-2 py-1.5
              focus:outline-none focus:ring-1 focus:ring-indigo-400 resize-none
              text-gray-700 placeholder-gray-400 bg-white"
            onKeyDown={e => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleInvite();
              if (e.key === "Escape") { setExpanded(false); setContext(""); }
            }}
          />
          <div className="flex gap-1.5 mt-1.5">
            <button
              onClick={handleInvite}
              className="flex-1 py-1 text-[10px] font-semibold text-white
                bg-indigo-600 hover:bg-indigo-700 rounded transition-colors"
            >
              Convidar · ⌘↵
            </button>
            <button
              onClick={() => { setExpanded(false); setContext(""); }}
              className="px-2 py-1 text-[10px] text-gray-500 border border-gray-200
                rounded hover:bg-gray-100 transition-colors"
            >
              Cancelar
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

// ── Props ──────────────────────────────────────────────────────────────────────

interface AgentesTabProps {
  /** Display name of the human agent (primary). */
  agentName:                string;
  // Seção A
  supervisorState:          SupervisorState | null;
  sessionMessages:          ChatMessage[];
  onTerminateSegment?:      (instanceId: string) => void;
  /** Substitution mode state — button migrated here from ActionBar (Fase B). */
  substitutionMode:         boolean;
  onToggleSubstitutionMode: () => void;
  // Seção B
  mentionableAgents:        MentionableAgent[];
  onAddSpecialist:          (alias: string, context: string) => void;
  /** Opens DelegarTarefaDrawer. */
  onDelegar:                () => void;
  // Session state
  sessionClosed:            boolean;
}

// ── Main component ─────────────────────────────────────────────────────────────

export const AgentesTab: React.FC<AgentesTabProps> = ({
  agentName,
  supervisorState,
  sessionMessages,
  onTerminateSegment,
  substitutionMode,
  onToggleSubstitutionMode,
  mentionableAgents,
  onAddSpecialist,
  onDelegar,
  sessionClosed,
}) => {
  // Track pending invites: alias → invited but agent_type_id not yet in ai_participants
  const [pendingAliases, setPendingAliases] = useState<Set<string>>(new Set());

  const aiParticipants = supervisorState?.ai_participants ?? [];

  // Clear pending state for agents that have joined
  useEffect(() => {
    if (pendingAliases.size === 0) return;
    const joinedTypes = new Set(aiParticipants.map(p => p.agent_type_id));
    const stillPending = new Set(
      [...pendingAliases].filter(alias => {
        const agent = mentionableAgents.find(a => a.alias === alias);
        return agent ? !joinedTypes.has(agent.agent_type_id) : false;
      })
    );
    if (stillPending.size !== pendingAliases.size) {
      setPendingAliases(stillPending);
    }
  }, [aiParticipants, mentionableAgents, pendingAliases]);

  function handleInvite(alias: string, context: string) {
    onAddSpecialist(alias, context);
    setPendingAliases(prev => new Set([...prev, alias]));
  }

  return (
    <div className="flex flex-col gap-4 p-3 overflow-y-auto h-full">

      {/* ── Seção A: Ativos na Sessão ── */}
      <section>
        <h3 className="text-[10px] font-bold text-gray-400 uppercase tracking-widest mb-2">
          Ativos na Sessão
        </h3>

        <div className="flex flex-col gap-2">
          {/* Human agent — always present */}
          <HumanAgentCard
            agentName={agentName}
            sessionClosed={sessionClosed}
            substitutionMode={substitutionMode}
            onToggleSubstitutionMode={onToggleSubstitutionMode}
          />

          {/* AI participants */}
          {aiParticipants.map(p => (
            <AiParticipantCard
              key={p.instance_id}
              participant={p}
              sessionMessages={sessionMessages}
              onTerminateSegment={onTerminateSegment}
            />
          ))}

          {aiParticipants.length === 0 && (
            <p className="text-[11px] text-gray-400 text-center py-1 italic">
              Nenhum agente AI na sessão
            </p>
          )}
        </div>
      </section>

      {/* ── Seção B: Adicionar Agente ── */}
      <section>
        <h3 className="text-[10px] font-bold text-gray-400 uppercase tracking-widest mb-2">
          Adicionar Agente
        </h3>

        {mentionableAgents.length === 0 ? (
          <p className="text-[11px] text-gray-400 text-center py-2 italic">
            Sem agentes disponíveis para este pool
          </p>
        ) : (
          <div className="flex flex-col gap-2">
            {mentionableAgents.map(agent => (
              <AgentInviteRow
                key={agent.alias}
                agent={agent}
                inviteState={getInviteState(agent, aiParticipants, pendingAliases)}
                disabled={sessionClosed}
                onInvite={handleInvite}
              />
            ))}
          </div>
        )}

        {/* Delegar Tarefa — general delegation */}
        <button
          onClick={onDelegar}
          disabled={sessionClosed}
          title="Delegar uma tarefa a um agente AI via instrução"
          className="mt-2 w-full py-2 text-xs font-medium border border-orange-200 text-orange-700
            bg-orange-50 hover:bg-orange-100 rounded-lg transition-colors
            disabled:opacity-40 disabled:cursor-not-allowed"
        >
          📤 Delegar Tarefa
        </button>
      </section>

      {/* ── Seção C: Pós-Atendimento ──
           Oculto até Arc 14 Fase A estar implementado.
           Quando ativo, recebe posatt segment states via supervisorState (tbd).
      */}
    </div>
  );
};
