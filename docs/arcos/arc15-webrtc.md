# Arc 15 — Canal WebRTC com SFU Enterprise

> ✅ **Atualização de 2026-09-14 (VOZ-01) — o bloco de 2026-08-20 abaixo descreve o estado ANTERIOR.**
> O compose demo sobe `livekit` (v1.8.4) + `coturn` (4.6.3); `livekit-api`/`livekit` são dependência;
> `_dev_mode` saiu e sem credencial o provider levanta `WebRTCProviderUnavailable` nomeando a env; a rota
> de token exige Bearer + capacidade no pool da sessão. Medido só dentro da rede do compose — browser no
> host é a VOZ-04. Detalhe: `CHANGELOG.md` § 2026-09-14 (10) e `adr-voice-media-plane.md` §5 V-F0.

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
| `voice` | Tronco SIP → serviço SIP do SFU (VOZ-02, §19) · legado: Twilio TwiML | Clientes no telefone, URA | **a mesma sala LiveKit** (SIP) · Twilio conference bridge (legado) |
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

> **Endereço da chamada (VOZ-26, 2026-09-17).** O segmento do caminho é um **identificador**, não
> necessariamente um pool: o gateway o resolve pela tabela `ChannelEndpoint` do agent-registry
> (`channel=webrtc`) e, sem linha, o trata como `pool_id` (e, vazio, usa `webrtc_default_pool_id`).
> Como no webchat, o endpoint dá à URL publicada um nome estável e permite trocar o pool por trás
> dela — a troca vale na próxima chamada, porque o `registry.changed` invalida o cache do resolvedor.
> Até esta data a resolução era ramo morto: o registro recusava `channel=webrtc` (400) e a tela de
> canais não oferecia o canal. Hoje a aba WebRTC tem **Endpoints** (sem conta de integração — o SFU é
> fiação da instalação) e **Configurações** (segmentação da fala). Gate:
> `infra/test/probe_webrtc_channel_endpoint.sh`.

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

> **Quem pode pedir (VOZ-01 + VOZ-15).** Bearer obrigatório · capacidade por papel recortada ao pool
> da sessão (`agent_assist.atender` publica; `contacts.monitorar` assina oculto) · identidade na sala
> vinda do JWT, nunca da query · e, para `role=agent`, **estar entre os ATENDENTES do contato**
> (`channel:webrtc:{sid}:media`, onde a instância humana é `human-{sub}`). Capacidade no pool
> responde *"você pode atender contatos daqui?"*; a chamada de um cliente pede a outra pergunta, e
> até 2026-09-17 ninguém a fazia — outro portador do mesmo grant recebia 200. Estado de mídia sem
> atendentes é a corrida normal do `routing.assigned`: responde `room_not_ready` (404), que o Console
> repete, e não 403, que o faria desistir. **Supervisor é exceção declarada** — assinar sem atender é
> a função. Ramos ao vivo: `probe_webrtc_media_plane.sh` F11 · `probe_webrtc_agent_console.sh` G8.

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

## 9. Gravação da chamada — egress, por PARTES (VOZ-06, 2026-09-18)

> ⚠️ O que esta seção descrevia até 2026-09-18 (a "Fase D": vídeo MP4 em layout `speaker`,
> `pool.webrtc_recording`, `sleep(5)` antes de ler o arquivo) **nunca rodou**: não havia serviço de
> egress em compose nenhum e o campo não tinha produtor. Foi substituído, não consertado.

**Decisões do dono:** só ÁUDIO, a sala MISTURADA (cliente, atendente, voz do bot) em OGG; liga pelo
**POOL** (`media_policy.recording`); o **fluxo pergunta** e a plataforma honra a recusa
(`core.contact.recording_opt_out`).

```
atendente de pool com recording=true entra      → aviso (texto e/ou voz; espera TOCAR) → parte N
conjunto de pools que gravam muda                → a parte fecha, a próxima abre (sem novo aviso)
bloco mascarado (NIV-07) começa                  → a parte fecha ANTES do prompt; nada é gravado
bloco termina                                    → parte nova
recusa lida (antes ou a cada 2 s durante)        → a parte em curso é DESCARTADA; nenhuma nova
chamada termina                                  → a parte fecha
parte fechada → wait_egress (fim REAL, sem sleep) → AttachmentStore `call_recording`, audio/ogg,
                expira pela retenção DA CLASSE → `recording.completed` no stream → rascunho apagado
```

- **Serviço:** `livekit-egress` no compose, mesmo Redis próprio do SFU; o arquivo é escrito num volume
  que ele e o gateway montam no MESMO caminho (`webrtc_egress_output_dir`). O egress roda como uid
  1001/gid 0: o gateway cria o diretório da sessão com modo `0770`.
- ⚠️ **Medido: `audio_only` NÃO dispensa o Chrome** (`sourceType WEB`) — misturar a sala é composição,
  e composição é o navegador. Ativo em ~2-3 s; imagem de 4,1 GB. Parte parada antes disso volta
  `EGRESS_ABORTED` sem arquivo: é **parte vazia** (`recording.discarded`, `reason=empty`), não falha.
- **Sem aviso entregue, não se grava** (`recording.skipped`, `notice_undeliverable`). Recusa ilegível
  antes de começar também não grava (`opt_out_unreadable`). Valor da tag que não é "falso" vale como
  recusa, com aviso no log.
- **Nunca finge gravar:** egress que não começa, que termina falho, arquivo fora do rascunho ou store
  ausente viram `recording.failed` com o motivo.
- **A gravação NÃO sai pela porta pública de anexos** (`/webchat/v1/attachments/{id}`, que toma o
  file_id como credencial): classe diferente de `webchat_attachment` responde 404.
- **Ouvir e exportar** (VOZ-36): `GET /v1/recordings/sessions/{sid}` · `/{file_id}/audio` ·
  `/{file_id}/export` no gateway, com Bearer + **`contacts.recording`** (`read_only` ouve,
  `read_write` exporta; só `admin`/`supervisor` nascem ouvindo, ninguém exportando). O escopo é o
  pool que **atendeu a parte** (`attrs.pools`, gravado pelo `CallRecorder`), no grant **e** no
  domínio de linhas; parte sem `pools` é recusada. Capacidade antes de resolver o id (sem oráculo
  de existência). Cada escuta, exportação e recusa (inclusive sem credencial) vai ao
  `audit_access_log` pelo tópico `audit.access` — a trilha tem uma escritora, a analytics-api. Na
  tela: `RecordingsPanel` na transcrição, que só busca o áudio no clique (buscar é escutar). Gate:
  `probe_voz36_recording_access.sh`.
- **Por onde o aviso vale** (decisão do dono, 2026-09-21): onde há TELA — o widget WebRTC —, o texto
  basta, como nas salas de conferência. Onde não há — a chamada telefônica —, só a fala entrega, e isso
  sai por construção: o aviso conta se chegou por texto OU voz, a chamada SIP não tem WebSocket de
  cliente, e sem aviso entregue a parte é pulada.
