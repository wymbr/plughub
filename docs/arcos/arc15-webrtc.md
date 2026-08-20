# Arc 15 — Canal WebRTC com SFU Enterprise

> Última atualização: 2026-08-20 · Status: Fases A–F implementadas **no plano de sinalização** —
> **o plano de MÍDIA não está provisionado em ambiente algum do repositório**.

> ⚠️ **Medição de 2026-08-20 (o "Arc 15 completo" que estava aqui era do canal, não da solução).**
> Existe e roda: signaling WS (`main.py:729`), emissão de token (`main.py:754`), `LiveKitProvider`/
> `LiveKitRoomClient` (`adapters/webrtc_provider.py:143`, `webrtc_room_client.py:184`) e cliente real no
> browser (`platform-ui/package.json:11,16`). **Não existe:** serviço LiveKit em `docker-compose*.yml`
> algum (grep → zero), env `LIVEKIT_*`/`WEBRTC_*` em `.env*`/compose/scripts (zero), manifesto k8s sob
> `infra/` para a topologia da §5 abaixo, e o SDK `livekit` sequer é dependência do pacote
> (`packages/channel-gateway/pyproject.toml:6-23`) — a imagem não o instala e os imports caem no ramo de
> degradação (`webrtc_room_client.py:217-220`). Com credenciais vazias (`config.py:228-232`) o provider
> entra em `_dev_mode` (`webrtc_provider.py:167`) e devolve **token, room, participantes e egress mock**.
> Este doc nunca prometeu o SFU no compose — a §5 prescreve Kubernetes; foi o "completo" do cabeçalho
> que passou a ser lido como solução de mídia pronta. **Provisionar o SFU é pré-requisito de qualquer
> trabalho de WebRTC, não detalhe de deploy.**

**Versão:** 1.6 — 2026-05-20  
**Escopo:** `packages/channel-gateway/` · `packages/agent-registry/` · `packages/routing-engine/` · `packages/platform-ui/` · infraestrutura LiveKit

---

## 1. Visão Geral e Motivação

O canal WebRTC eleva o PlugHub de uma plataforma de mensageria para uma **plataforma de comunicação rica em tempo real**. Diferente dos outros canais (que têm um medium fixo — texto, voz PSTN, email), o WebRTC é um **protocolo de transporte com medium negociado em tempo real** entre o cliente e o agente.

**Coexistência com o canal voice (PSTN):**

| Canal | Transporte | Uso típico | Infraestrutura de mídia |
|---|---|---|---|
| `voice` | PSTN via Twilio trunk | Clientes externos, URA, discagem ativa | Twilio conference bridge |
| `webrtc` | Browser-to-SFU | Clientes na webapp/widget, atendimento enriquecido | LiveKit SFU |

Agentes podem ter ambos em `channel_types` e atender os dois em paralelo. Pools podem ser configurados com qualquer combinação.

**O que torna o WebRTC diferente dos outros canais:**

1. **Medium negociado** — o que o agente suporta (video/voice/text) determina o que o cliente recebe; há cascade de fallback automático.
2. **Dois planos simultâneos** — plano de controle (Redis/Kafka, igual aos demais) + plano de mídia (LiveKit SFU, novo).
3. **SFU obrigatório para enterprise** — gravação por segmento, supervisão em tempo real, e conferência multi-participante exigem um Selective Forwarding Unit, não P2P.
4. **Capacidades por agente** — cada agent type declara `media_capabilities: [video, voice, text]`; o Channel Gateway negocia o medium mais rico que o par suporta.

---

## 2. Arquitetura — Dois Planos

