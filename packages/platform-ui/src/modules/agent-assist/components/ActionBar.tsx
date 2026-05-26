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
import { useTranslation } from "react-i18next";
import { Timer, MessageSquare } from "lucide-react";
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

function sentimentInfo(score: number, t: (key: string) => string): SentimentInfo {
  if (score >= 0.3)  return { emoji: "😊", label: t('actionBar.sentiment.satisfied'),  cls: "text-green-text bg-green-light border-green/30" };
  if (score >= -0.3) return { emoji: "😐", label: t('actionBar.sentiment.neutral'),    cls: "text-muted bg-surface border-border" };
  if (score >= -0.6) return { emoji: "😤", label: t('actionBar.sentiment.frustrated'), cls: "text-contested-text bg-contested-light border-contested/30" };
  return             { emoji: "😡", label: t('actionBar.sentiment.angry'),     cls: "text-red-text bg-red-light border-red/30" };
}

const SentimentChip: React.FC<{ score: number }> = ({ score }) => {
  const { t } = useTranslation('agentAssist');
  const { emoji, label, cls } = sentimentInfo(score, t);
  return (
    <span
      className={`flex items-center gap-1 px-2 py-0.5 rounded-full border text-xs font-medium flex-shrink-0 ${cls}`}
      title={t('actionBar.sentimentTitle', { label, score: score.toFixed(2) })}
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
  ok:      "text-green-text bg-green-light border-green/30",
  warning: "text-warning-text bg-warning-light border-warning/30 font-semibold",
  breach:  "text-red-text bg-red-light border-red font-bold animate-pulse",
};

const ReplySlaChip: React.FC<{
  startedAt:     number;
  maxReplyTimeMs:number;
}> = ({ startedAt, maxReplyTimeMs }) => {
  const [nowMs, setNowMs] = useState(Date.now());
  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 1_000);
    return () => clearInterval(id);
  }, []);

  const waitMs  = nowMs - startedAt;
  const level   = replySlaLevel(waitMs, maxReplyTimeMs);
  const elapsed = formatElapsed(waitMs);
  const limitFmt = formatElapsed(maxReplyTimeMs);

  const { t } = useTranslation('agentAssist');
  return (
    <span
      className={`flex items-center gap-1 px-2 py-0.5 rounded-full border text-xs flex-shrink-0 ${REPLY_SLA_CLS[level]}`}
      title={t('actionBar.replySlaTitle', { elapsed, limit: limitFmt })}
    >
      <MessageSquare className="w-3 h-3" aria-hidden="true" /> {elapsed}
    </span>
  );
};

