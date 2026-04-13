# Layer 1 — Channel Layer

> Spec de referência: v24.0 seções 3.5, 7.1–7.4
> Responsabilidade: abstração e normalização de todos os canais de comunicação — WhatsApp, SMS, Chat Web/App, Email, Voz
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
| **Voice Adapter** | Interface com Voice Gateway e STT Router (componentes Go de alta concorrência, Horizonte 1) |
| **Channel Normalizer** | Converte todos os eventos de canal para envelope interno único; correlação cross-canal com mesmo `session_id` (janela 30min); rate limiting por `customer_id`, não por canal |

---

## Interfaces

**Entrada (inbound):**

| Canal | Protocolo | Observações |
|---|---|---|
| WhatsApp | Webhook HTTPS (Meta) | HMAC verificado, resposta 200 imediata, processamento assíncrono |
| Chat Web | WebSocket / SSE (fallback) | Streaming de tokens |
| SMS | Webhook HTTPS (provider) | — |
| Email | SMTP inbound / API (SendGrid, SES, Mailgun, Exchange) | — |
| Voz (SIP) | SIP trunk → Voice Gateway (Go) → STT Router | Áudio → transcrição → envelope normalizado |
| WebRTC | WebRTC Gateway (Horizonte 2) | Mesmo pipeline STT; vídeo somente para agente humano |

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
- Seção 7.4 — WebRTC Gateway (Horizonte 2)
- Seção 5.1–5.3 — Arquitetura Multi-Site