- **A faixa FIXA do widget** (VOZ-39, 2026-09-24). O aviso é mensagem de chat, que rola. O ESTADO vai ao
  widget como `webrtc.recording {state}` — `recording` · `paused` · `stopped` —, anunciado pelo
  gravador só quando MUDA e calculado depois de cada entrada:
  - `recording` sai depois do aviso e do egress;
  - a troca de atendentes não pisca;
  - `paused` é o bloco mascarado depois de já ter havido gravação, e na prática é da perna SIP, que
    não tem tela.
  Os dois widgets de demo mostram a faixa no cabeçalho. Gate: `probe_voz39_recording_badge.sh`.
  ⚠️ O egress que aborta DURANTE a parte só é visto no `stop`, e a faixa segue dizendo "Gravando" até
  lá (`VOZ-44`).
- **Config:** aviso em `webrtc.recording_notice` (aba WebRTC); retenção em
  `storage.call_recording_retention_days` (Plataforma → Retenção de dados), carimbada na hora de
  guardar — mudar o número vale para gravações NOVAS. Lidos a cada parte que começa, sem cache.
- Código: `adapters/webrtc_recording.py` (`CallRecorder`), `recording_config.py`. Eventos no stream
  (`agents_only`): `recording.completed` · `recording.skipped` · `recording.discarded` ·
  `recording.failed`. Estado vivo: `channel:webrtc:{sid}:recording`.
- Gate: `infra/test/probe_voz06_recording.sh` (três chamadas pelo tronco simulado; o conteúdo de cada
  parte é TRANSCRITO para provar que tem fala e que o pedido do PIN não está em nenhuma).

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
- `WebRTCSupervisorView.tsx` — view read-only para supervisores: montada na transcrição do contato (`SessionTranscript`) depois que o supervisor ENTRA na sessão, quando o canal (devolvido pelo `POST /supervisor/join`, do meta da sessão) tem sala de mídia; token de papel `supervisor` (oculto, sem publicar). *Até 2026-09-21 (VOZ-38) não era montada em lugar nenhum.*

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
channel:webrtc:{session_id}:recording       → parte gravando AGORA {part, egress_id, pools} (VOZ-06)
channel:webrtc:{call_sid}:session            → session_id (para correlação)
channel:sip:room:{room}                      → session_id ("abrindo" enquanto nasce) — idempotência do
                                               webhook `participant_joined` da chamada SIP (VOZ-02)
```

⚠️ **`channel:webrtc:{sid}:room_name` é gravada ANTES do `create_room` e apagada no fechamento**
(VOZ-02): é a testemunha que o controle compensatório do `auto_create` lê — sala `plughub-{uuid}`
que nasce sem ela é apagada (§19).

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
```

> **`webrtc_tts_injection_enabled` saiu em 2026-09-15 (VOZ-05 fatia 3).** O default real era
> `False` (este bloco dizia `True`), e com ele o agente de IA ganhava teto de áudio e ficava MUDO.
> Ligado, continuaria mudo: o bot entrava `hidden`, e participante oculto não entrega trilha a
> ninguém (medido contra o SFU). Hoje o agente de IA fala sempre que o bot leg converte (STT e
> TTS disponíveis) — ver `CHANGELOG.md` 2026-09-15 (26).

> **O bot leg são DOIS participantes da sala desde 2026-09-16 (VOZ-05 fatia 4, parte 1)** — e
> nenhum dos dois é participante da SESSÃO (sem segmento, fora do roster):
>
> | papel | identidade | na sala | existe quando |
> |---|---|---|---|
> | ouvinte | `bot-{sid[:8]}` | `hidden`, só assina — única entrada de STT | há atendente de áudio (humano ou IA) e STT |
> | voz | `voz-{sid[:8]}` | visível, só publica — TTS | há agente de IA de áudio, STT e TTS |
>
> Oculto assina normalmente; o que oculto não faz é ser ouvido. O barge-in é local: o ouvinte
> percebe o cliente e a voz para. A decisão da voz é tomada no `routing.assigned` ANTES de qualquer
> `await` — a primeira fala da IA chega junto da atribuição. Só a frase final sai do gateway;
> áudio para o STT vem do assinante, não do egress (medido: `TODO.md` § *VOZ-05 — áudio para o
> STT*). Ver `CHANGELOG.md` 2026-09-16 (2).
>
> **A trilha da voz nasce na PRIMEIRA fala, e a primeira fala espera ela ser assinada** (2026-09-22,
> ORQ-11). A negociação do transporte do publicador leva ~2 s (medido no SFU: `mediaTrack published`
> 2 s depois de o cliente entrar), e o que se capturava antes ia a lugar nenhum — o prompt curto do
> menu sumia inteiro, em toda chamada, sem erro. `publish_audio` espera `wait_for_subscription()` da
> publicação (teto `_FIRST_SUBSCRIPTION_S` = 3 s; estourou, fala e loga WARNING). Só a primeira fala
> espera; o log diz `trilha da voz assinada em X s`.

> **Transcrição da chamada — a conversa falada é MENSAGEM do falante (fatia 4, parte 2).** O
> ouvinte assina uma trilha por falante: o cliente (`customer-…`) e cada atendente humano
> (`agent-{sub}`, que na sessão é `human-{sub}`); a voz da IA e o supervisor não são transcritos.
> Só a frase final sai do gateway, como `NormalizedInboundEvent` com `content_type:
> "audio_transcript"` e confiança/janela em `content.payload`. O bridge a grava no stream no
> layout canônico com `payload.content.type = "audio_transcript"` (e `speech`), visibilidade `all`,
> e publica `message_sent` com o mesmo `content_type` — ClickHouse `messages` guarda a marca, e o
> transcript do supervisor a mostra como "voz". Decisões do dono:
>
> | destino | fala do cliente | fala do humano |
> |---|---|---|
> | stream da sessão (avaliador, supervisor) | sim, marcada | sim, marcada, papel do roster |
> | ClickHouse `messages` | `content_type=audio_transcript` | idem |
> | Console do humano (`agent:events`) | **não** — ele ouviu | **não** |
> | cliente (`conversations.outbound`) | — | **não** — ele ouviu |
> | steps `receive` (copiloto) | como hoje | **sim**, pelo mesmo `message_sent` do texto digitado |
> | histórico de chat (`session:{id}:messages`) | **não** | **não** |
>
> Gate ao vivo: `infra/test/probe_webrtc_human_transcript.sh`. Ver `CHANGELOG.md` 2026-09-16 (3).