```
┌─────────────────────────────────────────────────────────────┐
│                    PLANO DE CONTROLE                        │
│  Redis stream · Kafka · SessionRegistry · ContextStore      │
│  (identical to all other channels — no changes)            │
└──────────────────────────┬──────────────────────────────────┘
                           │ conversations.inbound/outbound
┌──────────────────────────▼──────────────────────────────────┐
│               CHANNEL GATEWAY — WebRTC Adapter              │
│                                                             │
│  WebSocket /ws/webrtc/{pool_id}  ←→  Customer browser      │
│  Signaling: offer/answer, token issue, medium negotiation   │
│                                                             │
│  STT pipeline: audio track → Deepgram → transcript → Kafka  │
│  TTS pipeline: ElevenLabs bytes → LiveKit track injection   │
└──────────────────────────┬──────────────────────────────────┘
                           │ LiveKit server SDK
┌──────────────────────────▼──────────────────────────────────┐
│                    PLANO DE MÍDIA                           │
│                  LiveKit SFU (self-hosted)                  │
│                                                             │
│  Room: plughub-{session_id}                                 │
│  Participants: customer · agent · supervisor (subscribe)    │
│  Tracks: video / audio / DataChannel (text fallback)        │
│  Egress: recording per segment → S3/filesystem              │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. LiveKit como SFU

**Escolha:** LiveKit (open source, self-hosted).

**Justificativas:**

| Requisito enterprise | Suporte LiveKit |
|---|---|
| Self-hosted — sem lock-in de cloud | ✅ Docker / Kubernetes |
| Gravação por segmento → S3/filesystem | ✅ Egress API (composite + per-track) |
| Supervisão "escuta invisível" | ✅ `canSubscribe=true, canPublish=false` |
| SDKs Python + TypeScript + browser JS | ✅ Todos disponíveis |
| Conferência multi-participante (escalável) | ✅ Selective Forwarding (não mistura) |
| Webhooks de evento de sala | ✅ `RoomEvent` — participant join/leave/track |
| TURN integrado para NAT traversal | ✅ built-in |
| DTLS-SRTP obrigatório | ✅ por default |

**Topologia recomendada:**

```
Kubernetes cluster:
  livekit-server          (2+ réplicas, stateless)
  livekit-egress          (gravação, escalável)
  livekit-redis           (cluster state — pode reutilizar Redis existente)
  coturn                  (TURN server — ou usar LiveKit built-in TURN)
```

**Autenticação:** tokens JWT assinados com `LIVEKIT_API_SECRET` — emitidos *exclusivamente* pelo Channel Gateway. Nunca expostos ao browser diretamente.

---

## 4. Modelo de Capacidade de Mídia por Agente

### 4.1 Schema — Agent Type

```typescript
// @plughub/schemas — AgentTypeSchema
media_capabilities: z.array(
  z.enum(["video", "voice", "text"])
).default(["text"])
```

Exemplos de configuração:

```yaml
# Agente de atendimento premium (video + voz + texto)
agent_type_id: agente_premium_v1
channel_types: [webrtc]
media_capabilities: [video, voice, text]

# Agente de suporte técnico (voz + texto, sem câmera)
agent_type_id: agente_suporte_v1
channel_types: [webrtc, voice]
media_capabilities: [voice, text]

# Agente AI (texto apenas via DataChannel)
agent_type_id: agente_ia_webrtc_v1
channel_types: [webrtc]
media_capabilities: [text]

# Agente que atende PSTN e WebRTC
agent_type_id: agente_multimídia_v1
channel_types: [voice, webrtc]
media_capabilities: [voice, text]
```

### 4.2 Cascade de Negociação

Ordem de prioridade (mais rico → mais simples):

```
video → voice → text
```

O Channel Gateway verifica o que o agente suporta e tenta o medium mais rico. Se o cliente não tiver câmera/mic disponível, o WebRTC SDP offer/answer resolve isso nativamente (tracks não publicados).

```python
MEDIUM_PRIORITY = ["video", "voice", "text"]

def negotiate_medium(
    agent_capabilities: list[str],
    pool_fallback_order: list[str] | None = None,
) -> str:
    order = pool_fallback_order or MEDIUM_PRIORITY
    for medium in order:
        if medium in agent_capabilities:
            return medium
    return "text"   # always available
```

Pool config pode sobrescrever a ordem:

```yaml
webrtc_media_fallback_order: [voice, text]   # pool sem video
```

### 4.3 Re-negociação Mid-Session

Quando um agente transfere para outro com capacidades diferentes:

```
Agente A (video+voice) → Agente B (voice only)
  ↓
Gateway detecta downgrade via routing.assigned event
  ↓
Envia webrtc.renegotiate ao customer (medium=voice)
  ↓
Customer browser desabilita câmera, mantém mic
  ↓
