# Módulo: ai-gateway (@plughub/ai-gateway)

> Pacote: `ai-gateway` (serviço)
> Runtime: Python 3.11+ · FastAPI · Anthropic SDK
> Spec de referência: seção 2 (AI Gateway)

## O que é

O `ai-gateway` é o **ponto único de acesso a LLMs** na plataforma. Nenhum componente chama um provider de linguagem diretamente — toda inferência passa por aqui. Ele é responsável por:

- Processar turnos de conversa e devolver respostas ao agente chamador
- Extrair parâmetros de sessão (`intent`, `sentiment_score`, `risk_flag`) a cada turno
- Executar raciocínio estruturado com schema de saída validado (step `reason` do Skill Flow)
- Aplicar cache semântico para evitar chamadas redundantes
- Aplicar rate limiting por tenant/agente
- Persistir parâmetros extraídos no Redis **antes** de retornar a resposta

---

## Invariante central

> O AI Gateway é **stateless** — processa um turno por chamada LLM. Nenhum estado é mantido entre turnos na memória do processo. Todo estado de sessão vive no Redis e é passado explicitamente no `ContextPackage` de cada requisição.

---

## Estrutura do Pacote

```
ai-gateway/src/plughib-ai-gateway/
  main.py         ← FastAPI app, rotas HTTP
  gateway.py      ← AIGateway — engine legada (/v1/turn)
  inference.py    ← InferenceEngine — engine nova (/inference)
  reason.py       ← ReasonEngine — step reason do Skill Flow
  context.py      ← Extração de parâmetros e flags semânticas
  cache.py        ← SemanticCache — SHA-256 + Redis
  rate_limit.py   ← RateLimiter — janela deslizante por tenant/agente
  models.py       ← Modelos Pydantic (contratos de entrada/saída)
  config.py       ← Configuração via variáveis de ambiente
```

---

## Rotas HTTP

| Método | Rota | Engine | Descrição |
|---|---|---|---|
| `POST` | `/v1/turn` | `AIGateway` | Engine legada — turno de conversa |
| `POST` | `/inference` | `InferenceEngine` | Engine nova — turno com cache, rate limit e extração |
| `POST` | `/v1/reason` | `ReasonEngine` | Raciocínio estruturado com output_schema |
| `GET` | `/v1/health` | — | Health check |

---

## Engine legada: `AIGateway` (`/v1/turn`)

`AIGateway.process_turn()` é a implementação original do processamento de turno:

1. Constrói a lista de mensagens a partir do `ContextPackage.conversation_history`
2. Injeta o prompt do sistema (SOUL + DUTIES do agente)
3. Chama o provider LLM configurado
4. Chama `extract_context_from_response()` sobre a resposta
5. Retorna `TurnResponse` com `response_text` e `extracted_params`

Não realiza cache nem rate limiting — responsabilidade da `InferenceEngine`.

---

## Engine nova: `InferenceEngine` (`/inference`)

`InferenceEngine.infer()` implementa o fluxo completo de inferência em 8 etapas ordenadas:

```
1. Rate limit check        → RateLimiter.check_and_increment()
                             Aborta com 429 se limite excedido

2. Cache check             → SemanticCache.get()
                             Retorna imediatamente se cache hit

3. Provider call           → LLM primário configurado (model_profile)
   │
   └─ on failure           → LLM fallback (se configurado)

4. Parameter extraction    → extract_context_from_response()
                             Extrai intent, confidence, sentiment_score,
                             risk_flag, semantic_flags

5. Redis write             → ANTES de retornar — sempre
                             Chave: {tenant_id}:session:{session_id}:turn:{turn_id}:params

6. Cache write             → SemanticCache.set() com TTL 5min

7. Return InferenceResponse

(8. Fallback path)         → Se provider primário falhou e fallback disponível,
                             repete etapas 4–7 com resposta do fallback
```

> **Invariante crítica**: o Redis write (etapa 5) ocorre **antes** do return — nunca após. Se a gravação falhar, o erro é propagado; a resposta não é retornada sem que os parâmetros estejam persistidos.