> **Coleta por teclado e fala — menu com `collect` (fatia 5b).** A semântica mora numa casa só,
> `collect_core.py` (sem I/O, testável sem relógio): casa tecla, dígitos e fala com o menu, valida
> domínio/tamanho/pertinência, roda os prazos e emite UM desfecho — `value`, `invalid` (esgotou
> `max_invalid`) ou `timeout`. O adapter WebRTC é o primeiro renderizador:
>
> | o quê | como |
> |---|---|
> | prompt | falado com as teclas ("Para Email, tecle um ou diga Email."); a tela mostra as opções como sempre |
> | tecla | `sip_dtmf_received` no OUVINTE; o SFU a entrega a todos na sala, e **só a do `customer-…` responde** |
> | fala | a frase final é publicada como REGISTRO (`audio_transcript`) e só responde o menu se `collect.input` tem `voice` |
> | texto digitado | recusado (`collect_input_not_accepted`) quando `collect.input` não tem `text` |
> | eco | `plain` fala a tecla · `masked` bipa pela voz · `none` cala |
> | barge-in | tecla corta o prompt; fala corta só se a coleta aceita fala |
> | prazo da 1ª entrada | arma quando o prompt termina de tocar (ou quando não há o que tocar) |
> | inválido | ignorado sem eco, ou `invalid_message` falada e no widget; o prazo recomeça |
> | desfecho | `menu_result` — `{menu_id, interaction, result}` ou `{menu_id, outcome}`, que o bridge entrega como SINAL (fatia 5a) |
> | fim sem desfecho | resposta pela tela, menu novo, sessão fechada, ou o motor não espera mais (`menu:waiting` sumiu) |
>
> ⚠️ **No bridge, fala transcrita nunca responde menu** — até aqui ele a entregava crua, e "espera um
> pouco" virava escolha de botão. Menu de voz sem `collect` com `voice` não ouve o cliente.
> Menu mascarado continua no campo protegido da tela (fatia A); `checklist`/`form` com coleta
> por teclado/fala são respondidos pela tela, ditos no log. Gate ao vivo:
> `infra/test/probe_webrtc_voice_collect.sh`. Ver `CHANGELOG.md` 2026-09-16 (5).
>
> **Parâmetros de fala (VOZ-18).** `collect.voice` vale por menu:
>
> | parâmetro | como vale |
> |---|---|
> | `min_confidence` | contra a confiança MEDIDA do speaches — `exp` da média de `avg_logprob` dos segmentos do `verbose_json`, ponderada pela duração. Abaixo do limite = tentativa inválida, com a medida no log (o texto não). Sem segmento, a confiança é `None` (não medida): o limite não se aplica e o log diz |
> | `end_silence_ms` / `max_speech_s` | num ajuste (`SpeechTuning`) lido a CADA QUADRO do fluxo de STT do cliente: liga quando a coleta com voz começa, desliga no desfecho ou no fim sem desfecho. Default do provedor: 700 ms e 15 s |
>
> **Ruído não é fala (VOZ-19).** O speaches é chamado com o VAD do serviço ligado: sem ele, o Whisper
> transcrevia ruído, tom e cliques como "Obrigado."/"Tchau." (44 de 44 trechos medidos) e a transcrição
> gastava a tentativa do menu; com ele, 1 de 44, sem perder fala. Trecho esvaziado pelo VAD aparece no log
> do gateway como `sem fala pelo VAD`.
>
> ⚠️ **Sem default de `min_confidence`, e ele não filtra ruído**: as falas CERTAS vão de 0,35 a 0,92
> (`TODO.md` § VOZ-19). Tudo medido com voz sintetizada — fala humana é a `VOZ-20`. Provedor que não mede
> ou não ajusta (`measures_confidence` / `supports_tuning` falsos) é dito no log ao armar a coleta.
> Gate ao vivo: `infra/test/probe_webrtc_speech_tuning.sh`. Ver `CHANGELOG.md` 2026-09-17 (1).
>
> **Segmentação da fala por tenant (VOZ-21).** Os defaults do provedor são config do config-api,
> namespace `webrtc` (o canal é o recorte — a perna SIP terá os dela), editáveis em Configuração →
> Canais → WebRTC:
>
> | chave | default | faixa |
> |---|---|---|
> | `stt_energy_threshold` | 400 | 50–5000 (vale também para o barge-in) |
> | `stt_end_silence_ms` | 700 | 100–5000 |
> | `stt_gap_ms` | 700 | 100–5000 |
> | `stt_min_speech_ms` | 250 | 50–2000 |
> | `stt_max_speech_ms` | 15000 | 1000–60000 |
> | `stt_vad_filter` | true | booleano |
>
> O config-api não valida valor: o gateway valida (`speech_config.py`) e cai no default nomeando a
> chave. A chamada resolve os valores UMA vez, ao abrir o STT, e o log diz a procedência de cada um
> (`end_silence_ms=2500 (tenant)`, `gap_ms=700 (default: valor invalido)`); o `config.changed` do
> namespace invalida o cache, e com o config-api fora vale o último valor bom. O `collect.voice` de um
> menu continua vencendo enquanto espera. Provedor sem `supports_tuning` (Deepgram legado) é dito no
> log: a config não se aplica a ele. Gate ao vivo: `infra/test/probe_webrtc_stt_config.sh`. Ver
> `CHANGELOG.md` 2026-09-17 (3).
>
> **Telemetria passiva da fala (VOZ-22).** Cada chamada publica em `speech.metrics` (chave =
> sessão), só números: no fim do fluxo do CLIENTE, um `stt_stream_summary` (chão de ruído, falas,
> descartes pelo VAD e por curtas, cortes pela fala máxima, erros, confiança, segmentação em vigor com
> escopo); no fim de cada coleta por voz, um `collect_outcome` (desfecho, tentativas, recusas por
> confiança, tecla depois de fala). O provedor conta o que só ele vê (`SpeechStats`), o núcleo conta
> tentativas (`CollectSession.counters`), o renderizador publica numa task própria — o fluxo costuma
> acabar cancelado. A fala do atendente não entra: a recalibragem é do ambiente do cliente. Leitura:
> `GET /reports/speech/quality` por pool, com `sample_sufficient` abaixo de 30 chamadas. Gate ao vivo:
> `infra/test/probe_speech_metrics.sh`. Ver `CHANGELOG.md` 2026-09-17 (5).
>
> **Perfil de fala por ponto de entrada (VOZ-25).** O endpoint WebRTC aponta um perfil em
> `settings.speech_profile_id`; o perfil mora no config-api, namespace `speech_profiles` (uma chave por
> perfil, valor = objeto com qualquer `stt_*` da segmentação e `stt_model`, `stt_language`, `tts_model`,
> `tts_voice`). Resolução por chamada, UMA vez e compartilhada entre STT e TTS
> (`WebRTCAdapter._speech_settings` → `speech_config.resolve_session`): **menu → perfil → tenant →
> global → default**; modelo, língua e voz sem perfil vêm do tenant e, na ausência, do env (`VOZ-17`). O modelo é
> escolhido **dentro do mesmo serviço** speaches (a URL continua topologia). Perfil ausente, campo
> inválido, chave desconhecida e config-api fora são ditos no log e não valem; `config.changed` de
> `speech_profiles` invalida o cache e vale na próxima chamada. O log da chamada traz `voz da chamada
> session=… perfil=…` com a procedência de cada campo (`profile:<id>`), a telemetria grava o
> `speech_profile_id` em vigor (e o `stt_model` no resumo), e `/reports/speech/quality` agrupa por
> pool × perfil. Tela: aba WebRTC → Configurações → *Perfis de fala*, e o seletor no endpoint. Gate ao
> vivo: `infra/test/probe_webrtc_speech_profile.sh`. Ver `CHANGELOG.md` 2026-09-17 (7).
>
> **O default sem perfil, e a conferência contra o serviço (VOZ-17).** Duas metades da mesma frase
> — *"o que a chamada usa quando ninguém escolheu"* e *"o que é escolhível"*.
>
> **(a) A camada do TENANT.** `stt_model`, `stt_language`, `tts_model` e `tts_voice` passam a viver no
> namespace `webrtc`, com os MESMOS nomes do perfil, entre o perfil e o env: **perfil → tenant →
> env**. O env não some — é o que faz a imagem subir falando sem config-api —, mas deixou de ser o
> único lugar: config de negócio em env, sem tela, é a dívida que a `VOZ-05` abriu. A leitura sai da
> MESMA ida ao config-api que já trazia a segmentação (`SpeechSegmentationConfig.voice`), porque duas
> leituras do mesmo namespace podem discordar dentro da mesma chamada; chave ausente **não é valor**
> (quem não declara segue no env, e a procedência diz isso: `env` · `tenant`/`global` · `profile:<id>`).
>
> **(b) A conferência na GRAVAÇÃO.** Antes, a única checagem era o PADRÃO do nome, que não diz nada
> sobre existir: um `stt_model` plausível e não instalado era aceito e só falhava na chamada — 404 do
> serviço a cada frase, `fala PERDIDA` no log, nada vermelho. Agora o channel-gateway é a porta:
> `GET /v1/speech-models` (o que o serviço tem INSTALADO, por tarefa, com as vozes de cada modelo de
> TTS), `PUT`/`DELETE /v1/speech-profiles/{id}` e `GET`/`PUT /v1/speech-defaults` — todas com Bearer +
> `config.channels` (leitura para ver, escrita para gravar), o tenant vindo do TOKEN. A conferência é
> sobre a resolução COMPLETA (env ⊕ tenant ⊕ perfil), nunca sobre o corpo isolado: um perfil que troca
> só a voz é conferido contra o modelo que VAI valer. Recusa por modelo ausente, tarefa trocada
> (modelo de TTS no campo de STT), voz que não é daquele modelo, língua não declarada, campo
> desconhecido e faixa fora. **Serviço de fala inalcançável ⇒ 503 e NÃO grava** (decisão do dono):
> config gravada sem conferência é indistinguível da conferida na leitura seguinte, e com o serviço
> fora não há fala acontecendo.
>
> ⚠️ **O catálogo é `/v1/models` (instalados), nunca `/v1/registry`** (726 no demo): modelo baixável e
> não baixado falha igual na chamada. ⚠️ **A porta CRUA do config-api continua aceitando** quem tem
> `config.channels` — esta entrega fecha o caminho da TELA, e a diferença é decisão, medida pelo ramo
> C1 do gate e nomeada em `VOZ-29`. Tela: aba WebRTC → Configurações → *Padrão sem perfil*, e os
> campos de modelo/voz dos perfis viraram seleção do catálogo (com o serviço fora, a tela diz e não
> deixa gravar). Gates: `infra/test/probe_speech_model_conference.sh` e a FASE 4 de
> `probe_webrtc_speech_profile.sh` (a chamada SEM perfil abrindo com o modelo do tenant e
> transcrevendo com ele). Ver `CHANGELOG.md` 2026-09-17 (13).
>
> **Verificação ativa do caminho de fala (VOZ-23).** Camada B da recalibragem: um processo próprio
> (`speech-check`, a imagem do gateway com outro comando) é um CLIENTE do gateway — cria um endpoint
> WebRTC **temporário** apontando o pool de calibração `speech_check` com o perfil a verificar, entra na
> chamada como participante LiveKit, fala as frases de referência (`speech_check/reference.py`,
> versionadas) e mede na saída do caminho real: as transcrições em `conversations.inbound` e o resumo em
> `speech.metrics` — que diz qual perfil e modelo o gateway APLICOU, porque o pedido não prova a
> aplicação. O resultado (`speech_check_result`, só números) vai ao ClickHouse `speech_checks`; o
> endpoint temporário é apagado sempre. Disparo: pool webhook `speech_check_trigger` → tool MCP
> `speech_check_run`. A **linha de base** é uma execução que uma PESSOA marca em
> `speech_check_baselines` (config-api); `GET /reports/speech/checks/compare` mostra deltas e os itens
> que regrediram, e cada ausência de comparação tem status próprio (`no_baseline`, `baseline_invalid`,
> `reference_changed`, `baseline_unavailable`) — nunca um delta zero. Mede o caminho; **não** propõe
> limite de cliente (voz sintetizada não os decide, VOZ-18/19) e não aplica nada. Gate ao vivo:
> `infra/test/probe_speech_check.sh`. Ver `CHANGELOG.md` 2026-09-17 (8).
>
> **A superfície da verificação (VOZ-27).** A `VOZ-23` deixou tudo isso atrás de `curl` com token de
> serviço, e medição que só roda quando alguém lembra não é vigilância. Três peças fecham o arco:
> **(a) periodicidade** — a Agenda `speech_check_default` (diária, 03:30) aciona o MESMO pool webhook,
> e ela nasce de arquivo: `infra/scheduler/*.json` semeado pelo job `agenda-seed` (seed-if-absent,
> `docs/product/scheduler-agenda-spec.md` § 4a). O grão é uma agenda por perfil, e **perfil novo não
> ganha agenda sozinho** — a tela diz isso em vez de deixar o silêncio parecer cobertura.
> **(b) a tela** (aba WebRTC → Configurações → *Verificação do caminho de fala*): histórico por perfil,
> executar agora, marcar a linha de base e a comparação com os itens que regrediram. Ela nunca declara
> regressão nem marca base sozinha.
> **(c) a porta com portão** — `POST /v1/speech-checks` no channel-gateway, Bearer + `config.channels`
> em ESCRITA, que repassa ao executor interno com o token de serviço. Existe porque as duas portas
> anteriores não serviam à tela: o token de serviço não pode viajar ao browser, e a porta do pool
> webhook é anônima por construção. **`requested_by` sai do TOKEN** (`user:{sub}`), nunca do corpo —
> autoria preenchida pelo chamador não é autoria; a Agenda carimba `agenda:{seed_id}`.
>
> ⚠️ **A verificação RECUSADA vira linha** (`failure_reason: check_running`, agregados nulos): o 409
> de "já há uma em curso" morre numa resposta HTTP que a Agenda não lê, e sem o registro a noite em
> que a verificação não rodou fica idêntica à noite em que rodou e foi bem. É a única das quatro
> recusas do serviço que vira evento — as outras três são pedido malformado, cujo autor recebe o erro
> na hora. Gate ao vivo: `infra/test/probe_speech_check_surface.sh`. Ver `CHANGELOG.md` 2026-09-17 (9).
>
> **Teclado do widget (fatia 5c).** O `webrtc.interaction` leva `collect` — só o que a TELA precisa
> (`input`, `domain`, `min_digits`, `max_digits`, `terminator`; nunca mensagem nem prazo) — e o
> widget desenha o teclado pelo domínio (`*`/`#` só quando o domínio ou o terminador os pedem):
>
> | menu | a tecla do widget vira | por onde |
> |---|---|---|
> | comum (botões, ou campo só de teclado) | DTMF (`publishDtmf`) — o mesmo evento da perna SIP | SFU → ouvinte |
> | campo de dígitos mascarado | caractere no campo protegido, enviado no `webrtc.menu_submit` | WebSocket do gateway — **nunca** o SFU, que entregaria o dígito a atendente e supervisor |
>
> A resposta de campo de dígitos que chega pela TELA passa pela mesma regra da tecla
> (`CollectPlan.accepts_digits`): fora do domínio ou do tamanho não vai ao menu, o widget recebe
> `conn.error collect_invalid` (com a `invalid_message` do fluxo, se houver) e, esgotado
> `max_invalid`, o menu recebe o desfecho `invalid`. O valor recusado nunca vai ao log.
> Gate: `infra/test/probe_webrtc_keypad.sh`. Roteiro assistido do navegador (medido em 2026-09-16):
> `http://localhost:5173/webrtc-widget.html?pool=probe_voz05c_keypad` → tecla do menu de botões →
> o gateway registra `value por dtmf`; PIN pelo teclado → `voz05c-pin-recebido`, e **nenhuma**
> tecla a mais chega ao ouvinte naquela sessão (`grep -cE 'por dtmf|webrtc dtmf: tecla'` = 1), com
> o PIN ausente do log do gateway, do bridge e do stream. Ver `CHANGELOG.md` 2026-09-16 (6).

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

