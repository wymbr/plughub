/**
 * MediaControls
 *
 * Bottom control bar for the WebRTC overlay.
 * Renders mic / camera toggles and a disconnect button.
 * Camera toggle is shown only when the operator's own ceiling includes video (VOZ-09).
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
interface MediaControlsProps {
  /** Meu teto inclui vídeo — só então há câmera a ligar/desligar (VOZ-09) */
  canVideo:     boolean;
  micMuted:     boolean;
  cameraOff:    boolean;
  onToggleMic:  () => void;
  onToggleCam:  () => void;
  onDisconnect: () => void;
  /** WCH-07 — chamada presa a contato de chat: o botão vermelho DESLIGA a chamada para todos */
  onHangupCall?: () => void;
  hangingUp?:    boolean;
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
        ? "bg-red hover:bg-red-text text-white"
        : active
          ? "bg-white/10 hover:bg-white/20 text-white"
          : "bg-red/80 hover:bg-red text-white",
    ].join(" ")}
  >
    {children}
  </button>
);

export const MediaControls: React.FC<MediaControlsProps> = ({
  canVideo,
  micMuted,
  cameraOff,
  onToggleMic,
  onToggleCam,
  onDisconnect,
  onHangupCall,
  hangingUp = false,
}) => {
  const { t } = useTranslation("webrtc");

  return (
    <div className="flex items-center justify-center gap-4 py-3 px-4
                    bg-black/30 backdrop-blur-sm">

      {/* Medium badge */}
      <span className="text-xs text-white/60 font-medium uppercase tracking-widest mr-2 select-none">
        {t(canVideo ? "medium.video" : "medium.voice")}
      </span>

      {/* Mic toggle */}
      <ControlButton
        label={micMuted ? t("controls.unmute") : t("controls.mute")}
        active={!micMuted}
        onClick={onToggleMic}
      >
        {micMuted ? <MicOff size={18} /> : <Mic size={18} />}
      </ControlButton>

      {/* Camera toggle (only when my ceiling includes video) */}
      {canVideo && (
        <ControlButton
          label={cameraOff ? t("controls.cameraOn") : t("controls.cameraOff")}
          active={!cameraOff}
          onClick={onToggleCam}
        >
          {cameraOff ? <VideoOff size={18} /> : <Video size={18} />}
        </ControlButton>
      )}

      {/* Disconnect — ou, na chamada de chat, desligar a chamada (WCH-07) */}
      <ControlButton
        label={onHangupCall ? t("controls.hangupCall") : t("controls.disconnect")}
        danger
        onClick={onHangupCall && !hangingUp ? onHangupCall : onHangupCall ? () => {} : onDisconnect}
      >
        <PhoneOff size={18} />
      </ControlButton>
    </div>
  );
};
