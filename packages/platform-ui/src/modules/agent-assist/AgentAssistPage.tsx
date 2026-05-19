/**
 * AgentAssistPage — Multi-contact, Multi-pool Agent Assist UI
 *
 * All persistent state (WS connections, contact map, pool presence, toasts)
 * lives in AgentAssistContext (provided at Shell level) so it survives
 * navigation. This component only holds UI-local state that is fine to reset:
 *   activeTab, substitutionMode, filterKey, lastCopilotEvent.
 *
 * Layout (after UX redesign):
 *   ┌────────────────────────────────────────────────────────────────┐
 *   │  Header row 1: agente, pool, sessão, SLA, WS status            │
 *   │  Header row 2: pool combo dropdown                             │
 *   ├──────────┬───────────────────────────────┬────────────────────┤
 *   │  Contact │  ParticipantFilterBar          │  Right Panel       │
 *   │  List    │  ChatArea (current messages)   │  Estado|Ctx|Hist   │
 *   │ (~200px) │  CopilotBanner                 │   (~280px)         │
 *   │          │  AgentInput                    │                    │
 *   └──────────┴───────────────────────────────┴────────────────────┘
 *
 * Changes from previous version:
 *   - Removed Current/History center tab switcher (always current)
 *   - Removed Capacidades tab from right panel (copilot → CopilotBanner)
 *   - Removed Orquestração tab from right panel (AI cards → ParticipantFilterBar)
 *   - Histórico tab added to right panel (was in center column)
 *   - TransferCombo replaces flat Transferir button
 *   - CollaborateCombo replaces AdicionarEspecialista + Delegar buttons
 */