### Fase D — Egress Recording ⚠️ nunca rodou — substituída pela VOZ-06 (§9) em 2026-09-18
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
- `infra/demo/web/webrtc-widget.html` — widget standalone (single HTML, sem build); usa `livekit-client@2` via CDN (`cdn.jsdelivr.net`)
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
| Whisper do supervisor via DataChannel (formato e visibilidade) | Usar `agents_only` visibility no stream — igual a nota interna |
| Qualidade de vídeo adaptativa (simulcast) | LiveKit suporta nativamente; habilitar por pool |
| End-to-end encryption (E2EE) | LiveKit suporta; ponderar vs gravação (E2EE inviabiliza egress do servidor) |

---

## 18. Invariantes

- **Channel Gateway é o único emissor de tokens LiveKit** — nunca emitir no browser, nunca expor `LIVEKIT_API_SECRET`.
- **Uma room LiveKit por sessão PlugHub** — `plughub-{session_id}` quando a sessão cria a sala; a da chamada SIP nasce no serviço SIP (`plughub-sip-…`) e é **adotada** como a da sessão (`_room_of`). Um nome por sessão, nunca duas salas.
- **Sala `plughub-{uuid}` sem sessão viva não sobrevive** — `auto_create` está ligado desde a VOZ-02, e o gateway a apaga no `room_started` (§19).
- **Grava só quando o POOL de quem atende pede** (`media_policy.recording: true`) — ausente = não grava (VOZ-06).
- **Sem aviso entregue, não se grava; recusa honrada até o fim; o bloco mascarado nunca é gravado** (§9).
- **Supervisor sempre hidden=true** — nunca revelar presença ao cliente.
- **medium=text é o fallback universal** — toda sessão WebRTC deve funcionar sem media tracks.
- **Re-negociação nunca reinicia a sessão** — a LiveKit room persiste, apenas tracks são ajustados.

