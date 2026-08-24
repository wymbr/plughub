/**
 * ContactList
 * Left column listing active contacts, sorted by arrival time (FIFO — oldest first).
 *
 * Each row shows:
 *   - Channel icon
 *   - ANI / user_id (contactId) or short session_id fallback
 *   - Unread badge
 *   - AI-typing indicator
 *   - Segment timer (from sessionStartedAt = `assigned_at` do servidor)
 *   - (a mini-barra de SLA foi REMOVIDA na D14.1 — ver o bloco de comentário abaixo)
 *   - Sentiment dot
 *   - Red tint + "encerrado" when session is closed
 */

import React, { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Timer, Sparkles, Clock, MessageSquare, CheckCheck } from "lucide-react";
import { ContactSession, ResponseTimer } from "../types";
import { poolDisplayLabel } from "../poolLabel";

interface ContactListProps {
  contacts:          ContactSession[];
  selectedSessionId: string | null;
  aiTypingSessions:  Set<string>;
  onSelect:          (sessionId: string) => void;
}

// ── Channel icons ──────────────────────────────────────────────────────────────
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

function channelIcon(channel: string): string {
  return CHANNEL_ICON[channel] ?? "💬";
}

// ── Sentiment colour ───────────────────────────────────────────────────────────
function sentimentColor(score: number | null): string {
  if (score === null) return "bg-border-strong";
  if (score >= 0.3)   return "bg-green";
  if (score >= -0.3)  return "bg-warning";
  if (score >= -0.6)  return "bg-contested";
  return "bg-red";
}

// ── SLA REMOVIDO (D14.1, 2026-08-24) ───────────────────────────────────────────
//
// A barra de SLA e a cor de urgência da borda saíram daqui, e a razão não é
// estética: **não existe alvo de SLA de atendimento por segmento neste produto**.
// `sla_target_ms` é alvo de ESPERA em fila (decisão D14.1) e a espera já acabou
// quando o contato aparece nesta lista — normalizar o tempo de um contato já
// atendido por um alvo de fila não mede nada.
//
// E não era só semântica: os dois indicadores liam `supervisorState.sla`, que o
// endpoint HTTP devolvia como CONSTANTES (`server.ts`: `target_ms: 480_000`,
// `percentage: 0`, `breach_imminent: false`). Efeitos medidos antes da remoção:
// todo contato normalizado por 8 min independentemente do pool, e o `??` do
// `breach_imminent` nunca caindo fora — um `false` do produtor desarmando a
// guarda que esta tela já tinha.
//
// O que SOBROU e mede de verdade: o timer de resposta (💬, colorido por
// `max_reply_time_ms`, que é campo real e por-mensagem) e o relógio do segmento
// (⏱), agora ancorado no `assigned_at` do servidor.
//
// NÃO reintroduzir uma barra aqui sem antes existir um campo de alvo — a versão
// anterior parecia informação e era uma constante pintada.

