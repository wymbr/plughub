# Channel Gateway — Arquitetura Multi-Canal

**Versão:** 1.1 — 2026-05-20  
**Status:** Phase 1 (abstrações) + WhatsApp + SMS + Email + Voice implementados.  
**Escopo:** `packages/channel-gateway/`

---

## 1. Contexto e Princípios

### 1.1 O modelo "todo contato é uma conferência"

O PlugHub modela cada contato como uma sala de conferência. O Redis stream
`session:{id}:stream` é o barramento canônico onde todos os eventos fluem.
Participantes entram com roles (`primary`, `specialist`, `supervisor`,
`evaluator`) e recebem mensagens de acordo com regras de visibilidade
(`all`, `agents_only`, `[participant_ids]`).

Este modelo se aplica **sem mudança** a todos os canais. O que varia por canal
é apenas como o cliente entra na sala e como mensagens chegam e saem.

### 1.2 Os dois planos

Para canais de texto (webchat, WhatsApp, SMS, email) existe apenas um plano:

```
Plano de controle  →  Redis stream + Kafka  (texto, eventos, visibilidade)
```

Para voz, emerge um segundo plano:

```
Plano de controle  →  Redis stream + Kafka   (texto, transcrições, eventos)
Plano de mídia     →  CPaaS conference room  (áudio, mixing, PSTN)
```

O `VoiceAdapter` é a ponte entre os dois planos. Todo o resto da plataforma
— AI Gateway, ContextStore, Skill Flow, Arc 6, Arc 11 — opera exclusivamente
no plano de controle (texto) e não precisa de nenhuma mudança.

### 1.3 Premissas de design

1. **Agentes IA são sempre texto**, em qualquer canal. Para voz, STT converte
   a fala do cliente em texto na entrada; TTS converte a resposta da IA em
   áudio na saída. O pipeline central é sempre texto.
2. **Supervisores e especialistas IA operam em texto**, mesmo em sessões de
   voz. O agente humano lê as sugestões na tela — não as ouve.
3. **O único streaming de áudio fim-a-fim** é entre agente humano e cliente,
   gerenciado pelo CPaaS. O PlugHub não toca esse áudio.
4. **STT do agente humano é opcional e "ligável"**: a fala do agente pode ser
   transcrita via Deepgram no segundo leg da conference bridge, sem nenhuma
   mudança de arquitetura — só ligar o pipe.
5. **Providers são abstraídos**: CPaaS (Twilio/Telnyx), STT (Deepgram), TTS
   (Deepgram Aura / ElevenLabs) ficam atrás de interfaces; a troca de
   provider não exige refactor do adapter.

---

## 2. Modelo de Dados — Mudanças em `models.py`

Duas adições em modelos compartilhados, necessárias antes de qualquer novo
canal. Custo zero — backward-compatible, campos opcionais com defaults.

### 2.1 `content_type` em `NormalizedInboundEvent`

```python
class NormalizedInboundEvent(BaseModel):
    # ... campos existentes ...
    channel: str                    # era Literal["webchat"] — relaxado para str
    content_type: Literal[
        "text",
        "audio_transcript",         # STT output de canal de voz
        "image", "document", "video"
    ] = "text"
```

`audio_transcript` identifica que o `content` é saída de STT, não texto
digitado. Permite ao AI Gateway ajustar contexto e ao Arc 6 avaliar
transcripts de voz com critérios específicos.

### 2.2 `channel` relaxado em eventos de lifecycle

```python
# ContactOpenEvent e ContactClosedEvent:
channel: str    # era Literal["webchat"]
```

### 2.3 `channel_session_id` em `ContactOpenEvent`

ID de sessão do lado do canal — correlaciona a sessão PlugHub com o
identificador nativo de cada provider:

| Canal     | `channel_session_id`              |
|-----------|-----------------------------------|
| WhatsApp  | `wamid` (WhatsApp Message ID)     |
| SMS       | ID da mensagem do provider        |
| Email     | `Message-ID` do header SMTP       |
| Voice     | `CallSid` do CPaaS                |
| Webchat   | `null` (session_id já é suficiente) |

```python
class ContactOpenEvent(BaseModel):
    # ... campos existentes ...
    channel_session_id: str | None = None
```

---

## 3. Hierarquia de Adapters

```
ChannelAdapter (ABC)  —  adapters/base.py
├── WebSocketAdapter  —  adapters/ws_base.py
│   ├── WebchatAdapter    adapters/webchat.py   (refatorado)
│   └── WebRTCAdapter     adapters/webrtc.py    (futuro)
├── WebhookAdapter    —  adapters/webhook_base.py
│   ├── WhatsAppAdapter   adapters/whatsapp.py
│   ├── SMSAdapter        adapters/sms.py
│   └── EmailAdapter      adapters/email.py
└── VoiceAdapter      —  adapters/voice.py
        IVoiceProvider (Protocol)
        ├── TwilioProvider
        └── TelnyxProvider
        ISTTProvider (Protocol)
        └── DeepgramProvider
        ITTSProvider (Protocol)
        ├── DeepgramAuraProvider
        └── ElevenLabsProvider
```

---

## 4. `ChannelAdapter` — Base ABC

**Arquivo:** `adapters/base.py`

### 4.1 Interface obrigatória (abstract)

```python
class ChannelAdapter(ABC):
    channel_name: ClassVar[str]   # "webchat" | "whatsapp" | "sms" | "email" | "voice"

    @abstractmethod
    async def handle(self) -> None:
        """Ciclo de vida completo do contato. Chamado uma vez por contato."""

    @abstractmethod
    async def deliver_outbound(self, event_type: str, payload: dict) -> None:
        """Entrega uma mensagem outbound para o cliente via canal nativo."""

    async def close_from_platform(self, reason: str) -> None:
        """Fechamento iniciado pela plataforma (ex: session.closed no Kafka).
        Implementação padrão é no-op; adapters com conexão persistente sobrescrevem."""
```

### 4.2 Infraestrutura compartilhada (concrete, fornecida pela base)

```python
# Lifecycle de sessão
async def _open_session(
    self,
    contact_id: str,
    session_id: str,
    pool_id: str,
    channel: str,
    tenant_id: str,
    customer_participant_id: str,
    channel_session_id: str | None = None,
    started_at: datetime | None = None,
) -> None:
    """Escreve Redis keys de sessão + publica ContactOpenEvent."""

async def _close_session(self, session_id: str, reason: str) -> None:
    """Remove Redis keys + publica ContactClosedEvent."""

# Roteamento
async def _check_already_routed(self, session_id: str, tenant_id: str) -> bool:
    """Guard de reconexão: verifica 5 Redis keys do orchestrator-bridge."""

async def _route_inbound(self, pool_id: str, contact_id: str, session_id: str,
                          channel: str, tenant_id: str) -> None:
    """Publica payload de roteamento em conversations.inbound."""

# Mensagens
async def _publish_inbound(self, event: NormalizedInboundEvent) -> None:
    """Publica em conversations.inbound via Kafka producer."""

async def _publish_event(self, event: ContactOpenEvent | ContactClosedEvent) -> None:
    """Publica em conversations.events via Kafka producer."""

async def _get_context_snapshot(self, session_id: str) -> ContextSnapshot:
    """Lê ContextStore via ContextReader."""

def _normalize_text(
    self,
    text: str,
    session_id: str,
    contact_id: str,
    tenant_id: str,
    content_type: str = "text",
) -> NormalizedInboundEvent:
    """Constrói NormalizedInboundEvent para mensagem de texto ou transcript."""

def _normalize_menu_result(
    self,
    menu_id: str,
    interaction: str,
    result: Any,
    session_id: str,
    contact_id: str,
    tenant_id: str,
) -> NormalizedInboundEvent:
    """Constrói NormalizedInboundEvent para menu.submit."""
```

### 4.3 Constructor padrão

```python
def __init__(
    self,
    producer: AIOKafkaProducer,
    redis: aioredis.Redis,
    settings: Settings,
    tenant_id: str,
    store: AttachmentStore | None = None,
):
```

---

## 5. `WebSocketAdapter` — Sub-protocolo para conexões persistentes