---

## 19. Perna SIP — chamada telefônica entrante (VOZ-02, 2026-09-18)

Primeira fatia da V-F5 do [`adr-voice-media-plane.md`](../adr/adr-voice-media-plane.md): o cliente
no **telefone** chega à mesma sala, ao mesmo bot leg e ao mesmo agente que o cliente no browser.
O canal é **`voice`** (ADR V2), não `webrtc` — o canal diz onde o cliente está.

```
tronco SIP ──INVITE (digest)──► livekit-sip ──JOIN──► sala plughub-sip-_<ani>_<rand>
                                                         │ webhook assinado (room_started,
                                                         │ participant_joined/left, room_finished)
                                                         ▼
                              channel-gateway  POST /v1/livekit/webhook
                                 └─ _sip_arrived: DNIS → ChannelEndpoint `voice` → pool
                                    _open_session(canal=voice) → routing → bot leg na sala
```

| Peça | Onde |
|---|---|
| Conversor SIP ↔ sala | serviço `livekit-sip` (imagem por digest) + `livekit-redis` (psrpc) no compose demo |
| Tronco e regra de despacho | `infra/sip/*.json`, semeados pelo job `sip-seed` (`infra/seed/seed_sip.py`, seed-if-absent; `SIP_SEED_RECONCILE=true` recria); senha só por env. **Diretório vazio sai 3** (mount que não montou, nunca "nada a fazer"; `SIP_SEED_ALLOW_EMPTY=true` para instalação sem SIP) e o job tem `restart: on-failure:5` (VOZ-41) |
| Conferência de cobertura | `sip_trunk_watch.py` (task de boot `sip-trunk-watch`, 5 min): todo `identifier` de endpoint `voice` ATIVO tem de estar em `numbers` de algum tronco de entrada no SFU (tronco sem `numbers` aceita todos). DESCOBERTO → ERROR nomeando os números, repetido de hora em hora; SFU ou registro fora → WARNING *"NÃO conferida"*; volta → INFO *restaurado*. Existe porque o Redis do SFU não persiste (`--save ""`) e, sem tronco, o `livekit-sip` descarta a chamada como `flood` sem 4xx e sem linha no gateway (VOZ-41) |
| Leitura do participante SIP | `adapters/sip_leg.py` (`parse_sip_participant`: kind SIP, `sip.trunkPhoneNumber` = DNIS, `sip.phoneNumber` = ANI) |
| Nascimento, desligar, recusa | `adapters/webrtc.py`: `on_livekit_event` · `_sip_arrived` · `_sip_hangup` · `_sip_platform_close` · `_sip_refuse` |
| Saída de fala | `adapters/voice_router.py` — o canal `voice` tem dois donos; a sessão SIP vai ao adapter WebRTC, o resto ao Twilio |
| Política de mídia | `voice` passa a EXIGIR `media_policy` no pool (registry) e o bridge a leva no `routing.assigned` |

**Regras que a fatia fixou:**

- **O número DISCADO é o endereço** — `ChannelEndpoint` `voice` com o DNIS em E.164. **Sem
  endpoint, a chamada é RECUSADA** (sala apagada → 486) e o motivo vai ao log nomeando o número;
  nunca há pool default. Registro inalcançável também recusa.
