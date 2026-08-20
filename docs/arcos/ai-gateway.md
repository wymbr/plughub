# AI Gateway — LLM Inference & Multi-Account Rotation

> Última atualização: 2026-07-01 · Estado: Arc 16 + LLM Accounts Catalog
>
> Full reference for AI Gateway architecture, multi-account rotation, model profiles, and sentiment emission.
> See CLAUDE.md for architectural summary.

---

## Architectural Role

AI Gateway is a **stateless** Python FastAPI service. It processes one LLM turn per call — no session state, no conversation history management. Callers (Skill Flow Engine, orchestrator-bridge) pass the full context window on every request.

Sole responsibilities:
- Receive `InferenceRequest` (messages, tools, model profile, permissions)
- Select the best LLM account (multi-account rotation)
- Call the LLM provider (Anthropic, OpenAI fallback)
- Return `InferenceResponse` (assistant turn, tool calls, stop reason)
- Emit `sentiment.updated` Kafka event for every customer message scored

**Never does**: session management, tool execution, context persistence, routing decisions.

---

## Multi-Account Rotation — AccountSelector

`account_selector.py` manages per-provider API key pools. Activated when `PLUGHUB_ANTHROPIC_API_KEYS` contains multiple comma-separated keys.

### Algorithm (per call)

1. For each registered account, check throttle key `ai_gw:{provider}:{key_id}:throttled` in Redis
2. Compute load score: `rpm_used/rpm_limit × 0.7 + tpm_used/tpm_limit × 0.3`
3. Pick account with lowest score (ignoring throttled accounts)
4. On `429`/`529` response: call `mark_throttled(key_id, ttl=throttle_retry_after_s)` → retry with next account
5. If all accounts for primary provider are throttled: cross-provider fallback via `FallbackConfig`

Redis keys:
```
ai_gw:{provider}:{key_id}:throttled    — string, TTL = throttle_retry_after_s (default 60s)
ai_gw:{provider}:{key_id}:rpm_used     — counter, TTL 60s sliding window
ai_gw:{provider}:{key_id}:tpm_used     — counter, TTL 60s sliding window
```

### Environment configuration

```bash
PLUGHUB_ANTHROPIC_API_KEYS=sk-ant-key1,sk-ant-key2,sk-ant-key3
PLUGHUB_OPENAI_API_KEYS=sk-oai-key1   # optional cross-provider fallback
```

Single key = AccountSelector disabled, direct call.

### Config API namespace `ai_gateway`

| Key | Default | Description |
|---|---|---|
| `account_rotation_enabled` | `true` | Toggle rotation without restart |
| `throttle_retry_after_s` | `60` | TTL for throttle sentinel key |
| `evaluation_model` | `"claude-haiku-4-5"` | Model used by evaluation agents (isolated) |

---

## LLM Accounts Catalog — Configuration-driven accounts (2026-07-01)

Extends the env-only `PLUGHUB_ANTHROPIC_API_KEYS`/`PLUGHUB_OPENAI_API_KEYS` mechanism above with a
**Configuration-managed catalog** so operators can create named LLM accounts, assign each an id, and
bind pools to preferred accounts — without touching env vars per account. Follows the platform's
**Single Source Invariant**: only the API key itself is a secret (env var); everything else
(provider, display name, rpm/tpm limits, active flag) lives in config-api and is UI-editable.

### Entity: LLM Account

Stored in config-api namespace **`llm_accounts`** (`GET/PUT/DELETE /config/llm_accounts/{id}`), one
entry per account id:
```json
{
  "provider":     "anthropic" | "openai",
  "display_name": "Conta Principal",
  "rpm_limit":    50,
  "tpm_limit":    100000,
  "active":       true
}
```
The API key is **never stored here**. It lives in the env var
`PLUGHUB_LLM_ACCOUNT_<ID_UPPER_SNAKE>_API_KEY` on the ai-gateway container only (e.g. account id
`conta_principal` → `PLUGHUB_LLM_ACCOUNT_CONTA_PRINCIPAL_API_KEY`). This naming convention removes the
need for a stored/free-typed env-var-name field, avoiding typo/mismatch risk between config-api and env.

**platform-ui**: `LlmAccountsPage.tsx` (Resources → LLM Accounts tab) — CRUD over the catalog,
displays the expected env var name per account so operators know what to set.

### Boot-time loading — `llm_accounts_catalog.py`

`load_llm_accounts_catalog()` (new module) fetches the whole `llm_accounts` namespace from config-api
at ai-gateway startup (`PLUGHUB_CONFIG_API_URL`, `PLUGHUB_TENANT_ID`), and for each **active** entry
whose env var is set, builds an `LLMAccount` with `config_id = <catalog id>`. If the catalog fetch
fails or returns nothing, `main.py` falls back unchanged to the legacy
`PLUGHUB_ANTHROPIC_API_KEYS`/`PLUGHUB_OPENAI_API_KEYS` construction — **graceful degradation**, ai-gateway
never fails to boot because config-api is unreachable. An entry with no matching env var is skipped
with a warning, never blocks boot.