**Arquivo:** `adapters/ws_base.py`

Extraído de `WebchatAdapter`. Implementa o padrão de três tasks concorrentes
que é compartilhado entre webchat e WebRTC.

```python
class WebSocketAdapter(ChannelAdapter):
    """
    Sub-protocolo para canais com conexão WebSocket persistente.
    Implementa o padrão: receive_loop + delivery_loop + ancillary_loop
    """

    @abstractmethod
    async def _receive_loop(self) -> None:
        """Loop de recebimento de mensagens do cliente via WS."""

    @abstractmethod
    async def _delivery_loop(self) -> None:
        """Loop de entrega de mensagens para o cliente via WS."""

    async def _ancillary_loop(self) -> None:
        """Loop auxiliar opcional (typing indicators, heartbeat).
        Implementação padrão é no-op."""

    async def _run_three_tasks(self) -> None:
        """Inicia os três loops concorrentes. Cancela os outros quando
        o primeiro termina. Chamado pelo handle() da subclasse."""

    async def _ws_send_json(self, data: dict) -> None:
        """Envia JSON via WebSocket com tratamento de disconnect."""

    async def _ws_keepalive(self, redis_key: str, ttl: int) -> None:
        """Renova TTL do keepalive Redis key. Chamado a cada frame recebido."""
```

---

## 6. `WebhookAdapter` — Sub-protocolo para canais stateless

**Arquivo:** `adapters/webhook_base.py`

Canais webhook não têm conexão persistente. Cada inbound event chega como
um POST HTTP independente. A "sessão" é mantida no Redis, não numa conexão.

```python
class WebhookAdapter(ChannelAdapter):
    """
    Sub-protocolo para canais HTTP webhook stateless.
    WhatsApp, SMS, Email — um POST por evento, sem conexão mantida.
    """

    @abstractmethod
    async def verify_signature(self, request: Request) -> bool:
        """Valida assinatura HMAC ou API key do provider."""

    @abstractmethod
    async def parse_inbound(self, body: dict) -> list[NormalizedInboundEvent]:
        """Transforma payload do provider em NormalizedInboundEvents."""

    async def _sequential_menu_collect(
        self,
        session_id: str,
        menu_id: str,
        fields: list[dict],
    ) -> None:
        """
        Coleta sequencial de campos de formulário via Redis state machine.
        Necessário para canais que não suportam formulários nativos (SMS).
        Redis key: channel:{channel}:{session_id}:menu_collect
        """

    async def deliver_outbound(self, event_type: str, payload: dict) -> None:
        """
        Implementação base: roteia por event_type para o método correto.
        Subclasses sobrescrevem _send_text, _send_menu, _send_media.
        """

    @abstractmethod
    async def _send_text(self, contact_id: str, text: str) -> None: ...

    @abstractmethod
    async def _send_menu(self, contact_id: str, menu: dict) -> None: ...

    async def _send_media(self, contact_id: str, media: dict) -> None:
        """Implementação padrão: converte para texto descritivo.
        Subclasses com suporte a mídia sobrescrevem."""
```

---

## 7. `OutboundConsumer` — Registry de Adapters

**Arquivo:** `outbound_consumer.py` (refatorado)

Hoje há um `if channel != "webchat": return` que ignora todos os outros
canais silenciosamente. Substituído por um registry de adapters.

```python
class OutboundConsumer:
    _registry: dict[str, ChannelAdapter] = {}

    def register(self, adapter: ChannelAdapter) -> None:
        self._registry[adapter.channel_name] = adapter

    async def _dispatch(self, msg: dict) -> None:
        channel     = msg.get("channel", "")
        event_type  = msg.get("type", "")
        adapter     = self._registry.get(channel)

        if adapter is None:
            logger.warning("No adapter registered for channel=%s", channel)
            return

        await adapter.deliver_outbound(event_type, msg)
```

O registro acontece no lifespan do FastAPI (`main.py`), uma vez por canal
ativo na configuração.

---

## 8. Canais de Texto — Especificações

### 8.1 Webchat (existente, refatorado)

**Transporte:** WebSocket persistente (FastAPI)  
**Auth:** JWT em mensagem WS (`conn.authenticate`) — nunca na URL  
**Inbound flow:**

```
Cliente WS ──msg.text──→ _receive_loop
                              ↓ _normalize_text(content_type="text")
                              ↓ _publish_inbound → conversations.inbound
```

**Outbound flow:**

```
conversations.outbound (Kafka)
    ↓ OutboundConsumer._dispatch → WebchatAdapter.deliver_outbound
    ↓ StreamSubscriber (XREAD session stream)
    ↓ ws.send_json → Cliente
```

**Especificidades que ficam no WebchatAdapter:**
- Upload 2-stage (`upload.request` → HTTP POST → `upload.committed`)
- Typing indicators (Redis pub/sub `session:{id}:typing`)
- Masked fields (menu com `masked: true`)
- Reconnect via cursor (`conn.authenticate.cursor`)
- JWT secret por tenant via Redis (`{tenant_id}:config:webchat:jwt_secret`)

**Redis keys (proprietárias do webchat):**
```
session:{id}:ws_alive           TTL=ws_timeout+120s, renovado por frame
chat:session:{contact_id}       registry de sessão ativa
chat:deliver:{contact_id}       pub/sub para entrega cross-instance
```

---

### 8.2 WhatsApp

**Transporte:** Webhook HTTP POST  
**Provider inicial:** Meta Cloud API direta  
**Auth inbound:** HMAC-SHA256 com `X-Hub-Signature-256` header  
**Inbound endpoint:** `POST /webhooks/whatsapp`  
**Verificação endpoint:** `GET /webhooks/whatsapp`

#### 8.2.1 Abstração de provider

O `WhatsAppAdapter` não chama a Meta API diretamente — usa um `IWhatsAppProvider`
Protocol. Isso permite suportar BSPs (Twilio, Infobip, 360dialog) sem tocar o
adapter: cada BSP é um provider concreto que traduz para/do formato Meta canônico.

```python
class IWhatsAppProvider(Protocol):
    async def send_text(self, to: str, text: str) -> str:
        """Envia mensagem de texto. Retorna wamid."""

    async def send_interactive_buttons(
        self, to: str, body: str, buttons: list[dict]
    ) -> str: ...

    async def send_interactive_list(
        self, to: str, header: str, body: str, sections: list[dict]
    ) -> str: ...

    async def send_media(
        self, to: str, media_type: str, link: str, caption: str | None
    ) -> str: ...

    async def get_media_url(self, media_id: str) -> str:
        """Resolve media_id → URL de download temporária (Graph API)."""

    async def download_media(self, url: str) -> tuple[bytes, str]:
        """Baixa mídia. Retorna (bytes, mime_type)."""
```

Provider padrão: `MetaCloudProvider` — chama `graph.facebook.com/v19.0/`.
Provider de teste: `MockWhatsAppProvider` — respostas simuladas, sem I/O de rede.

#### 8.2.2 Resolução de credenciais

Mesmo padrão do webchat JWT: env var como default por instalação + override Redis
por tenant (para SaaS). A resolução tenta Redis primeiro, cai no env var.

```
Env vars (padrão por instalação):
  PLUGHUB_WHATSAPP_ACCESS_TOKEN      token de Sistema da WABA
  PLUGHUB_WHATSAPP_PHONE_NUMBER_ID   phone_number_id da Meta
  PLUGHUB_WHATSAPP_VERIFY_TOKEN      token de verificação do webhook (GET)

Redis (override por tenant — opcional):
  {tenant_id}:config:whatsapp:access_token
  {tenant_id}:config:whatsapp:phone_number_id
```

O `PLUGHUB_WHATSAPP_VERIFY_TOKEN` é global por instalação — não há routing por
tenant no endpoint de verificação GET.

#### 8.2.3 Modelo de sessão

`contact_id` = número E.164 do remetente (ex: `+5511999990000`).  
`phone_number_id` = número de destino da instalação — fixo por configuração.

Lookup de sessão ativa por número de cliente:

```
channel:whatsapp:{contact_id}:session   →  session_id  (TTL 24h)
```