import React, { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/useAuth";
import { Clock, WifiOff } from "lucide-react";

import { ActiveTab, ClosePayload }         from "./types";
import { useAgentAssist, aggregateStatus } from "./AgentAssistContext";
import { useSupervisorState }              from "./hooks/useSupervisorState";
import { useSupervisorCapabilities }       from "./hooks/useSupervisorCapabilities";
import { useCopilotState }                 from "./hooks/useCopilotState";
import { useMentionableAgents }            from "./hooks/useMentionableAgents";
import { Header }              from "./components/Header";
import { ActionBar }           from "./components/ActionBar";
import { ChatArea }            from "./components/ChatArea";
import { AgentInput }          from "./components/AgentInput";
import { PauseReasonModal }    from "./components/PauseReasonModal";
import { RightPanel }          from "./components/RightPanel";
import { ContactList }         from "./components/ContactList";
import { ToastContainer }      from "./components/ToastContainer";
import { DelegarTarefaDrawer } from "./components/DelegarTarefaDrawer";
import {
  ParticipantFilterBar,
  FilterKey,
  messageMatchesFilter,
} from "./components/ParticipantFilterBar";
import { CopilotBanner }  from "./components/CopilotBanner";
import { JourneyPanel }   from "./components/JourneyPanel";

// ── AgentAssistPage ────────────────────────────────────────────────────────
export const AgentAssistPage: React.FC = () => {
  const { t } = useTranslation("agentAssist");
  const { session } = useAuth();
  const agentName   = session?.name ?? t("session.none");

  // ── All persistent state from context ──────────────────────────────────
  const {
    availablePools,
    activePools,
    handleTogglePool,
    handleJoinAll,
    handleLeaveAll,
    statuses,
    lastEvent,
    send,
    contacts,
    setContacts,
    selectedSessionId,
    setSelectedSessionId,
    aiTypingSessions,
    toasts,
    addToast,
    dismissToast,
    handledSessions,
  } = useAgentAssist();

  // ── UI-local state ─────────────────────────────────────────────────────
  const [activeTab,         setActiveTab]         = useState<ActiveTab>("agentes");
  const [substitutionMode,  setSubstitutionMode]   = useState(false);
  const [lastCopilotEvent,  setLastCopilotEvent]   = useState(0);
  const [isPaused,          setIsPaused]           = useState(false);
  const [showPauseModal,    setShowPauseModal]      = useState(false);
  // Participant filter bar
  const [filterKey,         setFilterKey]          = useState<FilterKey>(null);
  // Arc 11 Fase C — message selection and delegation
  const [selectedMessageIds, setSelectedMessageIds] = useState<Set<string>>(new Set());
  const [showDelegarDrawer,  setShowDelegarDrawer]  = useState(false);
  const [delegatedAgents,    setDelegatedAgents]    = useState<Set<string>>(new Set());
  // Arc 11 Fase 2 Fase E — center area tab
  const [centralTab, setCentralTab] = useState<"current" | "journey">("current");

  // Reset UI-local state when selected contact changes
  useEffect(() => {
    setSubstitutionMode(false);
    setFilterKey(null);
    setSelectedMessageIds(new Set());
    setCentralTab("current");
  }, [selectedSessionId]);

  // ── Derived state needed before hook calls ────────────────────────────
  const selected = selectedSessionId ? contacts.get(selectedSessionId) ?? null : null;

  // Auto-close sessions that arrive already closed (client disconnected before
  // this agent received the contact). Arc 14: wrap-up is handled by hook agents,
  // so we skip the manual CloseModal and call agent_done immediately with defaults.
  useEffect(() => {
    if (!selected?.pendingCloseModal || !selectedSessionId) return;
    handleClose(selectedSessionId, {
      issue_status: t("message.clientDisconnected"),
      outcome:      "abandoned",
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected?.pendingCloseModal, selectedSessionId]);

  // ── Supervisor/copilot hooks ───────────────────────────────────────────
  const lastWsEvent = lastEvent as import("./types").WsServerEvent | null;
  const { state: supervisorState, refresh: refreshSupervisorState } = useSupervisorState(selectedSessionId, lastWsEvent);
  const capabilities = useSupervisorCapabilities(selectedSessionId, supervisorState);
  const copilotSuggestions = useCopilotState(selectedSessionId, lastCopilotEvent);

  // Arc 11 Fase B — mentionable agents for the current contact's pool
  const currentPoolId     = selected?.poolId ?? null;
  const mentionableAgents = useMentionableAgents(currentPoolId);

  // Listen for copilot.updated on selected session
  useEffect(() => {
    if (!lastEvent) return;
    const event = lastEvent as import("./types").WsServerEvent;
    if (event.type === "copilot.updated" &&
        event.session_id &&
        event.session_id === selectedSessionId) {
      setLastCopilotEvent(Date.now());
    }
    // Arc 11 Fase C — notify when a delegated agent finishes its task
    if (event.type === "session.agent_done" && delegatedAgents.size > 0) {
      addToast(t("message.delegateDone"), "info");
      setDelegatedAgents(new Set());
    }
  }, [lastEvent, selectedSessionId, delegatedAgents.size, addToast]);

  // Sync supervisor data back into the selected contact's state
  useEffect(() => {
    if (!selectedSessionId) return;
    setContacts(prev => {
      const c = prev.get(selectedSessionId);
      if (!c) return prev;
      const next = new Map(prev);
      next.set(selectedSessionId, {
        ...c,
        supervisorState: supervisorState ?? c.supervisorState,
        capabilities:    capabilities    ?? c.capabilities,
        slaTargetMs:     supervisorState?.sla?.target_ms ?? c.slaTargetMs,
      });
      return next;
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [supervisorState, capabilities]);

  // ── Handlers ─────────────────────────────────────────────────────────
  const handleSelectContact = useCallback((sessionId: string) => {
    setSelectedSessionId(sessionId);
    setActiveTab("agentes");
    setContacts(prev => {
      const c = prev.get(sessionId);
      if (!c || c.unreadCount === 0) return prev;
      const next = new Map(prev);
      next.set(sessionId, { ...c, unreadCount: 0 });
      return next;
    });
  }, [setSelectedSessionId, setContacts]);

  const handleSend = useCallback(
    (text: string) => {
      if (!selectedSessionId) return;
      send(text, selectedSessionId);
      const isMention = text.trimStart().startsWith("@");
      setContacts(prev => {
        const c = prev.get(selectedSessionId);
        if (!c) return prev;
        const next = new Map(prev);
        next.set(selectedSessionId, {
          ...c,
          messages: [...c.messages, {
            id:         `local-${Date.now()}`,
            author:     "agent_human",
            text,
            timestamp:  new Date().toISOString(),
            visibility: isMention ? "agents_only" : undefined,
          }],
        });
        return next;
      });
    },
    [send, selectedSessionId, setContacts]
  );

  const handleClose = useCallback(
    async (sessionId: string, payload: ClosePayload) => {
      handledSessions.current.add(sessionId);
      try {
        await fetch(`/api/agent_done/${sessionId}`, {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify(payload),
        });
        setContacts(prev => {
          const c = prev.get(sessionId);
          if (!c) return prev;
          const next = new Map(prev);
          next.set(sessionId, { ...c, sessionClosed: true });
          return next;
        });
        addToast(t("message.closingWrapUp"), "info");
      } catch {
        addToast(t("message.closingError"), "error");
      }
    },
    [addToast, setContacts, handledSessions]
  );

  const handleMenuSubmit = useCallback(
    async (menuId: string, result: import("./components/MenuCard").SubmitResult) => {
      if (!selectedSessionId) return;
      const contact = contacts.get(selectedSessionId);
      const menuMsg = contact?.messages.find(m => m.menuData?.menu_id === menuId);
      const interaction = menuMsg?.menuData?.interaction ?? "button";

      let displayText: string;
      if (typeof result === "string") {
        // Button / list / text selection — look up human-readable label
        const opt = menuMsg?.menuData?.options?.find(o => o.id === result);
        displayText = opt ? opt.label : result;
      } else {
        // Form submission — redact masked field values before echoing to Console
        const maskedFields = menuMsg?.menuData?.masked_fields;
        if (maskedFields && maskedFields.length > 0) {
          const redacted: Record<string, unknown> = { ...(result as Record<string, unknown>) };
          for (const fieldId of maskedFields) {
            if (fieldId in redacted) redacted[fieldId] = "••••••";
          }
          displayText = JSON.stringify(redacted);
        } else {
          displayText = JSON.stringify(result);
        }
      }

      const echoSessionId = selectedSessionId;
      setContacts(prev => {
        const c = prev.get(echoSessionId);
        if (!c) return prev;
        const next = new Map(prev);
        next.set(echoSessionId, {
          ...c,
          messages: [...c.messages, {
            id:         `local-menu-${Date.now()}`,
            author:     "agent_human",
            text:       displayText,
            timestamp:  new Date().toISOString(),
            visibility: "agents_only",
          }],
        });
        return next;
      });

      try {
        const resp = await fetch(`/api/menu_submit/${selectedSessionId}`, {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({ menu_id: menuId, interaction, result, displayText }),
        });
        if (resp.ok) {
          setSubstitutionMode(false);
        } else {
          addToast(t("message.menuSubmitFailed"), "error");
        }
      } catch {
        addToast(t("message.networkError"), "error");
      }
    },
    [selectedSessionId, contacts, addToast, setContacts]
  );

  const handleDesligar = useCallback(() => {
    if (!selectedSessionId) return;
    handleClose(selectedSessionId, {
      issue_status: t("message.hungUpByAgent"),
      outcome: "abandoned",
    });
  }, [selectedSessionId, handleClose, t]);

  const handleResume = useCallback(() => {
    setIsPaused(false);
    addToast(t("message.agentResumed"), "info");
    const poolId = activePools[0] ?? "";
    if (poolId) {
      fetch(`/api/agent-resume`, {
        method:  "PUT",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ pool_id: poolId }),
      }).catch(() => { /* non-fatal */ });
    }
  }, [activePools, addToast, t]);

  const handlePauseRequest = useCallback(() => {
    setShowPauseModal(true);
  }, []);

  const handlePauseConfirm = useCallback(
    (reasonId: string, reasonLabel: string, note?: string) => {
      setShowPauseModal(false);
      setIsPaused(true);
      const detail = note ? ` — ${note}` : "";
      addToast(t("message.agentPaused", { reason: reasonLabel, detail }), "info");
      const poolId = activePools[0] ?? "";
      fetch(`/api/agent-pause`, {
        method:  "PUT",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ pool_id: poolId, reason_id: reasonId, reason_label: reasonLabel, note }),
      }).catch(() => { /* non-fatal */ });
    },
    [activePools, addToast, t]
  );

  // Arc 11 — terminate an AI segment via @mention
  const handleTerminateSegment = useCallback(
    (instanceId: string) => { handleSend(`@${instanceId} terminate_self`); },
    [handleSend],
  );

  // Arc 11 Fase B — invite a specialist via @mention
  // alias = key in mentionable_pools (e.g. "auth", "copilot"), NOT agent_type_id
  const handleAddSpecialist = useCallback(
    (alias: string, context: string) => {
      const text = context ? `@${alias} ${context}` : `@${alias}`;
      handleSend(text);
      addToast(t("message.specialistInvited", { alias }), "info");
    },
    [handleSend, addToast],
  );

  // Arc 11 Fase C — toggle message selection
  const handleToggleMessageSelection = useCallback((messageId: string) => {
    setSelectedMessageIds(prev => {
      const next = new Set(prev);
      if (next.has(messageId)) next.delete(messageId); else next.add(messageId);
      return next;
    });
  }, []);

  // Arc 11 Fase C — submit delegation
  // alias = key in mentionable_pools (e.g. "auth", "copilot"), NOT agent_type_id
  const handleDelegate = useCallback(
    (alias: string, instruction: string, _visibility: "all" | "agents_only") => {
      if (!selectedSessionId) return;
      handleSend(`@${alias} ${instruction}`);
      setDelegatedAgents(prev => new Set([...prev, alias]));
      addToast(t("message.taskDelegated", { alias }), "info");
      setShowDelegarDrawer(false);
      setSelectedMessageIds(new Set());
    },
    [selectedSessionId, handleSend, addToast],
  );

  // Transfer to pool
  const handleTransferTo = useCallback(
    (poolId: string) => {
      addToast(t("message.transferComingSoon") + ` → ${poolId}`, "info");
    },
    [addToast, t],
  );

  // Arc 10 Phase D — Iniciar Processo
  const handleIniciarProcesso = useCallback(
    async (skillId: string) => {
      if (!selectedSessionId) return;
      const tenantId = session?.tenantId ?? "";
      try {
        const res = await fetch("/v1/journeys", {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({ tenant_id: tenantId, skill_id: skillId, session_id: selectedSessionId }),
        });
        if (res.ok) {
          const name = skillId.replace(/^skill_|_v\d+$/g, "").replace(/_/g, " ");
          addToast(t("message.processStarted", { name }), "info");
        } else {
          addToast(t("message.processStartFailed"), "error");
        }
      } catch {
        addToast(t("message.processStartError"), "error");
      }
    },
    [selectedSessionId, session, addToast],
  );

  // ── Derived state ──────────────────────────────────────────────────────
  const wsStatus     = aggregateStatus(statuses, activePools);
  const headerPoolId = selected?.poolId ?? activePools[0] ?? "";
  const mentionableJourneys = (
    availablePools.find(p => p.pool_id === selected?.poolId)?.mentionable_journeys ?? []
  );
  const prefilledContext = [...selectedMessageIds]
    .map(id => selected?.messages.find(m => m.id === id)?.text ?? "")
    .filter(Boolean)
    .join("\n---\n");

  // Filtered messages for the chat area
  const visibleMessages = filterKey === null
    ? (selected?.messages ?? [])
    : (selected?.messages ?? []).filter(m => messageMatchesFilter(m, filterKey));

  // AI participants from supervisor state
  const aiParticipants = supervisorState?.ai_participants ?? [];

  // Supervisor / admin role check
  const isSupervisor = session?.role === "supervisor" || session?.role === "admin";

  // Right panel tab labels (Arc 11 Fase C: "estado" → "agentes")
  const rightTabLabels: Record<string, string> = {
    agentes:   t("rightTab.agentes",  { defaultValue: "Agentes"  }),
    contexto:  t("rightTab.contexto"),
    historico: t("rightTab.historico", { defaultValue: "Histórico" }),
  };

  // ── Render ────────────────────────────────────────────────────────────
  return (
    <div className="flex flex-col h-full overflow-hidden bg-surface-alt">
      <Header
        agentName={agentName}
        poolId={headerPoolId}
        sessionId={selectedSessionId}
        wsStatus={wsStatus}
        sla={selected?.supervisorState?.sla ?? null}
        sessionStartedAt={selected?.sessionStartedAt ?? null}
        contactCount={contacts.size}
        pools={availablePools}
        activePools={activePools}
        poolStatuses={statuses}
        onTogglePool={handleTogglePool}
        onJoinAll={handleJoinAll}
        onLeaveAll={handleLeaveAll}
        isPaused={isPaused}
        onTogglePause={handleResume}
        onPauseRequest={handlePauseRequest}
      />

      {/* ── Unified 3-column layout ─────────────────────────────────────── */}
      <div className="flex flex-col flex-1 overflow-hidden">

        {/* ── Shared sub-header row (h-12) ──────────────────────────────── */}
        <div className="flex h-12 flex-shrink-0 border-b border-border bg-white">

          {/* Contact list header */}
          <div className="w-[200px] flex-shrink-0 bg-surface-alt border-r border-border
                          flex items-center px-3 gap-1.5">
            <span className="text-xs font-semibold text-muted uppercase tracking-wide">
              {t("contacts.label")}
            </span>
            {contacts.size > 0 && (
              <span className="text-xs text-muted">({contacts.size})</span>
            )}
          </div>

          {/* Center column header: ActionBar only (no tab switcher) */}
          <div className="flex flex-1 overflow-hidden">
            <ActionBar
              contact={selected}
              onEncerrar={() => { if (selected) handleClose(selected.sessionId, { issue_status: "closed", outcome: "resolved" }); }}
              onTransferTo={handleTransferTo}
              onDesligar={handleDesligar}
              substitutionMode={substitutionMode}
              onToggleSubstitutionMode={() => setSubstitutionMode(prev => !prev)}
              mentionableJourneys={mentionableJourneys}
              onIniciarProcesso={handleIniciarProcesso}
            />
          </div>

          {/* Right-panel tab bar: Agentes · Contexto · Histórico */}
          <div className="w-[280px] flex-shrink-0 border-l border-border flex bg-surface-muted">
            {(["agentes", "contexto", "historico"] as ActiveTab[]).map((id) => (
              <button
                key={id}
                onClick={() => setActiveTab(id)}
                className={`flex-1 h-full flex items-end justify-center pb-2.5 text-xs font-medium transition-colors ${
                  activeTab === id
                    ? "border-b-2 border-primary text-primary"
                    : "text-muted hover:text-dark"
                }`}
              >
                {rightTabLabels[id]}
              </button>
            ))}
          </div>
        </div>

        {/* ── Content row ──────────────────────────────────────────────── */}
        <div className="flex flex-1 overflow-hidden">

          {/* Contact list */}
          <div className="w-[200px] flex-shrink-0 bg-surface-alt border-r border-border overflow-hidden">
            <ContactList
              contacts={[...contacts.values()]}
              selectedSessionId={selectedSessionId}
              aiTypingSessions={aiTypingSessions}
              onSelect={handleSelectContact}
            />
          </div>

          {/* Center column: [tab bar] ParticipantFilterBar + ChatArea/JourneyPanel + CopilotBanner + AgentInput */}
          <div className="flex flex-col flex-1 overflow-hidden bg-white">
            {!selected ? (
              <div className="flex-1 flex flex-col items-center justify-center text-muted text-sm select-none gap-3">
                {activePools.length === 0 ? (
                  <>
                    <WifiOff className="w-8 h-8 text-muted-light" aria-hidden="true" />
                    <p className="text-center leading-snug max-w-xs">
                      {t("empty.activatePool")}
                    </p>
                  </>
                ) : (
                  <>
                    <Clock className="w-8 h-8 animate-pulse text-muted-light" aria-hidden="true" />
                    <p>{t("empty.waitingForContact")}</p>
                  </>
                )}
              </div>
            ) : (
              <>
                {/* Arc 11 Fase E — center area tab switcher: Atual · Journey */}
                <div className="flex items-center border-b border-border bg-white flex-shrink-0 px-2">
                  {(["current", "journey"] as const).map(tab => (
                    <button
                      key={tab}
                      onClick={() => setCentralTab(tab)}
                      className={[
                        "px-3 py-2 text-xs font-medium transition-colors",
                        centralTab === tab
                          ? "border-b-2 border-primary text-primary"
                          : "text-muted hover:text-dark",
                      ].join(" ")}
                    >
                      {tab === "current" ? t("centerTab.atual") : t("centerTab.journey")}
                    </button>
                  ))}
                </div>

                {centralTab === "journey" ? (
                  /* ── Journey tab ── */
                  <JourneyPanel
                    customerId={selected.contactId}
                    tenantId={session?.tenantId}
                    currentSessionId={selected.sessionId}
                  />
                ) : (
                  /* ── Atual tab (existing behavior) ── */
                  <>
                    {/* Participant filter chips */}
                    <ParticipantFilterBar
                      messages={selected.messages}
                      aiParticipants={aiParticipants}
                      filterKey={filterKey}
                      onFilterChange={setFilterKey}
                      isSupervisor={isSupervisor}
                      sessionId={selected.sessionId}
                      mcpBase=""
                      onRefreshState={refreshSupervisorState}
                      onTerminateSegment={handleTerminateSegment}
                    />

                    {/* Chat messages */}
                    <ChatArea
                      messages={visibleMessages}
                      aiTyping={aiTypingSessions.has(selected.sessionId)}
                      sessionClosed={selected.sessionClosed}
                      liveState={selected.supervisorState ? {
                        sentimentScore: selected.supervisorState.sentiment.current,
                        sentimentAlert: selected.supervisorState.sentiment.alert,
                        sentimentTrend: selected.supervisorState.sentiment.trend,
                        intent:         selected.supervisorState.intent.current,
                        flags:          selected.supervisorState.flags,
                      } : null}
                      substitutionMode={substitutionMode}
                      onMenuSubmit={handleMenuSubmit}
                      selectedMessageIds={selectedMessageIds}
                      onToggleSelection={handleToggleMessageSelection}
                    />

                    {/* Copilot banner (above input) */}
                    <CopilotBanner
                      suggestions={copilotSuggestions}
                      lastUpdate={lastCopilotEvent}
                    />

                    {/* Agent input */}
                    <AgentInput
                      onSend={handleSend}
                      disabled={!selected}
                      sessionClosed={selected.sessionClosed}
                      capabilities={selected.capabilities ?? null}
                    />
                  </>
                )}
              </>
            )}
          </div>

          {/* Right panel */}
          <div className="w-[280px] flex-shrink-0 border-l border-border overflow-hidden bg-surface-muted">
            <RightPanel
              activeTab={activeTab}
              supervisorState={selected?.supervisorState ?? null}
              customerId={selected?.contactId ?? null}
              tenantId={session?.tenantId}
              sessionId={selected?.sessionId ?? null}
              sessionMessages={selected?.messages ?? []}
              onTerminateSegment={handleTerminateSegment}
              agentName={agentName}
              substitutionMode={substitutionMode}
              onToggleSubstitutionMode={() => setSubstitutionMode(prev => !prev)}
              mentionableAgents={mentionableAgents}
              onAddSpecialist={handleAddSpecialist}
              onDelegar={() => setShowDelegarDrawer(true)}
              sessionClosed={selected?.sessionClosed ?? false}
            />
          </div>

        </div>
      </div>


      {showPauseModal && (
        <PauseReasonModal
          onConfirm={handlePauseConfirm}
          onCancel={() => setShowPauseModal(false)}
        />
      )}

      {/* Arc 11 Fase C — Delegar Tarefa drawer */}
      <DelegarTarefaDrawer
        open={showDelegarDrawer}
        agents={mentionableAgents}
        prefilledContext={prefilledContext}
        onDelegate={handleDelegate}
        onClose={() => setShowDelegarDrawer(false)}
      />

      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
};
