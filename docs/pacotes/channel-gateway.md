# Módulo: channel-gateway (@plughub/channel-gateway)

> Última atualização: 2026-05-25 · Estado: Arc 16

> Pacote: `channel-gateway` (serviço)
> Runtime: Python 3.11+, FastAPI + aiokafka
> Spec de referência: seções 3.5, 4.7m

## O que é

O `channel-gateway` é a camada de normalização entre os canais externos (WhatsApp, SMS, web chat, e-mail, voz, WebRTC) e a plataforma PlugHub. Toda mensagem que entra ou sai da plataforma passa por ele.

É o **único** componente que conhece protocolos específicos de canal. Nenhum outro pacote depende de capacidades de canal.

---

## Invariantes centrais

> - **Nunca rotear conversas** — apenas normalizar e fazer bridge para o Kafka. O Routing Engine é o único árbitro de alocação.
> - **Nunca acessar `pipeline_state`** — só o estado de sessão de canal (coleta de menu, conexões WS) no Redis.
> - **Sempre emitir um único `MenuSubmitEvent`** por step de menu, independentemente de quantos turnos de canal foram necessários.
> - **`MenuSubmitEvent` deve ser indistinguível** de uma mensagem inbound normal, do ponto de vista do Routing Engine.
> - **Tokens (JWT, LiveKit) nunca são expostos na URL** — sempre via corpo de mensagem ou emitidos exclusivamente pelo gateway.
> - **Renderização específica de canal vive exclusivamente nos adapters** — skill-flow e Routing Engine só conhecem o formato neutro.

---

## Estrutura do Pacote

```
channel-gateway/
  src/plughub_channel_gateway/
    adapters/
      whatsapp.py        ← Meta Cloud API webhooks; Interactive Buttons, List Messages, texto
      sms.py             ← Webhooks de SMS provider; texto com fallback de menu numerado
      webchat.py         ← WebSocket; botões, listas, checkboxes, formulários, upload de anexos
      email.py           ← Parse de inbound + SMTP/API outbound; fallback texto para menus
      voice.py           ← Twilio Media Streams (PSTN); STT/TTS pipeline
      webrtc.py          ← WebRTC browser-to-SFU (Arc 15); signaling, media negotiation, egress
      webrtc_provider.py ← Abstração de SFU (LiveKit); tokens, rooms, egress
      webrtc_room_client.py ← Participação server-side em rooms LiveKit (pipeline STT/TTS)
      voice_provider.py  ← FallbackSTTProvider / FallbackTTSProvider (compartilhados voice + webrtc)
    main.py              ← FastAPI + rotas de webhook + WS + consumers Kafka
    normalizer.py        ← Conversão de eventos de canal → formato neutro de plataforma
    channel_capability_registry.py ← Arc 16 Fase D — matriz de capacidades + select_channel()
    config.py            ← settings via variáveis de ambiente
```

---

## Responsabilidades

### 1. Receber eventos inbound de canais

Cada adapter implementa o protocolo do canal correspondente — webhooks HTTP (WhatsApp, SMS, e-mail), WebSocket (web chat), Media Streams / SFU (voz, WebRTC) — e entrega eventos normalizados para a plataforma.

### 2. Normalizar para formato neutro e publicar no Kafka

Toda mensagem inbound é convertida para um formato neutro de plataforma e publicada no tópico `conversations.inbound`. O Routing Engine consome desse tópico e não sabe qual canal originou o evento.

### 3. Consumir `conversations.outbound` e entregar pelo canal

O gateway consome o tópico `conversations.outbound`, identifica o canal do destinatário e delega ao adapter correspondente para entrega.

### 4. Coletar input de menu

Quando o Skill Flow executa um step `menu` (entregue via `invoke: notification_send` no mcp-server), o Channel Gateway:

- **Canais com suporte nativo** (web chat, WebRTC): renderiza diretamente e aguarda um único evento de submit.
- **Canais sem suporte nativo** (WhatsApp, SMS, e-mail): executa coleta sequencial — envia cada campo/opção como mensagem separada, acumula respostas parciais no Redis (TTL-bound) e, ao completar todos os campos, emite um único `MenuSubmitEvent` para `conversations.inbound`.

### 5. Negociação de capacidade de canal (Arc 16 Fase D)

Quando um step `collect` declara `requires: [...]` em vez de um `channel` explícito, o `channel_capability_registry.py` seleciona o canal outbound. O step `notify` foi depreciado em favor de `invoke: notification_send`.

---

## Adapters de Canal

