# ADR: Instance Bootstrap — Controlador de Reconciliação de Instâncias e Pools

**Status:** Implementado  
**Data:** 2026-04-24  
**Componentes:** `packages/orchestrator-bridge`, `packages/config-api`, `packages/agent-registry`

---

## Contexto

O PlugHub precisa que instâncias de agentes existam no Redis antes que o Routing Engine
possa alocar sessões. A solução original era um script de seed que escrevia as chaves
diretamente na inicialização. Essa abordagem criava três problemas operacionais:

1. **Fragilidade ante mudanças de configuração** — qualquer alteração (novo agente, pool
   diferente, limite de sessões) exigia reinicialização manual ou re-execução do seed.
2. **Divergência silenciosa** — se uma chave expirava por gap de TTL ou era corrompida,
   o Routing Engine simplesmente não encontrava instâncias disponíveis, sem auto-correção.
3. **Responsabilidade mal distribuída** — o seed escrevia tanto no PostgreSQL (Agent Registry)
   quanto diretamente no Redis, criando dois pontos de verdade concorrentes para o mesmo estado.

O modelo de billing do PlugHub cobra por capacidade configurada, não por consumo. Isso
amplifica o problema: a instância no Redis não é só cache operacional — representa um
recurso faturável que precisa refletir fielmente o que foi contratado no Agent Registry.

---

## Decisão

### Padrão de reconciliação contínua (Kubernetes-style)

O Bootstrap deixou de ser um script de inicialização e passou a ser um **controlador de
reconciliação** que opera em loop contínuo: compara o estado desejado (Agent Registry,
PostgreSQL) com o estado atual (Redis) e aplica o diff mínimo para convergir os dois.

```
reconcile(tenant_id):

  [A] Instâncias de agentes
      desired = build_desired_state(GET /v1/agent-types + GET /v1/pools)
      actual  = SCAN {tenant}:instance:* no Redis

      diff:
        to_create  → escreve instância + SADD nos pool SETs
        to_delete  → status=ready  → DELETE + SREM
                     status=busy   → draining=True (heartbeat remove depois)
        to_update  → status=ready  → atualiza payload
                     status=busy   → pending_update=True (heartbeat aplica depois)
        to_renew   → EXPIRE apenas (payload idêntico)

  [B] Pool configs
      para cada pool ativo no Registry:
        pool_config ausente ou divergente → SET pool_config:{pool_id}
        pool_config idêntico             → EXPIRE apenas (renova TTL)
      para cada pool_config:* no Redis não presente no Registry:
        → DELETE pool_config:{pool_id}
        → se pool:{pool_id}:instances SET vazio → DELETE também

  [C] SET global {tenant}:pools
      adiciona IDs novos, remove IDs obsoletos
```

A propriedade central é a **idempotência**: reconciliar N vezes produz o mesmo resultado
que reconciliar uma. Isso permite que o controlador rode periodicamente sem efeitos colaterais
acumulativos.

### Três velocidades de operação

| Velocidade | Intervalo | O que faz | I/O |
|---|---|---|---|
| Heartbeat leve | 15s | Renova TTL de todas as instâncias em memória | Só Redis |
| Reconciliação sob demanda | imediato | Responde a `registry.changed` ou `config.changed` | Registry + Redis |
| Reconciliação periódica | 5 min | Auto-healing de qualquer drift acumulado | Registry + Redis |

O heartbeat leve é O(instâncias) e não faz chamadas HTTP. A reconciliação completa faz
chamadas ao Registry mas é controlada por frequência para não sobrecarregar.

### Tratamento seguro de instâncias em sessão ativa

Forçar a deleção de uma instância com status `busy` causaria perda de contexto de sessão
em andamento. O controlador nunca faz isso. Em vez disso:

- **draining=True**: instância que deve ser removida mas está ocupada. O heartbeat verifica
  periodicamente; quando o status volta a `ready`, remove com segurança.
- **pending_update=True**: instância cuja configuração mudou mas está em uso. O heartbeat
  aplica o update assim que a sessão encerrar.

Nenhuma sessão é interrompida. Nenhuma configuração fica desatualizada por mais de um
ciclo de heartbeat após o fim da sessão.

### Agent Registry como única fonte de verdade

O seed.py deixou de escrever qualquer coisa no Redis. Sua única responsabilidade agora
é registrar pools e agent types na API REST do Agent Registry (PostgreSQL). Tudo que
existe no Redis é derivado e gerenciado exclusivamente pelo Bootstrap.

### Dry-run para auditoria operacional

