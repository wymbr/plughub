/**
 * AgentAssistPage — Multi-contact, Multi-pool Agent Assist UI
 *
 * All persistent state (WS connections, contact map, pool presence, toasts)
 * lives in AgentAssistContext (provided at Shell level) so it survives
 * navigation. This component only holds UI-local state that is fine to reset:
 *   activeTab, centerTab, showCloseModal, substitutionMode, lastCopilotEvent.
 *
 * Layout:
 *   ┌────────────────────────────────────────────────────────────┐
 *   │  Header row 1: agente, pool, sessão, SLA, WS status        │
 *   │  Header row 2: pool combo dropdown                         │
 *   ├──────────┬──────────────────────────┬─────────────────────┤
 *   │  Contact │  [Atual | Histórico] tab │  Right Panel        │
 *   │  List    │  (selected contact)      │  [Estado|Cap|Ctx]   │
 *   │ (~200px) │     (flex-1)             │   (~280px)          │
 *   ├──────────┴──────────────────────────┴─────────────────────┤
 *   │  AgentInput  (shown only in "Atual" tab)                   │
 *   └────────────────────────────────────────────────────────────┘
 */

import React, { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/auth/useAuth";

import { ActiveTab, ClosePayload }         from "./types";
import { useAgentAssist, aggregateStatus } from "./AgentAssistContext";
import { useSupervisorState }              from "./hooks/useSupervisorState";
import { useSupervisorCapabilities }       from "./hooks/useSupervisorCapabilities";
import { useCopilotState }                 from "./hooks/useCopilotState";
import { Header }           from "./components/Header";
import { ActionBar }        from "./components/ActionBar";
import { ChatArea }         from "./components/ChatArea";
import { AgentInput }       from "./components/AgentInput";
import { CloseModal }        from "./components/CloseModal";
import { PauseReasonModal }  from "./components/PauseReasonModal";
import { RightPanel }        from "./components/RightPanel";
import { ContactList }       from "./components/ContactList";
import { ToastContainer }    from "./components/ToastContainer";
import { HistoricoTab }      from "./components/tabs/HistoricoTab";

type CenterTab = "atual" | "historico";

// ── AgentAssistPage ────────────────────────────────────────────────────────
export const AgentAssistPage: React.FC = () => {
  const { session } = useAuth();
  const agentName   = session?.name ?? "Agente";

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
    fetchHistory,
    handledSessions,
  } = useAgentAssist();

  // ── UI-local state (resets on navigation — acceptable) ─────────────────
  const [activeTab,       setActiveTab]       = useState<ActiveTab>("estado");
  const [centerTab,       setCenterTab]       = useState<CenterTab>("atual");
  const [showCloseModal,  setShowCloseModal]  = useState(false);
  const [substitutionMode, setSubstitutionMode] = useState(false);
  const [lastCopilotEvent, setLastCopilotEvent] = useState(0);
  const [isPaused,         setIsPaused]         = useState(false);
  const [showPauseModal,   setShowPauseModal]   = useState(false);

  // Reset substitution mode when selected contact changes
  useEffect(() => {
    setSubstitutionMode(false);
  }, [selectedSessionId]);

  // ── Supervisor/copilot hooks — scoped to selected contact ───────────────
  const lastWsEvent     = lastEvent as import("./types").WsServerEvent | null;
  const supervisorState = useSupervisorState(selectedSessionId, lastWsEvent);
  const capabilities    = useSupervisorCapabilities(selectedSessionId, supervisorState);
  const copilotSuggestions = useCopilotState(selectedSessionId, lastCopilotEvent);

  // Listen for copilot.updated on selected session
  useEffect(() => {
    if (!lastEvent) return;
    const event = lastEvent as import("./types").WsServerEvent;
    if (event.type === "copilot.updated" &&
        event.session_id &&
        event.session_id === selectedSessionId) {
      setLastCopilotEvent(Date.now());
    }
  }, [lastEvent, selectedSessionId]);

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

  // ── Handlers ────────────────────────────────────────────────────────────
  const handleSelectContact = useCallback((sessionId: string) => {
    setSelectedSessionId(sessionId);
    setActiveTab("estado");
    setCenterTab("atual");
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
        // Don't remove the contact yet — the bridge may fire on_human_end hooks
        // (wrapup, NPS) that still need the agent to interact.  Mark the contact
        // as wrapping-up so the UI can show a visual indicator.  The actual
        // removal happens when session.closed arrives via WebSocket (published
        // by the bridge after all hooks complete).
        setContacts(prev => {
          const c = prev.get(sessionId);
          if (!c) return prev;
          const next = new Map(prev);
          next.set(sessionId, { ...c, sessionClosed: true });
          return next;
        });
        addToast("Aguardando finalização (wrap-up/NPS)…", "info");
      } catch {
        addToast("Erro ao encerrar atendimento.", "error");
      }
    },
    [addToast, setContacts, handledSessions]
  );

  const handleMenuSubmit = useCallback(
    async (menuId: string, result: import("./components/MenuCard").SubmitResult) => {
      if (!selectedSessionId) return;
      try {
        const resp = await fetch(`/api/menu_submit/${selectedSessionId}`, {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({ menu_id: menuId, interaction: "button", result }),
        });
        if (resp.ok) {
          addToast("Resposta enviada ao Skill Flow.", "info");
          setSubstitutionMode(false);
        } else {
          addToast("Falha ao enviar resposta.", "error");
        }
      } catch {
        addToast("Erro de rede ao enviar resposta.", "error");
      }
    },
    [selectedSessionId, addToast]
  );

  const handleDesligar = useCallback(() => {
    if (!selectedSessionId) return;
    handleClose(selectedSessionId, {
      issue_status: "Desligado pelo agente",
      outcome: "abandoned",
    });
  }, [selectedSessionId, handleClose]);

  // Resume: direct (no modal)
  const handleResume = useCallback(() => {
    setIsPaused(false);
    addToast("Agente retomado — disponível para novos contatos.", "info");
  }, [addToast]);

  // Pause: intercepted by PauseReasonModal
  const handlePauseRequest = useCallback(() => {
    setShowPauseModal(true);
  }, []);

  const handlePauseConfirm = useCallback(
    (reasonId: string, reasonLabel: string, note?: string) => {
      setShowPauseModal(false);
      setIsPaused(true);
      const detail = note ? ` — ${note}` : "";
      addToast(`Agente pausado (${reasonLabel}${detail}). Novos contatos não serão recebidos.`, "info");
      // Best-effort API call — endpoint is deferred; graceful degradation on failure
      fetch(`/api/agent-pause`, {
        method:  "PUT",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ reason_id: reasonId, reason_label: reasonLabel, note }),
      }).catch(() => { /* endpoint not yet in orchestrator-bridge — ignore */ });
    },
    [addToast]
  );

  const handleInviteAgent = useCallback(
    (agentTypeId: string) => addToast(`Convite enviado: ${agentTypeId}`, "info"),
    [addToast]
  );
  const handleEscalate = useCallback(
    (targetPoolId: string) => addToast(`Escalando para: ${targetPoolId}`, "warning"),
    [addToast]
  );

  // ── Derived state ────────────────────────────────────────────────────────
  const selected     = selectedSessionId ? contacts.get(selectedSessionId) ?? null : null;
  const wsStatus     = aggregateStatus(statuses, activePools);
  const headerPoolId = selected?.poolId ?? activePools[0] ?? "";

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="flex flex-col h-full overflow-hidden bg-gray-50">
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

      {/* ── Unified 3-column layout ────────────────────────────────────────── */}
      <div className="flex flex-col flex-1 overflow-hidden">

        {/* ── Shared sub-header row (h-12) ────────────────────────────────── */}
        <div className="flex h-12 flex-shrink-0 border-b border-gray-200">

          {/* Contact list header */}
          <div className="w-[200px] flex-shrink-0 bg-gray-100 border-r border-gray-200
                          flex items-center px-3 gap-1.5">
            <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
              Contatos
            </span>
            {contacts.size > 0 && (
              <span className="text-xs text-gray-400">({contacts.size})</span>
            )}
          </div>

          {/* Center column header: Atual / Histórico tabs + ActionBar */}
          <div className="flex flex-1 overflow-hidden">
            {/* Center tab pills */}
            <div className="flex border-r border-gray-100">
              {(["atual", "historico"] as CenterTab[]).map(tab => (
                <button
                  key={tab}
                  onClick={() => setCenterTab(tab)}
                  className={`px-4 h-full flex items-end justify-center pb-2.5 text-xs font-medium transition-colors ${
                    centerTab === tab
                      ? "border-b-2 border-indigo-600 text-indigo-600"
                      : "text-gray-500 hover:text-gray-700"
                  }`}
                >
                  {tab === "atual" ? "Atual" : "Histórico"}
                </button>
              ))}
            </div>

            {/* ActionBar — only relevant in Atual tab */}
            <ActionBar
              contact={selected}
              onEncerrar={() => setShowCloseModal(true)}
              onTransferir={() => addToast("Transferir: em breve", "info")}
              onDesligar={handleDesligar}
              substitutionMode={substitutionMode}
              onToggleSubstitutionMode={() => setSubstitutionMode(prev => !prev)}
            />
          </div>

          {/* Right-panel tab bar */}
          <div className="w-[280px] flex-shrink-0 border-l border-gray-200 flex bg-white">
            {(["estado", "capacidades", "contexto"] as ActiveTab[]).map((id) => {
              const label = { estado: "Estado", capacidades: "Capacidades", contexto: "Contexto" }[id];
              return (
                <button
                  key={id}
                  onClick={() => setActiveTab(id)}
                  className={`flex-1 h-full flex items-end justify-center pb-2.5 text-xs font-medium transition-colors ${
                    activeTab === id
                      ? "border-b-2 border-indigo-600 text-indigo-600"
                      : "text-gray-500 hover:text-gray-700"
                  }`}
                >
                  {label}
                </button>
              );
            })}
          </div>

        </div>

        {/* ── Content row ────────────────────────────────────────────────── */}
        <div className="flex flex-1 overflow-hidden">

          {/* Contact list */}
          <div className="w-[200px] flex-shrink-0 bg-gray-100 border-r border-gray-200 overflow-hidden">
            <ContactList
              contacts={[...contacts.values()]}
              selectedSessionId={selectedSessionId}
              aiTypingSessions={aiTypingSessions}
              onSelect={handleSelectContact}
            />
          </div>

          {/* Center column */}
          <div className="flex flex-col flex-1 overflow-hidden bg-white">

            {centerTab === "historico" ? (
              /* ── Histórico tab ── */
              <div className="flex-1 overflow-hidden">
                <HistoricoTab customerId={selected?.contactId ?? null} />
              </div>
            ) : (
              /* ── Atual tab ── */
              <>
                {!selected ? (
                  <div className="flex-1 flex flex-col items-center justify-center text-gray-400 text-sm select-none gap-3">
                    {activePools.length === 0 ? (
                      <>
                        <span className="text-3xl">🟢</span>
                        <p className="text-center leading-snug max-w-xs">
                          Ative um pool no cabeçalho para ficar disponível.
                        </p>
                      </>
                    ) : (
                      <>
                        <span className="text-3xl animate-pulse">⏳</span>
                        <p>Aguardando próximo atendimento…</p>
                      </>
                    )}
                  </div>
                ) : (
                  <ChatArea
                    messages={selected.messages}
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
                  />
                )}

                <AgentInput
                  onSend={handleSend}
                  disabled={!selected}
                  sessionClosed={selected?.sessionClosed ?? false}
                  capabilities={selected?.capabilities ?? null}
                />
              </>
            )}
          </div>

          {/* Right panel */}
          <div className="w-[280px] flex-shrink-0 border-l border-gray-200 overflow-hidden bg-white">
            <RightPanel
              activeTab={activeTab}
              supervisorState={selected?.supervisorState ?? null}
              capabilities={selected?.capabilities ?? null}
              copilotSuggestions={copilotSuggestions}
              customerId={selected?.contactId ?? null}
              onInviteAgent={handleInviteAgent}
              onEscalate={handleEscalate}
            />
          </div>

        </div>
      </div>

      {/* Close modal */}
      {(showCloseModal || selected?.pendingCloseModal) && selected && (
        <CloseModal
          defaultIssueStatus={selected.sessionClosed ? "Cliente desconectou" : ""}
          defaultOutcome={selected.sessionClosed ? "abandoned" : "resolved"}
          onConfirm={(payload) => {
            setShowCloseModal(false);
            handleClose(selected.sessionId, payload);
          }}
          onCancel={() => {
            setShowCloseModal(false);
            if (selected.sessionClosed) {
              handleClose(selected.sessionId, {
                issue_status: "Cliente desconectou",
                outcome: "abandoned",
              });
            }
          }}
        />
      )}

      {showPauseModal && (
        <PauseReasonModal
          onConfirm={handlePauseConfirm}
          onCancel={() => setShowPauseModal(false)}
        />
      )}

      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
};
