# Layer 2 — Gateway Layer

> Última atualização: 2026-05-25 · Estado: Arc 16
> Spec de referência: v24.0 seções 7.1–7.4, 2.2a
> Responsabilidade: tradução entre o mundo físico dos canais e o envelope de eventos interno — autenticação de borda, normalização de mídia, controle de acesso LLM
> Implementado por: `channel-gateway` (normalização + pipeline STT/TTS), `ai-gateway` (acesso a modelos)

---

## Visão geral

A Gateway Layer opera na fronteira entre o exterior (canais de comunicação, provedores de LLM) e o interior da plataforma. Tem dois sub-domínios distintos:

**Gateway de canal** — converte eventos físicos de canal (webhooks, WebSocket, SIP, SMTP) em eventos normalizados para o Message Bus, e entrega respostas do interior de volta ao canal nativo.

**AI Gateway** — ponto único de acesso a todos os modelos LLM e NLP da plataforma. Nenhum componente interno chama um modelo diretamente.

---

## Componentes

> ⚠️ **Correção de 2026-08-19 — medido.** As linhas de **Voice Gateway** e de **canal WebRTC** desta
> camada descrevem **projeto, não estado** — o desenho segue válido, a afirmação de que estão
> implementados é **falsa**.
> - **Não existe "Voice Gateway em Go" no repositório** — nenhum componente Go. O canal `voice` é o
>   `VoiceAdapter` Python, e ele **não roda**: `handle_inbound` chama cinco métodos inexistentes —
>   `_open_session`, `_route_inbound`, `_publish_inbound`, `_normalize_text`, `_normalize_menu_result`
>   (`packages/channel-gateway/adapters/voice.py:236,247,433,558,565`; ausentes em
>   `adapters/base.py:44-77`) — mockados em `tests/test_voice_adapter.py:116-121`, e é por isso que a
>   suíte é verde. Em runtime real levanta `AttributeError` antes de publicar em
>   `conversations.inbound`. Correlatos: `channel_name` em vez de `channel` (`voice.py:90`);
>   `stt_queue` nunca drenada e `_handle_stt_result` sem chamador ⇒ **collect por voz morto**, só DTMF.
> - **`webrtc` roda só na sinalização.** O plano de **mídia** nunca foi provisionado: zero serviço
>   LiveKit em compose algum, zero env `LIVEKIT_*`, SDK fora de
>   `packages/channel-gateway/pyproject.toml:6-23`, e sem credencial o provider entra em `_dev_mode`
>   devolvendo token, sala e egress **placebo** (`webrtc_provider.py:167`). Ver
>   [`../arcos/arc15-webrtc.md:3-17`](../arcos/arc15-webrtc.md).
>
> Consequência: o discador está **BLOQUEADO** por falta de plano de mídia — não apenas "planejado".
> Reconstrução: [`adr-voice-media-plane.md`](../adr/adr-voice-media-plane.md).

### Gateway de canal

| Componente | Runtime | Responsabilidade |
|---|---|---|
| **Channel Normalizer** | Python (channel-gateway) | Envelope único para todos os canais; correlação cross-canal; rate limit por customer_id |
| **WhatsApp / Chat / SMS / Email Adapters** | Python (channel-gateway) | Protocolo-específico: HMAC, dedup, janela, mídia, thread management |
| **Voice Gateway** | *(projeto — Go)* | Recepção SIP, mixing de áudio, interface com STT Router. **Não existe no repositório** (nenhum componente Go); o canal `voice` hoje é o `VoiceAdapter` Python, que não roda. Desenho, não estado. |
| **STT Router** | *(projeto — Go)* | Roteamento de stream de áudio para NVIDIA Riva ou Deepgram com fallback automático; fine-tuning LoRA por tenant. **Não existe no repositório** (mesmo caso do Voice Gateway acima — nenhum componente Go). O que existe é `FallbackSTTProvider` em Python (`adapters/voice_provider.py`), e ele não tem stream de áudio a rotear enquanto os canais de áudio não subirem. |
| **Canal WebRTC** | Python (channel-gateway) + LiveKit (SFU self-hosted — **não provisionado**) | Canal `webrtc`: **só a sinalização roda**. Negociação de medium (vídeo → voz → texto), pipeline STT/TTS e gravação por egress são **projeto** — sem SFU, sem SDK, `_dev_mode` placebo. Ver [`../arcos/arc15-webrtc.md`](../arcos/arc15-webrtc.md). |

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

**STT (pipeline de voz)** — *projeto; nenhuma das duas pontas de áudio está de pé (ver correção acima)*:

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

**Voice Gateway — componente Go *(projeto, não implementado)*:** latência crítica (≤ 1.500ms budget); *previsto* em Go para alta concorrência, fora do monorepo principal no Horizonte 1. **Não existe** — não há componente Go no repositório, e o `VoiceAdapter` Python que ocupa hoje esse papel levanta `AttributeError` em runtime real (ver correção em § Componentes).

**STT fine-tuning:** LoRA por tenant para vocabulário específico de domínio (termos técnicos, nomes de produtos). Métricas WER por tenant, fallback automático Riva → Deepgram.

**SLA AI Gateway:** 99,95% (4,4h/ano). Depende do SLA do provider LLM (Anthropic) como dependência externa. 3 réplicas + circuit breaker local por agente.

---

## Referência spec

- Seção 2.2a — AI Gateway
- Seção 7.1 — Messaging Gateway
- Seção 7.3 — Email Multi-Provider
- Seção 7.4 — WebRTC Gateway (canal `webrtc`: sinalização de pé, plano de mídia **não provisionado** — ver correção em § Componentes)
- Seção 5.5 — SLAs por Componente
