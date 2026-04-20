# Channel Gateway — Adapter Webchat (Piloto)

> Spec de referência: v24.0 seção 3.5
> Módulo pai: [channel-gateway.md](channel-gateway.md)
> Escopo: adapter webchat para o piloto. Os demais adapters (WhatsApp, SMS,
> e-mail, voz) pertencem ao roadmap do módulo completo.

---

## Visão geral

O adapter webchat é o único adapter do Channel Gateway necessário para o piloto.
Implementa comunicação bidirecional via WebSocket com o cliente e publica/consome
os tópicos Kafka da plataforma no formato normalizado definido em `channel-gateway.md`.

Por ser um canal com suporte nativo completo, o adapter webchat nunca executa
coleta sequencial de menu — todos os tipos de interação (`text`, `button`, `list`,
`checklist`, `form`) são renderizados diretamente e retornam um único submit.

---

## Protocolo WebSocket

### Endpoint

```
GET /ws/chat?contact_id={uuid}   ← contato existente (reconexão)
GET /ws/chat                     ← novo contato (servidor gera contact_id)
```

O servidor retorna o `contact_id` na mensagem `connection.accepted` imediatamente
após a conexão. O cliente usa esse ID para reconectar em caso de queda.

### Ciclo de vida da conexão

```
Cliente conecta
  ↓
Servidor emite  → connection.accepted  { contact_id, session_id }
  ↓
Channel Gateway publica → contact_open  (conversations.events)
  ↓
[troca de mensagens]
  ↓
Cliente desconecta  OU  agente fecha contato via agent_done
  ↓
Channel Gateway publica → contact_closed  (conversations.events)
```

`contact_closed` é publicado em dois cenários:
- WebSocket fecha (cliente ou timeout)
- Evento `session.closed` recebido via `conversations.outbound` do Routing Engine

---

## Eventos WebSocket — cliente → servidor

Todos os eventos do cliente chegam como JSON no WebSocket.

### Mensagem de texto

```json
{
  "type": "message.text",
  "text": "Quero verificar minha portabilidade"
}
```

### Submit de menu

Emitido pelo cliente após interagir com qualquer componente de menu renderizado.
O campo `result` varia conforme o tipo de interação declarado no `MenuPayload`.

```json
{
  "type": "menu.submit",
  "menu_id": "uuid",
  "interaction": "button | list | checklist | form",
  "result": "string | string[] | object"
}
```

| `interaction` | Tipo de `result` | Exemplo |
|---|---|---|
| `button` | `string` (option id) | `"opt_portabilidade"` |
| `list` | `string` (option id) | `"opt_portabilidade"` |
| `checklist` | `string[]` (option ids) | `["opt_a", "opt_c"]` |
| `form` | `object` (field → valor) | `{"nome": "João", "cpf": "123"}` |

---

## Eventos WebSocket — servidor → cliente

### Mensagem de texto (agente ou sistema)

```json
{
  "type": "message.text",
  "message_id": "uuid",
  "author": {
    "type": "agent_human | agent_ai | system",
    "display_name": "Atendente | Assistente | null"
  },
  "text": "Olá, como posso ajudar?",
  "timestamp": "2026-04-06T14:00:00Z"
}
```

### MenuPayload — renderização de menu interativo

Enviado ao cliente quando o Skill Flow executa um step `menu`.
O cliente renderiza o componente adequado (botões, lista, checkboxes, formulário)
e aguarda o submit do usuário.

```json
{
  "type": "menu.render",
  "menu_id": "uuid",
  "interaction": "button | list | checklist | form | text",
  "prompt": "Qual é o motivo do contato?",
  "options": [
    { "id": "opt_portabilidade", "label": "Portabilidade" },
    { "id": "opt_cobranca",      "label": "Cobrança" },
    { "id": "opt_cancelamento",  "label": "Cancelamento" }
  ],
  "fields": null
}
```

Para `interaction: form`, `options` é null e `fields` contém a definição dos campos:

```json
{
  "type": "menu.render",
  "menu_id": "uuid",
  "interaction": "form",
  "prompt": "Preencha seus dados para continuar",
  "options": null,
  "fields": [
    { "id": "nome",    "label": "Nome completo", "type": "text",  "required": true },
    { "id": "telefone","label": "Telefone",       "type": "text",  "required": true },
    { "id": "motivo",  "label": "Motivo",         "type": "select","required": true,
      "options": ["Portabilidade", "Cancelamento", "Financeiro"] }
  ]
}
```

### Confirmação de conexão

```json
{
  "type": "connection.accepted",
  "contact_id": "uuid",
  "session_id": "uuid"
}
```

### Indicador de digitação

