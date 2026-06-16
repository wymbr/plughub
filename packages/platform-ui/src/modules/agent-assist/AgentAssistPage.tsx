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

import React, { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/useAuth";
import { Clock, WifiOff } from "lucide-react";

import { ActiveTab, ClosePayload, ResponseTimer, ChatMessage } from "./types";
import { useAgentAssist, aggregateStatus } from "./AgentAssistContext";
import { useSupervisorState }              from "./hooks/useSupervisorState";
import { useSupervisorCapabilities }       from "./hooks/useSupervisorCapabilities";
import { useCopilotState }                 from "./hooks/useCopilotState";
import { useMentionableAgents }     from "./hooks/useMentionableAgents";
import { Header }              from "./components/Header";
import { ActionBar }           from "./components/ActionBar";
import { ChatArea }            from "./components/ChatArea";
import { AgentInput }          from "./components/AgentInput";
import { PauseReasonModal }    from "./components/PauseReasonModal";
import { RightPanel }          from "./components/RightPanel";
import { ContactList }         from "./components/ContactList";
import { PullInboxPanel }      from "./components/PullInboxPanel";
import { ToastContainer }      from "./components/ToastContainer";
import { DelegarTarefaDrawer } from "./components/DelegarTarefaDrawer";
import {
  ParticipantFilterBar,
  FilterKey,
  messageMatchesFilter,
} from "./components/ParticipantFilterBar";
import { CopilotBanner }   from "./components/CopilotBanner";
import { WebRTCOverlay }   from "./components/WebRTCOverlay";

// Set vazio estável para o preview read-only (ChatArea sem seleção de mensagens).
const EMPTY_MESSAGE_IDS: Set<string> = new Set<string>();

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
  const [activeTab,         setActiveTab]         = useState<ActiveTab>("acoes");
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
  // Reset UI-local state when selected contact changes
  useEffect(() => {
    setSubstitutionMode(false);
    setFilterKey(null);
    setSelectedMessageIds(new Set());
  }, [selectedSessionId]);

  // ── Derived state needed before hook calls ────────────────────────────
  const selected = selectedSessionId ? contacts.get(selectedSessionId) ?? null : null;

  // F2b-2b-2 — capacidade do agente (gating do claim na fila pull) + SLA por pool
  // (cor de urgência das linhas). maxConcurrentSessions vem do JWT (default 3).
  const maxConcurrent = session?.maxConcurrentSessions ?? 3;
  const atCapacity    = contacts.size >= maxConcurrent;
  const poolSlaMap: Record<string, number | null> = {};
  for (const p of availablePools) poolSlaMap[p.pool_id] = p.sla_target_ms ?? null;
  // Pools pull ativos (accessible ∩ dispatch_mode=pull). Se houver, a inbox divide
  // a coluna esquerda em duas metades (atendidos × fila pull) em vez de ficar no rodapé.
  const pullPoolIds = activePools.filter(p =>
    availablePools.find(ap => ap.pool_id === p)?.dispatch_mode === "pull"
  );
  const hasPullQueues = pullPoolIds.length > 0;

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

  // ── F2b-2b — Preview read-only de contato em fila (pull) antes do claim ──
  // Reusa os endpoints read-only existentes (conversation_history + supervisor_state),
  // keyed só por sessionId — sem virar participante. Sem cache: ao trocar de alvo
  // (ou sair do preview) o anterior é descartado (D2).
  const [previewSessionId, setPreviewSessionId] = useState<string | null>(null);
  const [previewPoolId,    setPreviewPoolId]    = useState<string | null>(null);
  const [previewMessages,  setPreviewMessages]  = useState<ChatMessage[]>([]);
  const { state: previewSupervisorState } = useSupervisorState(previewSessionId, lastWsEvent);

  useEffect(() => {
    if (!previewSessionId) { setPreviewMessages([]); return; }
    let alive = true;
    const load = async () => {
      try {
        const res  = await fetch(`/api/conversation_history/${previewSessionId}`);
        const data = res.ok ? (await res.json() as { messages?: ChatMessage[] }) : { messages: [] };
        if (alive) setPreviewMessages(data.messages ?? []);
      } catch { /* transient — preview pode ficar momentaneamente vazio */ }
    };
    load();
    const id = setInterval(load, 4000);   // D3 — segue o poll
    return () => { alive = false; clearInterval(id); };
  }, [previewSessionId]);

  const handlePreviewQueueContact = useCallback((sessionId: string, poolId: string) => {
    setSelectedSessionId(null);   // sai do atendimento focado → centro mostra o preview
    setPreviewSessionId(sessionId);
    setPreviewPoolId(poolId);
  }, [setSelectedSessionId]);

  const claimPreviewContact = useCallback(async (sessionId: string, poolId: string) => {
    const instanceId = session?.userId ? `human-${session.userId}` : "";
    try {
      const res = await fetch(`/api/work_queue/claim/${encodeURIComponent(sessionId)}`, {
        method:  "POST",
        headers: { "content-type": "application/json" },
        body:    JSON.stringify({ pool_id: poolId, instance_id: instanceId }),
      });
      const result = await res.json() as { claimed?: boolean; reason?: string };
      if (result.claimed) {
        setPreviewSessionId(null);
        setPreviewPoolId(null);
        setSelectedSessionId(sessionId);   // o WS conversation.assigned anexa o contato real
      } else {
        addToast(t(`pullInbox.claimReason.${result.reason ?? "failed"}`, { defaultValue: result.reason ?? "" }), "error");
      }
    } catch (e) {
      addToast(String(e), "error");
    }
  }, [session, setSelectedSessionId, addToast, t]);

  // Arc 11 / console-acoes-tab — mentionable agents for current pool
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
    setActiveTab("acoes");
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
      const now = Date.now();
      setContacts(prev => {
        const c = prev.get(selectedSessionId);
        if (!c) return prev;
        const next = new Map(prev);
        // Freeze the response timer optimistically when the agent sends a
        // customer-visible message (not a @mention, which is agents_only).
        // Rules c/d: agent reply + counting → frozen; agent reply + frozen → no change.
        const responseTimer: ResponseTimer =
          !isMention && c.responseTimer.status === 'counting'
            ? { status: 'frozen', elapsedMs: now - c.responseTimer.startedAt }
            : c.responseTimer;
        next.set(selectedSessionId, {
          ...c,
          messages: [...c.messages, {
            id:         `local-${now}`,
            author:     "agent_human",
            text,
            timestamp:  new Date(now).toISOString(),
            visibility: isMention ? "agents_only" : undefined,
          }],
          responseTimer,
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
        // G7 Slice 1: envia o instance_id DESTE console (do conversation.assigned)
        // para o platform atribuir o close ao participante certo. Sem isso o
        // mcp-server cai no meta.instance_id global (last-writer) e, em
        // multi-humano, o agent_done de um humano é atribuído a outro. Ver g7 §10.
        const instanceId = contacts.get(sessionId)?.instanceId ?? undefined;
        await fetch(`/api/agent_done/${sessionId}`, {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({ ...payload, instance_id: instanceId }),
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
    [addToast, setContacts, handledSessions, contacts, t]
  );

  const handleMenuSubmit = useCallback(
    async (menuId: string, result: import("./components/MenuCard").SubmitResult) => {
      if (!selectedSessionId) return;
      const contact = contacts.get(selectedSessionId);
      const menuMsg = contact?.messages.find(m => m.menuData?.menu_id === menuId);
      const interaction = menuMsg?.menuData?.interaction ?? "button";
      // G7 (c): instance de origem do menu → roteamento determinístico no backend.
      const sourceInstance = menuMsg?.menuData?.source_instance ?? "";

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
          body:    JSON.stringify({ menu_id: menuId, interaction, result, displayText, agent_key: sourceInstance }),
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
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${session?.accessToken ?? ""}` },
        body:    JSON.stringify({ pool_id: poolId }),
      }).catch(() => { /* non-fatal */ });
    }
  }, [activePools, addToast, t, session]);

  const handlePauseRequest = useCallback(() => {
    setShowPauseModal(true);
  }, []);

  // Restore the pause button state on mount: isPaused is UI-local (resets on
  // navigation), but the durable pause marker survives. Read it once so the
  // button reflects reality after a reconnect. The backend keeps the agent
  // actually paused (registerHumanAgent + heartbeat carry status=paused).
  const stateChecked = useRef(false);
  useEffect(() => {
    if (stateChecked.current || !session?.accessToken) return;
    stateChecked.current = true;
    fetch(`/api/agent-state`, { headers: { Authorization: `Bearer ${session.accessToken}` } })
      .then(r => (r.ok ? r.json() : null))
      .then(d => { if (d?.paused) setIsPaused(true); })
      .catch(() => { /* non-fatal */ });
  }, [session]);

  const handlePauseConfirm = useCallback(
    (reasonId: string, reasonLabel: string, note?: string, maxMinutes?: number) => {
      setShowPauseModal(false);
      setIsPaused(true);
      const detail = note ? ` — ${note}` : "";
      addToast(t("message.agentPaused", { reason: reasonLabel, detail }), "info");
      const poolId = activePools[0] ?? "";
      fetch(`/api/agent-pause`, {
        method:  "PUT",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${session?.accessToken ?? ""}` },
        body:    JSON.stringify({ pool_id: poolId, reason_id: reasonId, reason_label: reasonLabel, note, max_minutes: maxMinutes }),
      }).catch(() => { /* non-fatal */ });
    },
    [activePools, addToast, t, session]
  );

  // Arc 11 — terminate an AI segment via @mention
  const handleTerminateSegment = useCallback(
    (instanceId: string) => { handleSend(`@${instanceId} terminate_self`); },
    [handleSend],
  );

  // Arc 11 Fase B — invite a specialist via @mention
  // alias = key in mentionable_pools (e.g. "auth", "copilot"), NOT agent_type_id
  const handleAddSpecialist = useCallback(
    (alias: string, instruction: string, _visibility: "all" | "agents_only") => {
      const text = instruction ? `@${alias} ${instruction}` : `@${alias}`;
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

  // Transfer to pool (G7 — Stage 1): calls /api/session_transfer which mirrors the
  // session_escalate tool (mode: transfer) — current agent leaves the conference and
  // the session is re-routed to the target pool. The transfer-aware wrap-up
  // (on_human_end as segment-end) is wired in the orchestrator-bridge in a follow-up.
  const handleTransferTo = useCallback(
    (poolId: string) => {
      if (!selectedSessionId) return;
      fetch(`/api/session_transfer/${selectedSessionId}`, {
        method:  "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${session?.accessToken ?? ""}` },
        body:    JSON.stringify({ target_pool: poolId, handoff_reason: "agent_transfer" }),
      })
        .then(r => {
          if (r.ok) addToast(t("message.transferDone", { pool: poolId }), "info");
          else      addToast(t("message.transferFailed", { pool: poolId }), "error");
        })
        .catch(() => addToast(t("message.transferFailed", { pool: poolId }), "error"));
    },
    [selectedSessionId, session, addToast, t],
  );

  // ── Derived state ──────────────────────────────────────────────────────
  const wsStatus     = aggregateStatus(statuses, activePools);
  const headerPoolId = selected?.poolId ?? activePools[0] ?? "";
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

  // Right panel tab labels (console-acoes-tab: "agentes" → "acoes")
  const rightTabLabels: Record<string, string> = {
    acoes:     t("rightTab.acoes",    { defaultValue: "Ações"    }),
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

          {/* Center column header: ActionBar (atendimento) ou barra de preview (fila pull) */}
          <div className="flex flex-1 overflow-hidden">
            {(!selected && previewSessionId) ? (
              <div className="flex items-center gap-2 px-4 w-full">
                <span className="text-xs font-mono text-muted truncate">{previewSessionId.slice(0, 8)}</span>
                {previewPoolId && <span className="text-2xs text-muted-light">· {previewPoolId}</span>}
                <div className="flex-1" />
                <button
                  type="button"
                  disabled={atCapacity}
                  title={atCapacity ? t("pullInbox.atCapacity", { defaultValue: "Capacidade máxima de atendimentos atingida" }) : undefined}
                  onClick={() => { if (previewSessionId) claimPreviewContact(previewSessionId, previewPoolId ?? ""); }}
                  className="rounded bg-primary px-3 py-1.5 text-xs font-medium text-white hover:bg-primary/90 disabled:opacity-50"
                >
                  {t("pullInbox.claim", { defaultValue: "Atender (Pull)" })}
                </button>
                <button
                  type="button"
                  onClick={() => { setPreviewSessionId(null); setPreviewPoolId(null); }}
                  className="rounded border border-border px-2.5 py-1.5 text-xs text-muted hover:text-dark"
                >
                  {t("common.close", { defaultValue: "Fechar" })}
                </button>
              </div>
            ) : (
              <ActionBar
                contact={selected}
                onEncerrar={() => { if (selected) handleClose(selected.sessionId, { issue_status: "closed", outcome: "resolved" }); }}
                onTransferTo={handleTransferTo}
                onDesligar={handleDesligar}
                substitutionMode={substitutionMode}
                onToggleSubstitutionMode={() => setSubstitutionMode(prev => !prev)}
              />
            )}
          </div>

          {/* Right-panel tab bar: Agentes · Contexto · Histórico */}
          <div className="w-[280px] flex-shrink-0 border-l border-border flex bg-surface-muted">
            {(["acoes", "contexto", "historico"] as ActiveTab[]).map((id) => (
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
          <div className="w-[200px] flex-shrink-0 bg-surface-alt border-r border-border overflow-hidden flex flex-col">
            {/* Contatos atendidos — quando há fila pull, divide a coluna em duas
                metades (atendidos × pull); senão ocupa tudo. Cada metade rola. */}
            <div className="flex-1 min-h-0 overflow-hidden">
              <ContactList
                contacts={[...contacts.values()]}
                selectedSessionId={selectedSessionId}
                aiTypingSessions={aiTypingSessions}
                onSelect={handleSelectContact}
              />
            </div>
            {/* Frente 1 — inbox das filas pull: metade inferior da coluna (não rodapé) */}
            {hasPullQueues && (
              <div className="flex-1 min-h-0 overflow-y-auto border-t border-border">
                <PullInboxPanel
                  pullPools={pullPoolIds}
                  instanceId={session?.userId ? `human-${session.userId}` : ""}
                  poolSla={poolSlaMap}
                  claimDisabled={atCapacity}
                  claimDisabledReason={t("pullInbox.atCapacity", { defaultValue: "Capacidade máxima de atendimentos atingida" })}
                  previewSessionId={previewSessionId}
                  onPreview={handlePreviewQueueContact}
                  onPreviewInvalid={() => { setPreviewSessionId(null); setPreviewPoolId(null); }}
                  onClaimed={(sid) => { setPreviewSessionId(null); setSelectedSessionId(sid); }}
                />
              </div>
            )}
          </div>

          {/* Center column: ParticipantFilterBar + ChatArea + CopilotBanner + AgentInput */}
          <div className="flex flex-col flex-1 overflow-hidden bg-white">
            {(!selected && !previewSessionId) ? (
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
            ) : selected ? (
              <>
                {/* WebRTC overlay — renders only when channel=webrtc and medium≠text */}
                {selected.channel === "webrtc" && (
                  <WebRTCOverlay
                    sessionId={selected.sessionId}
                    channel={selected.channel}
                    agentIdentity={session?.userId ?? agentName}
                  />
                )}

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
            ) : (
              /* F2b-2b — preview read-only do contato em fila (sem input; "Atender" na action bar) */
              <>
                <div className="flex items-center gap-2 px-3 py-1.5 bg-warning-light border-b border-warning/30 text-xs text-warning-text">
                  <Clock className="w-3.5 h-3.5 flex-shrink-0" aria-hidden="true" />
                  <span>{t("pullInbox.previewBanner", { defaultValue: "Contato em espera — visualização read-only. Use \"Atender\" para assumir." })}</span>
                </div>
                <ChatArea
                  messages={previewMessages}
                  aiTyping={false}
                  sessionClosed={false}
                  liveState={previewSupervisorState ? {
                    sentimentScore: previewSupervisorState.sentiment.current,
                    sentimentAlert: previewSupervisorState.sentiment.alert,
                    sentimentTrend: previewSupervisorState.sentiment.trend,
                    intent:         previewSupervisorState.intent.current,
                    flags:          previewSupervisorState.flags,
                  } : null}
                  substitutionMode={false}
                  onMenuSubmit={() => {}}
                  selectedMessageIds={EMPTY_MESSAGE_IDS}
                  onToggleSelection={() => {}}
                />
              </>
            )}
          </div>

          {/* Right panel */}
          <div className="w-[280px] flex-shrink-0 border-l border-border overflow-hidden bg-surface-muted">
            <RightPanel
              activeTab={activeTab}
              supervisorState={selected ? (selected.supervisorState ?? null) : previewSupervisorState}
              customerId={selected?.contactId ?? null}
              tenantId={session?.tenantId}
              sessionId={selected?.sessionId ?? previewSessionId}
              sessionMessages={selected ? selected.messages : previewMessages}
              onTerminateSegment={handleTerminateSegment}
              agentName={agentName}
              substitutionMode={substitutionMode}
              onToggleSubstitutionMode={() => setSubstitutionMode(prev => !prev)}
              mentionableAgents={mentionableAgents}
              onAddSpecialist={handleAddSpecialist}
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
