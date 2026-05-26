# Layer 1 — Channel Layer

> Última atualização: 2026-05-25 · Estado: Arc 16
> Spec de referência: v24.0 seções 3.5, 7.1–7.4
> Responsabilidade: abstração e normalização de todos os canais de comunicação — WhatsApp, SMS, Chat Web/App, Email, Voz, WebRTC
> Implementado por: `channel-gateway`

---

## Visão geral

A Channel Layer é o ponto de entrada físico de toda comunicação com o cliente. Ela absorve a heterogeneidade dos canais — cada um com seu protocolo, formato, restrições de janela e capacidades de interação — e entrega para o restante da plataforma um envelope de evento normalizado e uniforme.

Nenhum componente interno conhece o protocolo de origem. Um agente IA responde da mesma forma independentemente de o cliente estar no WhatsApp, no chat web ou numa ligação telefônica.

O Channel Gateway também é responsável pela **coleta sequencial de MenuPayload** em canais sem suporte nativo a menus interativos — convertendo múltiplos turnos de coleta num único evento normalizado entregue ao Skill Flow Engine.

---

## Componentes

| Componente | Responsabilidade |
|---|---|
| **WhatsApp Adapter** | Recebe webhooks Meta (verificação HMAC, resposta em < 20s), deduplica por Message-ID, gerencia janela de 24h (mensagens livres vs templates aprovados), faz download de mídia antes da expiração da URL (~5min) |
| **Chat Web Adapter** | WebSocket com fallback SSE, streaming de resposta token a token, reconexão com continuidade de sessão (janela 30min) |
| **SMS Adapter** | Adaptação para canais de texto simples sem formatação rica |
| **Email Adapter** | Inbound Processor (classificação, extração HTML, attachments), Thread Manager (agrupamento por In-Reply-To → References → similaridade, janela 7 dias), priorização por tier e sinais |
| **Voice Adapter** | Interface com tronco PSTN (Twilio Media Streams) e pipeline STT/TTS server-side |
| **WebRTC Adapter** | Canal browser-to-SFU (Arc 15) — sinalização WS `/ws/webrtc/{pool_id}`, sala LiveKit self-hosted, negociação de medium em tempo real (video → voice → text), pipeline STT/TTS via LiveKit Room SDK, gravação via Egress API |
| **Channel Normalizer** | Converte todos os eventos de canal para envelope interno único; correlação cross-canal com mesmo `session_id` (janela 30min); rate limiting por `customer_id`, não por canal |

---

## Canais suportados

Oito canais ativos. `channel` é um filtro hard de roteamento (match obrigatório); `medium` (`voice`, `video`, `message`, `email`) é apenas fator de score.

```
whatsapp · webchat · voice · email · sms · instagram · telegram · webrtc
```

## Interfaces

**Entrada (inbound):**

| Canal | Protocolo | Observações |
|---|---|---|
| WhatsApp | Webhook HTTPS (Meta) | HMAC verificado, resposta 200 imediata, processamento assíncrono |
| Chat Web (`webchat`) | WebSocket / SSE (fallback) | Streaming de tokens; cliente não é participante nomeado |
| SMS | Webhook HTTPS (provider) | — |
| Email | SMTP inbound / API (SendGrid, SES, Mailgun, Exchange) | — |
| Instagram / Telegram | Webhook HTTPS (provider) | — |
| Voz (`voice`) | Tronco PSTN (Twilio Media Streams) → pipeline STT/TTS | Áudio → transcrição → envelope normalizado |
| WebRTC (`webrtc`) | WS `/ws/webrtc/{pool_id}` → SFU LiveKit self-hosted | Arc 15 — clientes na webapp; medium negociado video → voice → text; STT/TTS server-side via LiveKit Room SDK |

**Saída (para o restante da plataforma):**

- Publica em `conversations.inbound` (Kafka) — evento normalizado por conversa
- Lê de `conversations.outbound` (Kafka) — entrega física de respostas ao cliente

**Latency budget por canal** (definido no Channel Normalizer):