```python
report = await bootstrap.dry_run("tenant_demo")
print(report.summary())
# [DRY-RUN] tenant=tenant_demo created=2 deleted=1 drained=0 updated=1
#           renewed=7 unchanged=0 pools_written=1 pools_removed=0
#           pools_set=0 errors=0 (43ms)
```

Quando um módulo precisa ser auditável antes de ser aplicado, saiu do regime de
"script utilitário" e entrou no regime de "infraestrutura crítica".

---

## Triggers de reconciliação

| Trigger | Origem | Ação |
|---|---|---|
| Startup do Bridge | interno | `reconcile()` — full diff |
| `registry.changed` (Kafka) | Agent Registry | `reconcile()` imediato |
| `config.changed` namespace=`quota` (Kafka) | Config API | `reconcile()` imediato |
| Heartbeat periódico (5 min) | interno | `reconcile()` — auto-healing |
| Heartbeat leve (15s) | interno | `_heartbeat_tick()` — só TTL |

O tópico `config.changed` foi adicionado para fechar o gap entre mudanças de parâmetros
operacionais (como `quota.max_concurrent_sessions`) e a convergência do Redis. O roteamento
por namespace garante que apenas alterações relevantes disparam reconciliação: namespaces
de runtime (`routing`, `masking`, `session`, etc.) propagam naturalmente via cache Redis
com TTL de 60s, sem coordenação central.

---

## Consequências

### Positivas

**Operação sem reinicialização** — qualquer mudança de configuração (novo agente, escala
de slots, adição/remoção de pool) converge para o Redis em até 15s sem intervenção humana.

**Consistência billing** — a divergência entre o que o Registry declara (faturável) e o
que o Routing Engine vê (operacional) tem vida máxima de 5 minutos por design. O auto-healing
periódico elimina a possibilidade de drift silencioso de longo prazo.

**Resiliência a falhas parciais** — se o Redis perde uma chave por restart ou expiry
inesperado, o heartbeat a restaura no próximo tick (15s) sem qualquer alarme manual.

**Separação limpa de responsabilidades** — o seed.py faz apenas registro declarativo.
O Bootstrap faz apenas convergência de estado. O Routing Engine consome o estado sem
precisar saber como ele foi gerado.

### Restrições

**Human agents fora do escopo** — o login de agentes humanos é iniciado pelo usuário via
Agent Assist UI e segue o contrato `agent_login → agent_ready`. O Bootstrap não toca
instâncias humanas.

**Dependência de disponibilidade do Registry** — se o Agent Registry estiver indisponível,
a reconciliação é pulada e logada como aviso. O heartbeat leve continua operando para
manter o TTL das instâncias existentes, evitando que o Redis esvazie durante uma falha
temporária do Registry.

**Consistência eventual, não imediata** — entre o momento em que uma mudança é feita no
Registry e o momento em que ela reflete no Redis, pode haver um gap de até 15s (heartbeat).
Em troca, o sistema não tem nenhum ponto de sincronização síncrona no hot path de roteamento.

---

## Alternativas consideradas

**Invalidação síncrona via webhook** — o Registry chamaria diretamente o Bridge ao detectar
uma mudança. Descartado: cria acoplamento forte entre dois serviços que hoje são independentes
e introduz latência no hot path de escrita do Registry.

**Redis Keyspace Notifications** — o Routing Engine monitoraria expirações e recriaria chaves.
Descartado: coloca lógica de bootstrap fora do Bootstrap, viola o princípio de separação
de responsabilidades, e não resolve o problema de mudança de configuração.

**Seed re-executável como cron** — rodar o script de seed periodicamente.
Descartado: não resolve instâncias busy, não tem dry-run, não produz relatório estruturado,
e mantém o Redis como segundo ponto de escrita fora do controle do Registry.

---

## Arquivos relevantes

| Arquivo | Responsabilidade |
|---|---|
| `packages/orchestrator-bridge/src/plughub_orchestrator_bridge/instance_bootstrap.py` | Controller principal — reconciliação, heartbeat, dry-run |
| `packages/orchestrator-bridge/src/plughub_orchestrator_bridge/main.py` | Startup, consumer Kafka, dispatch `registry.changed` e `config.changed` |
| `packages/config-api/src/plughub_config_api/kafka_emitter.py` | Publicação de `config.changed` após PUT/DELETE |
| `packages/e2e-tests/scenarios/15_instance_bootstrap.ts` | Scenario E2E: valida instâncias, pool SETs e pool_config keys no Redis |
| `infra/seed/seed.py` | Apenas registro no Agent Registry (PostgreSQL) — sem escrita Redis |
