# AI Gateway — LLM Inference & Multi-Account Rotation

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
}
```

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