Se a chave existe → reusa a sessão (continua conversa). Se não → nova sessão,
novo roteamento. TTL renovado a cada mensagem recebida do cliente. O que acontece
com sessões encerradas internamente enquanto a janela Meta ainda está aberta é
responsabilidade do pool/routing — o gateway entrega apenas para sessões ativas.

#### 8.2.4 Inbound flow

```
Meta Cloud API ──POST──→ WhatsAppAdapter.verify_signature
                              ↓ HMAC inválido → HTTP 400
                         HTTP 200 imediato  ←─────────────────────┐
                              ↓                                     │
                         asyncio.create_task(_process_inbound)      │
                              ↓                                     │
                         _resolve_session(contact_id)               │ responde antes
                              ↓                                     │ de processar
                         tipo == texto?                             │
                              ↓ sim                                 │
                         NormalizedInboundEvent(content_type="text")│
                         _publish_inbound → conversations.inbound ──┘

                         tipo == media?
                              ↓
                         provider.get_media_url(media_id)
                              ↓
                         provider.download_media(url)
                              ↓
                         AttachmentStore.store(bytes, mime_type)
                              ↓
                         NormalizedInboundEvent(content_type="image"|"document"|"video")
                         _publish_inbound → conversations.inbound
```

**HTTP 200 é retornado antes de qualquer processamento.** O Kafka publish ocorre
dentro do background task, após download de mídia quando aplicável. O agente vê
mensagens de mídia com 1-3s de delay — comportamento idêntico ao WhatsApp próprio.

#### 8.2.5 Tipos de mensagem inbound suportados

| Tipo Meta | `content_type` | Tratamento |
|-----------|----------------|------------|
| `text` | `text` | Direto — sem I/O adicional |
| `image`, `video`, `document` | `image` / `video` / `document` | Background: `get_media_url` → download → AttachmentStore |
| `audio` | `audio_transcript` | Background: download → STT opcional (fase futura); por ora armazenado como documento |
| `interactive.button_reply` | `text` | Mapeado para `menu_result` |
| `interactive.list_reply` | `text` | Mapeado para `menu_result` |
| `location` | `text` | Formatado como `"lat:{lat} lng:{lng}"` |
| `sticker` | — | Ignorado (log warning) |

#### 8.2.6 Outbound — modos de mensagem

| Payload Kafka | Formato Meta | Condição |
|---|---|---|
| `message.text` | `type: text` | Sempre |
| `menu.payload` ≤ 3 opções | `type: interactive, interactive.type: button` | Botões nativos WhatsApp |
| `menu.payload` 4–10 opções | `type: interactive, interactive.type: list` | List message |
| `menu.payload` > 10 opções ou `form` | `type: text` + coleta sequencial | Fallback (ver §8.2.7) |
| `session.closed` | — | Nenhum envio ao cliente; limpeza de Redis apenas |

#### 8.2.7 Coleta sequencial de formulário (fallback)

Quando a interação não cabe em Interactive Message (>10 opções, tipo `form`, ou
campos `masked`), `_sequential_menu_collect` envia os campos um a um via texto,
acumula respostas no Redis, e publica um único `NormalizedInboundEvent(menu_result)`
ao receber o último campo.

Estado intermediário em `channel:whatsapp:{session_id}:menu_collect` (TTL 30min):
`{ menu_id, fields: [...], current_index, answers: {...} }`.

Campos `masked` em coleta sequencial: não há suporte nativo a input mascarado no
WhatsApp. O campo é enviado como texto normal com instrução de privacidade
(`"Este campo é confidencial. Sua resposta será tratada com segurança."`). O
valor é armazenado mascarado no pipeline de mascaramento padrão.

#### 8.2.8 Redis keys (proprietárias do WhatsApp)

```
channel:whatsapp:{contact_id}:session        session_id ativo (TTL 24h, renovado)
channel:whatsapp:{session_id}:menu_collect   estado da coleta sequencial (TTL 30min)
{tenant_id}:config:whatsapp:access_token     override de token por tenant
{tenant_id}:config:whatsapp:phone_number_id  override de phone_number_id por tenant
```

#### 8.2.9 Verificação de webhook Meta (GET)

```
GET /webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=TOKEN&hub.challenge=CHALLENGE
→ Se hub.verify_token == PLUGHUB_WHATSAPP_VERIFY_TOKEN: responde CHALLENGE (HTTP 200)
→ Caso contrário: HTTP 403
```

Endpoint único, sem routing por tenant. Respondido pelo `main.py` antes de
instanciar o adapter (não requer Redis).

#### 8.2.10 Testes sem número real

**Testes automatizados:** `MockWhatsAppProvider` — respostas hardcoded, sem rede.
Cobre 100% dos casos de inbound/outbound via pytest.

**Testes de integração real:** Meta Developer sandbox — toda conta Meta Developer
inclui um número de teste gratuito. Fluxo: criar app no Meta Developer Portal →
configurar webhook com ngrok → usar o número de teste para enviar mensagens para
até 5 números pessoais pré-autorizados. Sem aprovação de WABA necessária.

**`channel_session_id`:** `wamid` do payload Meta (`entry[0].changes[0].value.messages[0].id`)

---

### 8.3 SMS

**Transporte:** Webhook HTTP POST  
**Auth:** HMAC por provider (Twilio: HMAC-SHA1 `X-Twilio-Signature`; extensível via `ISMSProvider`)  
**Inbound endpoint:** `POST /webhooks/sms`  

#### 8.3.1 Provider abstraction

```python
class ISMSProvider(Protocol):
    async def send_text(self, to: str, body: str) -> str: ...        # → message SID
    async def verify_signature(
        self, url: str, params: dict, signature: str
    ) -> bool: ...
```

Implementações concretas: `TwilioProvider` (produção) + `MockSMSProvider` (testes).
A `SMSAdapter` recebe `provider: ISMSProvider | None = None`; se `None`, instancia
`TwilioProvider` com credenciais resolvidas do Redis ou env vars.

Adicionar novo provider (Telnyx, Vonage, AWS SNS): implementar o Protocol, registrar
no `_get_provider()` via env var `PLUGHUB_SMS_PROVIDER` — sem tocar no adapter.

#### 8.3.2 Resolução de credenciais

Mesma cadeia do WhatsApp: env var por instalação → Redis override por tenant.

```
Env vars padrão:              PLUGHUB_SMS_ACCOUNT_SID
                               PLUGHUB_SMS_AUTH_TOKEN
                               PLUGHUB_SMS_FROM_NUMBER
                               PLUGHUB_SMS_PROVIDER          (twilio | telnyx | mock)

Redis override por tenant:    {tenant_id}:config:sms:account_sid
                               {tenant_id}:config:sms:auth_token
                               {tenant_id}:config:sms:from_number
```

`_resolve_credential(key, default)` tenta Redis primeiro; fallback para env var.
Dev mode: se `SMS_AUTH_TOKEN` vazio, `verify_signature` retorna `True` (bypass).

#### 8.3.3 Modelo de sessão

`contact_id` = número do remetente em E.164 normalizado (ex: `+5511999990000`).

```
Redis: channel:sms:{contact_id}:session  →  session_id  (TTL 24h, renovado a cada inbound)
```

Enquanto a key existir, todo inbound do número vai para o mesmo `session_id`.
Quando o Core encerra a sessão (`session.closed`), o adapter deleta a key — próximo
contato do mesmo número gera nova sessão (mesmo mecanismo do WhatsApp).

`ContactOpenEvent.channel = "sms"`, `channel_session_id = SmsMessageSid` do primeiro
fragmento (ou mensagem única).

#### 8.3.4 Inbound flow

```
Twilio ──POST──→ POST /webhooks/sms
                     ↓ SMSAdapter.verify_signature  (HMAC-SHA1 do URL + params)
                     ↓ parse_inbound_form           (Twilio envia form-encoded, não JSON)
                     ↓ _handle_inbound (background task)
                          ↓ _accumulate_parts       (SMS concatenado)
                          ↓ quando completo: _resolve_session
                          ↓                    ↓ sessão ativa → renovar TTL
                          ↓                    ↓ nova sessão → contact_open + publish
                          ↓ NormalizedInboundEvent(content_type="text")
                          ↓ _publish_inbound → conversations.inbound
```