---

## Extração de Parâmetros de Sessão (`context.py`)

`extract_context_from_response()` analisa o texto da resposta do LLM e produz `ExtractedParams`:

### Parâmetros extraídos

| Campo | Tipo | Descrição |
|---|---|---|
| `intent` | string | Classificação da intenção do cliente |
| `confidence` | float [0.0–1.0] | Confiança da classificação |
| `sentiment_score` | float [-1.0, 1.0] | Sentimento do turno atual |
| `risk_flag` | bool | True se qualquer flag de risco ativa |
| `semantic_flags` | dict | Flags semânticas individuais |

### Flags semânticas

| Flag | Significado |
|---|---|
| `churn_signal` | Intenção de cancelar ou trocar de provedor |
| `high_frustration` | Expressão explícita de frustração ou insatisfação |
| `urgency` | Urgência temporal ou operacional |
| `high_value` | Indicador de alto valor de negócio |
| `escalation_hint` | Pedido implícito ou explícito de escalação |

**Regra de `risk_flag`**: qualquer um de `high_frustration`, `escalation_hint` ou `urgency` ativos → `risk_flag = True`.

### Classificação de intent

`_classify_intent()` mapeia keywords para 6 classes de intenção:

| Classe | Exemplos de keywords |
|---|---|
| `cancellation` | cancelar, cancelamento, churn |
| `complaint` | reclamação, problema, falha |
| `support` | ajuda, suporte, assistência |
| `upgrade` | upgrade, melhorar, plano superior |
| `billing` | fatura, cobrança, pagamento |
| `general` | (fallback) |

---

## Cache Semântico (`cache.py`)

`SemanticCache` evita chamadas redundantes ao LLM para mensagens semanticamente equivalentes.

### Mecanismo

```
1. Normaliza o histórico de mensagens (lowercase, strip)
2. Calcula SHA-256 do histórico normalizado
3. Chave Redis: {tenant_id}:cache:{hash[:32]}
4. GET → cache hit: retorna resposta cached, sem chamar LLM
5. SET → após chamada bem-sucedida, TTL: 300s (5 minutos)
```

### Invalidação

`invalidate_tenant(tenant_id)` usa `SCAN` para encontrar e remover todas as chaves `{tenant_id}:cache:*` — útil após mudanças de configuração ou de prompt.

---

## Rate Limiter (`rate_limit.py`)

`RateLimiter` implementa janela deslizante por `(tenant_id, agent_type_id)` por minuto.

### Mecanismo

```
Chave Redis: {tenant_id}:ratelimit:{agent_type_id}:{unix_timestamp // 60}

check_and_increment():
  1. INCR chave
  2. Se valor == 1 → EXPIRE 60s (primeira chamada da janela)
  3. Se valor > limite configurado → raise RateLimitExceeded → HTTP 429
```

O limite por minuto é configurável por `TenantConfig.rate_limits.requests_per_minute`.

---

## ReasonEngine (`/v1/reason`)

`ReasonEngine.process()` implementa o step `reason` do Skill Flow — inferência com saída estruturada:

```
1. Recebe ReasonRequest:
     prompt_id       ← referência ao prompt template
     input           ← dados de entrada (pipeline_state snapshot)
     output_schema   ← JSON Schema do objeto esperado
     attempt         ← número da tentativa (0 = primeira)

2. Formata descrição do schema para o prompt do sistema

3. Se attempt > 0: injeta contexto de retry
     ("Tentativa anterior falhou — siga estritamente o schema")

4. Chama provider LLM

5. Faz parse do JSON na resposta

6. Valida contra output_schema

7. Retorna ReasonResponse:
     result          ← objeto validado
     raw_response    ← texto bruto do LLM (para debug)
     attempt         ← repete o valor recebido
```

Se o JSON for inválido ou não conformar ao schema, o Skill Flow retenta até `max_format_retries` (configurado no step, 0–3). A cada nova chamada, `attempt` é incrementado, ativando o contexto de retry no prompt.

---