| Canal | Budget |
|---|---|
| Voz | 1.500ms |
| Chat Web | 2.000ms |
| WhatsApp | 5.000ms |
| Email | 7.200.000ms (2h) |

---

## Fluxo de dados

```
Cliente envia mensagem
↓
Adapter do canal recebe e responde ACK (< 20s no WhatsApp)
↓
Channel Normalizer:
  - valida e dedup por Message-ID
  - correlaciona session_id cross-canal (janela 30min)
  - aplica rate limit por customer_id
  - aplica latency_budget_ms
↓
Publica em conversations.inbound (Kafka)
↓
[Restante da plataforma processa a conversa]
↓
Routing Engine / Skill Flow publica em conversations.outbound
↓
Adapter do canal entrega ao cliente no formato nativo
```

**Coleta sequencial de MenuPayload (canais sem suporte nativo):**

Para canais que não suportam menus interativos (button, list, checklist, form), o Channel Gateway coleta o payload em múltiplos turnos e entrega um único evento normalizado para o Skill Flow Engine. O skill-flow sempre recebe o resultado completo — nunca os turnos intermediários.

---

## Channel Capability Negotiation (Arc 16, Fase D)

O step `collect` do Skill Flow passa a aceitar `requires: [text | audio | video | file_upload | masked_input | rich_menu]` em vez de um `channel` explícito. O Channel Gateway seleciona o canal outbound em tempo de execução:

- `channel_capability_registry.py` mantém `CHANNEL_CAPABILITIES` — matriz estática de capacidades por canal (whatsapp, sms, email, voice, webchat, webrtc) — e `_CHANNEL_PRIORITY` para ordem de preferência.
- `select_channel(available, requires, preferred)` — algoritmo 2-step: honra `journey.canal_preferido`, depois escolhe por prioridade entre os canais que satisfazem todos os `requires`.
- O Channel Gateway escreve `journey.available_channels` no ContextStore de Journey na primeira mensagem de cada canal; o `collect` lê esse contexto para descobrir os canais disponíveis para o cliente.
- `journey.pending_collect_info` é gravado após o despacho, permitindo a MCP tool `journey_check_pending` localizar journeys com `collect` pendente (Inbound Journey Resume, Arc 16 Fase E).

---

## Considerações operacionais

**Multi-site active-active:** cada site tem seus próprios Channel Gateways. O Global Load Balancer (Anycast / GeoDNS) distribui o tráfego. Sessões WebSocket usam sticky routing por `session_id` para manter a conexão no mesmo site enquanto ativa.

**Deduplicação:** idempotency key `{tenant_id}:cgw:dedup:{message_id}` no Redis compartilhado entre sites — evita reprocessamento de webhooks duplicados entregues pelo provider.

**Email multi-provider:** SendGrid (alta prioridade), AWS SES (volume/custo), Mailgun (fallback/inbound robusto), Exchange/M365 (B2B enterprise). Circuit breaker por provider: CLOSED → OPEN após 5 falhas ou success rate < 85% em 1h → HALF-OPEN após 60s. Dead Letter Queue para emails que falham em todos os providers após 24h.

**Prompt injection:** o Channel Normalizer não filtra o texto do cliente por padrão — instruções injetadas no input podem chegar ao agente. Mitigação fica no nível do agente e das políticas de prompt (seção 7.5 da spec).

---

## Referência spec

- Seção 3.5 — Channel Gateway
- Seção 7.1 — Messaging Gateway (WhatsApp + Chat Web)
- Seção 7.2 — Email Gateway
- Seção 7.3 — Email Multi-Provider
- Seção 7.4 — Canal WebRTC (implementado no Arc 15 — SFU LiveKit self-hosted)
- Seção 5.1–5.3 — Arquitetura Multi-Site
- [`../arcos/arc15-webrtc.md`](../arcos/arc15-webrtc.md) — Canal WebRTC
- [`../arcos/arc16-flow-orchestration.md`](../arcos/arc16-flow-orchestration.md) — Channel Capability Negotiation