- **Idempotência do nascimento** por `SET NX channel:sip:room:{room}` — o webhook pode repetir.
- **Número oculto** vira contato `sip:{callID}`; nunca um ANI inventado.
- **A última fala toca antes do BYE (VOZ-42).** Encerrando pela plataforma, a fila de fala inteira
  é drenada (o que o fluxo já mandou falar, e a despedida atrás), no teto `_SIP_FAREWELL_MAX_S`;
  chamador que desliga na espera fecha UMA vez, como `customer_hangup`. Gate: S5f do `probe_voz02`.
- **Desligar pelos dois lados.** O chamador sai (`participant_left` da identidade SIP ou
  `room_finished`) → `customer_hangup`. A plataforma encerra → a despedida é falada (teto 10 s),
  depois `agent_done` e a sala é apagada — o que manda BYE ao telefone. Um `contact_closed` só.
- **Texto no telefone:** fala de `agent_ai` é sintetizada; texto de outra origem é logado como
  *NÃO entregue* (não há tela). Menu sem coleta tem o prompt falado; com coleta, segue a coleta por
  voz/DTMF do bot leg (§ Fase C e VOZ-08/VOZ-19).
- **Controle compensatório do `auto_create`.** O serviço SIP entra por JOIN e não cria sala; com
  `auto_create: false` toda chamada levava 486. Ligado por decisão do dono, com a garantia movida
  para REAÇÃO: `police_room` apaga, no `room_started`, sala `plughub-{uuid}` sem
  `channel:webrtc:{sid}:room_name`. Salas com prefixo SIP e salas fora do prefixo não são policiadas.
- **Toda chamada é ATENDIDA no nascimento, pela LINHA (VOZ-35, 2026-09-23).** O serviço SIP só
  manda o 200 OK quando há trilha na sala para o participante SIP assinar. Até a VOZ-35 só o agente
  de IA publicava, e uma chamada para pool humano (fila inclusa) tocava 60 s e caía com 486, contada
  como `customer_hangup` (medido). Hoje, em toda chamada SIP, com IA ou sem, a
  **linha** (`linha-{sid[:8]}`, visível, só publica) entra no `_sip_arrived` e publica uma trilha
  **muda**. Medido: basta a trilha, sem quadro nenhum; a chamada é atendida em 0,3 s e o conversor
  mantém o RTP contínuo sozinho (1247 de 1250 pacotes, maior intervalo 40 ms). Ela sai no
  teardown. **O piso é silêncio** (decisão do dono): conteúdo na espera é de um agente de fila, e
  a fala da IA e o microfone do humano tocam por cima. Linha que não entra ou não publica é ERROR
  no log, nunca *"atendida"*. Gate: `probe_voz35_sip_line.sh`.
- **O conversor transcodifica** G.711 ↔ Opus (medido: trilha `audio/opus`, PCMU no SIP). O ADR §8
  foi corrigido.
- **Teclas do telefone (VOZ-31).** Fora de banda (RFC 4733) o serviço SIP entrega a tecla como
  `sip_dtmf_received` com a identidade `sip_…`, e ela responde o menu de teclado como a do widget.
  **Sem `telephone-event` negociado a tecla NÃO chega**: a chamada é atendida assim mesmo e o tom
  dentro do áudio é ignorado (medido).
- **Dado protegido pelo telefone é coletado por TECLA, sob PAUSA DE MÍDIA (NIV-07, 2026-09-18).**
  A tecla SIP chega a todos na sala (medido), então, no menu mascarado, o gateway grava
  `channel:webrtc:{sid}:media_hold`, tira da sala quem não é o cliente nem bot
  (`listener_identity`/`voice_identity`/`line_identity`) e só então fala o prompt. A rota de token responde **409
  `masked_collect_in_progress`**, e o Console mostra *"Áudio pausado"* e reconecta ao fim. Quem entra
  no meio desfaz a coleta (`aborted` → `on_failure`), e pausa que falha desfaz sem prompt.
  `masked` + fala é recusado (NIV-08), a transcrição é descartada no bloco, o histórico recebe a linha
  redigida, e eco `plain` vira bipe até a NIV-06. Sem `telephone-event` a coleta expira, e nada vaza.
  A perna Twilio **recusa** menu mascarado. Risco residual: tecla adiantada antes do menu, com o
  humano ainda na sala, chega a ele.
- **O evento é do CANAL DA SESSÃO**: transcrição, fala do atendente e desfecho de coleta de uma
  chamada SIP saem com `channel: voice` (até a VOZ-31 saíam `webrtc`, o nome do adapter).

**Gates:** `infra/test/probe_voz02_sip_inbound.sh` (chamada real, com um cliente SIP de teste em
G.711 + digest — `_sip_ua.py`) e `probe_webrtc_media_plane.sh` A3/D4/D4g/D5 (o controle
compensatório, com controle positivo). O mesmo probe mede as teclas (K1–K4, B1). Testes:
`tests/test_sip_leg.py`.

**Borda SIP (VOZ-32, 2026-09-23, publicação opt-in):** a camada
`docker-compose.sip-edge.yml` publica 5060/UDP e a faixa `rtp_port` e liga `use_external_ip`; o `up.sh`
só a inclui com `PLUGHUB_SIP_EDGE=true` no `.env.demo`, e **recusa abrir** (fica fechada e diz por quê)
se faltar a senha de algum tronco ou se a de demo for a do `.env.demo.example`. As senhas **não** passam
pela camada: o `sip-seed` as lê do `.env.demo` por `env_file` com a borda aberta ou fechada (VOZ-43 —
quando a camada as fornecia, fechar a borda deixava o tronco no SFU com uma senha e o seed com outra). **Autenticação é digest, nunca lista de IPs**: o Docker Desktop reescreve a origem de todo
pacote que entra por porta publicada (medido: `172.17.0.1`), e o seed recusa tronco sem autenticação.
Com a Twilio, o produto é o número de voz com TwiML `<Dial><Sip username password>` (modelo em
`infra/sip/twilio_inbound.json.example`). Gate: `infra/test/probe_sip_edge_surface.sh` (+ `mut_`).
Medida com celular pela Twilio (`twilio_inbound.json`): PT 101, sequência de teclas inteira, PIN
mascarado. ⚠️ **No Windows, a rede tem de estar como PRIVADA**: o Docker Desktop nasce com regras de
BLOQUEIO no perfil Público, que vencem as de permissão — a chamada chega à Twilio e morre antes do
serviço SIP, sem INVITE no log. Passo a passo e medição: `CHANGELOG.md` § 2026-09-23 (14).

**Fora da fatia:** TLS/SRTP, `REFER` e chamada
sainte, tecla longa e SIP INFO com operadora, tela para tronco e regra de despacho, e
provedor por **registro** (o conversor recebe por tronco, não se registra), e o eco `plain` de
segredo (`NIV-06`). Fichas `VOZ-33`, `VOZ-34` e `NIV-06` no `pending.md`.


