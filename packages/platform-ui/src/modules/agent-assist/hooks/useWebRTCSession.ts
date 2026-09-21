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
  DisconnectReason,
  type RoomOptions,
} from "livekit-client";

/**
 * Canais cujo contato tem SALA de mídia. `voice` entrou na VOZ-02: a chamada de telefone chega pela
 * perna SIP numa sala do SFU, com a mesma mídia do browser — o atendente humano entra nela igual.
 * Uma casa só para a pergunta, que o Console fazia em quatro lugares com `=== "webrtc"`.
 */
export function hasMediaRoom(channel: string | null | undefined): boolean {
  return channel === "webrtc" || channel === "voice";
}

/**
 * WCH-01 — o contato tem mídia AGORA: o canal é de sala, ou o cliente de um chat abriu uma
 * chamada (`media.call`). A chamada é MEIO do contato, não canal (adr-chat-call-as-medium).
 */
export function hasMedia(channel: string | null | undefined, callActive?: boolean): boolean {
  return hasMediaRoom(channel) || callActive === true;
}

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
  /**
   * NIV-07: a plataforma PAUSOU a mídia deste participante — o cliente está teclando um dado
   * protegido no telefone, e a tecla chegaria a todos na sala. Volta sozinho no fim do bloco.
   */
  mediaHold: boolean;
  /**
   * VOZ-38: a CHAMADA acabou para quem observa — só no papel `supervisor`. O gateway não apaga a
   * sala do SFU no fim de um contato de browser (só a perna SIP e a sala órfã), e o stream da
   * sessão não recebe `session_closed` no WebRTC: medido em 2026-09-21, a visão ficava em
   * "Aguardando vídeo" numa sala vazia. O sinal que chega é a sala: todos os outros saíram depois
   * de terem estado nela, ou o SFU a fechou.
   */
  ended: boolean;
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
  /** True quando o browser BLOQUEOU tocar o áudio remoto (autoplay) — a tela pede um clique */
  audioBlocked: boolean;
  /** Libera o áudio remoto; tem de ser chamado dentro de um gesto do usuário */
  startAudio: () => Promise<void>;
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

// Esperas entre tentativas enquanto a sala ainda não existe (~15 s no total). Passou
// disso, a tela mostra `room_not_ready` em vez de ficar tentando calada.
const TOKEN_RETRY_DELAYS_MS = [500, 1000, 1500, 2000, 3000, 3000, 4000];
// NIV-07: durante a coleta de dado protegido no telefone a rota responde 409 — sem teto de
// tentativas, porque o fim é do BLOCO (o cliente teclando), não de uma corrida de roteamento.
const MEDIA_HOLD_RETRY_MS = 2000;

