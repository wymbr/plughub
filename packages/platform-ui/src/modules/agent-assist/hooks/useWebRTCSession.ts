/**
 * useWebRTCSession
 *
 * Manages the full lifecycle of a WebRTC session from the agent's perspective:
 *   1. Fetches a LiveKit token from the channel-gateway token endpoint.
 *   2. Connects to the LiveKit room.
 *   3. Exposes participant tracks and the negotiated medium so the overlay can
 *      render the appropriate view (video grid / waveform / nothing).
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

export type NegotiatedMedium = "video" | "voice" | "text";

export interface WebRTCSessionState {
  /** LiveKit Room instance — null while connecting or after disconnect */
  room: Room | null;
  /** Negotiated medium for this session */
  medium: NegotiatedMedium;
  /** Participant tracks keyed by participant identity */
  remoteTracks: Map<string, RemoteTrack[]>;
  /** Local camera + mic tracks (published when medium is video/voice) */
  localTracks: LocalTrack[];
  /** True while fetching token or connecting */
  connecting: boolean;
  /** Non-null when a connection error has occurred */
  error: string | null;
  /** Toggle local microphone mute */
  toggleMic: () => Promise<void>;
  /** Toggle local camera (video medium only) */
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
  negotiated_medium: NegotiatedMedium;
}

const ROOM_OPTIONS: RoomOptions = {
  adaptiveStream: true,
  dynacast:       true,
};

export function useWebRTCSession(
  sessionId: string | null,
  agentIdentity: string,
  channel: string | undefined
): WebRTCSessionState {
  const roomRef = useRef<Room | null>(null);
  const [room,         setRoom]         = useState<Room | null>(null);
  const [medium,       setMedium]       = useState<NegotiatedMedium>("text");
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
  const connect = useCallback(async (sid: string, identity: string) => {
    setConnecting(true);
    setError(null);

    try {
      const res = await fetch(
        `/api/webrtc/token/${sid}?role=agent&identity=${encodeURIComponent(identity)}`,
        // Token em MEMORIA (`auth/token-store`). A leitura do localStorage aqui mandava
        // `Bearer ` vazio — chave que ninguem escreve.
        { headers: { Authorization: `Bearer ${getAccessToken() ?? ""}` } }
      );
      if (!res.ok) throw new Error(`token_fetch_failed:${res.status}`);

      const { token, livekit_url, negotiated_medium }: TokenResponse = await res.json();
      setMedium(negotiated_medium);

      if (negotiated_medium === "text") {
        // Text medium — no media needed, just note the medium and return
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

      await r.connect(livekit_url, token);

      // Publish local tracks for agent
      const trackOptions =
        negotiated_medium === "video"
          ? { audio: true, video: true }
          : { audio: true, video: false };

      const local = await createLocalTracks(trackOptions);
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
  }, [rebuildRemoteTracks]);

  const disconnectRoom = useCallback(() => {
    const r = roomRef.current;
    if (r) {
      r.disconnect();
      roomRef.current = null;
    }
    setRoom(null);
    setLocalTracks([]);
    setRemoteTracks(new Map());
    setMedium("text");
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
    connect(sessionId, agentIdentity);
    return () => { disconnectRoom(); };
  // agentIdentity is stable (derived from JWT); channel rarely changes
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, channel]);

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

  return {
    room,
    medium,
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
