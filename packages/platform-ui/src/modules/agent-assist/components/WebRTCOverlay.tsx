/**
 * WebRTCOverlay
 *
 * Conditional container rendered above the ChatArea when the selected
 * session uses the "webrtc" channel. Adapts its content to the negotiated
 * medium:
 *
 *   video → VideoGrid (2-up) + transcript + MediaControls
 *   voice → AnimatedWaveform + transcript + MediaControls
 *   text  → No overlay; normal Console layout used as-is.
 *
 * This component owns the LiveKit connection lifecycle via useWebRTCSession.
 * When dismissed (disconnect button) it collapses to nothing until the
 * session changes again.
 */

import React, { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { apiFetch } from "@/api/apiFetch";
import { AlertTriangle, Loader2, Lock } from "lucide-react";

import { hasMedia, useWebRTCSession } from "../hooks/useWebRTCSession";
import { VideoGrid }        from "./VideoGrid";
import { RemoteAudio }      from "./RemoteAudio";
import { MediaControls }    from "./MediaControls";

// ── Animated waveform (voice medium) ───────────────────────────────────────
const BAR_COUNT = 20;

const AnimatedWaveform: React.FC<{ active: boolean }> = ({ active }) => (
  <div
    className="flex items-center justify-center gap-[3px] h-16 py-2"
    role="img"
    aria-label="Audio waveform"
  >
    {Array.from({ length: BAR_COUNT }).map((_, i) => (
      <div
        key={i}
        className={[
          "w-1 rounded-full transition-transform",
          active
            ? "bg-primary animate-[waveform_0.8s_ease-in-out_infinite_alternate]"
            : "bg-white/30 h-2",
        ].join(" ")}
        style={
          active
            ? {
                animationDelay: `${(i * 40) % 800}ms`,
                height: "100%",
              }
            : undefined
        }
      />
    ))}
  </div>
);

// ── Main overlay ────────────────────────────────────────────────────────────
interface WebRTCOverlayProps {
  sessionId:    string;
  channel:      string;
  /** JWT identity used for LiveKit participant label */
  agentIdentity: string;
  /** WCH-01 — chamada presa a um contato de chat (evento `media.call`) */
  callActive?:  boolean;
}

export const WebRTCOverlay: React.FC<WebRTCOverlayProps> = ({
  sessionId,
  channel,
  agentIdentity,
  callActive = false,
}) => {
  const { t } = useTranslation("webrtc");

  const {
    room,
    view,
    publish,
    remoteTracks,
    localTracks,
    connecting,
    error,
    micMuted,
    cameraOff,
    toggleMic,
    toggleCamera,
    disconnect,
    audioBlocked,
    startAudio,
    mediaHold,
  } = useWebRTCSession(sessionId, agentIdentity, channel, "agent", callActive);

  // WCH-07: numa chamada presa a um contato de chat, o botão vermelho DESLIGA A CHAMADA para
  // todos (o contato segue). O gateway grava o pedido e o observador da chamada a encerra; a
  // sobreposição sai quando chega o `media.call ended`, como no desligar do cliente.
  const isChatCall = channel !== "webrtc" && callActive;
  const [hangingUp,   setHangingUp]   = useState(false);
  const [hangupError, setHangupError] = useState<string | null>(null);
  const hangupCall = async () => {
    setHangingUp(true);
    setHangupError(null);
    try {
      const r = await apiFetch(`/webrtc/call/${encodeURIComponent(sessionId)}/end`, { method: "POST" });
      if (!r.ok) {
        let code = `HTTP ${r.status}`;
        try {
          const body = await r.json() as { detail?: { code?: string } | string };
          code = typeof body.detail === "string" ? body.detail : (body.detail?.code ?? code);
        } catch { /* corpo ilegível: fica o status */ }
        console.error(`[webrtc] desligar a chamada RECUSADO (session=${sessionId}): ${code}`);
        setHangupError(code);
      }
    } catch (e) {
      const motivo = e instanceof Error ? e.message : String(e);
      console.error(`[webrtc] desligar a chamada FALHOU (session=${sessionId}): ${motivo}`);
      setHangupError(motivo);
    } finally {
      setHangingUp(false);
    }
  };

  // Duration timer
  const [elapsedSec, setElapsedSec] = useState(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (room) {
      setElapsedSec(0);
      timerRef.current = setInterval(() => setElapsedSec(s => s + 1), 1000);
    } else {
      if (timerRef.current) clearInterval(timerRef.current);
    }
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [room]);

  const formatDuration = (sec: number) => {
    const m = Math.floor(sec / 60).toString().padStart(2, "0");
    const s = (sec % 60).toString().padStart(2, "0");
    return `${m}:${s}`;
  };

  if (!hasMedia(channel, callActive)) return null;

  // ── Loading ──────────────────────────────────────────────────────────────
  // VOZ-04: "conectando" e "erro" vêm ANTES do teto. Enquanto o token não chega os tetos
  // são vazios, e o `view === "none"` escondia justamente estes dois estados — a falha do
  // token nunca aparecia na tela.
  // NIV-07: pausa de mídia — o cliente está teclando um dado protegido no telefone. Vem
  // antes do "conectando", que também é verdade durante a pausa mas não diz por quê.
  if (mediaHold) {
    return (
      <div className="flex items-center gap-2 py-3 px-4 mx-2 mt-2
                      bg-warning-light border border-warning/30 rounded-lg text-warning-text text-sm">
        <Lock size={16} className="flex-shrink-0" />
        <span>{t("overlay.mediaHold")}</span>
      </div>
    );
  }

  if (connecting) {
    return (
      <div className="flex items-center justify-center gap-2 py-4
                      bg-dark text-white/80 text-sm rounded-lg mx-2 mt-2">
        <Loader2 size={16} className="animate-spin" />
        {t("overlay.connecting")}
      </div>
    );
  }

  // ── Error ────────────────────────────────────────────────────────────────
  if (error) {
    return (
      <div className="flex items-center gap-2 py-3 px-4 mx-2 mt-2
                      bg-red-light border border-red/30 rounded-lg text-red-text text-sm">
        <AlertTriangle size={16} className="flex-shrink-0" />
        <span>{t("overlay.error", { message: error })}</span>
      </div>
    );
  }

  // Nada a mostrar quando nenhum teto (meu ou do cliente) inclui mídia
  if (view === "none") return null;

  // ── Connected ────────────────────────────────────────────────────────────
  return (
    <div className="flex flex-col bg-dark rounded-lg mx-2 mt-2 overflow-hidden shadow-xl">

      {/* Status bar */}
      <div className="flex items-center justify-between px-3 py-1.5
                      bg-black/30 border-b border-white/5">
        <div className="flex items-center gap-2">
          <span
            className={`w-2 h-2 rounded-full flex-shrink-0 ${
              room ? "bg-green animate-pulse" : "bg-muted"
            }`}
          />
          <span className="text-xs text-white/80 font-medium">
            {room ? t("overlay.connected") : t("overlay.waitingRoom")}
          </span>
        </div>
        {room && (
          <span className="text-xs text-white/50 tabular-nums">
            {formatDuration(elapsedSec)}
          </span>
        )}
      </div>

      {/* Áudio remoto — sem isto o agente vê o cliente e não o ouve */}
      <RemoteAudio remoteTracks={remoteTracks} />
      {audioBlocked && (
        <button
          type="button"
          onClick={() => { void startAudio(); }}
          className="mx-3 mt-2 py-1.5 rounded-md bg-warning text-white text-xs font-medium"
        >
          {t("overlay.enableAudio")}
        </button>
      )}

      {/* Media area */}
      {view === "video" ? (
        <VideoGrid remoteTracks={remoteTracks} localTracks={localTracks} />
      ) : (
        /* voice medium — animated waveform */
        <div className="py-4 px-6 bg-black/20">
          <AnimatedWaveform active={!!room && !micMuted} />
          <p className="text-center text-xs text-white/50 mt-1 select-none">
            {micMuted ? t("overlay.micMuted") : t("overlay.listening")}
          </p>
        </div>
      )}

      {/* Controls */}
      <MediaControls
        canVideo={publish.includes("video")}
        micMuted={micMuted}
        cameraOff={cameraOff}
        onToggleMic={toggleMic}
        onToggleCam={toggleCamera}
        onDisconnect={disconnect}
        onHangupCall={isChatCall ? () => { void hangupCall(); } : undefined}
        hangingUp={hangingUp}
      />
      {hangupError && (
        <p className="px-3 pb-2 text-xs text-red-light">{t("overlay.hangupFailed", { reason: hangupError })}</p>
      )}
    </div>
  );
};