## 20. Chamada presa a um contato de chat (WCH-01, 2026-09-21)

Decisão em [`adr-chat-call-as-medium.md`](../adr/adr-chat-call-as-medium.md): o contato do site é
`webchat`, e a chamada é MEIO que ele ganha. O texto segue pela conexão do chat (stream canônico,
cursor, anexos, árvore); a chamada é uma **segunda conexão, só de chamada**, na mesma página.

| Peça | Onde |
|---|---|
| Porta | `WS /ws/call` (`main.py`) → `WebRTCAdapter.handle_call_ws` (`adapters/webrtc_call.py`) |
| Credencial | o token do CHAT (mesmo segredo por tenant) + `session_id`; a sessão tem de ser `webchat`, do mesmo `sub`, aberta (`closed_recorded` ausente) e sem outra chamada. Alheia e inexistente recebem a MESMA recusa (`session_not_found`) |
| Atendentes | `_call_stream_watcher`: lê o stream desde o início e mantém o conjunto de AGORA (`routing.assigned` / `participant_left`). A sala nasce quando esse conjunto oferece mídia — nunca pelo primeiro atendente, que pode ser um agente de fila que já saiu |
| Política do pool | lida pelo gateway, FRESCA do registry, só quando há chamada — o bridge manda `not_webrtc` para chat (uma leitura HTTP por ativação de todo chat seria custo sem leitor). Registry fora → `registry_unavailable`, que não oferece nada |
| Sem oferta | `webrtc.call_pending {reason, policy_sources}` ao widget e INFO no log; nenhuma sala. A chamada monta quando a oferta aparece |
| Fim | desligar/cair encerra SÓ a chamada (sala apagada, gravação fechada, chaves `room_name`/`media`/`media_hold` apagadas) e **nunca** publica `contact_closed`; o `ws_alive` do contato é do chat e não é tocado. `session_closed` no stream encerra a chamada (`webrtc.call_ended {reason: contact_closed}`) |
| Texto | recusado nesta conexão (`text_goes_through_chat`) |
| Anúncio | `media.call {state: started|ended, reason}` no stream (`agents_only`, registro do trecho) e em `agent:events:{sid}` — o Console monta a sobreposição num contato de chat por ele (`ContactSession.callActive`, `hasMedia`) |
| Tela de pools | o editor de `media_policy` aparece para `webchat`, OPCIONAL (sem ela o cliente do chat não liga); continua obrigatório para `webrtc`/`voice` |
| Widget de demo | botão 📞 Ligar no `infra/demo/web/webchat-test.html` (`livekit-client` na mesma versão do widget webrtc) |

**Sem bot leg nesta fatia:** nem transcrição nem voz de IA. O agente de IA não consome áudio numa
chamada de chat (`_ceiling(state, sid)` passa `bot_leg_audio=False`), e o estado de mídia diz
`NO_BOT_LEG_REASON` em vez do ERROR de "não transcrita".

**Achado de passagem, corrigido:** o XREAD dos dois observadores bloqueava 5 000 ms, igual ao
`socket_timeout` padrão do redis-py 8 — toda leitura ociosa estourava o socket, caía no `except` e
dormia 1 s (no observador do canal isso era `debug`, calado). `_STREAM_BLOCK_MS = 3 000`, com teste
que compara com o timeout do cliente.

**Supervisor (WCH-05, 2026-09-21).** A visão oculta decidia pelo canal e não via chamada de chat.
Hoje a transcrição da supervisão deriva `callActive` do último `media.call` do stream (sobrevive a
recarregar) e a visão decide por `hasMedia(channel, callActive)`, o predicado do Console; uma segunda
chamada remonta a visão (`key` pela entrada). A nota de `/supervisor/message` é publicada também em
`agent:events:{sid}` — o Console não lê o stream — com autor `supervisor`. A saída do supervisor
(`participant_left`, autor só no JSON aninhado) não entra no cálculo do teto e não é alarme.

**Console recarregado (WCH-06, 2026-09-22).** `GET /api/conversation_history` projeta o stream
(`lib/console-history.ts`): `call_active` do último `media.call` e as notas do supervisor, intercaladas
pela hora. O Console aplica no `fetchHistory`; F5 no meio da chamada remonta a sobreposição.

**O atendente desliga a chamada (WCH-07, 2026-09-22).** `POST /webrtc/call/{sid}/end` — mesmo
portão do token de mídia (`_webrtc_media_caller`), só quem ATENDE, só contato `webchat` com chamada
em curso. Grava `media.call.end_requested` no stream, com o autor; o observador da chamada a encerra
com `agent_hangup` se o pedido for posterior à anexação (o replay desde o início não pode derrubar
uma chamada nova com o pedido da anterior). No Console, é o botão vermelho da sobreposição.

**A chamada nos relatórios (WCH-08, 2026-09-22).** O gateway publica o INTERVALO no tópico
`media.calls` (`call_events.py`, schema `@plughub/schemas/media-calls.ts`, chave `session_id`):
`call_started` na anexação e `call_ended` no fim, com o **mesmo `call_id` = id da entrada
`media.call started` no stream** — o discriminador da CHAMADA, não da sessão, porque um contato tem
N chamadas. O fim leva a linha inteira do início (+ `ended_at`, `duration_ms`, `end_reason`); o pool é
o de quem ATENDE (procedência `pool:<id>` da política de mídia, D10) e o tenant é o da SESSÃO. Sem id
de stream não há `call_id` e nada é publicado, com ERROR; fim sem início conhecido é WARNING.
A analytics-api grava `call_intervals` (`ReplacingMergeTree(row_version)`, versão
`coalesce(ended_at, started_at)`, partição pelo INÍCIO). Em `/analise/sessions`: filtro
**"Com chamada"** no predicado único (lista, série e tokens) e, na célula de duração, uma 2ª linha
📞 com a soma das chamadas TERMINADAS, `×N` e selo "em curso" — nunca coluna própria (a coluna reabriu
o scroll horizontal e escondeu `process`, medido no browser). A transcrição de contato fechado, lida
do ClickHouse quando o stream expira, recebe as chamadas como `media.call` com duração.
Duração de chamada é tempo de VOZ: não se soma nem se compara com `elapsed_time_ms`.
Medido: duas chamadas num contato — 92,9 s `agent_hangup` + 54,2 s `customer_hangup` → `2m 27s ×2`.

**A IA na chamada de chat (WCH-09, 2026-09-22).** O bot leg entra como no canal `webrtc`: ouvinte
quando há atendente de áudio (humano ou IA) e STT; voz quando a IA atende com `agent_publish` de
áudio e o gateway converte. As quatro peças que o tornam possível:

