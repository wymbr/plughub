/**
 * RemoteAudio
 *
 * Toca TODA trilha de áudio remota da sala, em elementos <audio> fora da tela.
 *
 * VOZ-04 (2026-09-15): o Console nunca tocou áudio. O `VideoGrid` anexa só a trilha de VÍDEO,
 * e nenhum outro componente anexava as de áudio — elas chegavam assinadas ao browser e iam
 * para lugar nenhum. Medido na primeira chamada de gente: vídeo nos dois sentidos, as duas
 * trilhas de áudio com som de verdade no SFU, e o agente sem ouvir nada. Mora fora do
 * `VideoGrid` porque a chamada só de voz não renderiza grade nenhuma.
 */

import React, { useEffect, useRef } from "react";
import type { RemoteTrack } from "livekit-client";
import { Track } from "livekit-client";

function TrackAudio({ track }: { track: RemoteTrack }) {
  const ref = useRef<HTMLAudioElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    track.attach(el);
    return () => { track.detach(el); };
  }, [track]);

  return <audio ref={ref} autoPlay />;
}

export const RemoteAudio: React.FC<{ remoteTracks: Map<string, RemoteTrack[]> }> = ({ remoteTracks }) => {
  const audios: RemoteTrack[] = [];
  for (const tracks of remoteTracks.values()) {
    for (const t of tracks) if (t.kind === Track.Kind.Audio) audios.push(t);
  }
  return (
    <div className="hidden" aria-hidden>
      {audios.map(t => <TrackAudio key={t.sid ?? t.mediaStreamTrack.id} track={t} />)}
    </div>
  );
};
