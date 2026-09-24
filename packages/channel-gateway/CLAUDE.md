# @plughub/channel-gateway — Channel Gateway

## What it is

The inbound and outbound normalisation layer between external channels
(WhatsApp, SMS, web chat, email, voice) and the PlugHub platform.

Every message that enters or leaves the platform passes through the Channel Gateway.
It is the sole component that knows channel-specific protocols — no other package
depends on channel capabilities.

## Responsibilities

1. Receive inbound events from channels (webhooks, WebSocket, polling)
2. Normalise to platform-neutral format and publish to `conversations.inbound` Kafka topic
3. Subscribe to `conversations.outbound` Kafka topic and deliver via the appropriate channel adapter
4. Handle `MenuPayload` from the Notification Agent:
   - Channels with native support (web chat): render natively and await a single submit event
   - Channels without native support (WhatsApp, SMS): collect fields sequentially, aggregate, then emit a single `MenuSubmitEvent`
5. Publish `MenuSubmitEvent` to `conversations.inbound` as a normalised single-turn event

## What is NOT this package's responsibility

- Does not route conversations — Routing Engine is the sole arbiter
- Does not validate business rules on captured data — that belongs to subsequent flow steps
- Does not store conversation history — responsibility of Conversation Writer (Fase 2)
- Does not implement skill-flow logic — it only bridges channels to Kafka

## Channel adapters

```
adapters/
  whatsapp.py    — Meta Cloud API webhooks; Interactive Buttons, List Messages, text
  sms.py         — SMS provider webhooks; text-only with numbered menu fallback
  webchat.py     — WebSocket; native buttons, lists, checkboxes, and form rendering
  email.py       — Inbound parse + outbound SMTP/API; text-only fallback for menus
```

## Menu/Form collection — channel matrix

| Interaction | WhatsApp | SMS | Web Chat | Email |
|---|---|---|---|---|
| `text` | Native | Native | Native | Native |
| `button` | Interactive Buttons (≤3) | Numbered fallback | Native buttons | Numbered fallback |
| `list` | List Message (≤10) | Numbered fallback | Native list | Numbered fallback |
| `checklist` | Sequential + comma input | Sequential + comma input | Native checkboxes | Not supported → on_failure |
| `form` | Sequential field-by-field | Sequential field-by-field | Native HTML form | Not supported → on_failure |

### O `menu` leva UM nível (ORQ-19)

Uma opção de `menu` **não tem filhos** no canal. A árvore de um DialogForm se percorre
**nível a nível pelo fluxo** (`dialog_tree_level`): cada turno mostra uma lista plana, e
o gateway desenha só aquele nível (botões ≤ 3 · lista 4–10 · texto numerado > 10).

O ramo que desenhava a árvore num turno só — seções tituladas no WhatsApp e grupos no
widget, com a linha respondendo o CAMINHO (`sac.info_plano`) — foi **aposentado em
2026-09-23**. Ele nunca recebeu árvore pelo caminho vivo: o `notification_send` removia
os filhos antes de publicar. Hoje é lá que o contrato de um nível mora: filhos e
`on_return` saem **nomeados** (`oneLevelMenuOptions`, WARN com a sessão), porque o
cliente veria a pasta como se fosse folha.

⚠️ O `chosen_id` pontuado **continua** aceito pelo `dialog_tree_level` — quem o produz
agora é o classificador por LLM e o esclarecimento da ORQ-13, não o canal. E a proibição
de ponto no id da opção continua, pelos mesmos motivos.

### Segunda linha da opção — `description` (ORQ-15)

A opção pode trazer `description` (texto do CLIENTE, já resolvido na língua pelo `render`,
ausente quando não há — nunca `""`). **Quem tem onde pôr, põe; quem não tem, descarta NOMEANDO**
com `option_tree.note_dropped_descriptions` (uma linha INFO por menu, só quando havia texto).

| superfície | a descrição |
|---|---|
| webchat / WebRTC no browser | segunda linha no botão/checkbox (o widget desenha) |
| WhatsApp lista (4–10) | `description` da linha, cortada em 72 (teto do provider = do autor) |
| WhatsApp botões (≤3), texto (>10) | descarte nomeado |
| SMS · e-mail · voz Twilio · telefone SIP | descarte nomeado |

⚠️ `examples` nunca chega aqui: é do classificador. O Zod do `notification_send` já o remove.
Gate: `infra/test/probe_orq15_option_description.sh`.

