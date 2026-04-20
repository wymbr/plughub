# plughub-routing-engine

Árbitro único de alocação da **PlugHub Platform**.
Toda conversa passa por aqui — nenhum componente roteia sem o Routing Engine.

## Iniciar

```bash
pip install -e ".[dev]"
PLUGHUB_KAFKA_BROKERS=localhost:9092 \
PLUGHUB_REDIS_URL=redis://localhost:6379 \
PLUGHUB_AGENT_REGISTRY_URL=http://localhost:3300 \
python -m plughub_routing.main
```

## Fluxo

```
Kafka: conversations.inbound
  → identifica pools candidatos (canal + tenant)
  → busca instâncias agent_ready no Redis
  → calcula priority_score por instância
  → aloca a instância com maior score (timeout: 150ms)
  → garante afinidade de sessão para stateful
Kafka: conversations.routed  (alocado)
Kafka: conversations.queued  (sem agente disponível)
```

## priority_score (spec 3.3)

```
score = (peso_sla × sla_urgency) + (peso_espera × tempo_espera_norm)
      + (peso_tier × tier_score) + (peso_churn × churn_risk)
      + (peso_negocio × business_score)

sla_urgency = elapsed_ms / sla_target_ms
```

Pesos configurados por pool no `routing_expression` do Agent Registry.

## Testes

```bash
pytest
```

## Spec de referência

- 3.3  — dimensões de roteamento e priority_score
- 3.3a — comportamento com pools saturados
- 4.5  — ciclo de vida de instância
