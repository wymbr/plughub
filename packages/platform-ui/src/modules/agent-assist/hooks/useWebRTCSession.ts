/**
 * useWebRTCSession
 *
 * Manages the full lifecycle of a WebRTC session from the agent's perspective:
 *   1. Fetches a LiveKit token from the channel-gateway token endpoint.
 *   2. Connects to the LiveKit room.
 *   3. Exposes participant tracks and the media CEILINGS (own + customer) so the
 *      overlay can render the appropriate view (video grid / waveform / nothing).
 *
 * VOZ-09 (2026-09-14): o servidor mandava UM meio para a sessão (`negotiated_medium`)
 * e o hook o impunha — em `text` nem entrava na sala, em `video` publicava câmera à
 * força. Hoje a resposta traz TETOS por participante (`publish`, `customer_publish`):
 * o hook publica só o que o próprio teto permite, e ligar/desligar é ESCOLHA do
 * operador dentro dele. O papel (`agent` | `supervisor`) viaja na query — a visão de
 * supervisor pedia `role=agent` e entraria publicando.
 *
 * Connection is established only when sessionId is non-null and the contact's
 * channel is "webrtc". It tears down automatically when sessionId changes or
 * the component unmounts.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { getAccessToken } from "@/auth/token-store";
import {
  Room,
  RoomEvent,
  LocalTrack,
  RemoteTrack,
  Track,
  createLocalTracks,
  type RoomOptions,
} from "livekit-client";

export type MediaKind = "audio" | "video";
/** O que a tela mostra, derivado dos tetos — não é mais escolha do servidor. */
export type MediaView = "none" | "audio" | "video";
export type RoomRole = "agent" | "supervisor";

export interface WebRTCSessionState {
  /** LiveKit Room instance — null while connecting or after disconnect */
  room: Room | null;
  /** O que mostrar: vídeo se algum teto (meu ou do cliente) inclui vídeo, áudio se só áudio */
  view: MediaView;
  /** Meu teto de publicação */
  publish: MediaKind[];
  /** Teto do cliente no momento do token */
  customerPublish: MediaKind[];
  /** Participant tracks keyed by participant identity */
  remoteTracks: Map<string, RemoteTrack[]>;
  /** Local camera + mic tracks (only the kinds in my own ceiling) */
  localTracks: LocalTrack[];
  /** True while fetching token or connecting */
  connecting: boolean;
  /** Non-null when a connection error has occurred */
  error: string | null;
  /** Toggle local microphone mute */
  toggleMic: () => Promise<void>;
  /** Toggle local camera (only when my ceiling includes video) */
  toggleCamera: () => Promise<void>;
  /** True when local mic is muted */
  micMuted: boolean;
  /** True when local camera is off */
  cameraOff: boolean;
  /** Hang up — disconnect from the room without ending the session */
  disconnect: () => void;
}

interface TokenResponse {
  token: string;
  livekit_url: string;
  room_name: string;
  publish: MediaKind[];
  customer_publish: MediaKind[];
  hidden: boolean;
}

const ROOM_OPTIONS: RoomOptions = {
  adaptiveStream: true,
  dynacast:       true,
};

