# Layer 2 — Gateway Layer

> Spec de referência: v24.0 seções 7.1–7.4, 2.2a
> Responsabilidade: tradução entre o mundo físico dos canais e o envelope de eventos interno — autenticação de borda, normalização de mídia, controle de acesso LLM
> Implementado por: `channel-gateway` (normalização), componentes de voz Go (Horizonte 1), `ai-gateway` (acesso a modelos)

---

## Visão geral

A Gateway Layer opera na fronteira entre o exterior (canais de comunicação, provedores de LLM) e o interior da plataforma. Tem dois sub-domínios distintos:

**Gateway de canal** — converte eventos físicos de canal (webhooks, WebSocket, SIP, SMTP) em eventos normalizados para o Message Bus, e entrega respostas do interior de volta ao canal nativo.

**AI Gateway** — ponto único de acesso a todos os modelos LLM e NLP da plataforma. Nenhum componente interno chama um modelo diretamente.

---

## Componentes

### Gateway de canal

| Componente | Runtime | Responsabilidade |
|---|---|---|
| **Channel Normalizer** | Python (channel-gateway) | Envelope único para todos os canais; correlação cross-canal; rate limit por customer_id |
| **WhatsApp / Chat / SMS / Email Adapters** | Python (channel-gateway) | Protocolo-específico: HMAC, dedup, janela, mídia, thread management |
| **Voice Gateway** | Go | Recepção SIP, mixing de áudio, interface com STT Router. Alta concorrência e baixa latência. Horizonte 1. |
| **STT Router** | Go | Roteamento de stream de áudio para NVIDIA Riva ou Deepgram com fallback automático. Fine-tuning LoRA por tenant. |
| **WebRTC Gateway** | Go + LiveKit/Daily.co | Sinalização STUN/TURN, negociação SDP, monitoramento de qualidade, degradação adaptativa. Horizonte 2. |

### AI Gateway

| Componente | Runtime | Responsabilidade |
|---|---|---|
| **AI Gateway (`/inference`)** | Python (ai-gateway) | Roteamento para modelo por model_profile, extração de parâmetros de sessão, fallback entre modelos, semantic cache, rate limiting |
| **AI Gateway (`/v1/turn`)** | Python (ai-gateway) | Rota legada — loop de raciocínio do agente |
| **AI Gateway (`/v1/reason`)** | Python (ai-gateway) | Saída estruturada para step `reason` do Skill Flow |

---

## Interfaces

**Gateway de canal:**

- Entrada: webhooks HTTPS, WebSocket/SSE, SIP trunk, SMTP
- Saída: `conversations.inbound` (Kafka) — evento normalizado
- Entrada de retorno: `conversations.outbound` (Kafka)
- Saída de retorno: entrega física ao canal nativo

**AI Gateway:**

- Entrada: chamadas HTTP internas de agentes, Skill Flow Engine, Rules Engine
- Saída para modelos: Anthropic API (ou outros providers configurados via `model_profile`)
- Saída de estado: Redis `session:{session_id}:ai` + pub/sub `session:updates:{session_id}` (consumido pelo Rules Engine)

**STT (pipeline de voz):**

```
Voice Gateway (SIP/WebRTC) → stream de áudio
↓
STT Router → NVIDIA Riva (primário) / Deepgram (fallback)
↓
transcrição em texto → Channel Normalizer
↓
mesmo envelope de evento dos canais de texto
```

---

## Fluxo de dados

**Canal → plataforma:**
```
Evento físico de canal
↓ Gateway de canal (adapter + normalizer)
↓ conversations.inbound (Kafka)
↓ Orchestration Layer
```

**Plataforma → LLM:**
```
Agente / Skill Flow / Rules Engine
↓ POST /inference (AI Gateway)
↓ model_profile → provider
↓ extrai parâmetros → Redis session:{session_id}:ai
↓ publica session:updates:{session_id} (Redis pub/sub)
↓ retorna InferenceResponse
```

**LLM → plataforma:**
```
InferenceResponse devolvido ao chamador
Rules Engine avaliou em paralelo via pub/sub
```

---

## Considerações operacionais

**AI Gateway — stateless por design:** processa um turno por chamada, sem estado entre turnos. Estado de sessão vive no Redis e é lido no início de cada chamada. Escala horizontalmente sem coordenação.

**Semantic cache:** respostas para inputs semanticamente similares (SHA-256 + threshold de similaridade) são reutilizadas com TTL 5min. Reduz custo e latência em cargas repetitivas.

**Rate limiting:** sliding window de 60s por `tenant_id` + `agent_type_id`. Configurável por tenant. Retorna 429 com `RateLimitExceeded` se excedido.

**Fallback de modelo:** quando o provider primário retorna `ProviderError` retryável, o AI Gateway tenta automaticamente o `fallback` declarado no `model_profile`. Transparente para o chamador.

**Voice Gateway — componente Go:** latência crítica (≤ 1.500ms budget). Implementado em Go para alta concorrência. Fora do monorepo principal no Horizonte 1 — repositório de infra separado.

**STT fine-tuning:** LoRA por tenant para vocabulário específico de domínio (termos técnicos, nomes de produtos). Métricas WER por tenant, fallback automático Riva → Deepgram.

**SLA AI Gateway:** 99,95% (4,4h/ano). Depende do SLA do provider LLM (Anthropic) como dependência externa. 3 réplicas + circuit breaker local por agente.

---

## Referência spec

- Seção 2.2a — AI Gateway
- Seção 7.1 — Messaging Gateway
- Seção 7.3 — Email Multi-Provider
- Seção 7.4 — WebRTC Gateway (Horizonte 2)
- Seção 5.5 — SLAs por Componente