// ── Handle-time counter ───────────────────────────────────────────────────────
const HandleTimer: React.FC<{ startedAt: Date }> = ({ startedAt }) => {
  const { t } = useTranslation('agentAssist');
  const [ms, setMs] = useState(Date.now() - startedAt.getTime());
  useEffect(() => {
    const id = setInterval(() => setMs(Date.now() - startedAt.getTime()), 1_000);
    return () => clearInterval(id);
  }, [startedAt]);

  return (
    <span
      className={`flex items-center gap-1 text-xs font-mono tabular-nums font-semibold
        ${ms >= 30 * 60_000 ? "text-warning" : "text-muted"}`}
      title={t('actionBar.handleTimeTitle')}
    >
      <Timer className="w-3 h-3" aria-hidden="true" /> {formatElapsed(ms)}
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
  const { t } = useTranslation('agentAssist');
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
        title={t('actionBar.transfer')}
        className="px-2.5 py-1 rounded text-xs font-medium border transition-colors
          text-warning-text bg-warning-light border-warning/30 hover:bg-warning/20 hover:border-warning/50
          disabled:opacity-40 disabled:cursor-not-allowed"
      >
        ↗ {t('actionBar.transfer')} {escalations.length > 0 ? "▾" : ""}
      </button>

      {open && dropPos && createPortal(
        <div
          ref={dropRef}
          style={{ position: "fixed", top: dropPos.top, left: dropPos.left }}
          className="z-dropdown bg-white border border-border rounded-lg shadow-modal
            min-w-[200px] overflow-hidden"
        >
          <div className="px-2.5 py-1.5 text-2xs font-bold text-muted uppercase tracking-wide border-b border-border">
            {t('actionBar.transferTo')}
          </div>

          {escalations.length === 0 ? (
            <div className="px-3 py-2.5 text-xs text-muted text-center">
              {t('actionBar.noDestinations')}
            </div>
          ) : (
            escalations.map(esc => (
              <button
                key={esc.pool_id}
                onClick={() => { onTransferTo(esc.pool_id); setDropPos(null); }}
                className="w-full text-left px-3 py-2 hover:bg-warning-light transition-colors
                  border-b border-border last:border-0"
              >
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold text-dark">
                    {esc.pool_id.replace(/_humano|_ia|_v\d+/gi, "").replace(/_/g, " ")}
                  </span>
                  {esc.recommended && (
                    <span className="text-micro px-1.5 py-0.5 rounded bg-green-light
                      text-green-text border border-green/30 font-medium ml-2">
                      {t('actionBar.recommended')}
                    </span>
                  )}
                </div>
                <div className="text-2xs text-muted mt-0.5 flex gap-2">
                  {esc.reason && <span className="truncate">{esc.reason}</span>}
                  {esc.estimated_wait_s != null && esc.estimated_wait_s > 0 && (
                    <span className="flex-shrink-0 text-muted-light">
                      {t('actionBar.waitEstimate', { min: Math.round(esc.estimated_wait_s / 60) })}
                    </span>
                  )}
                </div>
                <div className="text-micro text-muted-light font-mono mt-0.5">
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

// ── Props ─────────────────────────────────────────────────────────────────────
export interface ActionBarProps {
  contact:                  ContactSession | null;
  onEncerrar:               () => void;
  /** Called when operator selects a pool from the TransferCombo */
  onTransferTo?:            (poolId: string) => void;
  onDesligar?:              () => void;
  /**
   * substitutionMode / onToggleSubstitutionMode: kept in props so the parent's
   * state and menu-card rendering still work. The toggle button lives in
   * Aba Ações (console-acoes-tab) — not rendered here.
   */
  substitutionMode?:        boolean;
  onToggleSubstitutionMode?: () => void;
  // Removed (console-acoes-tab): mentionableJourneys + onIniciarProcesso
  // → Processes fully covered by Aba Ações (right panel Processos mode).
  // Removed (Fase E): mentionableAgents, onAddSpecialist, selectedCount, onDelegar
  // → Agents fully covered by Aba Ações (right panel Agentes mode).
}

// ── Main component ─────────────────────────────────────────────────────────────
export const ActionBar: React.FC<ActionBarProps> = ({
  contact,
  onEncerrar,
  onTransferTo,
  onDesligar,
  // substitutionMode and onToggleSubstitutionMode intentionally unused here —
  // button lives in Aba Ações (console-acoes-tab).
}) => {
  const { t } = useTranslation('agentAssist');

  if (!contact) {
    return (
      <div className="flex-1 bg-white flex items-center px-4 gap-2">
        <span className="text-sm text-muted-light select-none">—</span>
        <span className="text-xs text-muted">{t('actionBar.selectContact')}</span>
      </div>
    );
  }

  const sentimentScore = contact.supervisorState?.sentiment.current ?? null;

  return (
    <div className={`flex-1 flex items-center gap-2 px-3
      ${contact.sessionClosed ? "bg-warning-light" : "bg-white"}`}
    >
      {/* ── Handle timer ── */}
      <HandleTimer startedAt={contact.sessionStartedAt} />

      {/* ── Reply-SLA chip — only shown while timer is counting + pool has reply limit ── */}
      {!contact.sessionClosed
        && contact.responseTimer.status === 'counting'
        && contact.maxReplyTimeMs
        && (
          <ReplySlaChip
            startedAt={contact.responseTimer.startedAt}
            maxReplyTimeMs={contact.maxReplyTimeMs}
          />
        )
      }

      {/* ── Sentiment chip (only when session open and data available) ── */}
      {!contact.sessionClosed && sentimentScore !== null && (
        <SentimentChip score={sentimentScore} />
      )}

      {/* ── Divider ── */}
      <div className="w-px h-5 bg-border flex-shrink-0 mx-1" />

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
          title={t('input.hangup')}
          className="px-2.5 py-1 rounded text-xs font-medium border transition-colors
            text-red-text bg-red-light border-red/30 hover:bg-red/10 hover:border-red/50
            disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {t('input.hangup')}
        </button>

        {/* Iniciar Processo removed — moved to Aba Ações (console-acoes-tab) */}
      </div>

      {/* ── Spacer ── */}
      <div className="flex-1" />

      {/* ── Session-closed banner ── */}
      {contact.sessionClosed && (
        <span className="text-xs text-warning-text font-medium">
          {t('actionBar.sessionClosed')}
        </span>
      )}

      {/* ── Encerrar ── */}
      <button
        onClick={onEncerrar}
        title={t('input.close')}
        className="ml-2 px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors flex-shrink-0
          bg-red text-white hover:bg-red-text border border-red shadow-card"
      >
        {t('input.close')}
      </button>
    </div>
  );
};