**HTTP 200 é retornado imediatamente** — Twilio espera resposta rápida ou considera
falha. Todo processamento ocorre em `asyncio.create_task(_handle_inbound)`.

Resposta ao Twilio: XML vazio `<Response/>` (TwiML) — evita que Twilio faça callback
de voz indesejado.

#### 8.3.5 Concatenação de SMS longos

SMS >160 chars é dividido pelo provider em múltiplos webhooks com o mesmo
`SmsMessageSid` e campos `NumSegments` + `PartSequenceNumber` (UDH) ou índice implícito.

Estratégia Twilio:
1. Cada webhook carrega `SmsMessageSid`, `NumSegments`, `Body` (fragmento)
2. Adapter armazena fragmentos em Redis com TTL 5min:

```
channel:sms:{session_id}:sms_parts:{SmsMessageSid}  →  JSON list de fragmentos
```

3. Quando `len(fragments) == NumSegments`: concatena em ordem, publica evento único
4. Fragmentos expirados sem completar: descartados silenciosamente (log warning)

Nota: Twilio pode também receber SMS longos como mensagem única quando suportado
pelo carrier. O adapter aceita ambos os formatos (`NumSegments == 1` → publicação imediata).

#### 8.3.6 Outbound — divisão em segmentos

Textos longos são divididos em múltiplos SMS de até 153 chars (reserva 7 chars para
cabeçalho UDH em multipart) com sufixo `(N/T)`:

```python
MAX_SEGMENT   = 153          # chars por segmento com UDH
MAX_SEGMENTS  = 10           # limite máximo (1530 chars de conteúdo)
SUFFIX_TPLT   = " ({n}/{t})" # sufixo de 6-8 chars
```

Texto ≤ 160 chars → enviado como SMS único sem sufixo.
Texto > 1530 chars → truncado em 1530 chars + `…` antes de dividir.

Menus formatados como texto numerado:
```
Por favor, escolha uma opção:
1. Suporte técnico
2. Faturamento
3. Outros

Responda com o número da opção.
```

#### 8.3.7 Coleta sequencial de formulário

Único modo de interação — SMS não tem botões nativos. Mesma máquina de estados
do WhatsApp, adaptada para SMS:

Estado intermediário em `channel:sms:{session_id}:menu_collect` (TTL 30min):
`{ menu_id, fields: [...], current_index, answers: {...} }`.

Cada campo é enviado como texto simples; resposta do usuário é qualquer texto livre
ou número (para menus). Campos `masked`: instrução de privacidade no texto, sem
diferença visual possível em SMS.

Validação de resposta numérica para menus: se `options` definidas e resposta não for
índice válido → reenvio da pergunta com aviso: `"Opção inválida. Por favor, responda com 1, 2 ou 3."`.

#### 8.3.8 Redis keys (proprietárias do SMS)

```
channel:sms:{contact_id}:session              session_id ativo (TTL 24h, renovado)
channel:sms:{session_id}:sms_parts:{smsSid}   fragmentos de SMS concatenado (TTL 5min)
channel:sms:{session_id}:menu_collect         estado da coleta sequencial (TTL 30min)
{tenant_id}:config:sms:account_sid            override de credencial por tenant
{tenant_id}:config:sms:auth_token             override de auth token por tenant
{tenant_id}:config:sms:from_number            override de número remetente por tenant
```

#### 8.3.9 Verificação de webhook Twilio

Twilio assina cada request com HMAC-SHA1 sobre `URL_completo + params_sorted_alphabetically`.

```python
import hmac, hashlib, base64

def verify_twilio(url: str, params: dict, token: str, signature: str) -> bool:
    s = url + "".join(f"{k}{v}" for k, v in sorted(params.items()))
    computed = base64.b64encode(
        hmac.new(token.encode(), s.encode(), hashlib.sha1).digest()
    ).decode()
    return hmac.compare_digest(computed, signature)
```

Dev mode: `SMS_AUTH_TOKEN` vazio → `verify_signature` retorna `True`.

#### 8.3.10 Testes sem número real

**Testes automatizados:** `MockSMSProvider` — respostas hardcoded, `sent_messages: list[dict]`,
sem rede. Cobre 100% dos casos via pytest.

**Testes de integração real:** Twilio Trial — conta gratuita inclui número virtual
(~US$1 de crédito inicial). Recebe SMS reais para testes via ngrok. Sem aprovação de
operadora necessária para testes entre números Twilio.

**`channel_session_id`:** `SmsMessageSid` do payload Twilio

---

### 8.4 Email

**Transporte:** Webhook HTTP POST  
**Auth:** HMAC-SHA256 (Mailgun: `X-Mailgun-Signature-V2`; extensível via `IEmailProvider`)  
**Inbound endpoint:** `POST /webhooks/email`  

#### 8.4.1 Provider abstraction

```python
class IEmailProvider(Protocol):
    async def verify_signature(self, headers: dict, body: bytes) -> bool: ...
    async def parse_inbound(self, headers: dict, body: bytes) -> ParsedEmail: ...
    async def send(
        self,
        to:           str,
        subject:      str,
        body_text:    str,
        body_html:    str,
        from_address: str,
        reply_to:     str,
        in_reply_to:  str | None,
        references:   list[str],
        attachments:  list[EmailAttachment],
    ) -> str: ...   # → Message-ID enviado
```

`ParsedEmail`: `message_id`, `from_address`, `to_address`, `subject`, `body_text`,
`body_html`, `in_reply_to`, `references: list[str]`, `attachments: list[EmailAttachment]`.

Implementações concretas: `MailgunProvider` (produção) + `MockEmailProvider` (testes).
Providers futuros: SendGrid, AWS SES, Microsoft Graph API (Exchange/O365), Gmail API,
IMAP/SMTP genérico (polling — fase futura, modelo diferente de webhook).

#### 8.4.2 Configuração de mailbox — Configuration/Channels

Cada caixa postal é um `ChannelEndpoint` no agent-registry com `channel: "email"` e
`identifier` = endereço de entrada (ex: `suporte@empresa.com`). O `resolve_pool` do
Layer 2 mapeia `identifier → pool_id` — múltiplas caixas simultâneas resolvidas sem
código adicional.

Configuração específica por mailbox (metadados do ChannelEndpoint):
```json
{
  "provider":        "mailgun",
  "from_address":    "suporte@empresa.com",
  "reply_domain":    "mail.empresa.com",
  "signing_key":     "<HMAC key Mailgun>",
  "smtp_api_key":    "<Mailgun API key para envio>",
  "domain":          "empresa.com",
  "templates": {
    "acknowledgment": "Recebemos seu e-mail. Protocolo: {{protocol_number}}. Em breve retornaremos.",
    "closed":         "Seu atendimento foi encerrado. Protocolo: {{protocol_number}}."
  }
}
```

Credenciais residem no agent-registry (não em env vars), pois cada mailbox pode ter
credenciais diferentes. `EmailAdapter._get_provider(mailbox_id)` busca a config via
endpoint resolver e instancia o provider correto.

#### 8.4.3 Modelo de sessão e Reply-To

`contact_id` = endereço `From:` normalizado em lowercase (ex: `cliente@gmail.com`).

**Reply-To como mecanismo primário de correlação:**

```
[1] Email novo do cliente → suporte@empresa.com
        ↓ nova sessão (session_id = uuid-abc)

[2] Agente responde via PlugHub → MIME outbound:
        From:     suporte@empresa.com
        To:       cliente@gmail.com
        Reply-To: reply+uuid-abc@mail.empresa.com

[3] Cliente responde ao email
        → vai para reply+uuid-abc@mail.empresa.com
        → Mailgun catch-all → POST /webhooks/email
        → EmailAdapter extrai session_id do To: diretamente
        → sessão continuada sem lookup Redis
```

**Fallback — In-Reply-To header SMTP:**
Quando o cliente responde por outro meio (reencaminhamento, cliente sem suporte a
Reply-To), o adapter extrai o `Message-ID` do header `In-Reply-To` e faz lookup
`channel:email:{message_id_hash}:session` no Redis.

