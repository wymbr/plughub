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
import { AlertTriangle, Loader2 } from "lucide-react";

import { useWebRTCSession } from "../hooks/useWebRTCSession";
import { VideoGrid }        from "./VideoGrid";
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
            : "bg-gray-400 h-2",
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
}

export const WebRTCOverlay: React.FC<WebRTCOverlayProps> = ({
  sessionId,
  channel,
  agentIdentity,
}) => {
  const { t } = useTranslation("webrtc");

  const {
    room,
    medium,
    remoteTracks,
    localTracks,
    connecting,
    error,
    micMuted,
    cameraOff,
    toggleMic,
    toggleCamera,
    disconnect,
  } = useWebRTCSession(sessionId, agentIdentity, channel);

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

  // Nothing to show for text medium or non-webrtc channel
  if (channel !== "webrtc" || medium === "text") return null;

  // ── Loading ──────────────────────────────────────────────────────────────
  if (connecting) {
    return (
      <div className="flex items-center justify-center gap-2 py-4
                      bg-gray-900 text-gray-300 text-sm rounded-lg mx-2 mt-2">
        <Loader2 size={16} className="animate-spin" />
        {t("overlay.connecting")}
      </div>
    );
  }

  // ── Error ────────────────────────────────────────────────────────────────
  if (error) {
    return (
      <div className="flex items-center gap-2 py-3 px-4 mx-2 mt-2
                      bg-red-50 border border-red-200 rounded-lg text-red-700 text-sm">
        <AlertTriangle size={16} className="flex-shrink-0" />
        <span>{t("overlay.error", { message: error })}</span>
      </div>
    );
  }

  // ── Connected ────────────────────────────────────────────────────────────
  return (
    <div className="flex flex-col bg-gray-950 rounded-lg mx-2 mt-2 overflow-hidden shadow-xl">

      {/* Status bar */}
      <div className="flex items-center justify-between px-3 py-1.5
                      bg-gray-900/80 border-b border-white/5">
        <div className="flex items-center gap-2">
          <span
            className={`w-2 h-2 rounded-full flex-shrink-0 ${
              room ? "bg-green-400 animate-pulse" : "bg-gray-500"
            }`}
          />
          <span className="text-xs text-gray-300 font-medium">
            {room ? t("overlay.connected") : t("overlay.waitingRoom")}
          </span>
        </div>
        {room && (
          <span className="text-xs text-gray-500 tabular-nums">
            {formatDuration(elapsedSec)}
          </span>
        )}
      </div>

      {/* Media area */}
      {medium === "video" ? (
        <VideoGrid remoteTracks={remoteTracks} localTracks={localTracks} />
      ) : (
        /* voice medium — animated waveform */
        <div className="py-4 px-6 bg-gray-900">
          <AnimatedWaveform active={!!room && !micMuted} />
          <p className="text-center text-xs text-gray-500 mt-1 select-none">
            {micMuted ? t("overlay.micMuted") : t("overlay.listening")}
          </p>
        </div>
      )}

      {/* Controls */}
      <MediaControls
        medium={medium}
        micMuted={micMuted}
        cameraOff={cameraOff}
        onToggleMic={toggleMic}
        onToggleCam={toggleCamera}
        onDisconnect={disconnect}
      />
    </div>
  );
};
