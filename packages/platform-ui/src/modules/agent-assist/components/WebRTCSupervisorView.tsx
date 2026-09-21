/**
 * WebRTCSupervisorView
 *
 * Read-only subscriber view for supervisors watching a WebRTC session
 * from the Monitor tab. Connects to the LiveKit room as a hidden subscriber
 * (no local tracks published) and renders the remote video/audio.
 *
 * Usage:
 *   <WebRTCSupervisorView sessionId={session.session_id} channel={session.channel} />
 *
 * Automatically disconnects when sessionId changes or the component unmounts.
 */

import React from "react";
import { useTranslation } from "react-i18next";
import { Loader2, EyeOff, Lock } from "lucide-react";

import { hasMedia, useWebRTCSession } from "../hooks/useWebRTCSession";
import { VideoGrid }        from "./VideoGrid";
import { RemoteAudio }      from "./RemoteAudio";

const SUPERVISOR_IDENTITY = "supervisor_view";

interface WebRTCSupervisorViewProps {
  sessionId: string;
  channel:   string;
  /** Optional compact mode (smaller grid cells) */
  compact?:  boolean;
  /** WCH-02 — chamada presa a um contato de chat (último `media.call` do stream) */
  callActive?: boolean;
}

export const WebRTCSupervisorView: React.FC<WebRTCSupervisorViewProps> = ({
  sessionId,
  channel,
  compact = false,
  callActive = false,
}) => {
  const { t } = useTranslation("webrtc");

  const {
    room,
    view,
    remoteTracks,
    connecting,
    error,
    audioBlocked,
    startAudio,
    mediaHold,
    ended,
  } = useWebRTCSession(sessionId, SUPERVISOR_IDENTITY, channel, "supervisor", callActive);
  // ⚠️ O papel "supervisor" é o que dá token OCULTO e sem publicação. Até a VOZ-09 esta
  // visão pedia `role=agent` (o hook não recebia papel) e entraria na sala publicando.

  if (!hasMedia(channel, callActive)) return null;
  // VOZ-38: sem isto a visão ficava em "Aguardando vídeo" numa sala vazia depois do fim
  if (ended) {
    return (
      <div className="flex items-center gap-1.5 p-2 text-muted text-xs">
        <EyeOff size={14} />
        {t("supervisor.ended")}
      </div>
    );
  }
  // NIV-07: vem antes do `view`, que fica "none" enquanto não há token
  if (mediaHold) {
    return (
      <div className="flex items-center gap-1.5 p-2 text-warning-text text-xs">
        <Lock size={14} />
        {t("supervisor.mediaHold")}
      </div>
    );
  }
  if (view === "none")       return null;

  if (connecting) {
    return (
      <div className="flex items-center justify-center gap-2 p-4 text-muted text-xs">
        <Loader2 size={14} className="animate-spin" />
        {t("supervisor.connecting")}
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center gap-1.5 p-2 text-red-600 text-xs">
        <EyeOff size={14} />
        {t("supervisor.error")}
      </div>
    );
  }

  // Supervisão é ESCUTA: o áudio remoto toca nos dois modos (vídeo e voz).
  const audio = (
    <>
      <RemoteAudio remoteTracks={remoteTracks} />
      {audioBlocked && (
        <button
          type="button"
          onClick={() => { void startAudio(); }}
          className="mx-2 mt-1 py-1 rounded bg-warning text-white text-xs font-medium"
        >
          {t("overlay.enableAudio")}
        </button>
      )}
    </>
  );

  if (view === "video") {
    return (
      <div className="flex flex-col gap-1">
        {audio}
        <div className="flex items-center gap-1.5 px-2 pt-2">
          <span
            className={`w-1.5 h-1.5 rounded-full ${
              room ? "bg-green-400 animate-pulse" : "bg-gray-300"
            }`}
          />
          <span className="text-xs text-muted font-medium">
            {room ? t("supervisor.watching") : t("supervisor.waitingRoom")}
          </span>
        </div>
        <VideoGrid remoteTracks={remoteTracks} localTracks={[]} compact={compact} />
      </div>
    );
  }

  // voice — simpler waveform indicator for supervisor
  return (
    <div className="flex items-center gap-2 px-3 py-2">
      {audio}
      <span
        className={`w-2 h-2 rounded-full flex-shrink-0 ${
          room ? "bg-green-400 animate-pulse" : "bg-gray-400"
        }`}
      />
      <span className="text-xs text-muted">
        {room ? t("supervisor.voiceActive") : t("supervisor.waitingRoom")}
      </span>
    </div>
  );
};
