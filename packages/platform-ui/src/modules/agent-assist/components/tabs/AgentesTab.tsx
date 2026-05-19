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
import { useTranslation } from "react-i18next";
import { User, Bot, Upload } from "lucide-react";
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

const INVITE_STATE_CLS: Record<InviteState, { dot: string; btnCls: string; btnDisabled: boolean }> = {
  available: {
    dot:         "bg-border",
    btnCls:      "text-ai-text bg-ai-light border-ai/30 hover:bg-ai/10",
    btnDisabled: false,
  },
  pending: {
    dot:         "bg-warning animate-pulse",
    btnCls:      "text-muted-light bg-surface-muted border-border cursor-not-allowed",
    btnDisabled: true,
  },
  active: {
    dot:         "bg-green",
    btnCls:      "text-green-text bg-green-light border-green/30 cursor-default",
    btnDisabled: true,
  },
  done: {
    dot:         "bg-ai",
    btnCls:      "text-ai-text bg-ai-light border-ai/30 hover:bg-ai/10",
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
  const { t } = useTranslation('agentAssist');
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
    <div className="rounded-lg border border-primary/30 bg-primary-light p-2.5 relative">
      {/* Top row */}
      <div className="flex items-center gap-1.5">
        <User className="w-4 h-4 text-primary flex-shrink-0" aria-hidden="true" />
        <span className="text-xs font-semibold text-dark truncate flex-1">
          {agentName}
        </span>
        <span className="text-2xs font-medium px-1.5 py-0.5 rounded-full bg-primary-light text-primary flex-shrink-0">
          primary
        </span>

        {/* "..." overflow menu */}
        <div data-human-card-menu className="relative flex-shrink-0">
          <button
            onClick={() => setMenuOpen(v => !v)}
            className="text-muted-light hover:text-muted w-6 h-6 flex items-center justify-center
              rounded hover:bg-white/60 text-sm leading-none"
            title={t('agentes.options')}
          >
            ···
          </button>
          {menuOpen && (
            <div className="absolute right-0 top-full mt-1 z-10 bg-white border border-border
              rounded-lg shadow-lg min-w-[150px] py-1 overflow-hidden">
              <button
                onClick={() => { onToggleSubstitutionMode(); setMenuOpen(false); }}
                disabled={sessionClosed}
                className={[
                  "w-full text-left px-3 py-1.5 text-xs transition-colors",
                  "text-warning-text hover:bg-warning-light",
                  "disabled:opacity-40 disabled:cursor-not-allowed",
                ].join(" ")}
              >
                {substitutionMode ? t('agentes.substitution.stop') : t('agentes.substitution.start')}
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Status row */}
      <div className="mt-1.5 flex items-center gap-1.5 flex-wrap">
        <span className={[
          "text-2xs font-semibold border rounded px-1.5 py-0.5",
          sessionClosed
            ? "bg-surface-muted text-muted border-border"
            : "bg-green-light text-green-text border-green/30",
        ].join(" ")}>
          {sessionClosed ? t('agentes.status.closed') : t('agentes.status.attending')}
        </span>
        {substitutionMode && (
          <span className="text-2xs bg-warning-light text-warning-text border border-warning/30 rounded px-1.5 py-0.5">
            {t('agentes.substituting')}
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
  const { t } = useTranslation('agentAssist');
  const [expanded, setExpanded] = useState(false);
  const [context,  setContext]  = useState("");

  const cfg     = INVITE_STATE_CLS[inviteState];
  const canAct  = !disabled && !cfg.btnDisabled;

  const inviteStateLabel: Record<InviteState, string> = {
    available: t('agentes.invite'),
    pending:   t('agentes.waiting'),
    active:    t('agentes.active'),
    done:      t('agentes.invite'),
  };

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
    <div className="rounded-lg border border-border bg-white overflow-hidden">
      {/* Main row */}
      <div className="flex items-center gap-2 p-2.5">
        <Bot className="w-4 h-4 text-ai flex-shrink-0" aria-hidden="true" />

        <div className="flex-1 min-w-0">
          <div className="text-xs font-semibold text-dark truncate">
            {formatAgentName(agent.agent_type_id)}
          </div>
          {agent.description && (
            <div className="text-2xs text-muted leading-snug mt-0.5 truncate">
              {agent.description}
            </div>
          )}
          <div className="text-2xs text-ai font-mono">@{agent.alias}</div>
        </div>

        {/* State dot */}
        <span className={`w-2 h-2 rounded-full flex-shrink-0 ${cfg.dot}`} />

        {/* Invite button */}
        <button
          onClick={handleBtnClick}
          disabled={!canAct}
          className={[
            "px-2 py-0.5 rounded border text-2xs font-medium flex-shrink-0 transition-colors",
            cfg.btnCls,
            disabled ? "opacity-40 cursor-not-allowed" : "",
          ].join(" ")}
        >
          {inviteStateLabel[inviteState]}
        </button>
      </div>

      {/* Inline context form */}
      {expanded && canAct && (
        <div className="px-2.5 pb-2.5 border-t border-border bg-surface-muted">
          <textarea
            autoFocus
            value={context}
            onChange={e => setContext(e.target.value)}
            placeholder={t('agentes.contextPlaceholder')}
            rows={2}
            className="w-full mt-2 text-xs border border-border-strong rounded px-2 py-1.5
              focus:outline-none focus:ring-1 focus:ring-ai/40 resize-none
              text-dark placeholder-muted-light bg-white"
            onKeyDown={e => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleInvite();
              if (e.key === "Escape") { setExpanded(false); setContext(""); }
            }}
          />
          <div className="flex gap-1.5 mt-1.5">
            <button
              onClick={handleInvite}
              className="flex-1 py-1 text-2xs font-semibold text-white
                bg-ai hover:bg-ai-text rounded transition-colors"
            >
              {t('agentes.inviteCtrlEnter')}
            </button>
            <button
              onClick={() => { setExpanded(false); setContext(""); }}
              className="px-2 py-1 text-2xs text-muted border border-border
                rounded hover:bg-surface-alt transition-colors"
            >
              {t('agentes.cancel')}
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
  /** True when an active contact is selected; gates all session-level actions. */
  hasContact:               boolean;
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
  hasContact,
}) => {
  const { t } = useTranslation('agentAssist');
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
        <h3 className="text-2xs font-bold text-muted-light uppercase tracking-widest mb-2">
          {t('agentes.activeInSession')}
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
            <p className="text-xs text-muted-light text-center py-1 italic">
              {t('agentes.noAiAgents')}
            </p>
          )}
        </div>
      </section>

      {/* ── Seção B: Adicionar Agente ── */}
      <section>
        <h3 className="text-2xs font-bold text-muted-light uppercase tracking-widest mb-2">
          {t('agentes.addAgent')}
        </h3>

        {mentionableAgents.length === 0 ? (
          <p className="text-xs text-muted-light text-center py-2 italic">
            {t('agentes.noAgentsAvailable')}
          </p>
        ) : (
          <div className="flex flex-col gap-2">
            {mentionableAgents.map(agent => (
              <AgentInviteRow
                key={agent.alias}
                agent={agent}
                inviteState={getInviteState(agent, aiParticipants, pendingAliases)}
                disabled={!hasContact || sessionClosed}
                onInvite={handleInvite}
              />
            ))}
          </div>
        )}

        {/* Delegar Tarefa — general delegation */}
        <button
          onClick={onDelegar}
          disabled={!hasContact || sessionClosed}
          title={t('agentes.delegateTask')}
          className="mt-2 w-full py-2 text-xs font-medium border border-contested/30 text-contested-text
            bg-contested-light hover:bg-contested/10 rounded-lg transition-colors inline-flex items-center
            justify-center gap-1.5 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <Upload className="w-3.5 h-3.5" aria-hidden="true" />
          {t('agentes.delegateTask')}
        </button>
      </section>

      {/* ── Seção C: Pós-Atendimento ──
           Oculto até Arc 14 Fase A estar implementado.
           Quando ativo, recebe posatt segment states via supervisorState (tbd).
      */}
    </div>
  );
};