export function useWebRTCSession(
  sessionId: string | null,
  agentIdentity: string,
  channel: string | undefined,
  role: RoomRole = "agent"
): WebRTCSessionState {
  const roomRef = useRef<Room | null>(null);
  const [room,         setRoom]         = useState<Room | null>(null);
  const [publish,      setPublish]      = useState<MediaKind[]>([]);
  const [customerPublish, setCustomerPublish] = useState<MediaKind[]>([]);
  const [remoteTracks, setRemoteTracks] = useState<Map<string, RemoteTrack[]>>(new Map());
  const [localTracks,  setLocalTracks]  = useState<LocalTrack[]>([]);
  const [connecting,   setConnecting]   = useState(false);
  const [error,        setError]        = useState<string | null>(null);
  const [micMuted,     setMicMuted]     = useState(false);
  const [cameraOff,    setCameraOff]    = useState(false);

  // ── Remote track bookkeeping ────────────────────────────────────────────
  const rebuildRemoteTracks = useCallback((r: Room) => {
    const map = new Map<string, RemoteTrack[]>();
    for (const participant of r.remoteParticipants.values()) {
      const tracks: RemoteTrack[] = [];
      for (const publication of participant.trackPublications.values()) {
        if (publication.track) tracks.push(publication.track as RemoteTrack);
      }
      if (tracks.length > 0) map.set(participant.identity, tracks);
    }
    setRemoteTracks(new Map(map));
  }, []);

  // ── Connect / disconnect lifecycle ──────────────────────────────────────
  const connect = useCallback(async (sid: string) => {
    setConnecting(true);
    setError(null);

    try {
      const res = await fetch(
        `/api/webrtc/token/${sid}?role=${role}`,
        // Token em MEMORIA (`auth/token-store`). A leitura do localStorage aqui mandava
        // `Bearer ` vazio — chave que ninguem escreve.
        { headers: { Authorization: `Bearer ${getAccessToken() ?? ""}` } }
      );
      if (!res.ok) throw new Error(`token_fetch_failed:${res.status}`);

      const body: TokenResponse = await res.json();
      setPublish(body.publish);
      setCustomerPublish(body.customer_publish);

      if (body.publish.length === 0 && body.customer_publish.length === 0) {
        // Ninguém tem mídia a trocar — não há por que entrar na sala
        setConnecting(false);
        return;
      }

      const r = new Room(ROOM_OPTIONS);
      roomRef.current = r;

      // ── Room event listeners ──────────────────────────────────────────
      r.on(RoomEvent.TrackSubscribed, () => rebuildRemoteTracks(r));
      r.on(RoomEvent.TrackUnsubscribed, () => rebuildRemoteTracks(r));
      r.on(RoomEvent.ParticipantConnected, () => rebuildRemoteTracks(r));
      r.on(RoomEvent.ParticipantDisconnected, () => rebuildRemoteTracks(r));
      r.on(RoomEvent.Disconnected, () => {
        setRoom(null);
        setLocalTracks([]);
        setRemoteTracks(new Map());
      });

      await r.connect(body.livekit_url, body.token);

      // Publica só o que o PRÓPRIO teto permite; o SFU recusaria o resto.
      const trackOptions = {
        audio: body.publish.includes("audio"),
        video: body.publish.includes("video"),
      };
      const local = trackOptions.audio || trackOptions.video
        ? await createLocalTracks(trackOptions)
        : [];
      for (const t of local) {
        await r.localParticipant.publishTrack(t);
      }
      setLocalTracks(local);
      setRoom(r);
      rebuildRemoteTracks(r);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "webrtc_connect_failed";
      setError(msg);
    } finally {
      setConnecting(false);
    }
  }, [rebuildRemoteTracks, role]);

  const disconnectRoom = useCallback(() => {
    const r = roomRef.current;
    if (r) {
      r.disconnect();
      roomRef.current = null;
    }
    setRoom(null);
    setLocalTracks([]);
    setRemoteTracks(new Map());
    setPublish([]);
    setCustomerPublish([]);
    setError(null);
    setMicMuted(false);
    setCameraOff(false);
  }, []);

  // Connect when sessionId appears and channel is webrtc; tear down when gone
  useEffect(() => {
    if (!sessionId || channel !== "webrtc") {
      disconnectRoom();
      return;
    }
    connect(sessionId);
    return () => { disconnectRoom(); };
  // A identidade na sala vem do JWT no servidor (VOZ-01); channel/role raramente mudam
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, channel, role]);

  // ── Media controls ──────────────────────────────────────────────────────
  const toggleMic = useCallback(async () => {
    const audioTrack = localTracks.find(t => t.kind === Track.Kind.Audio);
    if (!audioTrack) return;
    if (micMuted) {
      await audioTrack.unmute();
      setMicMuted(false);
    } else {
      await audioTrack.mute();
      setMicMuted(true);
    }
  }, [localTracks, micMuted]);

  const toggleCamera = useCallback(async () => {
    const videoTrack = localTracks.find(t => t.kind === Track.Kind.Video);
    if (!videoTrack) return;
    if (cameraOff) {
      await videoTrack.unmute();
      setCameraOff(false);
    } else {
      await videoTrack.mute();
      setCameraOff(true);
    }
  }, [localTracks, cameraOff]);

  const kinds = new Set<MediaKind>([...publish, ...customerPublish]);
  const view: MediaView = kinds.has("video") ? "video" : kinds.has("audio") ? "audio" : "none";

  return {
    room,
    view,
    publish,
    customerPublish,
    remoteTracks,
    localTracks,
    connecting,
    error,
    toggleMic,
    toggleCamera,
    micMuted,
    cameraOff,
    disconnect: disconnectRoom,
  };
}