LiveKit room persiste — apenas tracks são desabilitados
```

Re-negociação para medium superior (upgrade) também possível quando um agente mais capaz assume.

---

## 5. Protocolo de Sinalização

O Channel Gateway expõe `WS /ws/webrtc/{pool_id}` para o browser do cliente. O protocolo é análogo ao webchat (`conn.hello` / `conn.authenticate`) com extensão para negociação de mídia.

### 5.1 Mensagens Server → Client

```jsonc
// 1. Saudação inicial (igual ao webchat)
{ "type": "conn.hello", "session_id": "sess-abc" }

// 2. Confirmação de autenticação + token LiveKit + medium negociado
{
  "type": "webrtc.ready",
  "livekit_url": "wss://livekit.empresa.com",
  "livekit_token": "<jwt>",
  "room_name": "plughub-sess-abc",
  "negotiated_medium": "voice",    // "video" | "voice" | "text"
  "session_id": "sess-abc"
}

// 3. Re-negociação de medium (transfer, downgrade/upgrade)
{
  "type": "webrtc.renegotiate",
  "negotiated_medium": "text",
  "reason": "agent_transfer"
}

// 4. Entrega de texto (medium=text, DataChannel fallback)
{
  "type": "webrtc.message",
  "content": { "text": "Olá, como posso ajudar?" },
  "author_role": "primary"
}

// 5. Interaction request — menu/collect via DataChannel
{
  "type": "webrtc.interaction",
  "menu": { "id": "...", "text": "...", "options": [...] }
}

// 6. Encerramento
{ "type": "webrtc.session_closed", "close_reason": "agent_hangup" }
```

### 5.2 Mensagens Client → Server

```jsonc
// Autenticação (igual ao webchat)
{ "type": "conn.authenticate", "token": "<jwt>", "cursor": null }

// Cliente confirmou conexão ao LiveKit
{ "type": "webrtc.connected", "medium": "voice" }

// Cliente confirmou re-negociação
{ "type": "webrtc.renegotiated", "medium": "text" }

// Mensagem de texto (medium=text ou DataChannel redundante)
{ "type": "webrtc.message", "content": { "text": "..." } }

// Input de interaction (opção de menu)
{ "type": "webrtc.interaction_reply", "menu_id": "...", "value": "1" }

// Sinalização de encerramento pelo cliente
{ "type": "webrtc.hangup" }
```

---

## 6. Ciclo de Vida da Sessão

```
1. Customer WS connect → /ws/webrtc/{pool_id}
2. Gateway: conn.hello → cliente autentica
3. Gateway: _open_session() → Kafka conversations.inbound
4. Routing Engine: aloca agente com channel=webrtc
5. Gateway recebe routing.assigned:
   a. Lê agent.media_capabilities do evento
   b. negotiate_medium() → negotiated_medium
   c. LiveKit API: create room "plughub-{session_id}"
   d. Gera token customer (canPublish=true, canSubscribe=true)
   e. Envia webrtc.ready ao customer
6. Agent receives conversations.routed:
   a. platform-ui WebRTC adapter recebe evento
   b. GET /api/webrtc/token/{session_id} → Gateway emite token agente
   c. Agent browser conecta ao LiveKit room
   d. Publica tracks conforme negotiated_medium
7. Sessão ativa:
   - Áudio customer → Deepgram STT → audio_transcript → Kafka
   - TTS do agente AI → ElevenLabs → Gateway → LiveKit track injection
   - Agente humano: áudio/video direto pelo LiveKit
   - Texto (DataChannel): normalizado como message.text no stream
8. Encerramento:
   a. hangup ou session_closed event
   b. Gateway: stop Egress (se gravando)
   c. LiveKit: delete room
   d. Gateway: _close_session() → close_reason
```

---

## 7. Endpoint de Token do Agente

O platform-ui nunca acessa a LiveKit API diretamente. Tokens são emitidos pelo Channel Gateway:

```
GET /webrtc/token/{session_id}
Authorization: Bearer <agent_jwt>

