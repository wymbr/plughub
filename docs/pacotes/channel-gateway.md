# Módulo: channel-gateway (@plughub/channel-gateway)

> Pacote: `channel-gateway` (serviço)
> Runtime: Python 3.11+, FastAPI + aiokafka
> Spec de referência: seções 3.5, 4.7m

## O que é

O `channel-gateway` é a camada de normalização entre os canais externos (WhatsApp, SMS, web chat, e-mail, voz) e a plataforma PlugHub. Toda mensagem que entra ou sai da plataforma passa por ele.

É o **único** componente que conhece protocolos específicos de canal. Nenhum outro pacote depende de capacidades de canal.

---

## Invariantes centrais

> - **Nunca rotear conversas** — apenas normalizar e fazer bridge para o Kafka. O Routing Engine é o único árbitro de alocação.
> - **Nunca acessar `pipeline_state`** — só o estado de sessão de canal (coleta de menu) no Redis.
> - **Sempre emitir um único `MenuSubmitEvent`** por step de menu, independentemente de quantos turnos de canal foram necessários.
> - **`MenuSubmitEvent` deve ser indistinguível** de uma mensagem inbound normal, do ponto de vista do Routing Engine.

---

## Estrutura do Pacote

```
channel-gateway/
  src/
    adapters/
      whatsapp.py   ← Meta Cloud API webhooks; Interactive Buttons, List Messages, texto
      sms.py        ← Webhooks de SMS provider; texto com fallback de menu numerado
      webchat.py    ← WebSocket; botões, listas, checkboxes e formulários nativos
      email.py      ← Parse de inbound + SMTP/API outbound; fallback texto para menus
    main.py         ← FastAPI + rotas de webhook + consumer Kafka de outbound
    normalizer.py   ← Conversão de eventos de canal → formato neutro de plataforma
    menu_collector.py ← Orquestração de coleta sequencial para canais sem suporte nativo
    models.py       ← MenuPayload, MenuSubmitEvent, NormalizedInboundEvent, etc.
    config.py       ← settings via variáveis de ambiente
```

---

## Responsabilidades

### 1. Receber eventos inbound de canais

Cada adapter implementa o protocolo do canal correspondente — webhooks HTTP (WhatsApp, SMS, e-mail), WebSocket (web chat) — e entrega eventos normalizados para a plataforma.

### 2. Normalizar para formato neutro e publicar no Kafka

Toda mensagem inbound é convertida para um formato neutro de plataforma e publicada no tópico `conversations.inbound`. O Routing Engine consome desse tópico e não sabe qual canal originou o evento.

### 3. Consumir `conversations.outbound` e entregar pelo canal

O gateway consome o tópico `conversations.outbound`, identifica o canal do destinatário e delega ao adapter correspondente para entrega.

### 4. Coletar input de menu (`MenuPayload`)

Quando o Notification Agent envia um `MenuPayload` via `notification_send` no mcp-server, o Channel Gateway:

- **Canais com suporte nativo** (web chat): renderiza diretamente e aguarda um único evento de submit.
- **Canais sem suporte nativo** (WhatsApp, SMS): executa coleta sequencial — envia cada campo/opção como mensagem separada, acumula respostas parciais no Redis (TTL-bound) e, ao completar todos os campos, emite um único `MenuSubmitEvent` para `conversations.inbound`.

---

## Adapters de Canal

| Adapter | Canal | Protocolo | Status | Referência |
|---|---|---|---|---|
| `webchat.py` | Web Chat | WebSocket | ✅ Piloto | [channel-gateway-webchat.md](channel-gateway-webchat.md) |
| `whatsapp.py` | WhatsApp | Meta Cloud API webhooks | Horizonte 2 | — |
| `sms.py` | SMS | Webhooks de provider | Horizonte 2 | — |
| `email.py` | E-mail | SMTP / API + inbound parse | Horizonte 2 | — |

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

## Fluxo MenuPayload → MenuSubmitEvent

```
Notification Agent
  └─ envia MenuPayload via mcp-server (notification_send)
       │
Channel Gateway
  ├─ canal com suporte nativo (web chat)
  │    └─ renderiza nativo → aguarda submit → MenuSubmitEvent
  └─ canal sem suporte nativo (WhatsApp / SMS)
       └─ coleta sequencial (múltiplos turnos) → agrega → MenuSubmitEvent
            │
conversations.inbound (Kafka)
            │
skill-flow  └─ retoma de __awaiting_selection__, armazena resultado, avança
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
| `channel:{channel}:{session_id}:menu_collect` | Estado parcial de coleta sequencial | TTL configurável (expira se usuário não responder) |

> O Channel Gateway acessa Redis **apenas** para o estado de coleta de menu. Nunca lê nem escreve `pipeline_state`.

---

## Tópicos Kafka

| Tópico | Direção | Conteúdo |
|---|---|---|
| `conversations.inbound` | **Publica** | Todos os eventos inbound normalizados, incluindo `MenuSubmitEvent` |
| `conversations.outbound` | **Consome** | Todos os outbound e `MenuPayload` originados pela plataforma |

---

## Stack

```
Python 3.11+
FastAPI          ← endpoints de webhook
aiokafka         ← producer/consumer Kafka assíncrono
redis[hiredis]   ← estado de coleta sequencial (TTL-bound)
pydantic         ← validação de payloads
```

---

## Dependências

```
channel-gateway
  └── depende de → @plughub/schemas  (MenuPayload, MenuSubmitEvent, contratos de mensagem)
```

Sem dependência de `skill-flow`, `ai-gateway` ou `routing-engine`.

---

## Relação com Outros Módulos

```
channel-gateway
  ├── recebe de → canais externos    (webhooks WhatsApp/SMS/email, WebSocket webchat)
  ├── publica → conversations.inbound  (eventos normalizados + MenuSubmitEvent)
  ├── consome → conversations.outbound (mensagens e MenuPayload para entrega)
  ├── lê/escreve → Redis             (estado de coleta sequencial — apenas menu)
  └── é acionado por → Notification Agent (via mcp-server notification_send → MenuPayload)
```

> **Nota de design:** Toda lógica de renderização e coleta específica de canal fica exclusivamente nos adapters dentro deste pacote. skill-flow, Notification Agent e Routing Engine nunca sabem qual canal está em uso — recebem e enviam sempre o formato neutro de plataforma.