**A resposta de menu de escolha viaja como ID da opção, nunca como rótulo** (NIV-13, 2026-09-24):
o motor recusa o que não é id (reenvia o menu, depois `on_invalid`/`on_failure`). O WhatsApp sem
coleta ativa manda o `id` do botão/linha; o texto numerado (WhatsApp >10, SMS, e-mail) ainda não
traduz o número digitado para o id — NIV-14/15/17.

**Sequential fallback protocol**: The adapter sends each field/option as a separate
WhatsApp/SMS message, stores partial responses in the adapter's session state (Redis TTL),
and emits a single `MenuSubmitEvent` to Kafka only when all required fields are collected.
The session state key is `channel:{channel}:{session_id}:menu_collect`.

## MenuPayload → MenuSubmitEvent flow

```
Notification Agent  →  MenuPayload (via mcp-server notification_send)
Channel Gateway     →  channel-specific rendering / sequential collection
                    →  MenuSubmitEvent published to conversations.inbound
skill-flow          →  resumes from awaiting_selection, stores result, advances
```

`MenuSubmitEvent` schema (normalised, channel-agnostic):

```python
@dataclass
class MenuSubmitEvent:
    session_id:   str
    interaction:  Literal["text", "button", "list", "checklist", "form"]
    result:       str | list[str] | dict   # matches interaction type
    channel:      str
    timestamp:    datetime
```

## Kafka topics

| Topic | Direction | Description |
|---|---|---|
| `conversations.inbound` | Produce | All normalised inbound messages, including MenuSubmitEvent |
| `conversations.outbound` | Consume | All outbound messages and MenuPayload from platform |

## Stack

- Python 3.11+
- FastAPI — webhook endpoints
- aiokafka — async Kafka producer/consumer
- redis[hiredis] — sequential collection session state (TTL-bound)
- pydantic — payload validation

## Dependencies

- `@plughub/schemas` — MenuPayload, MenuSubmitEvent, platform message contracts
- No dependency on skill-flow, ai-gateway, or routing-engine

## Invariants

- Never route conversations — only normalise and bridge
- Never access pipeline_state — only the channel session state for menu collection
- Always emit a single MenuSubmitEvent per menu step, regardless of how many channel turns were required
- MenuSubmitEvent must be indistinguishable from a regular inbound message from the Routing Engine's perspective

## `contact_closed` event — reason taxonomy

When a customer connection ends, the Channel Gateway publishes a `contact_closed`
event to `conversations.events`. The `reason` field is the discriminator used by
the Orchestrator Bridge to determine whether the **entire conversation** must be
torn down or only **one agent's session** should be cleaned up.

### Reasons emitted by Channel Gateway

| Reason | Trigger | `customer_side` |
|---|---|---|
| `"client_disconnect"` | Customer WebSocket / channel connection dropped unexpectedly | True |
| `"timeout"` | Customer idle timeout exceeded (adapter-level TTL) | True |
| `"agent_done"` | Platform explicitly closed the customer's outbound connection after normal resolution | True |

All three indicate the customer is no longer reachable. The bridge must push to
`session:closed:{session_id}` (unblocking any active menu BLPOP), notify all active
human agents, restore all Routing Engine instances, and clean up all session state.

### Reason emitted by mcp-server REST `/agent_done`

| Reason | Trigger | `customer_side` |
|---|---|---|
| `"agent_closed"` | One human agent ended their session via the Agent Assist UI | False |

This event does **not** originate from Channel Gateway — it comes from the
`mcp-server-plughub` REST endpoint. The Channel Gateway never emits `"agent_closed"`.

### Why Channel Gateway must use precise reason strings

The `reason` field is the **only** signal the Orchestrator Bridge has to distinguish
"customer left" from "one agent left". Using an ambiguous or generic reason (e.g.
`"closed"`) would cause the bridge to either:
- fail to clean up when the customer disconnects (agent instances remain occupied), or
- tear down a live conference when only one of several agents exits.

Channel Gateway adapters must always set `reason` to one of the three values above.
Any future adapter that introduces a new disconnect reason must update the bridge's
`customer_side` classification accordingly.

### `contact_closed` event schema

```python
@dataclass
class ContactClosedEvent:
    event_type:  Literal["contact_closed"]
    session_id:  str
    contact_id:  str
    channel:     str
    reason:      Literal["client_disconnect", "timeout", "agent_done"]
    timestamp:   datetime
    instance_id: str = ""  # only populated by mcp-server (agent_closed path)
```

See `packages/orchestrator-bridge/CLAUDE.md` for the full conference handling logic
that consumes these events.

## Spec reference

- 3.5  — Channel Gateway: normalisation, adapters, menu collection protocol
- 4.7m — menu step: MenuPayload contract and MenuSubmitEvent schema