Response:
{
  "livekit_url": "wss://livekit.empresa.com",
  "livekit_token": "<jwt>",
  "room_name": "plughub-{session_id}",
  "negotiated_medium": "voice"
}
```

Token do agente tem permissões:

```python
grants = VideoGrants(
    room_join     = True,
    room          = f"plughub-{session_id}",
    can_publish   = True,    # publica audio/video tracks
    can_subscribe = True,    # recebe audio/video do cliente
    can_publish_data = True, # DataChannel (text fallback)
)
```

Token do supervisor (subscribe-only):

```python
grants = VideoGrants(
    room_join     = True,
    room          = f"plughub-{session_id}",
    can_publish   = False,   # não entra na mídia
    can_subscribe = True,    # escuta/vê tudo
    can_publish_data = True, # pode sussurrar via DataChannel
    hidden        = True,    # não aparece na contagem de participantes
)
```

---

## 8. Pipeline STT no WebRTC

Para clientes com medium `voice` ou `video`, o Channel Gateway subscreve o audio track do cliente no LiveKit e encaminha para o pipeline STT existente:

```
LiveKit server SDK (Python)
  → subscibe to customer audio track
  → receive PCM frames (48kHz, opus decoded)
  → resample to 8kHz μ-law
  → FallbackSTTProvider([DeepgramSTTProvider, MockSTTProvider])
  → STTResult → Kafka audio_transcript
  → conversations.inbound (channel_session_id = livekit_participant_sid)
```

Para agentes AI que precisam de TTS no WebRTC:

```
ElevenLabsTTSProvider → MP3 bytes
  → decode to PCM (ffmpeg or pydub)
  → LiveKit LocalAudioTrack injection via server SDK
  → customer ouve diretamente pelo LiveKit room
```

**Nota:** O pipeline STT/TTS é idêntico ao canal voice — mesmos `FallbackSTTProvider`/`FallbackTTSProvider`. A diferença está no transporte de áudio: Twilio Media Streams μ-law WS (voice) vs LiveKit server SDK PCM frames (webrtc).

---

## 9. Gravação por Segmento — Egress

Análogo ao canal voice (§13 do doc de voz), mas usando **LiveKit Egress** em vez de Twilio Recording.

```
pool.webrtc_recording: true  →  egress ativo por segmento

Ao routing.assigned:
  1. TTS notice (LGPD): "Esta chamada poderá ser gravada..."
  2. LiveKit Egress API: StartRoomCompositeEgress ou StartTrackCompositeEgress
     - Output: S3 ou filesystem
     - Layout: "speaker" (vídeo dominante) ou "grid" (multi-câmera)
  3. egress_id armazenado em Redis: channel:webrtc:{session_id}:egress:{segment_id}

Ao agent_done / session_closed:
  1. LiveKit Egress API: StopEgress
  2. Download do arquivo → AttachmentStore
  3. Evento recording.completed no stream
```

**Egress types disponíveis:**

| Tipo | Uso |
|---|---|
| `RoomCompositeEgress` | Grava todos os participantes (layout configurável) |
| `TrackCompositeEgress` | Grava pares de tracks (ex: customer audio + agent audio) |
| `TrackEgress` | Grava track individual (ex: só customer) |

Para compliance dual-channel (equivalente ao `dual_channel` da voz), usar `TrackCompositeEgress` com customer audio track + agent audio track separados.

---

## 10. Supervisão em Tempo Real

O supervisor conecta ao LiveKit room como participante `hidden=true, canPublish=false`:

```
Supervisor acessa Monitor → SessionDetail → botão "Entrar como Supervisor"
  ↓
GET /api/webrtc/supervisor-token/{session_id}  (role: supervisor)
  ↓
Platform-UI WebRTC component conecta como subscriber
  ↓
Supervisor vê/ouve tudo — cliente e agente não sabem
  ↓
Sussurro ao agente: DataChannel message (visibility: ["part_agente_xyz"])
  (chega como agents_only no stream, cliente não vê)
  ↓