**Fallback final — sessão ativa por endereço:**
Se nenhum dos anteriores resolver, lookup `channel:email:{contact_email_hash}:session`.
Email sem correlação alguma → nova sessão sempre.

Redis keys:
```
channel:email:{contact_email_hash}:session    session_id ativo para este endereço
channel:email:{message_id_hash}:session       session_id para Message-ID (TTL 30d)
```

TTL da sessão: segue o ciclo de vida do Core — encerrada via `session.closed`.
Sem TTL de inatividade no gateway (email pode ficar dias sem resposta).

Mailgun catch-all: `match_recipient("reply\+(.*)@mail\.empresa\.com")` → forward webhook.
`mail.empresa.com` é subdomínio dedicado para Reply-To, separado do domínio de envio.

#### 8.4.4 Inbound flow

```
Mailgun ──POST──→ POST /webhooks/email
                      ↓ EmailAdapter.verify_signature  (HMAC-SHA256 Mailgun v2)
                      ↓ provider.parse_inbound         (extrai headers, body, attachments)
                      ↓ _handle_inbound (background task)
                           ↓ _resolve_session (Reply-To → In-Reply-To → endereço)
                           ↓ _extract_new_text          (strip quoted text)
                           ↓ _store_attachments         (AttachmentStore)
                           ↓ NormalizedInboundEvent(content_type="text")
                           ↓ _publish_inbound → conversations.inbound
```

HTTP 200 retornado imediatamente — todo processamento em `asyncio.create_task`.

#### 8.4.5 Extração de texto novo (strip quoted text)

Emails inbound carregam o histórico citado abaixo da nova mensagem. O adapter extrai
apenas o texto novo antes de publicar no stream:

**Fontes de texto (prioridade):**
1. `text/plain` do MIME — preferido por simplicidade
2. `text/html` convertido com `html2text` — quando só vem HTML

**Strip de quoted text — heurísticas:**
- Linhas começando com `>`
- Padrão `"On <date>, <name> wrote:"` / `"Em <data>, <nome> escreveu:"`
- Separadores `---`, `___`, `***` precedendo o histórico
- Header `"From: ... Sent: ... To: ... Subject: ..."` de reply Outlook

O texto completo original (com quoted text) é armazenado como `original_content` no
stream para auditoria LGPD. O agente vê apenas o texto novo em `content`.

O agente de triagem acessa o histórico estruturado via `email_get_thread` — nunca
depende do quoted text do email.

#### 8.4.6 Histórico de thread

Cada mensagem (inbound e outbound) é um evento no `session:{id}:stream`. O histórico
é acumulado no stream do PlugHub — não no quoted text do email.

`email_get_thread` retorna o histórico ordenado por timestamp:
```json
[
  {"role": "customer", "text": "Preciso de ajuda com minha fatura.", "at": "..."},
  {"role": "agent",    "text": "Olá! Vou verificar sua fatura.", "at": "..."},
  {"role": "customer", "text": "O valor está errado.", "at": "..."}
]
```

O email que o cliente recebe continua com o thread citado abaixo (comportamento
nativo esperado) — mas o PlugHub não depende desse cited text para funcionar.

#### 8.4.7 Outbound — MIME multipart

```
deliver_text(payload)
  ↓ busca assinatura do agente (AgentType.email_signature)
  ↓ renderiza Markdown → HTML (mistune)
  ↓ monta MIME multipart/alternative:
       text/plain  ← body_text + assinatura plain text
       text/html   ← body_html (Markdown renderizado) + assinatura HTML
  ↓ headers:
       From:        <from_address da mailbox>
       To:          <contact_id>
       Subject:     Re: <subject original>
       Reply-To:    reply+{session_id}@{reply_domain}
       In-Reply-To: <Message-ID do email anterior>
       References:  <cadeia de Message-IDs>
  ↓ provider.send(...)
```

**Assinatura de agentes:** campo `email_signature` no cadastro de `AgentType`
(HTML + plain text). Para agentes AI, assinatura padrão configurável por pool
nos metadados do ChannelEndpoint. Appended automaticamente pelo adapter — o Skill
Flow não precisa incluir a assinatura na mensagem.

**Templates:** `deliver_menu` para email usa `email_send_template` em vez de
coleta sequencial — email suporta HTML rico, formulários não interativos são
renderizados como lista numerada com instrução de reply.

#### 8.4.8 MCP tools para agentes (email-specific)

Declarados no `mcp-server-plughub`, acessíveis via Skill Flow YAML:

| Tool | Input | Output | Descrição |
|---|---|---|---|
| `email_get_thread` | `session_id` | `list[EmailMessage]` | Histórico completo do thread (estruturado, sem quoted text) |
| `email_get_metadata` | `session_id` | `EmailMetadata` | Subject, From, CC, data, lista de attachments, Message-ID original |
| `email_send_template` | `session_id`, `template_id`, `vars` | `message_id` | Envia template configurado na mailbox com variáveis substituídas |
| `email_set_label` | `session_id`, `label` | `ok` | Marca o email com label/categoria (para relatórios e triagem) |
| `email_get_attachment` | `session_id`, `filename` | `url` | URL pública do attachment pelo nome |

Respostas livres de agentes (não-template) usam `notification_send` já existente
no mcp-server → `conversations.outbound` Kafka → `deliver_text` no EmailAdapter.

#### 8.4.9 Redis keys (proprietárias do email)

```
channel:email:{contact_email_hash}:session    session_id ativo por endereço (sem TTL fixo)
channel:email:{message_id_hash}:session       session_id por Message-ID (TTL 30d)
{mailbox_id}:config:email:signing_key         override de credencial por mailbox
{mailbox_id}:config:email:smtp_api_key        override de API key por mailbox
```

#### 8.4.10 Verificação de webhook Mailgun

Mailgun assina cada request com HMAC-SHA256 sobre `timestamp + token`:

```python
import hmac, hashlib

def verify_mailgun(signing_key: str, token: str, timestamp: str, signature: str) -> bool:
    value    = timestamp + token
    computed = hmac.new(signing_key.encode(), value.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, signature)
```

Headers: `X-Mailgun-Timestamp`, `X-Mailgun-Token`, `X-Mailgun-Signature`.
Dev mode: `signing_key` vazio → `verify_signature` retorna `True`.

#### 8.4.11 Testes sem mailbox real

**Testes automatizados:** `MockEmailProvider` — `sent_messages: list[dict]`,
`verify_signature` sempre `True`, `parse_inbound` retorna `ParsedEmail` configurável.
Cobre 100% dos casos via pytest.

**Testes de integração real:** Mailgun sandbox — domínio `@sandbox<hash>.mailgun.org`
disponível sem DNS próprio. Recebe emails reais para até 5 endereços autorizados.
Inbound via ngrok. Sem aprovação de domínio necessária.

**Pending (fase futura):** Microsoft Graph API (Exchange/O365), Gmail API (Pub/Sub),
IMAP/SMTP genérico (polling — modelo diferente de webhook).

**`channel_session_id`:** `Message-ID` do header SMTP do email inbound

---

## 9. Canal de Voz — Especificação

### 9.1 Modelo arquitetural

A sala de conferência do PlugHub e a conference bridge do CPaaS são a **mesma
entidade**. O CPaaS é o media gateway central. O `VoiceAdapter` controla um
**bot leg** dentro da conference, que é o único ponto de conversão
áudio ↔ texto.

```
CPaaS Conference Bridge  (= "sala de conferência" do PlugHub)
  ├── Customer leg    PSTN / WebRTC     ← CPaaS gerencia
  ├── Human Agent leg WebRTC browser   ← CPaaS gerencia (quando há agente humano)
  └── Bot leg         VoiceAdapter     ← PlugHub controla
           ├── STT: áudio cliente  →  texto  →  session:{id}:stream
           └── TTS: texto IA       →  áudio  →  conference (cliente + agente ouvem)
```

**Todo o plano de controle — AI Gateway, ContextStore, Skill Flow, Arc 6,
Arc 11 — opera sobre texto e não muda.**