### Pool → LLM Account binding — `preferred_config_ids`

`Pool.llm_account_ids: string[]` (agent-registry, `PoolRegistrationSchema`) lists the catalog ids a
pool prefers, in preference order (not a strict chain). Wiring, end to end:

```
Pool.llm_account_ids (agent-registry)
  → Routing Engine _write_pool_context() writes session.pool.llm_account_ids[] to ContextStore
    → skill-flow-engine `reason` step reads it (resolvePreferredConfigIds()) and sets
      ReasonRequest.preferred_config_ids
        → ai-gateway ReasonEngine._select_provider() calls
          AccountSelector.pick(provider, preferred_config_ids=...)
```

`AccountSelector.pick()` already supported `preferred_config_ids` (previously wired only for
evaluation campaigns) — it picks the least-loaded account **within** the preferred set, and only
falls through to the full provider pool if every preferred account is unavailable. Empty/absent
`llm_account_ids` = no restriction (unchanged legacy behavior).

**`ReasonEngine` upgrade**: prior to this change, `/v1/reason` (used by every skill-flow `reason`
step) was hardcoded to a single legacy provider and had **no** multi-account support, unlike
`/v1/inference`'s `InferenceEngine`. `ReasonEngine` now accepts `providers`/`account_selector` and
calls `_select_provider()` in both `process()` and `_process_tool_use()` — without this fix,
`preferred_config_ids` would have been a no-op for all `reason` steps.

### What is NOT in scope

- No UI-side "test connection" / key validation flow — operators verify by checking ai-gateway logs
  after setting the env var and restarting.
- No per-account cost tracking (billing remains capacity-based, see Pricing Module).
- No hot-reload of API keys — changing/adding an env var still requires an ai-gateway restart
  (only the non-secret catalog fields are hot-editable via config-api; `config.changed` is not
  currently consumed by ai-gateway to re-fetch the catalog mid-run).

---

## Model Profiles

| Profile | Primary | Fallback | Used by |
|---|---|---|---|
| `realtime` | Claude Sonnet | gpt-4o | customer-facing sessions |
| `balanced` | Claude Haiku | gpt-4o-mini | background/bulk tasks |
| `evaluation` | Claude Haiku | — (no fallback) | evaluation agents (isolated quota) |

The `evaluation` profile is deliberately isolated so bulk evaluation runs do not affect realtime session RPM/TPM budgets.

---

## Sentiment Emission

After scoring each customer message, `sentiment_emitter.py` fires two side effects:

### Kafka: `sentiment.updated`

Schema: `SentimentUpdatedEventSchema`
```json
{
  "event_id":   "<uuid>",
  "tenant_id":  "...",
  "session_id": "...",
  "pool_id":    "...",
  "score":      -0.42,
  "timestamp":  "2026-05-09T..."
}
```

**Note**: No `category` field. Classification (satisfied/neutral/frustrated/angry) is a business interpretation using tenant-configurable band thresholds — this is the consumer's (analytics-api's) responsibility.

### Redis: `sentiment_live` hash

`{tenant_id}:sentiment_live:{pool_id}` — TTL 1h:
```
avg_score       — running average
score_total     — sum for incremental avg
count           — number of data points
last_session_id — most recent session scored
updated_at      — ISO-8601 last update
```

The `supervisor_state` tool reads this hash to show pool-level sentiment in real time.

---

## Inference Request / Response

`InferenceRequest`:
```python
{
  "session_id":    str,
  "tenant_id":     str,
  "model_profile": "realtime" | "balanced" | "evaluation",
  "messages":      [...],          # full context window
  "tools":         [...],          # pre-filtered by permissions[]
  "permissions":   [...],          # from JWT — filtered before sending to LLM
  "output_schema": {...} | None,   # for reason step (structured output)
  "system":        str | None,
  "journey_id":    str | None,     # Arc 16 — when set, AI Gateway prepends a Journey context block
}
```

### Journey context block (Arc 16 Fase A)

When `journey_id` is present, AI Gateway builds a Journey-scoped context block before inference:

- `_build_journey_context_block()` reads the Redis hash `{tenant}:ctx:journey:{journey_id}` (the `@ctx.journey.*` namespace shared across all sessions of the Journey) and filters entries with `confidence < 0.3`.
- `_prepend_journey_context()` injects the rendered block into the system message.
- `infer()` calls both helpers only when `req.journey_id` is set — sessions without a Journey are unaffected.