Take-over (promote): supervisor.can_publish → true (endpoint dedicado)
```

**Diferença vs canal voice:** No voice, a supervisão é via `coaching_mode` do Twilio (complexo, requer leg extra). No WebRTC, é nativo do LiveKit — participante hidden subscribe-only.

---

## 11. Console — Overlay WebRTC no Agente

**Modelo:** embutido no Console existente, sem janela separada.

```
Console layout com WebRTC ativo:
┌────────────────────────────────────────────────────┐
│  [Header com timer + pool + sentiment]              │
├──────────────────┬─────────────────────────────────┤
│                  │  [Video remoto do cliente]        │
│  Lista de        │  [Video local do agente (PiP)]   │
│  contatos        │  ┌──────────────────────────┐   │
│                  │  │  [Chat transcript]        │   │
│                  │  │  (DataChannel ou STT)     │   │
│                  │  └──────────────────────────┘   │
│                  │  [Controles: mic · cam · hangup] │
└──────────────────┴─────────────────────────────────┘
```

**Comportamento por medium negociado:**

| `negotiated_medium` | UI do agente |
|---|---|
| `video` | Grid 2x1 (vídeo cliente + PiP agente) + transcript + controls |
| `voice` | Waveform animada + transcript + controls (sem vídeo) |
| `text` | Layout normal do Console (sem media overlay) |

**Componentes novos (platform-ui):**

- `WebRTCOverlay.tsx` — container condicional (renderiza quando session tem `channel=webrtc`)
- `VideoGrid.tsx` — grid de vídeo (LiveKit React components)
- `MediaControls.tsx` — toggle mic/cam/hangup, indicador de medium negociado
- `useWebRTCSession(sessionId)` — hook: busca token, conecta ao LiveKit, expõe tracks
- `WebRTCSupervisorView.tsx` — view read-only para supervisores no Monitor

**Dependência:** `@livekit/components-react` + `livekit-client`.

---

## 12. Widget do Cliente (Browser)

O webchat widget existente ganha modo WebRTC opcional. O cliente é informado do medium disponível antes de iniciar:

```
Widget inicializa → conecta WS /ws/webrtc/{pool_id}
  ↓
Recebe webrtc.ready com negotiated_medium
  ↓
Se "video" ou "voice": solicita getUserMedia()
  ↓
Conecta ao LiveKit room com token customer
  ↓
Publica tracks conforme medium
  ↓
