# plughub-rules-engine

Rules Engine da **PlugHub Platform** — monitora conversas IA e aciona escalações reativamente.

## Iniciar

```bash
pip install -e ".[dev]"
PLUGHUB_REDIS_URL=redis://localhost:6379 \
PLUGHUB_MCP_SERVER_URL=http://localhost:3100 \
python -m plughub_rules.main
```

## Como funciona

```
Redis pub/sub: session:updates:*
  → carrega regras ativas do tenant (cache 60s)
  → avalia cada regra contra parâmetros do turno_atual
  → se regra dispara E tem pool_destino:
      status=ativo    → chama conversation_escalate no mcp-server-plughub
      status=shadow   → registra no ClickHouse, não aciona
  → interrompe após primeira regra de maior prioridade que dispara
```

## Configuração de regra

```json
{
  "rule_id":      "rule_churn_retencao",
  "tenant_id":    "tenant_telco",
  "name":         "Churn com Sentiment Baixo",
  "status":       "ativo",
  "condicoes": [
    { "parametro": "sentiment_score", "operador": "lt", "valor": -0.4, "janela_turnos": 3 },
    { "parametro": "intent_confidence", "operador": "lt", "valor": 0.6 }
  ],
  "logica":       "AND",
  "pool_destino": "humano_retencao",
  "prioridade":   1
}
```

## Ciclo de vida de regra (spec 3.2b)

```
rascunho → dry-run → shadow → ativo → desativado
```

Regras nunca vão direto para ativo sem passar por dry-run.

## Sandbox (spec 3.2b)

- **dry-run histórico** — simula regra contra N conversas do ClickHouse
- **shadow mode** — avalia e registra sem acionar
- **diff de regra** — compara duas versões da regra
- **simulador de sessão** — testa com parâmetros manuais

## Testes

```bash
pytest
```