### 9.2 VoiceAdapter — dois vínculos simultâneos

```
Vínculo 1: HTTP webhook  →  controle de chamada
  POST /webhooks/voice/inbound    (nova chamada)
  POST /webhooks/voice/status     (answer, hangup, transfer, no-answer)

Vínculo 2: WebSocket     →  bot leg de áudio (CPaaS → VoiceAdapter)
  WS /voice/media/{call_sid}      (stream de áudio μ-law do CPaaS)
```

O CPaaS conecta no WebSocket do VoiceAdapter após receber instrução no
response do webhook de inbound (TwiML/TXML: `<Connect><Stream.../></Connect>`).

### 9.3 Fluxo de chamada inbound (AI-only)

```
1. PSTN → CPaaS → POST /webhooks/voice/inbound
2. VoiceAdapter cria session_id, contact_id
3. Response: instrução CPaaS para abrir bot leg + criar conference
4. CPaaS abre WS /voice/media/{call_sid}
5. _open_session → ContactOpenEvent → conversations.events
6. _route_inbound → conversations.inbound → Routing Engine → AI agent

Loop STT:
7. CPaaS envia chunks de áudio μ-law via WS
8. VoiceAdapter → DeepgramProvider.stream(chunk) → transcript parcial
9. Deepgram → transcript final → _normalize_text(content_type="audio_transcript")
10. _publish_inbound → conversations.inbound → AI Gateway

Loop TTS (quando AI responde):
11. conversations.outbound → VoiceAdapter.deliver_outbound
12. text → TTSProvider.synthesize(text) → audio bytes
13. VoiceAdapter → CPaaS WS → cliente ouve (e agente humano, se presente)
```

### 9.4 Fluxo de transferência AI → Agente Humano

```
1. Routing Engine decide transferir para pool humano
2. orchestrator-bridge publica conversation.assigned → agent WebSocket
3. CPaaS: add_participant(conference_id, agent_sip_uri_ou_webrtc)
4. Bot leg permanece: STT continua, TTS da IA continua
5. Human agent leg: áudio direto com cliente, sem passar pelo bot leg
6. Human agent vê transcript em tempo real na tela (session stream)
7. Human agent vê sugestões IA (visibility: agents_only) — texto na tela
```

### 9.5 STT do agente humano (opcional, "ligável")

Quando ativado via config (`voice_agent_stt_enabled: true`):
- CPaaS fornece trilha de áudio separada por leg (Telnyx nativo; Twilio via
  dual-channel recording + streaming)
- DeepgramProvider.stream() no leg do agente → transcript com role "agent"
- Publicado em stream com `author.role: "primary"` (agente) e
  `content_type: "audio_transcript"`
- Nenhuma mudança de arquitetura — apenas ativação de pipe adicional

### 9.6 Supervisor e especialista IA em sessão de voz

Operam exatamente como hoje no webchat:
- Supervisor: vê transcript em tempo real (session stream), sem leg de áudio
- Especialista IA: lê transcript, responde em texto
  - `visibility: agents_only` → agente humano vê na tela
  - `visibility: all` → VoiceAdapter converte para TTS → cliente ouve

### 9.7 Interfaces de provider e seleção de TTS/STT

Três Protocols independentes — cada um pode ser substituído sem afetar os outros:

| Interface | Responsabilidade |
|---|---|
| `IVoiceProvider` | CPaaS: controle de chamada e conference (Twilio) |
| `ISTTProvider` | Speech-to-Text streaming (Deepgram WebSocket) |
| `ITTSProvider` | Text-to-Speech REST (ElevenLabs / Twilio Say) |

**Twilio é exclusivamente tronco de voz** — nunca produz TTS no plano de dados. O áudio TTS é sintetizado por um provedor externo e injetado via `conference.announce_url`.

#### Seleção de TTS

| Provider | Env var | Comportamento |
|---|---|---|
| `ElevenLabsTTSProvider` | `PLUGHUB_VOICE_ELEVENLABS_API_KEY` | Primário — REST API, retorna MP3 bytes |
| `DeepgramAuraTTSProvider` | `PLUGHUB_VOICE_TTS_PROVIDER=deepgram_aura` | Alternativo de alta qualidade |
| `TwilioSayTTSProvider` | (padrão quando sem chave externa) | Fallback — delega ao `<Say>` do Twilio, sem API externa |

A factory `_build_tts_provider()` em `VoiceAdapter` monta automaticamente um `FallbackTTSProvider` quando há providers externos configurados, com `TwilioSay` sempre como último recurso (nunca falha).

#### Seleção de STT

| Provider | Env var | Comportamento |
|---|---|---|
| `DeepgramSTTProvider` | `PLUGHUB_VOICE_DEEPGRAM_API_KEY` | Primário — WebSocket streaming, modelo nova-2 |
| `MockSTTProvider` (fallback) | (automático) | Silencioso — mantém a chamada ativa sem STT |

A factory `_build_stt_provider()` monta um `FallbackSTTProvider([deepgram, mock])` quando a API key está configurada. Queda do Deepgram não derruba a chamada.

#### Encadeamento de fallback

```
FallbackTTSProvider
  ├── ElevenLabsTTSProvider  ← primary (returns MP3 bytes or None on error)
  └── TwilioSayTTSProvider   ← last resort (returns None → CPaaS <Say>)

FallbackSTTProvider
  ├── DeepgramSTTProvider    ← primary (yields STTResult stream)
  └── MockSTTProvider        ← last resort (silent, call stays alive)
```

### 9.8 Redis keys (proprietárias do voice)

```
channel:voice:{call_sid}:session      session_id para este CallSid
channel:voice:{session_id}:conference conference_id do CPaaS
channel:voice:{session_id}:bot_leg    participant_id do bot leg
channel:voice:{session_id}:agent_leg  participant_id do agente humano (quando presente)
```

**`channel_session_id`:** `CallSid` do CPaaS

### 9.9 Modos de input — DTMF vs STT

O tipo de input aceito em `interaction.request` (menu/collect de voz) é controlado
pelo parâmetro `input_mode` no payload do step:

| `input_mode` | Mecanismo | Uso recomendado |
|---|---|---|
| `"dtmf"` | Aguarda evento `dtmf` no WS (dígito no teclado) | Menus numéricos (≤9 opções) |
| `"voice"` | Aguarda transcript Deepgram final | Campos livres, nomes, endereços |
| `"any"` | Aceita o que chegar primeiro | Menus simples com fallback de voz |

**DTMF no Twilio Media Streams** — o WebSocket recebe tipos de evento separados:

```json
// Áudio do cliente (→ Deepgram STT quando input_mode = "voice" ou "any")
{"event": "media", "media": {"track": "inbound_track", "payload": "<base64 μ-law>"}}

// DTMF (→ capturado diretamente quando input_mode = "dtmf" ou "any")
{"event": "dtmf", "dtmf": {"track": "inbound_track", "digit": "2"}}
```

Quando `input_mode: "dtmf"`, o áudio ainda flui para Deepgram (transcrição
passiva no stream), mas a resolução do collect aguarda o evento `dtmf` — sem
latência de STT. O texto TTS deve instruir o canal de input esperado:

- `"dtmf"` → *"Pressione 1 para Suporte Técnico, 2 para Financeiro…"*
- `"voice"` → *"Diga o assunto do seu contato…"*
- `"any"`   → *"Pressione ou diga o número da opção…"*

Estado de coleta ativa no Redis (TTL 30min):

```
channel:voice:{session_id}:collect
  { menu_id, fields: [...], current_index, answers: {}, input_mode }
```

### 9.10 Chamada outbound — `collect` step de workflow

O Skill Flow `collect` step pode contatar o cliente via voz. O `workflow-api`
publica em `collect.events`; o `VoiceAdapter` consome esse tópico e executa a
discagem.

**Fluxo:**