This lets a Business Workflow agent see data collected in `collect` sessions of the same Journey. See [`docs/arcos/arc16-flow-orchestration.md`](arc16-flow-orchestration.md).

`InferenceResponse`:
```python
{
  "role":       "assistant",
  "content":    [...],   # text blocks + tool_use blocks
  "stop_reason": "end_turn" | "tool_use" | "max_tokens",
  "usage":      { "input_tokens": int, "output_tokens": int },
}
```

Usage is forwarded to Kafka `usage.events` as `llm_tokens_input` and `llm_tokens_output` dimensions.

---

## Observabilidade — erro de provedor (2026-07-28)

`ProviderError` → HTTP 502 `upstream_model_error`, com `provider`, `code`, `retryable` e
`detail` (mensagem crua do SDK, incluindo `request_id`) **no corpo E no log**.

Nível pelo `retryable`: **ERROR** quando `False` (saldo zerado, credencial inválida,
modelo inexistente — nenhuma rotação de conta resolve, exige intervenção humana);
**WARNING** quando `True` (429/529, que o `AccountSelector` pode contornar via outra
conta ou provedor).

```
2026-07-28 20:54:35,066 [ERROR] plughub_ai_gateway.main — upstream_model_error
  path=/v1/reason provider=anthropic code=status_400 retryable=False
  detail=Error code: 400 - {... 'Your credit balance is too low ...'}
```

> **Cuidado ao adicionar logs aqui.** O pacote não tinha `logging.basicConfig` até
> 2026-07-28: sem handler, o Python usa o `lastResort`, que imprime só a mensagem (sem
> nível nem timestamp) e **descarta tudo abaixo de WARNING**. Qualquer `logger.info`
> escrito antes disso nunca apareceu. O `basicConfig` vive em `main.py`, no topo — se um
> refactor movê-lo ou removê-lo, os logs voltam a degradar em silêncio.

## Health de credencial — `GET /v1/health` (2026-08-23)

O health reporta o que foi **medido**, nunca o que foi configurado. Até 08-23 ele decidia
`anthropic: "ok"` a partir da *presença* da string da chave (`main.py:431-441`) — nada no
serviço contatava o provedor. Custo: **124 chamadas, 124 `status_401`, 200 verde o tempo
todo**, com o `on_failure` do step `reason` (ramo legítimo) servindo de anestésico.

### Mecanismo

Passivo, com uma sonda no boot. O fato já nascia num funil único — `provider_error_handler`
—, que apenas logava; log não é estado (some no recreate do container).

| Peça | Onde | O quê |
|---|---|---|
| `ProviderError.account_key_id` | `providers/base.py` | qual CONTA falhou — com N contas, "anthropic quebrou" não distingue *uma chave revogada* de *nenhuma funciona* |
| `key_id_for()` | `providers/base.py` | identidade da conta em **fonte única**; se providers e `LLMAccount` divergirem, o desfecho gravado por um não é achado pelo outro |
| `record_outcome()` | `account_selector.py` | `last_ok`/`last_err` **sem TTL** (é estado), contadores **com** janela declarada |
| sucesso | dentro de `record_usage()` | único ponto que roda depois de um `provider.call()` que retornou |
| sonda de boot | `main._probe_credentials_on_boot` | 1 chamada mínima por conta, 1 por start; `PLUGHUB_LLM_BOOT_PROBE` |

Chaves Redis: `ai_gw:{provider}:{key_id}:{last_ok|last_err|ok:{w}|err:{code}:{w}}`.

### Veredicto (o código HTTP faz parte dele — `docker ps` só lê isso)

```
redis inalcançável ..................... unhealthy / 503
nenhuma conta configurada .............. degraded  / 200   escolha declarada
≥1 conta com credencial ok ............. ok        / 200   inválidas seguem em `accounts`
nenhuma ok, ≥1 invalid ................. unhealthy / 503
nenhuma ok, última falha transitória ... degraded  / 200
nenhuma ok, nenhum desfecho ............ unknown   / 200   + nota de que NÃO julga
```

### Invariantes deste endpoint

- **`unknown` nunca vira `ok`.** Ausência de evidência não é saúde. Desligar a sonda de boot
  produz `unknown`, jamais `ok`.
- **`rate_limit`/rede nunca viram `invalid`.** Só `FATAL_CREDENTIAL_CODES` reprova o
  container; reprovar num soluço de rede faz o container piscar e o sinal perde o valor.
- **Chave ausente não reprova; chave presente e recusada reprova.** No segundo caso alguém
  *pretendia* ter LLM.
- **O contador de erro nunca anda sozinho.** `calls_ok` é a testemunha de presença; sem ela
  `errors: {}` é indistinguível de "ninguém chamou". A janela vai junto do número.
  ⚠️ `calls_ok` **subconta**: `record_usage` só roda quando o `AccountSelector` escolheu uma
  CONTA (`inference.py:287`, `reason.py:136`). Chamada que cai no alias legado do provedor
  tem sucesso sem incrementar. O health diz isso quando o contador está em zero.