| Adapter | Canal | Protocolo | Status | Referência |
|---|---|---|---|---|
| `webchat.py` | Web Chat | WebSocket | ✅ Implementado | [channel-gateway-webchat.md](channel-gateway-webchat.md) |
| `whatsapp.py` | WhatsApp | Meta Cloud API webhooks | ✅ Implementado | — |
| `sms.py` | SMS | Webhooks de provider | ✅ Implementado | — |
| `email.py` | E-mail | SMTP / API + inbound parse | ✅ Implementado | — |
| `voice.py` | Voz (PSTN) | Twilio Media Streams | ✅ Implementado | — |
| `webrtc.py` | WebRTC | LiveKit SFU (browser-to-SFU) | ⚠️ Adapter ✅ · **SFU não provisionado** | [`docs/arcos/arc15-webrtc.md`](../arcos/arc15-webrtc.md) |

`channel` e `medium` são distintos: `channel` é o canal específico (`whatsapp`, `webchat`, `voice`, `email`, `sms`, `webrtc`, etc.) — hard filter para roteamento; `medium` é o tipo base (`voice`, `video`, `message`, `email`) — fator de score.

---

## Canal WebRTC (Arc 15)

Canal browser-to-SFU com medium negociado em tempo real (video → voice → text). Coexiste com o canal voice: `voice` = callers externos via tronco PSTN (Twilio), `webrtc` = clientes na webapp. **SFU**: LiveKit self-hosted (Docker/k8s).

> ⚠️ **Medido em 2026-08-20: o SFU não está de pé em ambiente algum do repositório.** As 6 fases abaixo
> descrevem o adapter, que existe e roda. O que NÃO existe: serviço LiveKit em qualquer `docker-compose*.yml`
> (grep → zero), env `LIVEKIT_*`/`WEBRTC_*` em `.env*`/compose/scripts (zero), manifesto k8s sob `infra/`
> para a topologia de `arc15-webrtc.md:81-89`, e o SDK `livekit` como dependência (`pyproject.toml:6-23`) —
> a imagem não o instala e os imports degradam. Sem `api_key`/`api_secret` (`config.py:228-232`) o
> `LiveKitProvider` liga `_dev_mode` (`webrtc_provider.py:167`) e devolve **token, room e egress mock**
> (`:176`, `:213`, `:331`). O browser tem cliente real (`platform-ui/package.json:16`) sem SFU para onde
> apontar. Ler as fases como *plano de sinalização entregue*, não como mídia em produção.

Implementado em 6 fases:

| Fase | Escopo |
|---|---|
| A | Provider abstraction + WebSocket signaling (`/ws/webrtc/{pool_id}`) + emissão de tokens LiveKit |
| B | `media_capabilities` propagada do schema → adapter; re-negociação de medium mid-session |
| C | Pipeline STT/TTS server-side via LiveKit Python SDK; resampler PCM 48kHz → 8kHz μ-law; DataChannel text/menu |
| D | Egress recording (LiveKit Egress API) → AttachmentStore → evento `recording.completed` no stream |
| E | Console platform-ui overlay (`WebRTCOverlay.tsx`); video grid / waveform conforme medium |
| F | Widget standalone do cliente no browser |

**Medium negotiation**: `negotiate_medium(agent.media_capabilities, pool.webrtc_media_fallback_order)` tenta video → voice → text; re-negocia quando o agente muda. **Tokens** LiveKit são emitidos exclusivamente pelo Channel Gateway — nunca expostos ao browser. **STT/TTS** reusa os mesmos `FallbackSTTProvider` / `FallbackTTSProvider` do canal voice; o transporte muda de Twilio Media Streams para LiveKit server SDK PCM frames.

---

## Channel Capability Registry (Arc 16 Fase D)

`channel_capability_registry.py` permite que o step `collect` declare `requires: [text|audio|video|file_upload|masked_input|rich_menu]` em vez de um `channel` explícito. O gateway escolhe o canal outbound em runtime.

| Função | Papel |
|---|---|
| `CHANNEL_CAPABILITIES` | Matriz estática de capacidades por canal (whatsapp, sms, email, voice, webchat, webrtc) |
| `_CHANNEL_PRIORITY` | Ordem de preferência quando múltiplos canais satisfazem os requisitos |
| `channel_satisfies(channel, requires)` | Verifica se um canal suporta todos os capabilities solicitados |
| `select_channel(available, requires, preferred)` | Algoritmo 2-step: honra preferência, depois escolhe por prioridade |
| `read_journey_channel_context()` | Lê `journey.available_channels` + `journey.canal_preferido` do ContextStore de Journey |
| `write_journey_channel_context()` | Escreve canais disponíveis / preferido / contact_id por canal na journey (TTL 30d NX) |
| `get_journey_contact_id()` | Recupera o contact_id do cliente para um canal na journey |
| `write_journey_pending_collect()` | Grava `journey.pending_collect_info` para descoberta de journeys com collect pendente |

O consumer `collect.events` despacha em 2 passos: (1) canal explícito → adapter direto; (2) sem canal → `read_journey_channel_context` + `select_channel` + `get_journey_contact_id`.

---

## Matriz de Coleta de Menu/Formulário