```
collect.events (channel: "voice")
  { target: "+5511...", pool_id: "...", collect_token: "...",
    trunk_id: "...", journey_id?: "..." }
        ↓
VoiceAdapter._handle_collect_event
        ↓
TwilioVoiceProvider.create_call(to=target, from_=DID_do_trunk)
        ↓
cliente atende → POST /webhooks/voice/inbound (call_sid novo)
        ↓
VoiceAdapter detecta pending_collect:{call_sid} no Redis
        ↓
sessão aberta com pool_id do collect event — tratamento idêntico ao inbound
```

Redis key de correlação (TTL 5min — descartada se cliente não atender):

```
channel:voice:pending_collect:{call_sid}   { collect_token, pool_id, journey_id? }
```

### 9.11 Twilio — detalhes do protocolo

**CPaaS inicial:** Twilio (consistente com SMS). Verificação de assinatura idêntica
ao `TwilioProvider` de SMS: HMAC-SHA1 sobre `URL + params_sorted`. Header:
`X-Twilio-Signature`.

**TwiML de resposta para inbound:**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Start>
    <Stream url="wss://{host}/voice/media" track="inbound_track">
      <Parameter name="session_id" value="{session_id}"/>
    </Stream>
  </Start>
  <Dial>
    <Conference waitUrl="{wait_url}" beep="false"
                startConferenceOnEnter="true"
                statusCallbackEvent="leave join end"
                statusCallback="{host}/webhooks/voice/status">
      plughub-{session_id}
    </Conference>
  </Dial>
</Response>
```

`<Start><Stream>` abre o WS de áudio em paralelo; `<Dial>` coloca o cliente na
conference. O áudio do cliente flui simultaneamente para o nosso WS.

**TTS na conference** — via Twilio REST + endpoint `/voice/tts/{tts_id}`:

1. `VoiceAdapter` armazena texto em Redis:
   `channel:voice:tts:{tts_id}` → `{ text, voice }` (TTL 60s)
2. REST: `conference.update(announce_url="/voice/tts/{tts_id}")` → Twilio chama
   o endpoint e reproduz para todos os participantes da conference
3. Endpoint `/voice/tts/{tts_id}` retorna TwiML:
   `<Response><Say voice="{voice}">{text}</Say></Response>`

Voice padrão: `Polly.Camila-Neural` (Amazon Polly via Twilio, PT-BR).
Alternativa: `PLUGHUB_VOICE_TTS_PROVIDER=deepgram_aura` — sintetiza audio bytes
e serve como URL de áudio.

**Adição de agente humano à conference:**

```python
await voice_provider.add_participant(
    conference_sid = conference_sid,
    to             = agent_sip_uri_or_webrtc_identity,
    from_          = voice_from_number,
)
```

**Redis keys adicionais (Twilio-specific):**

```
channel:voice:{session_id}:conference_sid   SID da conference no Twilio
channel:voice:tts:{tts_id}                  texto TTS pendente (TTL 60s)
channel:voice:pending_collect:{call_sid}     correlação de discagem outbound (TTL 5min)
```

---

## 10. Roteamento Outbound — OutboundConsumer Registry

O `OutboundConsumer` consome `conversations.outbound` do Kafka e despacha
para o adapter correto pelo campo `channel` do payload.

```
conversations.outbound  (Kafka)
    ↓
OutboundConsumer._dispatch(msg)
    ↓
registry[msg["channel"]].deliver_outbound(msg["type"], msg)
```

### 10.1 Mapeamento `event_type` → ação por canal

| `event_type`           | Webchat      | WhatsApp         | SMS            | Email          | Voice          |
|------------------------|--------------|------------------|----------------|----------------|----------------|
| `notify`               | WS send_json | Text message API | SMS text       | MIME plain     | TTS → bot leg  |
| `interaction.request`  | WS send_json | Interactive msg  | Seq. collect   | MIME + form    | TTS + collect  |
| `session.closed`       | WS disconnect| — (sem ação)     | — (sem ação)   | — (sem ação)   | CPaaS hangup   |
| `typing.start`         | WS send_json | — (sem suporte)  | — (sem suporte)| — (sem suporte)| — (sem suporte)|
| `media.send`           | WS send_json | Media message    | — (sem suporte)| MIME attach    | — (sem suporte)|

### 10.2 Registro no lifespan

```python
# main.py — lifespan
outbound_consumer = OutboundConsumer(producer, redis, settings)
outbound_consumer.register(WebchatAdapter(...))
outbound_consumer.register(WhatsAppAdapter(...))
outbound_consumer.register(SMSAdapter(...))
outbound_consumer.register(EmailAdapter(...))
outbound_consumer.register(VoiceAdapter(...))  # quando implementado
```

---

## 11. `endpoint_resolver.py` — Já Multi-Canal

O resolver já é parametrizado por `channel` e `identifier`. Nenhuma mudança
necessária. Cada adapter chama:

```python
pool_id = await resolve_pool(
    channel=self.channel_name,     # "whatsapp" | "sms" | "email" | "voice"
    identifier=contact_address,   # E.164 | email | DID | slug
    tenant_id=tenant_id,
    agent_registry_url=settings.agent_registry_url,
    cache_ttl_s=settings.endpoint_cache_ttl_s,
)
```

O agent-registry já tem `GET /v1/channel-endpoints?channel=&identifier=&active=true`
com o filtro `identifier` adicionado na sessão anterior.

---

## 12. Sequência de Implementação

### Fase 1 — Pré-requisitos (sem novos canais) — ~1 dia

1. **`models.py`**: relaxar `channel: Literal["webchat"]` → `str`; adicionar
   `content_type`; adicionar `channel_session_id`
2. **`adapters/base.py`**: criar `ChannelAdapter` ABC com infraestrutura
   compartilhada extraída do `WebchatAdapter`
3. **`adapters/ws_base.py`**: criar `WebSocketAdapter` com padrão 3-tasks
4. **`adapters/webchat.py`**: refatorar para herdar de `WebSocketAdapter`;
   mover lógica compartilhada para base — **zero mudança de comportamento**
5. **`outbound_consumer.py`**: substituir `if channel != "webchat"` por registry
6. **`main.py`**: registrar `WebchatAdapter` no lifespan

### Fase 2 — WhatsApp — ~3 dias

7. `adapters/webhook_base.py` — `WebhookAdapter` com `_sequential_menu_collect`
8. `adapters/whatsapp.py` — `WhatsAppAdapter` completo
9. `main.py` — endpoint `POST /webhooks/whatsapp` + verificação GET
10. `config.py` — settings WhatsApp

### Fase 3 — SMS — ~2 dias

11. `adapters/sms.py` — `SMSAdapter` (herda `WebhookAdapter`)
12. Concatenação de fragmentos via Redis
13. `main.py` — endpoint `POST /webhooks/sms`

### Fase 4 — Email — ~2 dias

14. `adapters/email.py` — `EmailAdapter`
15. Parser MIME (html2text para plain)
16. Correlação por `In-Reply-To` / reply+{session_id}@ routing
17. `main.py` — endpoint `POST /webhooks/email`

### Fase 5 — Voice — planejamento separado

18. `adapters/voice.py` — `VoiceAdapter` + `IVoiceProvider` / `ISTTProvider` / `ITTSProvider`
19. Provider `TelnyxProvider` (ou `TwilioProvider`)
20. Provider `DeepgramSTTProvider` (streaming)
21. Provider `DeepgramAuraTTSProvider`
22. `main.py` — endpoints `/webhooks/voice/inbound` + `/webhooks/voice/status`
    + WS `/voice/media/{call_sid}`

---

## 13. Gravação de Voz — Arquitetura e LGPD

### 13.1 Princípio: gravação por segmento, não por sessão

A gravação não é ativada no início da chamada. Ela é ativada e desativada no
**ciclo de vida de cada segmento** — a participação de um agente em uma sessão.
Isso permite gravar apenas os trechos gerenciados por pools que exijam gravação
(tipicamente pools com agentes humanos) e omitir trechos de IVR, qualificação IA,
ou outros flows sem requisito de compliance.

O campo de controle é `voice_recording: bool = False` no schema de Pool. Um pool
com `voice_recording: true` indica que todos os segmentos atribuídos a ele devem
ser gravados. Pools sem esse campo (ou `false`) nunca ativam gravação.

### 13.2 Campo de configuração no Pool

```yaml
# infra/registry/pools/atendimento_humano.yaml
pool_id: atendimento_humano
channels: [voice]
voice_recording: true        # grava segmentos deste pool
recording_notice_key: voice.recording_notice   # Config API namespace voice, chave recording_notice
```

```yaml
# infra/registry/pools/qualificacao_ia.yaml
pool_id: qualificacao_ia
channels: [voice]
voice_recording: false       # segmentos de IA não são gravados
```

O `agent-registry` expõe `voice_recording` como campo opcional em `PoolConfig`
(`bool`, default `False`). O `routing-engine` inclui esse campo no evento
`conversations.routed` para que o `VoiceAdapter` possa ler sem consulta adicional.

### 13.3 Detecção de transição de segmento no VoiceAdapter

O `VoiceAdapter` mantém XREAD contínuo no `session:{id}:stream` (além do bot leg
WebSocket). Ao detectar `routing.assigned`, verifica `pool.voice_recording` no
payload e decide se inicia gravação para aquele segmento.

```python
# VoiceAdapter — estado interno
_active_recordings: dict[str, str] = {}
# segment_id → cpaaS recording SID