| peça | onde | por quê |
|---|---|---|
| registro da sessão (`_sessions`, canal `webchat`) | `_register_attached_session`, na sala pronta | sem ele a fala transcrita era descartada (`sessao sem registro de abertura`) |
| a fala da IA | `chat_call_outbound`, chamado pelo `OutboundConsumer` para todo `webchat`, **síncrono** e antes da entrega ao chat | a saída de um contato de chat vai ao adapter do webchat, que nunca conheceu a chamada |
| o menu que chegou ANTES da chamada | `_rearm_pending_menu`: o watcher guarda o último `interaction_request` ao cliente e, na sala pronta, rearma-o se `menu:waiting` ainda existe; dedupe por `menu_id` | o caso comum (o cliente lê o prompt e só então liga) deixava o menu sem fala e sem coleta — medido: cinco falas recusadas e o menu expirando |
| o chat do cliente | `StreamSubscriber._map_message` descarta `audio_transcript`, explícito | a fala do atendente tem visibilidade `all`; o cliente a OUVIU |

⚠️ **E a resposta por voz tem UM registro** (WCH-11, 2026-09-22): o `menu_result` de uma coleta por
`voice` continua sendo entregue ao menu, mas não vira segunda mensagem do cliente — o enunciado já
está na sessão como `audio_transcript`. Detalhe e mecanismo em `conference-mechanics.md` § Mudança 46.

A fala transcrita só RESPONDE menu com `collect.input` contendo `voice` (regra da VOZ-05 fatia 5b, que
não muda): o `skill_navegacao_llm_v1` passou a declarar `collect: {input: [voice, text]}` no `ouvir`.
Texto digitado pelo humano e aviso de sistema não são falados. Pool sem `media_policy` num contato de
chat é config legítima (o atendente responde pela tela) e loga INFO, não WARNING.

Medido no browser do dono (sessão `492aa613`): menu rearmado na sala pronta, desfecho `value por voice`,
o LLM classificou a frase falada (`destino.sac.especialista`), a voz saiu com a IA e o ouvinte ficou
para o humano, cuja fala foi transcrita; especialista de formulário respondeu pela tela.
**A transferência é DITA, e a voz termina a frase antes de sair (WCH-10, 2026-09-22).** São duas
metades, e uma sem a outra não entrega nada:

- **o aviso é do FLUXO, não do mapa.** `skill_navegacao_llm_v1` ganhou dois `notify` antes de
  transferir: `avisar_destino` (confirma o rótulo da folha, `{{nivel.node_label}}`) e
  `avisar_escape` (não soube classificar). Dois porque são desfechos diferentes — dizer *"Entendi:
  Nenhuma dessas"* confirmaria ao cliente uma coisa que ele não pediu. Mensagem POR FOLHA
  continua proibida (D7 do ADR da árvore): o texto é do fluxo, o rótulo vem da árvore.
- **a voz sai AGENDADA** (`_stop_voice_soon` → `_drain_speech`, teto `_VOICE_DRAIN_MAX_S` = 8 s).
  O `_stop_voice` direto — que continua certo no fim da chamada — cortava a frase em curso: o
  humano entra um instante depois do aviso, `_decide_voice` devolve `False` e o tocador era
  cancelado no meio. O que a drenagem lê é `_speech_pending`, contado **na enfileirada** e zerado
  quando a mensagem termina de qualquer jeito (tocada, interrompida ou não falada); `_speaking`
  não serviria, porque só é marcado depois de a sala responder. Estourou o teto: sai assim mesmo e
  loga WARNING. Agente de IA de áudio que volta durante a janela CANCELA a saída
  (`_cancel_voice_stop`); o fim da chamada (`_stop_bot_leg`) não drena nada — não há para quem
  falar. Testes: `TestVozNaTransferencia` (6 casos, com bateria de mutação: sem dreno, sem teto e
  sem o decremento, três mutações pegas).

**A fala tem registro, e "ouvi duas vezes" virou uma linha de log (WCH-13, 2026-09-22).** Cada
mensagem que a plataforma manda falar loga `webrtc fala: enfileirada #N (x car., y na fila)`, com
contador por sessão. Existe porque responder *"saiu daqui uma vez ou duas?"* exigia cruzar Kafka,
o serviço de TTS e o SFU — e a resposta, medida em três contatos, foi **uma**. O eco que o dono
ouviu era a BANCADA: quem testa as duas pontas com um fone só ouve a trilha da voz no widget **e**
no Console, que a assina ao entrar (comportamento certo — o atendente precisa ouvir o que a IA diz
ao cliente); por isso o eco só aparecia nos contatos em que o Console entrava DENTRO da frase, e
mudar a aba do Console o mata. ⚠️ Os widgets ganharam UM `<audio>` por `track.sid` (em vez de um
novo a cada evento de assinatura) como DEFESA, não como causa deste sintoma — `attach()` sem
argumento cria tocador novo, e reassinatura acontece.

Deixou fichas: `WCH-10` (transferência calada; voz corta a frase ao sair) · `WCH-11` (resposta falada
duplicada e rotulada `[Seleção: …]`) · `WCH-12` (fala depende da instância do gateway).

**Fora da fatia (`WCH-02`):** regra para atendente sem áudio além da espera dita.

Gate: `infra/test/probe_wch01_chat_call.sh` (porta, espera dita, independência chamada × contato).
O caminho com humano e áudio foi validado pelo dono no browser.

## 21. A chamada tem DONA entre réplicas (WCH-12, 2026-09-24)

A chamada vive na **memória** de uma réplica do gateway: a sala, o bot leg, a fila de fala, a coleta
e `_sip`/`_sip_by_room`. Duas entradas chegam a qualquer réplica:
- o `conversations.outbound`, um grupo Kafka com 3 partições;
- o webhook do SFU, pelo DNS de `channel-gateway`.

Por isso a chamada tem **dona** (`call_relay.py`):

| peça | o quê |
|---|---|
| posse | `channel:call:{sid}:owner = instance_id` — gravada antes do pedido de roteamento (`_open_session`) e ao anexar a chamada do chat; renovada nos keepalives; apagada só se ainda for desta réplica (Lua) |
| canal | `call:deliver:{instance_id}` — cada réplica ouve só o seu |
| saída | `OutboundConsumer`: `holds_call` (memória, síncrono) → local como sempre; senão `send_later` à dona; sem dona → local (o `voice` legado Twilio segue igual). No `webchat` o chat é entregue por quem consumiu e só a FALA vai à dona |
| webhook | sala SIP que não é daqui → sessão por `channel:sip:room:{room}` → dona; `abrindo` espera até 5 s |

- **Ordem:** perguntar a dona é `await`, então o envio é encadeado **por sessão**. A dona abre as
  tasks na ordem de chegada, o mesmo contrato do consumidor (a fala é enfileirada antes do primeiro
  `await`, VOZ-05 fatia 3).
- **Dona que não ouve** (0 receptores no `publish`) é ERROR, e nada é entregue no lugar dela.
- **Por que no `voice` era pior que silêncio:** fora da dona, o `VoiceChannelRouter` não via a
  sessão SIP e mandava a mensagem ao legado Twilio.

Gate: `infra/test/probe_wch12_two_replicas.sh` (ASSISTIDO: sobe uma réplica temporária atrás do
alias). Testes: `test_call_relay.py`.