| Interação | WhatsApp | SMS | Web Chat | E-mail |
|---|---|---|---|---|
| `text` | Nativo | Nativo | Nativo | Nativo |
| `button` | Interactive Buttons (≤ 3) | Fallback numerado | Botões nativos | Fallback numerado |
| `list` | List Message (≤ 10) | Fallback numerado | Lista nativa | Fallback numerado |
| `checklist` | Sequencial + input vírgula | Sequencial + input vírgula | Checkboxes nativos | Não suportado → `on_failure` |
| `form` | Sequencial campo a campo | Sequencial campo a campo | Formulário HTML nativo | Não suportado → `on_failure` |

### Protocolo de fallback sequencial

Para interações sem suporte nativo (WhatsApp, SMS), o adapter executa a coleta assim:

```
1. Envia cada campo ou opção como mensagem separada no canal
2. Armazena respostas parciais no Redis
   Chave: channel:{channel}:{session_id}:menu_collect  (TTL-bound)
3. Aguarda todas as respostas obrigatórias
4. Agrega e emite um único MenuSubmitEvent → conversations.inbound
```

---

## `MenuSubmitEvent` — schema normalizado

```python
@dataclass
class MenuSubmitEvent:
    session_id:   str
    interaction:  Literal["text", "button", "list", "checklist", "form"]
    result:       str | list[str] | dict   # corresponde ao tipo de interação
    channel:      str
    timestamp:    datetime
```

O `result` varia de acordo com `interaction`:

| `interaction` | Tipo de `result` | Exemplo |
|---|---|---|
| `text` | `str` | `"Quero cancelar"` |
| `button` | `str` (option id) | `"opt_cancelar"` |
| `list` | `str` (option id) | `"opt_portabilidade"` |
| `checklist` | `list[str]` (option ids) | `["opt_a", "opt_c"]` |
| `form` | `dict` (field → valor) | `{"nome": "João", "cpf": "..."}` |

---

## Chaves Redis

| Chave | Conteúdo | TTL |
|---|---|---|
| `channel:{channel}:{session_id}:menu_collect` | Estado parcial de coleta sequencial | TTL configurável |
| `webchat:session:{contact_id}` | Mapa `contact_id` → conexão WS ativa | duração do contato (default 4h) |
| `channel:webrtc:{session_id}:medium` | Medium negociado da sessão WebRTC | duração do contato |

> O Channel Gateway acessa Redis **apenas** para estado de canal (coleta de menu, conexões WS, medium WebRTC). Nunca lê nem escreve `pipeline_state`.

---

## Tópicos Kafka

| Tópico | Direção | Conteúdo |
|---|---|---|
| `conversations.inbound` | **Publica** | Todos os eventos inbound normalizados, incluindo `MenuSubmitEvent` |
| `conversations.outbound` | **Consome** | Todos os outbound e `MenuPayload` originados pela plataforma |
| `collect.events` | **Consome** | `collect.requested` — despacha prompt de coleta pelo canal apropriado (Arc 16) |
| `gateway.heartbeat` | **Publica** | Heartbeat do gateway — consumido pelo Routing Engine (TTL de instâncias) |
| `usage.events` | **Publica** | Eventos de metering — `webchat_attachments` (e funções prontas para `whatsapp_conversations`, `voice_minutes`, `sms_segments`, `email_messages`) |

---

## Stack

```
Python 3.11+
FastAPI          ← endpoints de webhook + WebSocket
aiokafka         ← producer/consumer Kafka assíncrono
redis[hiredis]   ← estado de canal (coleta, conexões WS, medium)
pydantic         ← validação de payloads
livekit-api      ← SFU para o canal WebRTC
```

---

## Dependências

```
channel-gateway
  └── depende de → @plughub/schemas  (MenuPayload, MenuSubmitEvent, CollectStepSchema, contratos de mensagem)
```

Sem dependência de `skill-flow`, `ai-gateway` ou `routing-engine`.

---

## Relação com Outros Módulos

```
channel-gateway
  ├── recebe de → canais externos    (webhooks WhatsApp/SMS/email, WebSocket webchat, SFU webrtc, PSTN voice)
  ├── publica → conversations.inbound  (eventos normalizados + MenuSubmitEvent)
  ├── consome → conversations.outbound (mensagens e MenuPayload para entrega)
  ├── consome → collect.events         (coleta de canal — Arc 16)
  ├── publica → gateway.heartbeat      (TTL de instâncias no Routing Engine)
  ├── publica → usage.events           (metering por dimensão)
  └── lê/escreve → Redis               (estado de canal — nunca pipeline_state)
```

> **Nota de design:** Toda lógica de renderização e coleta específica de canal fica exclusivamente nos adapters dentro deste pacote. skill-flow e Routing Engine nunca sabem qual canal está em uso — recebem e enviam sempre o formato neutro de plataforma.
