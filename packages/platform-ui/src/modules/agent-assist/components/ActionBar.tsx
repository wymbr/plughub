/**
 * ActionBar
 * Top bar of the chat column, showing:
 *   • Contact identity (channel icon + display id + pool badge + channel badge)
 *   • Action buttons: Transferir / Desligar  (left group)
 *   • SLA mini-bar (centre-right)
 *   • Encerrar button (rightmost)
 *
 * Note: Pausar is a global agent-level toggle; it lives in the Header, not here.
 *
 * Replaces the contact identity that was in the old ChatArea header and consolidates
 * the "Encerrar" button that was previously in AgentInput.
 */

import React, { useEffect, useState } from "react";
import { ContactSession, SlaState } from "../types";

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

// ── Props ─────────────────────────────────────────────────────────────────────
export interface ActionBarProps {
  contact:                  ContactSession | null;
  onEncerrar:               () => void;
  onTransferir?:            () => void;
  onDesligar?:              () => void;
  substitutionMode?:        boolean;
  onToggleSubstitutionMode?: () => void;
}

// ── Main component ─────────────────────────────────────────────────────────────
export const ActionBar: React.FC<ActionBarProps> = ({
  contact,
  onEncerrar,
  onTransferir,
  onDesligar,
  substitutionMode = false,
  onToggleSubstitutionMode,
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
      ${contact.sessionClosed
        ? "bg-amber-50"
        : "bg-white"
      }`}
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
        <button
          onClick={onTransferir}
          disabled={contact.sessionClosed}
          title="Transferir para outro agente ou pool"
          className="px-2.5 py-1 rounded text-xs font-medium border transition-colors
            text-amber-700 bg-amber-50 border-amber-200 hover:bg-amber-100 hover:border-amber-300
            disabled:opacity-40 disabled:cursor-not-allowed"
        >
          ↗ Transferir
        </button>
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
        {/* ── Menu substitution mode toggle ── */}
        {onToggleSubstitutionMode && (
          <button
            onClick={onToggleSubstitutionMode}
            disabled={contact.sessionClosed}
            title={substitutionMode
              ? "Desativar modo substituição (voltar para observação)"
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
      </div>

      {/* ── Spacer ── */}
      <div className="flex-1" />

      {/* ── SLA bar (centre-right) ── */}
      {sla && <SlaBar sla={sla} />}

      {/* ── Session-closed banner (replaces SLA when closed) ── */}
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