// ── Elapsed time ───────────────────────────────────────────────────────────────
function formatElapsed(ms: number): string {
  const totalSec = Math.floor(ms / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  if (h > 0) return `${h}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

// ── Response-wait indicator ────────────────────────────────────────────────────
// When the pool has max_reply_time_ms configured, thresholds are ratio-based:
//   ≥ 100% of limit → urgent  |  ≥ 50% → attention  |  < 50% → normal
// Fallback (no limit configured): 180 s = urgent, 60 s = attention.
type WaitLevel = "normal" | "attention" | "urgent";

function waitLevel(waitMs: number, maxReplyTimeMs: number | null): WaitLevel {
  if (maxReplyTimeMs) {
    const ratio = waitMs / maxReplyTimeMs;
    if (ratio >= 1.0) return "urgent";
    if (ratio >= 0.5) return "attention";
    return "normal";
  }
  if (waitMs >= 180_000) return "urgent";
  if (waitMs >= 60_000)  return "attention";
  return "normal";
}

const WAIT_COLOR: Record<WaitLevel, string> = {
  normal:    "text-green-text",
  attention: "text-warning-text",
  urgent:    "text-red-text font-bold animate-pulse",
};

// ── Display identity: prefer contactId (ANI/user_id), fallback to short sessionId ──
function displayId(contact: ContactSession): string {
  if (contact.contactId) return contact.contactId;
  return contact.sessionId.slice(0, 8);
}

// ── Single contact row ─────────────────────────────────────────────────────────
interface RowProps {
  contact:  ContactSession;
  selected: boolean;
  aiTyping: boolean;
  onSelect: () => void;
}

const ContactRow: React.FC<RowProps> = ({ contact, selected, aiTyping, onSelect }) => {
  const { t } = useTranslation('agentAssist');
  const [nowMs, setNowMs] = useState<number>(Date.now());

  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 1_000);
    return () => clearInterval(id);
  }, []);

  // Tempo NESTE SEGMENTO — desde que o contato foi atribuído a este agente.
  // `sessionStartedAt` vem do `assigned_at` do servidor (ver AgentAssistContext);
  // antes era `new Date()` do navegador e ZERAVA no F5.
  const handleMs    = nowMs - contact.sessionStartedAt.getTime();
  const sentimentScore = contact.supervisorState?.sentiment.current ?? null;

  // Tab visual: selected row bleeds right (box-shadow covers the container's right border)
  // creating the illusion of a browser tab extending into the white central surface.
  //
  // D14.1 — a borda deixou de codificar urgência de SLA (ver o bloco no topo). Ela
  // volta a ser só SELEÇÃO × ENCERRADO, que são os dois estados que a tela de fato
  // conhece. Pintar urgência exigiria um alvo, e não existe alvo de atendimento.
  const borderAccent = contact.sessionClosed
    ? (selected ? "#ef4444" : "#fca5a5")
    : (selected ? "#1B4F8A" : "transparent");

  const selectedStyle: React.CSSProperties = selected
    ? {
        backgroundColor: "#ffffff",
        // 2px white shadow to the right covers the container border at this row's height
        boxShadow: "2px 0 0 0 #ffffff",
        position:  "relative",
        zIndex:    1,
      }
    : {};

  return (
    <button
      onClick={onSelect}
      style={selectedStyle}
      className={`w-full text-left px-3 py-2.5 border-b transition-colors
        focus:outline-none focus:ring-inset focus:ring-1 focus:ring-primary
        border-l-[3px]
        ${contact.sessionClosed
          ? `${selected ? "bg-white" : "bg-red-light hover:bg-red/10"} border-b-red/20`
          : `${selected ? "bg-white" : "bg-transparent hover:bg-white/60"} border-b-border`
        }
      `}
      // Inline left-border colour (urgency or selection)
      ref={el => {
        if (el) el.style.borderLeftColor = borderAccent;
      }}
    >
      {/* Row 1: channel icon + identity + pool badge + unread badge */}
      <div className="flex items-center gap-1.5 min-w-0">
        <span
          className="text-base leading-none flex-shrink-0"
          title={contact.sessionClosed ? t('contactList.sessionClosed') : contact.channel}
        >
          {contact.sessionClosed ? "🔴" : channelIcon(contact.channel)}
        </span>
        <span
          className="flex-1 text-xs font-medium text-dark truncate font-mono"
          title={contact.contactId ?? contact.sessionId}
        >
          {displayId(contact)}
        </span>
        {contact.poolId && (
          /* D3 — chip E tooltip pelo rótulo de exibição. O `title` cru era o último
             lugar onde o id do espelho (`retencao_humano-int`) ainda vazava: o chip
             trunca em 72px e escondia a diferença, então só o hover a revelava. */
          <span
            className="flex-shrink-0 text-2xs text-primary bg-primary-light border border-primary/20
              px-1 py-0.5 rounded truncate max-w-[72px]"
            title={poolDisplayLabel(contact.poolId, t)}
          >
            {poolDisplayLabel(contact.poolId, t).replace(/_/g, " ")}
          </span>
        )}
        {contact.unreadCount > 0 && (
          <span className="flex-shrink-0 min-w-[1.25rem] h-5 rounded-full bg-primary
            text-white text-2xs font-bold flex items-center justify-center px-1">
            {contact.unreadCount > 99 ? "99+" : contact.unreadCount}
          </span>
        )}
      </div>

      {/* Row 2: 💬 response-wait + timer + SLA bar + ai typing + enc badge */}
      <div className="flex items-center gap-1.5 mt-1">

        {/* Response timer — shows agent's reply obligation.
            counting (orange/red) = agent owes a reply, live counter.
            frozen   (green ✓)   = agent replied; shows how long the reply took. */}
        {!contact.sessionClosed && (() => {
          const timer: ResponseTimer = contact.responseTimer;
          if (timer.status === 'frozen') {
            return (
              <span
                className="text-xs font-mono tabular-nums flex-shrink-0 text-green-text"
                title={t('contactList.responded')}
              >
                <CheckCheck className="w-3 h-3 inline-block mr-0.5" aria-hidden="true" />
                {formatElapsed(timer.elapsedMs)}
              </span>
            );
          }
          // counting — live counter
          const waitMs = nowMs - timer.startedAt;
          const wLevel = waitLevel(waitMs, contact.maxReplyTimeMs);
          return (
            <span
              className={`text-xs font-mono tabular-nums flex-shrink-0 ${WAIT_COLOR[wLevel]}`}
              title={t('contactList.awaitingResponse')}
            >
              <MessageSquare className="w-3 h-3 inline-block mr-0.5" aria-hidden="true" />
              {formatElapsed(waitMs)}
            </span>
          );
        })()}

        {/* Relógio do SEGMENTO — tempo desde a atribuição a este agente.
            Sem cor derivada de alvo: não existe alvo de atendimento (D14.1). */}
        <span
          className="inline-flex items-center gap-0.5 text-xs font-mono tabular-nums flex-shrink-0
            text-muted-light"
          title={t('contactList.segmentTime')}
        >
          <Timer className="w-3 h-3" aria-hidden="true" />{formatElapsed(handleMs)}
        </span>

        {/* Espaçador — ocupava a barra de SLA removida na D14.1. */}
        <span className="flex-1" />

        {aiTyping && (
          <span className="flex-shrink-0 animate-pulse text-ai" title={t('contactList.aiTyping')}>
            <Sparkles className="w-3 h-3" aria-hidden="true" />
          </span>
        )}

        {contact.sessionClosed && (
          <span className="flex-shrink-0 text-2xs bg-red-light text-red-text font-semibold
            px-1.5 py-0.5 rounded border border-red">
            {t('contactList.ended')}
          </span>
        )}
      </div>
    </button>
  );
};

// ── Main component ─────────────────────────────────────────────────────────────
export const ContactList: React.FC<ContactListProps> = ({
  contacts,
  selectedSessionId,
  aiTypingSessions,
  onSelect,
}) => {
  const { t } = useTranslation('agentAssist');
  // FIFO: oldest sessionStartedAt first; closed contacts always last
  const sorted = [...contacts].sort((a, b) => {
    if (a.sessionClosed !== b.sessionClosed) return a.sessionClosed ? 1 : -1;
    return a.sessionStartedAt.getTime() - b.sessionStartedAt.getTime();
  });

  return (
    <div className="flex flex-col h-full bg-surface-alt">
      {/* Rows — header is rendered in the shared sub-header row of AgentAssistPage */}
      {contacts.length === 0 ? (
        <div className="flex-1 flex flex-col items-center justify-center gap-2 text-muted p-4">
          <Clock className="w-8 h-8 text-muted-light" aria-hidden="true" />
          <p className="text-xs text-center leading-snug">
            {t('empty.waitingForContact')}
          </p>
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto">
          {sorted.map(contact => (
            <ContactRow
              key={contact.sessionId}
              contact={contact}
              selected={contact.sessionId === selectedSessionId}
              aiTyping={aiTypingSessions.has(contact.sessionId)}
              onSelect={() => onSelect(contact.sessionId)}
            />
          ))}
        </div>
      )}
    </div>
  );
};
