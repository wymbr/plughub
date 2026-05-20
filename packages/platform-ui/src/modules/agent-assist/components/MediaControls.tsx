/**
 * MediaControls
 *
 * Bottom control bar for the WebRTC overlay.
 * Renders mic / camera toggles and a disconnect button.
 * Camera toggle is hidden when medium is "voice".
 */

import React from "react";
import { useTranslation } from "react-i18next";
import {
  Mic,
  MicOff,
  Video,
  VideoOff,
  PhoneOff,
} from "lucide-react";
import type { NegotiatedMedium } from "../hooks/useWebRTCSession";

interface MediaControlsProps {
  medium:       NegotiatedMedium;
  micMuted:     boolean;
  cameraOff:    boolean;
  onToggleMic:  () => void;
  onToggleCam:  () => void;
  onDisconnect: () => void;
}

const ControlButton: React.FC<{
  label: string;
  active?: boolean;
  danger?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}> = ({ label, active = true, danger = false, onClick, children }) => (
  <button
    type="button"
    aria-label={label}
    onClick={onClick}
    className={[
      "flex items-center justify-center w-10 h-10 rounded-full transition-colors",
      danger
        ? "bg-red-600 hover:bg-red-700 text-white"
        : active
          ? "bg-white/10 hover:bg-white/20 text-white"
          : "bg-red-500/80 hover:bg-red-500 text-white",
    ].join(" ")}
  >
    {children}
  </button>
);

export const MediaControls: React.FC<MediaControlsProps> = ({
  medium,
  micMuted,
  cameraOff,
  onToggleMic,
  onToggleCam,
  onDisconnect,
}) => {
  const { t } = useTranslation("webrtc");

  return (
    <div className="flex items-center justify-center gap-4 py-3 px-4
                    bg-gray-900/90 backdrop-blur-sm">

      {/* Medium badge */}
      <span className="text-xs text-gray-400 font-medium uppercase tracking-widest mr-2 select-none">
        {t(`medium.${medium}`)}
      </span>

      {/* Mic toggle */}
      <ControlButton
        label={micMuted ? t("controls.unmute") : t("controls.mute")}
        active={!micMuted}
        onClick={onToggleMic}
      >
        {micMuted ? <MicOff size={18} /> : <Mic size={18} />}
      </ControlButton>

      {/* Camera toggle (video only) */}
      {medium === "video" && (
        <ControlButton
          label={cameraOff ? t("controls.cameraOn") : t("controls.cameraOff")}
          active={!cameraOff}
          onClick={onToggleCam}
        >
          {cameraOff ? <VideoOff size={18} /> : <Video size={18} />}
        </ControlButton>
      )}

      {/* Disconnect */}
      <ControlButton
        label={t("controls.disconnect")}
        danger
        onClick={onDisconnect}
      >
        <PhoneOff size={18} />
      </ControlButton>
    </div>
  );
};
