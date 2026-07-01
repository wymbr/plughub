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

## Invariants

- AI Gateway never maintains state between calls
- AI Gateway never classifies sentiment scores — only emits numeric scores
- `evaluation` profile is never shared with `realtime` account pool
- Tool list is filtered by `permissions[]` before LLM call — AI Gateway never sees tools the caller isn't allowed to use
- `SentimentUpdatedEventSchema` has no `category` field by design

→ See also [`docs/adr/adr-ai-gateway-separation.md`](../adr/adr-ai-gateway-separation.md) for the statelessness architecture decision.
