/**
 * ActionBar — Arc 11 Fase 2 (Fase E)
 * Top bar of the chat column, showing:
 *   • Handle timer (⏱) for the current session
 *   • Compact sentiment indicator (😊/😐/😤/😡 + label)
 *   • Action buttons: TransferCombo / Desligar / Processo
 *   • Encerrar button (rightmost)
 *
 * Removed (Fase B): contact identity section, SLA bar, Substituir button.
 * Removed (Fase E): CollaborateCombo — agent invite and delegation fully
 *   covered by Aba Agentes (right panel). Aba Agentes has:
 *   Seção B with AgentInviteRow per mentionable agent + Delegar Tarefa button.
 *
 * TransferCombo — dropdown with escalation pool destinations.
 */

import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ContactSession } from "../types";

function formatElapsed(ms: number): string {
  const s = Math.floor(ms / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}:${m.toString().padStart(2, "0")}:${sec.toString().padStart(2, "0")}`;
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

// ── Compact sentiment indicator ───────────────────────────────────────────────
// Arc 11 Fase 2 — shows current session sentiment in the action bar.
// Scale: satisfied (≥0.3) · neutral (-0.3–0.3) · frustrated (-0.6– -0.3) · angry (<-0.6)
interface SentimentInfo { emoji: string; label: string; cls: string }

function sentimentInfo(score: number): SentimentInfo {
  if (score >= 0.3)  return { emoji: "😊", label: "Satisfeito", cls: "text-green-700 bg-green-50 border-green-200" };
  if (score >= -0.3) return { emoji: "😐", label: "Neutro",     cls: "text-gray-500 bg-gray-50 border-gray-200" };
  if (score >= -0.6) return { emoji: "😤", label: "Frustrado",  cls: "text-orange-600 bg-orange-50 border-orange-200" };
  return             { emoji: "😡", label: "Irritado",   cls: "text-red-600 bg-red-50 border-red-200" };
}

const SentimentChip: React.FC<{ score: number }> = ({ score }) => {
  const { emoji, label, cls } = sentimentInfo(score);
  return (
    <span
      className={`flex items-center gap-1 px-2 py-0.5 rounded-full border text-xs font-medium flex-shrink-0 ${cls}`}
      title={`Sentimento: ${label} (${score.toFixed(2)})`}
    >
      {emoji} {label}
    </span>
  );
};

// ── Reply-SLA chip ────────────────────────────────────────────────────────────
// Shows elapsed time since the last customer message vs pool's max_reply_time_ms.
// Only rendered when session is open, customer is waiting, and maxReplyTimeMs is set.
type ReplySlaLevel = "ok" | "warning" | "breach";

function replySlaLevel(waitMs: number, limitMs: number): ReplySlaLevel {
  const ratio = waitMs / limitMs;
  if (ratio >= 1.0) return "breach";
  if (ratio >= 0.7) return "warning";
  return "ok";
}

const REPLY_SLA_CLS: Record<ReplySlaLevel, string> = {
  ok:      "text-green-700 bg-green-50 border-green-200",
  warning: "text-amber-700 bg-amber-50 border-amber-200 font-semibold",
  breach:  "text-red-700 bg-red-50 border-red-200 font-bold animate-pulse",
};

const ReplySlaChip: React.FC<{
  lastCustomerMessageAt: Date;
  maxReplyTimeMs:        number;
}> = ({ lastCustomerMessageAt, maxReplyTimeMs }) => {
  const [nowMs, setNowMs] = useState(Date.now());
  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 1_000);
    return () => clearInterval(id);
  }, []);

  const waitMs  = nowMs - lastCustomerMessageAt.getTime();
  const level   = replySlaLevel(waitMs, maxReplyTimeMs);
  const elapsed = formatElapsed(waitMs);
  const limitFmt = formatElapsed(maxReplyTimeMs);

  return (
    <span
      className={`flex items-center gap-1 px-2 py-0.5 rounded-full border text-xs flex-shrink-0 ${REPLY_SLA_CLS[level]}`}
      title={`Resposta: ${elapsed} / limite ${limitFmt}`}
    >
      💬 {elapsed}
    </span>
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
  /** Timestamp of the last customer message — used for the reply-SLA chip */
  lastCustomerMessageAt?:   Date | null;
  onEncerrar:               () => void;
  /** Called when operator selects a pool from the TransferCombo */
  onTransferTo?:            (poolId: string) => void;
  onDesligar?:              () => void;
  /**
   * substitutionMode / onToggleSubstitutionMode: kept in props so the parent's
   * state and menu-card rendering still work. The toggle button lives in
   * Aba Agentes (Fase C) — not rendered here.
   */
  substitutionMode?:        boolean;
  onToggleSubstitutionMode?: () => void;
  mentionableJourneys?:     string[];
  onIniciarProcesso?:       (skillId: string) => void;
  // Removed (Fase E): mentionableAgents, onAddSpecialist, selectedCount, onDelegar
  // → fully covered by Aba Agentes (right panel Seção B).
}

// ── Main component ─────────────────────────────────────────────────────────────
export const ActionBar: React.FC<ActionBarProps> = ({
  contact,
  lastCustomerMessageAt,
  onEncerrar,
  onTransferTo,
  onDesligar,
  // substitutionMode and onToggleSubstitutionMode intentionally unused here —
  // button lives in Aba Agentes (Fase C).
  mentionableJourneys = [],
  onIniciarProcesso,
}) => {
  if (!contact) {
    return (
      <div className="flex-1 bg-white flex items-center px-4 gap-2">
        <span className="text-sm text-gray-300 select-none">—</span>
        <span className="text-xs text-gray-400">Selecione um contato para iniciar o atendimento</span>
      </div>
    );
  }

  const sentimentScore = contact.supervisorState?.sentiment.current ?? null;

  return (
    <div className={`flex-1 flex items-center gap-2 px-3
      ${contact.sessionClosed ? "bg-amber-50" : "bg-white"}`}
    >
      {/* ── Handle timer ── */}
      <HandleTimer startedAt={contact.sessionStartedAt} />

      {/* ── Reply-SLA chip — customer waiting + pool has reply limit ── */}
      {!contact.sessionClosed
        && (lastCustomerMessageAt ?? contact.lastCustomerMessageAt)
        && contact.maxReplyTimeMs
        && (
          <ReplySlaChip
            lastCustomerMessageAt={(lastCustomerMessageAt ?? contact.lastCustomerMessageAt)!}
            maxReplyTimeMs={contact.maxReplyTimeMs}
          />
        )
      }

      {/* ── Sentiment chip (only when session open and data available) ── */}
      {!contact.sessionClosed && sentimentScore !== null && (
        <SentimentChip score={sentimentScore} />
      )}

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

      {/* ── Session-closed banner ── */}
      {contact.sessionClosed && (
        <span className="text-xs text-amber-700 font-medium">
          ⚠️ Sessão encerrada
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