Se "text": comportamento idêntico ao webchat atual (DataChannel = mensagens)
```

**Implementação:** LiveKit JS client SDK (`livekit-client`) + wrapper sobre o webchat widget existente. O DataChannel de texto é o mesmo canal de mensagens — fallback transparente.

**Permissões de media solicitadas por medium:**

| Medium | getUserMedia constraints |
|---|---|
| `video` | `{ video: true, audio: true }` |
| `voice` | `{ video: false, audio: true }` |
| `text` | nenhum — DataChannel apenas |

---

## 13. Redis Keys (WebRTC-específicas)

```
channel:webrtc:{session_id}:room_name        → "plughub-{session_id}"
channel:webrtc:{session_id}:room_sid         → LiveKit room SID
channel:webrtc:{session_id}:medium           → "video" | "voice" | "text"
channel:webrtc:{session_id}:customer_psid    → LiveKit participant SID (customer)
channel:webrtc:{session_id}:agent_psid       → LiveKit participant SID (agent)
channel:webrtc:{session_id}:egress:{seg_id}  → LiveKit egress ID (TTL 24h)
channel:webrtc:{call_sid}:session            → session_id (para correlação)
```

TTL padrão: session_ttl_seconds (24h).

---

## 14. Kafka

Nenhum tópico novo. O canal WebRTC usa os tópicos existentes:

| Tópico | Uso no WebRTC |
|---|---|
| `conversations.inbound` | Sessão aberta, mensagens de texto normalizadas, STT transcripts |
| `conversations.outbound` | TTS e mensagens de texto para o cliente |
| `collect.events` | Collect step via DataChannel (medium=text) |
| `audio_transcript` | STT results do audio track do cliente |

**Evento adicional no stream Redis** (`session:{id}:stream`):

```jsonc
// Quando o medium é negociado (inicial ou re-negociação)
{
  "event_type": "webrtc.medium_negotiated",
  "negotiated_medium": "voice",
  "previous_medium": null,
  "agent_capabilities": ["voice", "text"]
}
```

---

## 15. Config — Novas Variáveis de Ambiente

```python
# ── WebRTC (LiveKit SFU) ──────────────────────────────────────
webrtc_livekit_url:         str = "wss://livekit.empresa.com"
webrtc_livekit_api_key:     str = ""
webrtc_livekit_api_secret:  str = ""
# Token TTL para participantes (segundos)
webrtc_token_ttl_s:         int = 3600
# Default pool_id para sessões WebRTC sem ChannelEndpoint match
webrtc_default_pool_id:     str = ""
# Fallback de medium quando pool não configura explicitamente
webrtc_default_medium_order: str = "video,voice,text"
# Habilitar STT no audio track do cliente
webrtc_stt_enabled:         bool = True
# Habilitar TTS injection via LiveKit track (agentes AI)
webrtc_tts_injection_enabled: bool = True
```

---

## 16. Fases de Implementação

### Fase A — Infraestrutura e Signaling Básico ✅ (2026-05-20)
- `adapters/webrtc_provider.py`: `IWebRTCProvider` Protocol (runtime_checkable) + `LiveKitProvider` (create_room, delete_room, generate_token; start/stop_egress raises NotImplementedError até Fase D; dev mode sem api_key) + `MockWebRTCProvider` (in-memory, records all calls) + helpers `build_room_name`, `negotiate_medium`
- `adapters/webrtc.py`: `WebRTCAdapter(ChannelAdapter)` — WS `/ws/webrtc/{pool_id}`, signaling completo (conn.hello/authenticate/ready), `_stream_watcher` (routing.assigned → negotiate_medium → create_room → webrtc.ready), `deliver_text/menu/typing/session_closed`, `get_token` (agent/supervisor), `_close_session`
- `config.py`: 8 variáveis WebRTC/LiveKit
- `main.py`: WS route `/ws/webrtc/{pool_id}` + endpoint `GET /webrtc/token/{session_id}` + `_webrtc_adapter` registrado em `_channel_adapters["webrtc"]`
- `tests/test_webrtc_adapter.py`: 42+ testes cobrindo negotiate_medium, MockProvider, LiveKitProvider dev mode, outbound delivery, token endpoint, auth handshake, routing.assigned → webrtc.ready

### Fase B — Media Negotiation + Agent Capabilities ✅ (2026-05-20)
- `@plughub/schemas`: `media_capabilities: z.array(z.enum(["video","voice","text"])).default([])` em `AgentTypeRegistrationSchema`; 4 novos testes no `agent-registry.test.ts`
- `agent-registry`: migration `20260520200000_add_agent_media_capabilities` (`TEXT[] DEFAULT ARRAY[]::TEXT[]`); `schema.prisma` atualizado; POST cria com `media_capabilities ?? []`; PATCH patch-through quando presente no body
- `orchestrator-bridge`: `_write_routing_assigned_to_stream()` helper — escreve `routing.assigned` no session stream com `agent_type.media_capabilities` e `pool` JSON antes de cada ativação de agente (native, human, external-mcp)
- `channel-gateway/adapters/webrtc.py`: `_stream_watcher` atualizado — primeiro `routing.assigned` → `_on_routing_assigned()` (room + token + ready); subsequentes → `_on_routing_renegotiate()` (verifica mudança de medium, envia `webrtc.renegotiate` apenas se medium mudou, reutiliza room)

### Fase C — STT/TTS Pipeline no WebRTC ✅ (2026-05-20)
- `adapters/webrtc_room_client.py`: `IWebRTCRoomClient` Protocol + `LiveKitRoomClient` (livekit.rtc, graceful degradation sem SDK) + `MockRoomClient` (testes) + `resample_pcm_48_to_8()` (audioop + fallback struct-decimation para Python 3.13+) + `mp3_to_pcm()` (pydub, graceful)
- `adapters/webrtc.py`: `_start_stt_pipeline()` — bot token + `LiveKitRoomClient.connect()` + task `_stt_pipeline()`; `_stt_pipeline()` — `subscribe_customer_audio()` → `resample_pcm_48_to_8()` → `FallbackSTTProvider.stream()` → `_publish_transcript()` para Kafka `conversations.inbound` (`content_type=audio_transcript`); `_tts_inject()` — `FallbackTTSProvider.synthesize()` → `mp3_to_pcm()` → `room_client.publish_audio()`; `deliver_text()` dispara `_tts_inject()` quando `medium in (voice, video)` e `webrtc_tts_injection_enabled=True`; `_receive_loop()` trata `webrtc.message` → Kafka e `webrtc.interaction_reply` → Redis `menu:result:{session_id}`; `_close_session()` e `deliver_session_closed()` cancelam STT task e desconectam room client
- `tests/test_webrtc_stt_tts.py`: 30+ testes cobrindo resampler, MockRoomClient, STT pipeline → Kafka, TTS injection, DataChannel text/reply, STT disabled, teardown

### Fase D — Egress Recording ✅ (2026-05-20)
- `adapters/webrtc_provider.py`: `LiveKitProvider.start_egress()` implementado com `livekit-api` (`StartRoomCompositeEgressRequest` + `EncodedFileOutput(filepath=...)`); `stop_egress()` implementado (`StopEgressRequest`); dev_mode e ImportError tratados com graceful fallback (retorna mock egress_id); `MockWebRTCProvider.start_egress/stop_egress` sempre presentes
- `config.py`: 3 novas variáveis — `webrtc_recording_notice` (texto LGPD padrão), `webrtc_egress_output_dir` (diretório compartilhado LiveKit↔Gateway), `webrtc_egress_wait_s` (5s para LiveKit flushar o arquivo)
- `adapters/webrtc.py`: `__init__` recebe `attachment_store: Any | None` + `_session_egress: dict[str, dict[str, str]]` (session_id → {segment_id: egress_id}); `_start_egress(session_id, segment_id, room_name)` — guard Redis (`channel:webrtc:{sid}:egress:{seg}`), notice via TTS injection ou webrtc.message, `asyncio.sleep(1.5)`, `provider.start_egress()`, persiste egress_id Redis + dict; `_stop_all_egress(session_id)` — pop dict, dispara `_stop_egress_and_store` como task por segmento (idempotente); `_stop_egress_and_store(session_id, segment_id, egress_id)` — `stop_egress()` → sleep → `Path.read_bytes()` → `attachment_store.reserve/commit` → `redis.xadd(stream_key, {type: recording.completed, ...})` → `unlink` local file → `redis.delete(rec_key)`; `_on_routing_assigned()` dispara `_start_egress` quando `pool.webrtc_recording=True and medium in (voice, video)`; `_close_session()` e `deliver_session_closed()` chamam `_stop_all_egress()` antes do Phase C teardown
- `tests/test_webrtc_egress.py`: 30+ testes — `TestEgressStart` (notice, provider call, Redis), `TestEgressDoubleStartGuard` (second call no-op), `TestEgressRecordingOptOut` (flag=False, text medium), `TestEgressStopAndStore` (stop, commit, stream event, serving_url, redis delete, file deleted), `TestEgressStopNoFile` (graceful, event still written), `TestEgressStopAllIdempotent`, `TestEgressRoutingAssigned` (guard conditions), `TestEgressProviderImpl` (dev mode, MockProvider), `TestEgressNoAttachmentStore` (local path fallback)

### Fase E — Console Platform-UI (Overlay WebRTC) ✅ 2026-05-20
- `packages/platform-ui/package.json`: adicionados `@livekit/components-react@^2.6.0` + `livekit-client@^2.5.0`
- `hooks/useWebRTCSession.ts`: fetch token de `/api/webrtc/token/{sessionId}?role=agent&identity=<id>`, conexão `Room` LiveKit, publicação de local tracks (audio+video para video, só audio para voice), controles mic/câmera (mute/unmute), cleanup automático no unmount; medium=text retorna sem conectar
- `components/VideoGrid.tsx`: grid 2-up (remote main + local PiP) usando `track.attach(videoRef.current)` sem LiveKitRoom context; placeholder quando remote track ausente
- `components/MediaControls.tsx`: botões mic/câmera/desconectar, câmera oculta em medium=voice, badge de medium, tudo internacionalizado
- `components/WebRTCOverlay.tsx`: container condicional — medium=video → VideoGrid; medium=voice → AnimatedWaveform (20 barras CSS animadas); medium=text → null; status bar com indicator verde animado + timer de duração; conectando/erro tratados com Loader2/AlertTriangle
- `components/WebRTCSupervisorView.tsx`: connect como `supervisor_view` identity sem publicar tracks; renders VideoGrid (compact) ou indicador voz; null para text/non-webrtc
- `AgentAssistPage.tsx`: `WebRTCOverlay` injetado no início do bloco "Atual" tab quando `selected.channel === "webrtc"`; agentIdentity = `session.userId`
- `i18n/locales/en/webrtc.json` + `pt-BR/webrtc.json`: namespace `webrtc` completo (medium, overlay, controls, supervisor)
- `i18n/index.ts`: imports + registro do namespace `webrtc`

### Fase F — Widget do Cliente ✅ 2026-05-20
- `packages/agent-assist-ui/webrtc-widget.html` — widget standalone (single HTML, sem build); usa `livekit-client@2` via CDN (`cdn.jsdelivr.net`)
- Protocolo WS idêntico ao webchat (`conn.hello` → `conn.authenticate` → `conn.authenticated`) mas conecta em `/ws/webrtc/{pool_id}`
- `connectWebRTC()`: JWT auto-gerado (Web Crypto HS256), WS connect, auth handshake
- `webrtc.ready` handler: aplica medium indicator, chama `requestMedia()` → `connectLiveKit()` → publica local tracks (audio+video para video, só audio para voice)
- `requestMedia()`: `getUserMedia({ video, audio })` conforme medium; em caso de `NotAllowedError` ou qualquer falha → banner de permissão negada → cai para text → envia `webrtc.renegotiated: text` ao gateway
- `connectLiveKit()`: `new Room()` + `room.connect()` + `createLocalTracks()` + `publishTrack()`; remote video attached via `track.attach(remoteVideo)`; local vídeo PiP espelhado; waveform ativo no voice
- `handleRenegotiate(newMedium)`: `cleanupLocalTracks()` → `applyMedium()` → `requestMedia()` → `createLocalTracks()` → `publishTrack()` → `wsSend(webrtc.renegotiated)` — room LiveKit persiste, apenas tracks são ajustados
- Medium indicator bar: ícone (📹/🎤/💬) + label + badge; visível assim que `webrtc.ready` chega
- Media area: `video-grid` (2-up: remote full + local PiP) para video; `waveform` (20 barras CSS animadas) para voice; hidden para text
- Media controls: mic mute/unmute (🎤/🔇), câmera on/off (📷/🚫) — câmera oculta em voice; botão hangup → `webrtc.hangup` + `teardown()`
- Interaction cards: opções como botões → `webrtc.interaction_reply`; fallback text input quando sem opções
- `webrtc.session_closed` → `teardown()` + status "Encerrado"
- `teardown()`: desconecta room, para local tracks, fecha WS, reseta UI

---

## 17. Decisões em Aberto

| Questão | Status |
|---|---|
| Bridge PSTN → WebRTC (caller externo entra na sala LiveKit via SIP trunk) | ~~Deferido~~ **DECIDIDO 2026-08-20** — [`../adr/adr-voice-media-plane.md`](../adr/adr-voice-media-plane.md) **V3**: um único plano de mídia, a SALA; entrada por SIP ou por navegador, internamente sempre a sala. Custos nomeados lá: transcodificação G.711↔Opus, SFU como ponto único de falha, e `REFER` de saída no gateway SIP (risco novo). Esta linha ficou "deferido, precisa avaliação" por meses e a ideia foi repetida em 4 documentos sem nunca ser estudada — ver a emenda da V3 |
| Máximo de participantes por room (multi-party) | Definir por pool config: `webrtc_max_participants` |
| Layout de gravação por pool (speaker/grid) | `webrtc_egress_layout: "speaker" \| "grid"` |
| Whisper do supervisor via DataChannel (formato e visibilidade) | Usar `agents_only` visibility no stream — igual a nota interna |
| Qualidade de vídeo adaptativa (simulcast) | LiveKit suporta nativamente; habilitar por pool |
| End-to-end encryption (E2EE) | LiveKit suporta; ponderar vs gravação (E2EE inviabiliza egress do servidor) |

---

## 18. Invariantes

- **Channel Gateway é o único emissor de tokens LiveKit** — nunca emitir no browser, nunca expor `LIVEKIT_API_SECRET`.
- **Uma room LiveKit por sessão PlugHub** — nome sempre `plughub-{session_id}`.
- **Egress apenas quando pool.webrtc_recording=true** — nunca gravar sem configuração explícita.
- **LGPD notice obrigatório antes de iniciar egress** — mesmo guard do canal voice.
- **Supervisor sempre hidden=true** — nunca revelar presença ao cliente.
- **medium=text é o fallback universal** — toda sessão WebRTC deve funcionar sem media tracks.
- **Re-negociação nunca reinicia a sessão** — a LiveKit room persiste, apenas tracks são ajustados.
