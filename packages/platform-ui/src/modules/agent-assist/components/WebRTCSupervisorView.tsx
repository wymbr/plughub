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
import { Loader2, EyeOff } from "lucide-react";

import { useWebRTCSession } from "../hooks/useWebRTCSession";
import { VideoGrid }        from "./VideoGrid";

const SUPERVISOR_IDENTITY = "supervisor_view";

interface WebRTCSupervisorViewProps {
  sessionId: string;
  channel:   string;
  /** Optional compact mode (smaller grid cells) */
  compact?:  boolean;
}

export const WebRTCSupervisorView: React.FC<WebRTCSupervisorViewProps> = ({
  sessionId,
  channel,
  compact = false,
}) => {
  const { t } = useTranslation("webrtc");

  const {
    room,
    medium,
    remoteTracks,
    connecting,
    error,
  } = useWebRTCSession(sessionId, SUPERVISOR_IDENTITY, channel);

  if (channel !== "webrtc") return null;
  if (medium === "text")     return null;

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

  if (medium === "video") {
    return (
      <div className="flex flex-col gap-1">
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
