# @plughub/ai-gateway — AI Gateway

## What it is

Single LLM access point for the PlugHub Platform.
Every component that needs reasoning goes through here.
No component calls the Anthropic SDK directly.

## Responsibilities

1. Agent reasoning loop (`POST /v1/turn`)
2. Intra-turn extraction: sentiment, intent, confidence → Redis
3. Structured output for the `reason` step (`POST /v1/reason`)
4. Routing by model_profile — model switching is config, not code
5. Rate limiting and cost control per tenant

## What it is NOT

- Does not maintain state — stateless, one turn per call
- Does not know the skill flow — only processes the turn it receives
- Does not persist history — uses the session Redis it receives in the input

## Routes

- `POST /v1/turn`   — agent reasoning loop
- `POST /v1/reason` — structured output (Skill Flow reason step)
- `GET  /v1/health` — healthcheck

## Redis session structure (updated on each LLM call)

```json
{
  "consolidated_turns": [
    { "turn": 1, "intent": "portability_check", "confidence": 0.87,
      "sentiment_score": -0.10, "flags": [] }
  ],
  "current_turn": {
    "llm_calls":          [...],
    "partial_parameters": { "intent": "...", "confidence": 0, "sentiment_score": 0 },
    "detected_flags":     []
  }
}
```

## Invariants

- sentiment_score updated on every LLM call — not only at end of turn
- model_profile determines which model to use — never hardcoded
- session_id mandatory in all calls — for trace correlation
- tenant_id inferred from JWT — never from the request body

## Stack

- Python 3.11+
- FastAPI + Uvicorn
- Anthropic SDK
- Pydantic v2
- redis-py

## Spec reference

- 2.2a — AI Gateway responsibilities and structure
- 3.3  — extracted parameters (intent, confidence, sentiment)
- 4.7  — reason step (output_schema, max_format_retries)