## model_profile — Seleção de Modelo

O `model_profile` determina qual modelo LLM é usado para cada chamada:

| Profile | Uso típico |
|---|---|
| `fast` | Steps de baixa complexidade, alta frequência |
| `balanced` | Atendimento padrão — equilíbrio custo/qualidade |
| `powerful` | Raciocínio complexo, step `reason` com schemas elaborados |

> **Invariante**: o `model_profile` nunca é hardcoded no código — é sempre lido da configuração. Trocar de modelo é mudança de config, não de código.

O mapeamento `profile → modelo` e o fallback por profile são definidos em `config.py` via variáveis de ambiente.

---

## Modelos Pydantic (`models.py`)

### `TurnRequest` / `TurnResponse` (engine legada)

```python
TurnRequest {
  session_id:       UUID
  tenant_id:        str
  agent_type_id:    str
  context_package:  ContextPackage
  model_profile:    "fast" | "balanced" | "powerful"
}

TurnResponse {
  session_id:       UUID
  response_text:    str
  extracted_params: ExtractedParams
}
```

### `InferenceRequest` / `InferenceResponse` (engine nova)

```python
InferenceRequest {
  session_id:       UUID
  turn_id:          UUID
  tenant_id:        str
  agent_type_id:    str
  messages:         list[Message]   # histórico normalizado
  system_prompt:    str
  model_profile:    "fast" | "balanced" | "powerful"
}

InferenceResponse {
  session_id:       UUID
  turn_id:          UUID
  response_text:    str
  extracted_params: ExtractedParams
  cache_hit:        bool
}
```

### `ExtractedParams`

```python
ExtractedParams {
  intent:          str
  confidence:      float          # [0.0, 1.0]
  sentiment_score: float          # [-1.0, 1.0]
  risk_flag:       bool
  semantic_flags: {
    churn_signal:     bool
    high_frustration: bool
    urgency:          bool
    high_value:       bool
    escalation_hint:  bool
  }
}
```

### `ReasonRequest` / `ReasonResponse`

```python
ReasonRequest {
  session_id:    UUID
  tenant_id:     str
  prompt_id:     str
  input:         dict             # snapshot do pipeline_state
  output_schema: dict             # JSON Schema
  model_profile: "fast" | "balanced" | "powerful"
  attempt:       int              # 0 = primeira tentativa
}

ReasonResponse {
  session_id:    UUID
  result:        dict             # objeto validado contra output_schema
  raw_response:  str              # texto bruto do LLM
  attempt:       int
}
```

---

## Redis — Estrutura de Chaves

| Chave | Conteúdo | TTL |
|---|---|---|
| `{tenant_id}:session:{session_id}:turn:{turn_id}:params` | `ExtractedParams` serializado | Sem TTL (gerenciado pela sessão) |
| `{tenant_id}:cache:{sha256[:32]}` | `InferenceResponse` serializado | 300s |
| `{tenant_id}:ratelimit:{agent_type_id}:{window_minute}` | contador de chamadas | 60s |

---

## Dependências

```
ai-gateway
  ├── schemas     ← tipos de domínio (ContextPackage, AgentDone, etc.)
  ├── FastAPI     ← framework HTTP
  ├── Anthropic SDK ← chamadas ao provider Claude
  └── Redis       ← cache, rate limit, parâmetros de sessão
```

---

## Relação com Outros Módulos

```
ai-gateway ← chamado por
  ↑ skill-flow-engine   (step reason → POST /v1/reason)
  ↑ mcp-server-plughub  (agent_turn → POST /v1/turn ou /inference)
  ↑ sdk (PlugHubAdapter) (processo_turn → POST /v1/turn)

ai-gateway → chama
  → Redis (leitura de contexto, escrita de params, cache, rate limit)
  → LLM provider (Anthropic Claude — configurado via model_profile)
```

> **Invariante**: `ai-gateway` nunca é importado por pacotes TypeScript. Apenas componentes Python (`skill-flow-engine`, `mcp-server-plughub` se integrado) o consomem via HTTP.