async def _on_stream_event(self, event: dict) -> None:
    if event["type"] == "routing.assigned":
        segment_id = event["segment_id"]
        pool_config = event.get("pool", {})
        if pool_config.get("voice_recording"):
            await self._start_segment_recording(segment_id)

    elif event["type"] == "agent_done":
        segment_id = event["segment_id"]
        if segment_id in self._active_recordings:
            await self._stop_segment_recording(segment_id)
```

### 13.4 Fluxo completo de gravação por segmento

```
1. routing.assigned chega no stream  →  pool.voice_recording == true?
        ↓ sim
2. _announce_and_start_recording(segment_id)
        └─ TTS: aviso de gravação (texto do Config API / fallback padrão)
        └─ asyncio.sleep(1.0)   — pausa natural pós-aviso
        └─ voice_provider.start_recording(
               conference_id, dual_channel=True, trim="trim-silence"
           )
        └─ Redis: channel:voice:{sid}:rec_id:{segment_id} ← CPaaS recording SID
        └─ Redis: channel:voice:{sid}:recording_announced:{segment_id} ← "1" (TTL 24h)

3. Agente atende — conversa ocorre — CPaaS grava tudo

4. agent_done chega no stream  →  segment_id in _active_recordings?
        ↓ sim
5. voice_provider.stop_recording(recording_sid)
        └─ CPaaS processa arquivo de forma assíncrona

6. POST /webhooks/voice/recording  (webhook CPaaS → VoiceAdapter)
        └─ download do arquivo  →  AttachmentStore
               path: voice_recordings/{tenant_id}/{session_id}/{segment_id}.mp3
        └─ session stream ← recording.completed {
               segment_id, url, duration_ms, channels: 2, size_bytes
           }
        └─ ContextStore ← segment.{segment_id}.voice.recording_url
               (disponível para Arc 6 avaliar segmento específico)
```

**Endpoint de conclusão:** `POST /webhooks/voice/recording`  
Autenticado via assinatura HMAC do CPaaS (mesma verificação do webhook de inbound).  
O `segment_id` é passado ao CPaaS como metadata customizada no momento de `start_recording`
e retornado no webhook para correlação.

### 13.5 Dual-channel — relevância para Arc 6

Twilio e Telnyx suportam gravação dual-channel em conference bridge:

- **Canal 0**: cliente (voz PSTN/WebRTC)
- **Canal 1**: agente humano + bot TTS (o que o cliente ouviu do lado do atendimento)

Como a gravação é por `segment_id`, o `evaluation_context_get` pode expor
`recording_url` via `segment.{segment_id}.voice.recording_url` do ContextStore —
o avaliador recebe exatamente o trecho do segmento avaliado, sem silêncio de outros
participantes antes ou depois.

Critério novo possível em formulários Arc 6: `audio_quality` com tipo `voice` —
requer `recording_url` no contexto de avaliação do segmento.

### 13.6 LGPD — aviso por segmento

A gravação de chamadas para fins comerciais exige aviso prévio (CDC Art. 6°,
ANATEL, LGPD Art. 7°). Como a gravação é por segmento, **o aviso é emitido a
cada novo segmento gravado** — não apenas no início da chamada. Isso garante que
o cliente seja informado se a chamada passar por um segmento de IA não gravado
seguido de um segmento humano gravado.

```python
async def _announce_and_start_recording(self, segment_id: str) -> None:
    announced_key = f"channel:voice:{self._session_id}:recording_announced:{segment_id}"
    if await self._redis.exists(announced_key):
        return  # já anunciado neste segmento (ex: re-entry após hold)

    notice = await self._get_config("voice.recording_notice")
    await self._tts_to_conference(notice or _DEFAULT_RECORDING_NOTICE)
    await asyncio.sleep(1.0)

    recording_sid = await self._voice_provider.start_recording(
        conference_id=self._conference_id,
        dual_channel=True,
        trim="trim-silence",
        metadata={"segment_id": segment_id},
    )
    self._active_recordings[segment_id] = recording_sid
    await self._redis.set(
        f"channel:voice:{self._session_id}:rec_id:{segment_id}",
        recording_sid,
        ex=86400,
    )
    await self._redis.set(announced_key, "1", ex=86400)
```

Opt-out via DTMF ou voz pode ser capturado **antes do anúncio** na abertura da
chamada. Se opt-out global, `session.voice.recording_opt_out: true` é escrito no
ContextStore e nenhum segmento inicia gravação. Se opt-out for recusado em segmento
específico, `recording.skipped { segment_id, reason: "customer_opt_out" }` é
emitido no stream para auditoria.

### 13.7 Redis keys (voice recording por segmento)

```
channel:voice:{session_id}:rec_id:{segment_id}              SID da gravação no CPaaS
channel:voice:{session_id}:recording_announced:{segment_id} flag de consent (TTL 24h)
channel:voice:{session_id}:recording_opt_out                opt-out global (TTL = sessão)
```

### 13.8 Retenção e erasure

```
AttachmentStore path:  voice_recordings/{tenant_id}/{session_id}/{segment_id}.mp3
TTL padrão:            5 anos (configurável por tenant — mínimo regulatório ANATEL)
Formato:               MP3 dual-channel (64kbps por canal)
```

O pipeline de erasure LGPD (Audit LGPD Fase 4 — pendente) deve incluir
`voice_recordings/` no escopo de anonimização. A path com `segment_id` permite
apagar gravações de segmentos específicos sem afetar outros segmentos da mesma sessão.

### 13.9 Gravação — quando NÃO ativar

- `pool.voice_recording == false` ou campo ausente no Pool config
- `voice_recording_enabled: false` na config global do tenant (Config API)
- `session.voice.recording_opt_out: true` no ContextStore (opt-out do cliente)
- Segmento já possui `rec_id` ativo no Redis (guard contra double-start)

Pools de suporte jurídico, compliance interno, ou agentes IA de qualificação
simplesmente omitem `voice_recording: true` — sem necessidade de flag explícito
de desativação.

---

## 15. Invariantes — nunca violar

- **Adapter nunca acessa Redis stream diretamente** — usa `_publish_inbound`
  e `_publish_event` herdados da base; `StreamSubscriber` é exclusivo do
  `WebchatAdapter` no lado inbound.
- **TTS só acontece no `VoiceAdapter`** — nenhum outro adapter converte texto
  em áudio.
- **STT só acontece no `VoiceAdapter`** — o plano de controle sempre recebe
  texto; `content_type: "audio_transcript"` é apenas indicador de origem.
- **`verify_signature` falha → HTTP 400, sem processamento** — nenhum evento
  de webhook não autenticado entra no pipeline.
- **`channel_session_id` é imutável** — definido na abertura da sessão, nunca
  atualizado.
- **Coleta sequencial de formulário usa Redis com TTL de 30min** — se o
  cliente abandona a coleta, o estado expira automaticamente.
- **Provider de voz é injetado, nunca instanciado dentro do adapter** — permite
  troca de Twilio por Telnyx sem refactor do `VoiceAdapter`.