- **Credencial e disponibilidade são fatos separados.** `credentials: ok` + `throttled: true`
  é estado real e comum: chave válida, conta fora de circulação. Com TODAS throttled o
  `pick()` devolve `None` e tudo cai no alias legado ou no fallback de provedor — o health
  reporta `degraded`, nunca `ok`, porque `ok` ali seria verde sobre capacidade zero.
- **Valor lido do Redis passa por `_as_text()`, nunca por `str()`.** `str(b"…")` devolve
  `"b'…'"` sem levantar: o timestamp da falha vira 0 e a conta recusada aparece como
  `unknown`. O caminho vivo não exerce isso (`decode_responses=True`), o teste sim — a
  fixture é em bytes de propósito.

Gate `infra/test/probe_llm_credential_health.sh` (3 estados; **declara qual metade não
exerceu** — um ambiente exibe `ok` ou `invalid`, nunca os dois) +
`tests/test_account_selector.py::TestCredentialSummary` (8 casos, Redis mockado).

## Medição de sentimento (2026-08-23)

**`sentiment_emitter.py` publica; `sentiment_analyzer.py` mede.** A separação existe porque
o encanamento sempre funcionou e a fonte é que era falsa.

### O que havia antes

| Caminho | Produzia | Por que passava |
|---|---|---|
| `/v1/reason` | `result.get("sentiment_score", 0.0)` — auto-reportado pelo LLM | nenhum skill declara o campo ⇒ sempre `0.0` = NEUTRO = indistinguível de não-medido |
| `/inference` | `context.py:53-64` — contagem de 10 palavras negativas × 8 positivas, em PT | rota sem chamador algum; e heurística não sobrevive a ironia, negação ou outro idioma |

### Contrato

```
skill YAML   reason: { customer_utterance: "$.pipeline_state.pergunta.value" }
   ↓ engine  resolveCustomerUtterance() — aceita $. e @ctx., recusa literal
   ↓ HTTP    ReasonRequest.customer_utterance (+ tenant_id)
   ↓ gateway analyze_and_emit_sentiment() — haiku, fora do turno, fire-and-forget
   ↓         emit_sentiment_updated · update_sentiment_live · write_context_store_sentiment
```

### Invariantes

- **`None` ≠ `0.0`.** `None` é não-medido e pula o pipeline; `0.0` é um ponto legítimo da
  escala (cliente neutro). Confundi-los fez a plataforma inteira parecer medida por meses.
- **Nenhum caminho de falha escreve.** Modelo indisponível, resposta ilegível, tenant vazio,
  sem provider — todos registram o motivo e não gravam nada.
- **Fala literal é recusada.** Só referência. Texto fixo no YAML seria fala fabricada pelo
  autor do fluxo, medida como se fosse do cliente.
- **Não se pede `sentiment_score` no `output_schema`.** Isso põe invariante de plataforma em
  YAML de tenant e troca "a plataforma mede" por "o modelo se autoavalia".
- **O fallback de parse só procura número DEPOIS da chave `sentiment_score`.** Sem a âncora,
  `{"outro": 1}` virava `1.0`.
- **Prompt de sistema viaja como mensagem `role: "system"`.** Não existe kwarg `system` em
  `LLMProvider.call()` — foi esse engano que manteve o `copilot_emitter` mudo (junto com
  `.text`, que não existe em `LLMResponse`).
- **Mock nunca mais permissivo que a coisa mockada.** As fixtures usam `LLMResponse` real; um
  `MagicMock` com `.text` fabricado sob demanda foi o que deixou a suíte concordar com o bug.

Testes: `tests/test_sentiment_analyzer.py` (16 casos) + `tests/test_copilot_emitter.py`.

**Pendente:** nenhum skill declara `customer_utterance` — nada é medido até alguém declarar.

## Invariants

- Erro de provedor nunca é silencioso — motivo vai ao log, não só ao corpo da resposta
- `/v1/health` nunca reporta `ok` sem desfecho de provedor registrado — ausência é `unknown`
- Sentimento ausente é `None` e não é publicado — `0.0` significa cliente neutro, medido
- AI Gateway never maintains state between calls
- AI Gateway never classifies sentiment scores — only emits numeric scores
- `evaluation` profile is never shared with `realtime` account pool
- Tool list is filtered by `permissions[]` before LLM call — AI Gateway never sees tools the caller isn't allowed to use
- `SentimentUpdatedEventSchema` has no `category` field by design

→ See also [`docs/adr/adr-ai-gateway-separation.md`](../adr/adr-ai-gateway-separation.md) for the statelessness architecture decision.
