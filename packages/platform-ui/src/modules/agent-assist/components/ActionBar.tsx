/**
 * ActionBar
 * Top bar of the chat column, showing:
 *   • Contact identity (channel icon + display id + pool badge)
 *   • Handle timer
 *   • Action buttons: TransferCombo / CollaborateCombo / Desligar / Substituir / Processo
 *   • SLA mini-bar (centre-right)
 *   • Encerrar button (rightmost)
 *
 * TransferCombo  — dropdown with escalation pool destinations.
 * CollaborateCombo — unified dropdown: "Convidar especialista" + "Delegar tarefa".
 */

import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ContactSession, MentionableAgent, SlaState } from "../types";

const CHANNEL_ICON: Record<string, string> = {
  webchat:   "💬",
  whatsapp:  "📱",
  voice:     "📞",
  email:     "✉️",
  sms:       "📩",
  telegram:  "✈️",
  instagram: "📷",
  webrtc:    "🎙️",
};

function channelIcon(ch: string) { return CHANNEL_ICON[ch] ?? "💬"; }

function displayId(contact: ContactSession): string {
  return contact.contactId ?? contact.sessionId.slice(0, 8);
}

function formatElapsed(ms: number): string {
  const s = Math.floor(ms / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}:${m.toString().padStart(2, "0")}:${sec.toString().padStart(2, "0")}`;
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

// ── SLA bar ───────────────────────────────────────────────────────────────────
const SlaBar: React.FC<{ sla: SlaState }> = ({ sla }) => {
  const pct = Math.min(sla.percentage, 100);
  const color =
    sla.breach_imminent ? "bg-red-500"
    : pct > 70          ? "bg-yellow-400"
    : "bg-green-500";

  return (
    <div className="flex items-center gap-2 flex-shrink-0">
      <span className="text-xs text-gray-500 font-medium">SLA</span>
      <div className="w-20 h-2 bg-gray-200 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${color}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className={`text-xs font-mono tabular-nums w-10 text-right
        ${sla.breach_imminent ? "text-red-600 font-bold animate-pulse" : "text-gray-600"}`}>
        {formatElapsed(sla.elapsed_ms)}
      </span>
      {sla.breach_imminent && (
        <span className="text-[10px] font-bold text-red-600 uppercase bg-red-100
          px-1.5 py-0.5 rounded border border-red-300 animate-pulse">
          BREACH
        </span>
      )}
    </div>
  );
};

// ── Handle-time counter ───────────────────────────────────────────────────────
const HandleTimer: React.FC<{ startedAt: Date }> = ({ startedAt }) => {
  const [ms, setMs] = useState(Date.now() - startedAt.getTime());
  useEffect(() => {
    const id = setInterval(() => setMs(Date.now() - startedAt.getTime()), 1_000);
    return () => clearInterval(id);
  }, [startedAt]);

  return (
    <span
      className={`text-xs font-mono tabular-nums font-semibold
        ${ms >= 30 * 60_000 ? "text-orange-600" : "text-gray-500"}`}
      title="Tempo de atendimento"
    >
      ⏱ {formatElapsed(ms)}
    </span>
  );
};

// ── Dropdown position type ────────────────────────────────────────────────────
interface DropPos { top: number; left: number }

// ── TransferCombo — pool destinations from escalation suggestions ──────────────
const TransferCombo: React.FC<{
  contact:     ContactSession;
  onTransferTo: (poolId: string) => void;
}> = ({ contact, onTransferTo }) => {
  const [dropPos, setDropPos] = useState<DropPos | null>(null);
  const btnRef  = useRef<HTMLButtonElement>(null);
  const dropRef = useRef<HTMLDivElement>(null);
  const open    = dropPos !== null;
  const escalations = contact.capabilities?.escalations ?? [];

  useEffect(() => {
    if (!open) return;
    function handle(e: MouseEvent) {
      const t = e.target as Node;
      if (btnRef.current?.contains(t) || dropRef.current?.contains(t)) return;
      setDropPos(null);
    }
    document.addEventListener("mousedown", handle);
    return () => document.removeEventListener("mousedown", handle);
  }, [open]);

  function toggle() {
    if (open) { setDropPos(null); return; }
    if (btnRef.current) {
      const r = btnRef.current.getBoundingClientRect();
      setDropPos({ top: r.bottom + 4, left: r.left });
    }
  }

  return (
    <div className="flex-shrink-0">
      <button
        ref={btnRef}
        onClick={toggle}
        disabled={contact.sessionClosed}
        title="Transferir para outro pool"
        className="px-2.5 py-1 rounded text-xs font-medium border transition-colors
          text-amber-700 bg-amber-50 border-amber-200 hover:bg-amber-100 hover:border-amber-300
          disabled:opacity-40 disabled:cursor-not-allowed"
      >
        ↗ Transferir {escalations.length > 0 ? "▾" : ""}
      </button>

      {open && dropPos && createPortal(
        <div
          ref={dropRef}
          style={{ position: "fixed", top: dropPos.top, left: dropPos.left }}
          className="z-[9999] bg-white border border-gray-200 rounded-lg shadow-xl
            min-w-[200px] overflow-hidden"
        >
          <div className="px-2.5 py-1.5 text-[10px] font-bold text-gray-400 uppercase tracking-wide border-b border-gray-100">
            Transferir para
          </div>

          {escalations.length === 0 ? (
            <div className="px-3 py-2.5 text-xs text-gray-400 text-center">
              Sem destinos disponíveis
            </div>
          ) : (
            escalations.map(esc => (
              <button
                key={esc.pool_id}
                onClick={() => { onTransferTo(esc.pool_id); setDropPos(null); }}
                className="w-full text-left px-3 py-2 hover:bg-amber-50 transition-colors
                  border-b border-gray-50 last:border-0"
              >
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold text-gray-800">
                    {esc.pool_id.replace(/_humano|_ia|_v\d+/gi, "").replace(/_/g, " ")}
                  </span>
                  {esc.recommended && (
                    <span className="text-[9px] px-1.5 py-0.5 rounded bg-green-100
                      text-green-700 border border-green-200 font-medium ml-2">
                      ✓ Recomendado
                    </span>
                  )}
                </div>
                <div className="text-[10px] text-gray-500 mt-0.5 flex gap-2">
                  {esc.reason && <span className="truncate">{esc.reason}</span>}
                  {esc.estimated_wait_s != null && esc.estimated_wait_s > 0 && (
                    <span className="flex-shrink-0 text-gray-400">
                      ~{Math.round(esc.estimated_wait_s / 60)}min espera
                    </span>
                  )}
                </div>
                <div className="text-[9px] text-gray-400 font-mono mt-0.5">
                  {esc.pool_id}
                </div>
              </button>
            ))
          )}
        </div>,
        document.body
      )}
    </div>
  );
};

// ── CollaborateCombo — Especialista + Delegar in one dropdown ─────────────────
/**
 * Unified "Colaborar ▾" dropdown with two sections:
 *  1. Convidar Especialista: lists mentionable AI agents (2-step flow with context)
 *  2. Delegar Tarefa: opens the delegation drawer
 *
 * Shows a badge when messages are selected for delegation.
 */
type CollabStep =
  | { kind: "root" }
  | { kind: "specialist-pick" }
  | { kind: "specialist-context"; agent: MentionableAgent };

const CollaborateCombo: React.FC<{
  agents:        MentionableAgent[];
  selectedCount: number;
  disabled?:     boolean;
  onInvite:      (alias: string, context: string) => void;
  onDelegar:     () => void;
}> = ({ agents, selectedCount, disabled, onInvite, onDelegar }) => {
  const [dropPos, setDropPos] = useState<DropPos | null>(null);
  const [step, setStep] = useState<CollabStep>({ kind: "root" });
  const [context, setContext] = useState("");
  const btnRef  = useRef<HTMLButtonElement>(null);
  const dropRef = useRef<HTMLDivElement>(null);
  const open    = dropPos !== null;

  useEffect(() => {
    if (!open) return;
    function handle(e: MouseEvent) {
      const t = e.target as Node;
      if (btnRef.current?.contains(t) || dropRef.current?.contains(t)) return;
      setDropPos(null);
      setStep({ kind: "root" });
      setContext("");
    }
    document.addEventListener("mousedown", handle);
    return () => document.removeEventListener("mousedown", handle);
  }, [open]);

  if (agents.length === 0) {
    // With no agents, only show the "Delegar" button independently
    return (
      <div className="relative flex-shrink-0">
        <button
          onClick={onDelegar}
          disabled={disabled}
          title={selectedCount > 0
            ? `Delegar tarefa (${selectedCount} msg selecionada${selectedCount !== 1 ? "s" : ""})`
            : "Delegar tarefa a um agente AI"}
          className="px-2.5 py-1 rounded text-xs font-medium border transition-colors
            text-orange-700 bg-orange-50 border-orange-200 hover:bg-orange-100 hover:border-orange-300
            disabled:opacity-40 disabled:cursor-not-allowed"
        >
          📤 Delegar
        </button>
        {selectedCount > 0 && (
          <span className="absolute -top-1.5 -right-1.5 w-4 h-4 bg-orange-500 text-white
            text-[9px] font-bold rounded-full flex items-center justify-center pointer-events-none">
            {selectedCount > 9 ? "9+" : selectedCount}
          </span>
        )}
      </div>
    );
  }

  function formatAgentName(id: string) {
    return id.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
  }

  function toggle() {
    if (open) { setDropPos(null); setStep({ kind: "root" }); setContext(""); return; }
    if (btnRef.current) {
      const r = btnRef.current.getBoundingClientRect();
      setDropPos({ top: r.bottom + 4, left: r.left });
    }
    setStep({ kind: "root" });
  }

  function handleInviteSubmit(agent: MentionableAgent) {
    // Use alias (e.g. "auth") as the @mention target, not agent_type_id.
    // mcp-server resolves @alias via mentionable_pools → pool_id → routing.
    onInvite(agent.alias, context.trim());
    setDropPos(null);
    setStep({ kind: "root" });
    setContext("");
  }

  const dropContent = (
    <div
      ref={dropRef}
      style={{ position: "fixed", top: dropPos?.top ?? 0, left: dropPos?.left ?? 0 }}
      className="z-[9999] bg-white border border-gray-200 rounded-lg shadow-xl w-64 overflow-hidden"
    >
      {step.kind === "root" && (
        <>
          <div className="px-2.5 py-1.5 text-[10px] font-bold text-gray-400 uppercase tracking-wide border-b border-gray-100">
            Colaborar
          </div>

          {/* Specialists section */}
          <button
            onClick={() => setStep({ kind: "specialist-pick" })}
            className="w-full text-left px-3 py-2.5 hover:bg-purple-50 transition-colors
              border-b border-gray-100"
          >
            <div className="flex items-center gap-2">
              <span className="text-base">🤖</span>
              <div>
                <div className="text-xs font-semibold text-gray-800">Convidar Especialista</div>
                <div className="text-[10px] text-gray-500">
                  {agents.length} agente{agents.length !== 1 ? "s" : ""} disponível{agents.length !== 1 ? "is" : ""}
                </div>
              </div>
              <span className="ml-auto text-gray-400 text-xs">›</span>
            </div>
          </button>

          {/* Delegar section */}
          <button
            onClick={() => { onDelegar(); setDropPos(null); }}
            className="w-full text-left px-3 py-2.5 hover:bg-orange-50 transition-colors"
          >
            <div className="flex items-center gap-2">
              <span className="text-base">📤</span>
              <div>
                <div className="text-xs font-semibold text-gray-800">Delegar Tarefa</div>
                <div className="text-[10px] text-gray-500">
                  {selectedCount > 0
                    ? `${selectedCount} mensagem${selectedCount !== 1 ? "s" : ""} selecionada${selectedCount !== 1 ? "s" : ""}`
                    : "Enviar instrução a um agente AI"}
                </div>
              </div>
              {selectedCount > 0 && (
                <span className="ml-auto text-[9px] px-1.5 py-0.5 rounded-full
                  bg-orange-100 text-orange-700 font-bold flex-shrink-0">
                  {selectedCount}
                </span>
              )}
            </div>
          </button>
        </>
      )}

      {step.kind === "specialist-pick" && (
        <>
          <div className="flex items-center gap-1.5 px-2.5 py-1.5 border-b border-gray-100">
            <button
              onClick={() => setStep({ kind: "root" })}
              className="text-gray-400 hover:text-gray-600 text-sm leading-none"
              title="Voltar"
            >←</button>
            <span className="text-[10px] font-bold text-gray-400 uppercase tracking-wide">
              Escolher especialista
            </span>
          </div>
          {agents.map(agent => (
            <button
              key={agent.agent_type_id}
              onClick={() => { setStep({ kind: "specialist-context", agent }); }}
              className="w-full text-left px-3 py-2.5 hover:bg-purple-50 transition-colors
                border-b border-gray-50 last:border-0"
            >
              <div className="text-xs font-semibold text-gray-800">
                🤖 {formatAgentName(agent.agent_type_id)}
              </div>
              {agent.description && (
                <div className="text-[10px] text-gray-500 mt-0.5 leading-snug">
                  {agent.description}
                </div>
              )}
              <div className="text-[10px] text-gray-400 font-mono mt-0.5">
                <span className="text-purple-500 font-semibold">@{agent.alias}</span>
                {agent.pool_id && (
                  <span className="ml-1 text-purple-400">· {agent.pool_id}</span>
                )}
              </div>
            </button>
          ))}
        </>
      )}

      {step.kind === "specialist-context" && (
        <div className="p-3">
          <div className="flex items-center gap-1.5 mb-2">
            <button
              onClick={() => { setStep({ kind: "specialist-pick" }); setContext(""); }}
              className="text-gray-400 hover:text-gray-600 text-sm leading-none"
              title="Voltar"
            >←</button>
            <span className="text-xs font-semibold text-gray-700 truncate">
              🤖 {formatAgentName(step.agent.agent_type_id)}
            </span>
          </div>
          <label className="block text-[10px] font-semibold text-gray-500 uppercase tracking-wide mb-1">
            Contexto (opcional)
          </label>
          <textarea
            autoFocus
            value={context}
            onChange={e => setContext(e.target.value)}
            placeholder="Descreva o que o especialista deve fazer…"
            rows={3}
            className="w-full text-xs border border-gray-300 rounded-lg px-2 py-1.5
              focus:outline-none focus:ring-2 focus:ring-purple-400 resize-none text-gray-700
              placeholder-gray-400"
            onKeyDown={e => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleInviteSubmit(step.agent);
              if (e.key === "Escape") { setDropPos(null); setStep({ kind: "root" }); setContext(""); }
            }}
          />
          <button
            onClick={() => handleInviteSubmit(step.agent)}
            className="mt-2 w-full py-1.5 text-xs font-semibold text-white
              bg-purple-600 hover:bg-purple-700 rounded-lg transition-colors"
          >
            Convidar
          </button>
          <p className="text-[9px] text-gray-400 text-center mt-1">⌘↵ para convidar</p>
        </div>
      )}
    </div>
  );

  return (
    <div className="relative flex-shrink-0">
      <button
        ref={btnRef}
        onClick={toggle}
        disabled={disabled}
        title="Colaborar: convidar especialista ou delegar tarefa"
        className="px-2.5 py-1 rounded text-xs font-medium border transition-colors
          text-purple-700 bg-purple-50 border-purple-200 hover:bg-purple-100 hover:border-purple-300
          disabled:opacity-40 disabled:cursor-not-allowed"
      >
        🤝 Colaborar ▾
      </button>
      {selectedCount > 0 && (
        <span className="absolute -top-1.5 -right-1.5 w-4 h-4 bg-orange-500 text-white
          text-[9px] font-bold rounded-full flex items-center justify-center pointer-events-none">
          {selectedCount > 9 ? "9+" : selectedCount}
        </span>
      )}
      {open && dropPos && createPortal(dropContent, document.body)}
    </div>
  );
};

// ── Iniciar Processo dropdown (Arc 10 Phase D) ────────────────────────────────
const IniciarProcessoButton: React.FC<{
  skills:    string[];
  disabled?: boolean;
  onSelect:  (skillId: string) => void;
}> = ({ skills, disabled, onSelect }) => {
  const [dropPos, setDropPos] = useState<DropPos | null>(null);
  const btnRef  = useRef<HTMLButtonElement>(null);
  const dropRef = useRef<HTMLDivElement>(null);
  const open    = dropPos !== null;

  useEffect(() => {
    if (!open) return;
    function handle(e: MouseEvent) {
      const t = e.target as Node;
      if (btnRef.current?.contains(t) || dropRef.current?.contains(t)) return;
      setDropPos(null);
    }
    document.addEventListener("mousedown", handle);
    return () => document.removeEventListener("mousedown", handle);
  }, [open]);

  if (skills.length === 0) return null;

  function toggle() {
    if (open) { setDropPos(null); return; }
    if (btnRef.current) {
      const r = btnRef.current.getBoundingClientRect();
      setDropPos({ top: r.bottom + 4, left: r.left });
    }
  }

  return (
    <div className="flex-shrink-0">
      <button
        ref={btnRef}
        onClick={toggle}
        disabled={disabled}
        title="Iniciar um processo (Journey) vinculado a esta sessão"
        className="px-2.5 py-1 rounded text-xs font-medium border transition-colors
          text-blue-700 bg-blue-50 border-blue-200 hover:bg-blue-100 hover:border-blue-300
          disabled:opacity-40 disabled:cursor-not-allowed"
      >
        🗺️ Processo ▾
      </button>
      {open && dropPos && createPortal(
        <div
          ref={dropRef}
          style={{ position: "fixed", top: dropPos.top, left: dropPos.left }}
          className="z-[9999] bg-white border border-gray-200 rounded-lg shadow-xl
            min-w-[180px] overflow-hidden"
        >
          <div className="px-2.5 py-1.5 text-[10px] font-bold text-gray-400 uppercase tracking-wide border-b border-gray-100">
            Iniciar processo
          </div>
          {skills.map(skillId => (
            <button
              key={skillId}
              onClick={() => { onSelect(skillId); setDropPos(null); }}
              className="w-full text-left px-3 py-2 text-xs text-gray-700 hover:bg-blue-50
                hover:text-blue-700 transition-colors border-b border-gray-50 last:border-0"
            >
              {skillId.replace(/^skill_|_v\d+$/g, '').replace(/_/g, ' ')}
              <div className="text-[10px] text-gray-400 font-mono mt-0.5">{skillId}</div>
            </button>
          ))}
        </div>,
        document.body
      )}
    </div>
  );
};

// ── Props ─────────────────────────────────────────────────────────────────────
export interface ActionBarProps {
  contact:                  ContactSession | null;
  onEncerrar:               () => void;
  /** Called when operator selects a pool from the TransferCombo */
  onTransferTo?:            (poolId: string) => void;
  onDesligar?:              () => void;
  substitutionMode?:        boolean;
  onToggleSubstitutionMode?: () => void;
  mentionableJourneys?:     string[];
  onIniciarProcesso?:       (skillId: string) => void;
  mentionableAgents?:       MentionableAgent[];
  onAddSpecialist?:         (alias: string, context: string) => void;
  selectedCount?:           number;
  onDelegar?:               () => void;
}

// ── Main component ─────────────────────────────────────────────────────────────
export const ActionBar: React.FC<ActionBarProps> = ({
  contact,
  onEncerrar,
  onTransferTo,
  onDesligar,
  substitutionMode = false,
  onToggleSubstitutionMode,
  mentionableJourneys = [],
  onIniciarProcesso,
  mentionableAgents = [],
  onAddSpecialist,
  selectedCount = 0,
  onDelegar,
}) => {
  if (!contact) {
    return (
      <div className="flex-1 bg-white flex items-center px-4 gap-2">
        <span className="text-sm text-gray-300 select-none">—</span>
        <span className="text-xs text-gray-400">Selecione um contato para iniciar o atendimento</span>
      </div>
    );
  }

  const sla = contact.supervisorState?.sla ?? null;

  return (
    <div className={`flex-1 flex items-center gap-2 px-3
      ${contact.sessionClosed ? "bg-amber-50" : "bg-white"}`}
    >
      {/* ── Left: contact identity ── */}
      <div className="flex items-center gap-1.5 min-w-0 flex-shrink-0 max-w-[200px]">
        <span className="text-base leading-none flex-shrink-0" title={contact.channel}>
          {contact.sessionClosed ? "🔴" : channelIcon(contact.channel)}
        </span>
        <span
          className="text-sm font-semibold text-gray-800 truncate font-mono"
          title={contact.contactId ?? contact.sessionId}
        >
          {displayId(contact)}
        </span>
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-500
          font-medium border border-gray-200 flex-shrink-0 truncate max-w-[80px]"
          title={contact.poolId}
        >
          {contact.poolId.replace(/_humano|_ia|_v\d+/gi, "").replace(/_/g, " ")}
        </span>
      </div>

      {/* ── Handle timer ── */}
      <HandleTimer startedAt={contact.sessionStartedAt} />

      {/* ── Divider ── */}
      <div className="w-px h-5 bg-gray-200 flex-shrink-0 mx-1" />

      {/* ── Action buttons ── */}
      <div className="flex items-center gap-1.5 flex-shrink-0">

        {/* Transferir (combo with escalation destinations) */}
        <TransferCombo
          contact={contact}
          onTransferTo={poolId => onTransferTo?.(poolId)}
        />

        {/* Desligar */}
        <button
          onClick={onDesligar}
          disabled={contact.sessionClosed}
          title="Desligar chamada"
          className="px-2.5 py-1 rounded text-xs font-medium border transition-colors
            text-red-700 bg-red-50 border-red-200 hover:bg-red-100 hover:border-red-300
            disabled:opacity-40 disabled:cursor-not-allowed"
        >
          📵 Desligar
        </button>

        {/* Substituição mode toggle */}
        {onToggleSubstitutionMode && (
          <button
            onClick={onToggleSubstitutionMode}
            disabled={contact.sessionClosed}
            title={substitutionMode
              ? "Desativar modo substituição"
              : "Ativar modo substituição — supervisor responde menus em nome do cliente"}
            className={[
              "px-2.5 py-1 rounded text-xs font-medium border transition-colors",
              substitutionMode
                ? "text-amber-800 bg-amber-200 border-amber-400 hover:bg-amber-300"
                : "text-amber-700 bg-white border-amber-300 hover:bg-amber-50",
              "disabled:opacity-40 disabled:cursor-not-allowed",
            ].join(" ")}
          >
            {substitutionMode ? "🔄 Substituindo" : "🔄 Substituir"}
          </button>
        )}

        {/* Colaborar combo (Especialista + Delegar unified) */}
        {onDelegar && (
          <CollaborateCombo
            agents={mentionableAgents}
            selectedCount={selectedCount}
            disabled={contact.sessionClosed}
            onInvite={onAddSpecialist ?? (() => {})}
            onDelegar={onDelegar}
          />
        )}

        {/* Iniciar Processo (Arc 10 Phase D) */}
        {onIniciarProcesso && mentionableJourneys.length > 0 && (
          <IniciarProcessoButton
            skills={mentionableJourneys}
            disabled={contact.sessionClosed}
            onSelect={onIniciarProcesso}
          />
        )}
      </div>

      {/* ── Spacer ── */}
      <div className="flex-1" />

      {/* ── SLA bar ── */}
      {sla && <SlaBar sla={sla} />}

      {/* ── Session-closed banner ── */}
      {contact.sessionClosed && !sla && (
        <span className="text-xs text-amber-700 font-medium">
          ⚠️ Cliente desconectou
        </span>
      )}

      {/* ── Encerrar ── */}
      <button
        onClick={onEncerrar}
        title="Encerrar atendimento e registrar desfecho"
        className="ml-2 px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors flex-shrink-0
          bg-red-600 text-white hover:bg-red-700 border border-red-700 shadow-sm"
      >
        Encerrar
      </button>
    </div>
  );
};