```json
{
  "type": "agent.typing",
  "author_type": "agent_human | agent_ai"
}
```

---

## Publicação em `conversations.inbound`

A cada mensagem ou submit recebido do cliente, o adapter publica no Kafka
com o envelope normalizado. O `context_snapshot` é lido do Redis
(`session:{session_id}:ai`) no momento da publicação.

```json
{
  "message_id": "uuid",
  "contact_id": "uuid",
  "session_id": "uuid",
  "timestamp": "2026-04-06T14:00:00Z",
  "direction": "inbound",
  "author": {
    "type": "customer",
    "id": null,
    "display_name": null
  },
  "content": {
    "type": "text",
    "text": "Quero verificar minha portabilidade",
    "payload": null
  },
  "context_snapshot": {
    "intent": "portability_check",
    "sentiment_score": -0.10,
    "turn_number": 3
  }
}
```

Para submit de menu, `content.type` é `"menu_result"` e `content.payload`
carrega o `MenuSubmitEvent`:

```json
{
  "content": {
    "type": "menu_result",
    "text": null,
    "payload": {
      "menu_id": "uuid",
      "interaction": "button",
      "result": "opt_portabilidade"
    }
  }
}
```

---

## Consumo de `conversations.outbound`

O adapter consome o tópico `conversations.outbound` e entrega ao cliente
via WebSocket. Filtra por `channel: webchat` e `contact_id`.

| Tipo de evento outbound | Ação do adapter |
|---|---|
| Mensagem de texto | Emite `message.text` via WebSocket |
| `MenuPayload` | Emite `menu.render` via WebSocket e aguarda `menu.submit` |
| Indicador de digitação | Emite `agent.typing` via WebSocket |
| `session.closed` | Emite evento de encerramento, fecha WebSocket, publica `contact_closed` |

---

## Eventos de ciclo de vida em `conversations.events`

### `contact_open`

```json
{
  "event_type": "contact_open",
  "contact_id": "uuid",
  "session_id": "uuid",
  "channel": "webchat",
  "started_at": "2026-04-06T13:45:00Z"
}
```

### `contact_closed`

```json
{
  "event_type": "contact_closed",
  "contact_id": "uuid",
  "session_id": "uuid",
  "channel": "webchat",
  "reason": "agent_done | client_disconnect | timeout",
  "started_at": "2026-04-06T13:45:00Z",
  "ended_at": "2026-04-06T14:00:00Z"
}
```

O campo `reason` determina o `outcome` que o Conversation Writer propaga
para o `transcript.created` e, consequentemente, para o `evaluation.requested`.

---

## Estado Redis

O adapter webchat usa Redis apenas para mapear `contact_id` → WebSocket ativo,
necessário para entregar mensagens outbound à conexão correta em ambientes
com múltiplas instâncias do gateway.

```
key:   webchat:session:{contact_id}
value: { instance_id, connected_at }
TTL:   duração máxima do contato (default: 4h)
```

Não acessa `pipeline_state`. Não acessa estado de avaliação ou transcript.

---

## Configuração

```yaml
channel_gateway:
  webchat:
    endpoint: /ws/chat
    heartbeat_interval_seconds: 30
    connection_timeout_seconds: 300    # fecha se cliente não enviar nada em 5min
    contact_max_duration_seconds: 14400
  kafka:
    consumer_group: channel-gateway-webchat
    inbound_topic: conversations.inbound
    outbound_topic: conversations.outbound
    events_topic: conversations.events
  redis:
    session_ttl_seconds: 14400
```

---

## O que o adapter webchat não faz

- Não autentica o cliente — autenticação é responsabilidade do step `menu`
  com `interaction: form` ou de um agente IA de autenticação
- Não persiste mensagens — isso é responsabilidade do Conversation Writer
- Não roteia conversas — publica em Kafka e o Routing Engine decide
- Não executa coleta sequencial — web chat tem suporte nativo a todos os tipos
- Não conhece o estado do Skill Flow ou do pipeline

---

## Relações com outros módulos no piloto

| Módulo | Relação |
|---|---|
| `conversations.inbound` (Kafka) | Publica todas as mensagens e submits normalizados |
| `conversations.outbound` (Kafka) | Consome mensagens e MenuPayloads para entrega |
| `conversations.events` (Kafka) | Publica `contact_open` e `contact_closed` |
| `Redis` | Lê `context_snapshot` do AI Gateway; mantém mapa session → WebSocket |
| `Conversation Writer` | Consome `conversations.inbound` e `conversations.outbound` |
| `Routing Engine` | Consome `conversations.inbound` para alocação de agente |
| `Notification Agent` | Produz mensagens e MenuPayloads em `conversations.outbound` |
