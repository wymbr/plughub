# Channel Gateway — Adapter WebChat

> Última atualização: 2026-05-25 · Estado: Arc 16

> Spec de referência: seção 3.5
> Módulo pai: [channel-gateway.md](channel-gateway.md)
> ADR: [`docs/adr/adr-webchat-channel.md`](../adr/adr-webchat-channel.md)

---

## Visão geral

O adapter webchat implementa comunicação bidirecional via WebSocket com o cliente
no browser. É um dos três canais distintos baseados em browser/SFU: `webchat`
(WebSocket), `webrtc` (LiveKit SFU) e `whatsapp` (Meta Cloud API) — cada um com
adapter próprio.

Por ser um canal com suporte nativo completo, o adapter webchat nunca executa
coleta sequencial de menu — todos os tipos de interação (`text`, `button`, `list`,
`checklist`, `form`) são renderizados diretamente e retornam um único submit.

**Modelo de stream híbrido:** o cliente **não** é um participante nomeado da
sessão. O Channel Gateway faz `XREAD` diretamente sobre `session:{id}:stream`
(o canonical stream) para entregar eventos ao cliente, e publica as mensagens
inbound do cliente na plataforma. A reconexão usa um cursor sobre o stream —
zero mensagens perdidas quando o cliente cai e volta.

---

## Protocolo WebSocket

### Endpoint

```
GET /ws/chat?contact_id={uuid}   ← contato existente (reconexão)
GET /ws/chat                     ← novo contato (servidor gera contact_id)
```

O servidor retorna o `contact_id` na mensagem `connection.accepted` imediatamente
após a conexão. O cliente usa esse ID para reconectar em caso de queda.

### Autenticação — JWT via corpo da mensagem

O JWT é transmitido **no corpo da mensagem WebSocket**, nunca na URL (evita
vazamento em logs de proxy / histórico). O `jwt_secret` é resolvido por tenant
via Redis (`{tenant_id}:config:webchat:jwt_secret`).

### Reconexão por cursor

Na reconexão, o cliente envia o último `event_id` recebido. O adapter retoma o
`XREAD` do stream a partir desse cursor — todas as mensagens emitidas durante a
desconexão são entregues em ordem. Zero perda de mensagens.

### Tasks concorrentes do adapter

O `WebchatAdapter` mantém 3 tasks assíncronas por conexão:

| Task | Papel |
|---|---|
| `receive_loop` | Recebe eventos do cliente, normaliza, publica inbound |
| `stream_delivery_loop` | `XREAD` sobre `session:{id}:stream`, entrega ao cliente |
| `typing_listener` | Propaga indicadores de digitação |

---

## Upload de anexos — fluxo de 2 estágios

Anexos (imagem, documento, vídeo) usam um handshake de 2 estágios:

```
1. WS  upload.request    → cliente solicita upload (nome, mime, tamanho)
2. WS  upload.ready      → servidor responde com { file_id, upload_url }
3. HTTP POST binário     → cliente envia o arquivo bruto para upload_url
4. WS  upload.committed  → servidor confirma persistência
5. WS  msg.image / msg.document / msg.video → mensagem com o anexo entra no stream
```

**MIME allowlist:**

| Tipo | Formatos | Limite |
|---|---|---|
| Imagem | JPEG, PNG, WebP, GIF | 16 MB |
| Documento | PDF | 100 MB |
| Vídeo | MP4, WebM | 512 MB |

**Expiração:** soft-delete a cada hora; delete físico diário (com +24h de grace).

---

## Masked fields delivery chain

Quando um step `menu` declara campos mascarados, a cadeia de entrega é:

```
step.masked
  → notification_send args
  → conversations.outbound (Kafka)
  → WsMenuRender.masked_fields
  → interaction.request (evento WS)
  → <input type="password"> overlay no webchat
```

O cliente renderiza os campos mascarados como `<input type="password">`. Os valores
nunca trafegam em claro nem são persistidos no stream sem mascaramento.

---

## Eventos WebSocket — cliente → servidor

### Mensagem de texto

```json
{ "type": "message.text", "text": "Quero verificar minha portabilidade" }
```

### Submit de menu

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

### Upload

`upload.request` → servidor responde `upload.ready`. Após o POST binário, `upload.committed`.

---

## Eventos WebSocket — servidor → cliente

### Mensagem de texto

```json
{
  "type": "message.text",
  "message_id": "uuid",
  "author": { "type": "agent_human | agent_ai | system", "display_name": "..." },
  "text": "Olá, como posso ajudar?",
  "timestamp": "2026-05-25T14:00:00Z"
}
```

### MenuPayload — renderização de menu interativo

```json
{
  "type": "menu.render",
  "menu_id": "uuid",
  "interaction": "button | list | checklist | form | text",
  "prompt": "Qual é o motivo do contato?",
  "options": [
    { "id": "opt_portabilidade", "label": "Portabilidade" },
    { "id": "opt_cobranca",      "label": "Cobrança" }
  ],
  "fields": null,
  "masked_fields": []
}
```

Para `interaction: form`, `options` é null e `fields` contém a definição dos campos.
`masked_fields` lista os campos que o cliente deve renderizar como `<input type="password">`.

### Confirmação de conexão / indicador de digitação

```json
{ "type": "connection.accepted", "contact_id": "uuid", "session_id": "uuid" }
{ "type": "agent.typing", "author_type": "agent_human | agent_ai" }
```

---

## Estado Redis

O adapter webchat usa Redis para mapear `contact_id` → conexão WebSocket ativa
(necessário para entrega outbound correta em ambientes multi-instância) e para
resolver o `jwt_secret` do tenant.

```
key:   webchat:session:{contact_id}
value: { instance_id, connected_at }
TTL:   duração máxima do contato (default: 4h)

key:   {tenant_id}:config:webchat:jwt_secret
value: segredo HS256 do tenant
```

Não acessa `pipeline_state`. Não acessa estado de avaliação.

---

## O que o adapter webchat não faz

- Não autentica o cliente como usuário do PlugHub — valida apenas o JWT de canal
- Não persiste mensagens no transcript — o canonical stream é a fonte; o Stream Persister persiste no `session_closed`
- Não roteia conversas — publica em Kafka e o Routing Engine decide
- Não executa coleta sequencial — web chat tem suporte nativo a todos os tipos
- Não conhece o estado do Skill Flow ou do pipeline

---

## Relações com outros módulos

| Módulo | Relação |
|---|---|
| `session:{id}:stream` (Redis) | `XREAD` direto — fonte de entrega outbound ao cliente |
| `conversations.inbound` (Kafka) | Publica todas as mensagens e submits normalizados |
| `conversations.outbound` (Kafka) | Consome MenuPayloads e eventos para entrega |
| `Routing Engine` | Consome `conversations.inbound` para alocação de agente |
| `Stream Persister` | Persiste o stream em PostgreSQL no `session_closed` |