export function useWebRTCSession(
  sessionId: string | null,
  agentIdentity: string,
  channel: string | undefined,
  role: RoomRole = "agent",
  callActive = false,
): WebRTCSessionState {
  const roomRef = useRef<Room | null>(null);
  // Cada `connect` leva uma geração; trocar de sessão ou desmontar a invalida, e a
  // tentativa em curso para em vez de abrir a sala da sessão errada.
  const generationRef = useRef(0);
  const [room,         setRoom]         = useState<Room | null>(null);
  const [publish,      setPublish]      = useState<MediaKind[]>([]);
  const [customerPublish, setCustomerPublish] = useState<MediaKind[]>([]);
  const [remoteTracks, setRemoteTracks] = useState<Map<string, RemoteTrack[]>>(new Map());
  const [localTracks,  setLocalTracks]  = useState<LocalTrack[]>([]);
  const [connecting,   setConnecting]   = useState(false);
  const [error,        setError]        = useState<string | null>(null);
  const [micMuted,     setMicMuted]     = useState(false);
  const [cameraOff,    setCameraOff]    = useState(false);
  const [audioBlocked, setAudioBlocked] = useState(false);
  const [mediaHold,    setMediaHold]    = useState(false);
  const [ended,        setEnded]        = useState(false);
  // o `connect` se chama de volta quando o servidor tira este participante da sala (NIV-07)
  const connectRef = useRef<((sid: string) => Promise<void>) | null>(null);

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
    const generation = ++generationRef.current;
    setConnecting(true);
    setError(null);
    setEnded(false);

    try {
      // VOZ-04: a rota é do channel-gateway (`/webrtc`, proxy próprio). Era `/api/webrtc/…`,
      // que o proxy manda ao mcp-server — 404 sempre, e a sala nunca era aberta.
      let body: TokenResponse | null = null;
      let attempt = 0;
      for (;;) {
        const res = await fetch(
          `/webrtc/token/${sid}?role=${role}`,
          // Token em MEMORIA (`auth/token-store`). A leitura do localStorage aqui mandava
          // `Bearer ` vazio — chave que ninguem escreve.
          { headers: { Authorization: `Bearer ${getAccessToken() ?? ""}` } }
        );
        if (generation !== generationRef.current) return;   // sessão trocada no meio
        if (res.ok) { body = await res.json(); break; }
        const detail = await res.json().catch(() => null) as { detail?: { code?: string } } | null;
        // NIV-07: coleta de dado protegido no telefone — a porta fica fechada até o bloco
        // acabar. É pausa, não erro: a tela diz por quê e a tentativa segue sem teto.
        if (res.status === 409 && detail?.detail?.code === "masked_collect_in_progress") {
          setMediaHold(true);
          await new Promise(resolve => setTimeout(resolve, MEDIA_HOLD_RETRY_MS));
          if (generation !== generationRef.current) return;
          continue;
        }
        // A atribuição chega ao Console e a sala nasce do `routing.assigned` da MESMA
        // ativação: "ainda não" é corrida normal e se repete; qualquer outro 404 é
        // desistência, e o motivo aparece na tela.
        const notReady = res.status === 404 && detail?.detail?.code === "room_not_ready";
        if (!notReady || attempt === TOKEN_RETRY_DELAYS_MS.length) {
          throw new Error(notReady ? "room_not_ready" : `token_fetch_failed:${res.status}`);
        }
        await new Promise(resolve => setTimeout(resolve, TOKEN_RETRY_DELAYS_MS[attempt]));
        attempt++;
        if (generation !== generationRef.current) return;
      }
      setMediaHold(false);
      if (!body) throw new Error("token_fetch_failed");
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
      // VOZ-38: "já houve alguém" — sala vazia ANTES de o cliente entrar não é fim de chamada
      let othersSeen = false;
      r.on(RoomEvent.ParticipantConnected, () => { othersSeen = true; rebuildRemoteTracks(r); });
      r.on(RoomEvent.ParticipantDisconnected, () => {
        rebuildRemoteTracks(r);
        // Só o supervisor: o atendente tem ciclo próprio (o Console fecha o contato), e derrubar a
        // sala dele aqui mudaria a reconexão do cliente, que não é deste item.
        if (role === "supervisor" && othersSeen && r.remoteParticipants.size === 0
            && generation === generationRef.current && roomRef.current === r) {
          setEnded(true);
          roomRef.current = null;
          void r.disconnect();
        }
      });
      r.on(RoomEvent.Disconnected, (reason?: DisconnectReason) => {
        setRoom(null);
        setLocalTracks([]);
        setRemoteTracks(new Map());
        if (role === "supervisor" && generation === generationRef.current
            && (reason === DisconnectReason.ROOM_DELETED || reason === DisconnectReason.ROOM_CLOSED)) {
          setEnded(true);
        }
        // NIV-07: o servidor tirou este participante da sala para uma coleta de dado protegido
        // no telefone. A sessão continua; a tela volta a pedir token e mostra a pausa até o fim.
        if (reason === DisconnectReason.PARTICIPANT_REMOVED && generation === generationRef.current
            && roomRef.current === r) {
          roomRef.current = null;
          setMediaHold(true);
          void connectRef.current?.(sid);
        }
      });
      // O browser pode recusar tocar som que a página não iniciou por gesto (autoplay). O
      // LiveKit detecta ao anexar a trilha; sem esta escuta a recusa é MUDA — a chamada
      // parece conectada e ninguém ouve ninguém.
      r.on(RoomEvent.AudioPlaybackStatusChanged, () => setAudioBlocked(!r.canPlaybackAudio));

      await r.connect(body.livekit_url, body.token);
      if (r.remoteParticipants.size > 0) othersSeen = true;

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
      if (generation !== generationRef.current) return;
      const msg = err instanceof Error ? err.message : "webrtc_connect_failed";
      setError(msg);
    } finally {
      // Uma tentativa obsoleta não apaga o "conectando" da sessão que a substituiu.
      if (generation === generationRef.current) setConnecting(false);
    }
  }, [rebuildRemoteTracks, role]);
  connectRef.current = connect;

  const disconnectRoom = useCallback(() => {
    generationRef.current++;
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
    setAudioBlocked(false);
    setMediaHold(false);
  }, []);

  const startAudio = useCallback(async () => {
    const r = roomRef.current;
    if (!r) return;
    await r.startAudio();
    setAudioBlocked(!r.canPlaybackAudio);
  }, []);

  // Connect when sessionId appears and channel is webrtc; tear down when gone
  useEffect(() => {
    if (!sessionId || !hasMedia(channel, callActive)) {
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
    mediaHold,
    ended,
    toggleMic,
    toggleCamera,
    micMuted,
    cameraOff,
    disconnect: disconnectRoom,
    audioBlocked,
    startAudio,
  };
}
