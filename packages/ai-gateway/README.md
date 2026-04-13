# plughub-ai-gateway

AI Gateway da **PlugHub Platform** — ponto único de acesso a LLMs.

## Iniciar

```bash
pip install -e ".[dev]"
PLUGHUB_ANTHROPIC_API_KEY=sk-ant-... uvicorn plughub_ai_gateway.main:app --port 3200
```

## Rotas

| Rota | Descrição |
|---|---|
| `POST /v1/turn` | Loop de raciocínio do agente |
| `POST /v1/reason` | Structured output para step reason |
| `GET /v1/health` | Healthcheck |

## Variáveis de ambiente

```
PLUGHUB_ANTHROPIC_API_KEY  ← obrigatório
PLUGHUB_REDIS_URL          ← default: redis://localhost:6379
PLUGHUB_MODEL_FAST         ← default: claude-haiku-4-5-20251001
PLUGHUB_MODEL_BALANCED     ← default: claude-sonnet-4-6
PLUGHUB_MODEL_POWERFUL     ← default: claude-opus-4-6
PLUGHUB_PORT               ← default: 3200
PLUGHUB_WORKERS            ← default: 4
```

## Testes

```bash
pytest
```

## Spec de referência

- 2.2a — responsabilidades e estrutura do AI Gateway
- 4.7  — step reason (output_schema, max_format_retries)
