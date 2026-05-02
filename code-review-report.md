# PlugHub Platform — Relatório de Code Review
**Data:** 2026-04-25  
**Escopo:** Revisão arquitetural completa — 7 camadas, 30+ arquivos analisados  
**Metodologia:** Análise estática de código, verificação de contratos, comparação com spec (CLAUDE.md)

---

## Sumário Executivo

| Severidade | Achados | Componentes críticos |
|---|---|---|
| 🔴 Crítico | 7 | Injection Guard, Crash Detector, Bootstrap, Suspend Step, StreamSubscriber, UsageAggregator, Routing Tie-break |
| 🟠 Alto | 9 | JWT, TokenVault, Proxy assimetria, Pool config, Menu cleanup, Backpressure WS, Pricing timezone, RBAC SSE, Health checks |
| 🟡 Médio | 15 | Schemas, testes, timeout handling, logs, CLAUDE.md sync, ClickHouse FINAL, e outros |
| 🟢 Baixo | 1 | Magic bytes validation (phase 2 planejada) |

**Recomendação imediata:** Os 7 achados críticos representam riscos de corretude, segurança e conformidade que devem ser tratados antes de qualquer release de produção.

---

## CAMADA 1 — Schemas e Contratos

### 🟠 C1-01 — Ausência de validação de entrada nas rotas Python (InferenceRequest)

**Evidência:** `packages/ai-gateway/src/plughub_ai_gateway/models.py` (linhas 133–144) define `InferenceRequest` como Pydantic model, mas os esquemas Zod em `packages/schemas/src/` não têm um `InferenceRequestSchema` TypeScript equivalente que seja compartilhado. Upstream TypeScript pode enviar estrutura divergente sem contrato forte.

**Impacto:** Se payload enviado ao AI Gateway não corresponde ao modelo Pydantic, o erro ocorre dentro do serviço (422 Unprocessable Entity) sem feedback claro para o chamador sobre qual campo está errado.

**Sugestão:** Criar `InferenceRequestSchema` em `packages/schemas/src/ai-gateway.ts` e usá-lo como fonte única de verdade, gerando o modelo Pydantic por tooling (ex: `datamodel-code-generator`) ou validando manualmente no router FastAPI com `pydantic.model_validate`.

---

### 🟠 C1-02 — Brecha no refinement de `handoff_reason` em `AgentDoneV2Schema`

**Evidência:** `packages/schemas/src/session.ts` — `handoff_reason` é `.optional()` com refinement condicional. Um agente que envia `outcome: "resolved"` sem `handoff_reason` passa o schema. Se downstream lógica espera `handoff_reason` não-undefined para qualquer outcome, o erro é tardio.

**Impacto:** ZodError em runtime no consumer (Rules Engine, Analytics) em vez de na validação da entrada, dificultando debugging.

**Sugestão:** Tornar o refinement mais explícito com mensagem de erro clara, e adicionar um test case negativo que cubra `outcome !== "resolved"` sem `handoff_reason`.

---

### 🟡 C1-03 — `MenuStepSchema` sem validação de `on_timeout` quando `timeout_s = -1`

**Evidência:** `packages/schemas/src/skill.ts` (linhas 229–263) define `timeout_s: z.number().int().min(-1).default(300)`. Não há refinement que exija `on_timeout` quando `timeout_s === -1`.

**Impacto:** Um skill registrado com `timeout_s: -1` sem `on_timeout` cria um step que bloqueia a sessão indefinidamente se o cliente desconectar — deadlock silencioso.

**Sugestão:**
```typescript
MenuStepSchema.refine(
  (step) => step.timeout_s !== -1 || step.on_timeout !== undefined,
  { message: "timeout_s=-1 requires on_timeout to handle disconnection" }
)
```

---

### 🟡 C1-04 — Testes negativos incompletos em `skill.test.ts`

**Evidência:** Testes focam em happy path. Não há casos negativos para: flow com `entry` referenciando step inexistente, flow sem step terminal (`complete` ou `escalate`), step `collect` com `interaction` inválido.

**Impacto:** Skills inválidas registradas no Agent Registry falham apenas em runtime do Skill Flow Engine, não na CI.

**Sugestão:** Adicionar pelo menos 3 test cases negativos cobrindo estruturas de flow inválidas. Verificar que o ZodError ocorre no ponto de `parse()`, não depois.

---

### 🟡 C1-05 — `CollectStep` sem validação de capacidade de canal do alvo

**Evidência:** `packages/schemas/src/skill.ts` (linhas 296–341) define `channel` como enum estático, mas não valida se o `target` tem capacidade naquele canal. Validação ocorre apenas em runtime no `workflow-api`.

**Impacto:** Erro de configuração descoberto durante execução de workflow, distante do ponto de definição do skill.

**Sugestão:** Documentar explicitamente no schema que channel validation é responsabilidade do `workflow-api` em runtime. Adicionar comentário JSDoc no `CollectStepSchema`.

---

## CAMADA 2 — Segurança

### 🔴 C2-01 — Injection Guard bypass via L33tspeak e Unicode homoglyphs

**Evidência:** `packages/mcp-server-plughub/src/infra/injection_guard.ts` (linhas 39–118). A função interna de stringify não aplica normalização Unicode (NFD/NFKC). Os padrões regex cobrem variantes `ignore`, `disregard`, `forget`, mas não variantes com substituição de caracteres (`ign0re`, `d1sr3g4rd`) ou Unicode homoglyphs (`іgnore` com Cyrillic `і`).

**Impacto:** Ataque de prompt injection via L33tspeak ou Unicode bypassa completamente o guard e chega a domain MCP Servers. Vetor de jailbreak não detectado.

**Sugestão:**
1. Normalizar input antes de aplicar regex: `value.normalize("NFKC")` em `stringify()`
2. Adicionar padrões explícitos para substituições comuns: `/ign[o0]re|d[i1]sr[e3]g[a4]rd|f[o0]rg[e3]t/i`
3. Considerar transliteração de homoglyphs Cyrillic/Greek para Latin antes do regex

---

### 🟠 C2-02 — Assimetria de logging entre McpInterceptor e proxy sidecar

**Evidência:** `packages/sdk/src/mcp-interceptor.ts` (linhas 291–300) loga o conteúdo `matched` do padrão de injeção detectado. `packages/sdk/src/proxy/server.ts` (linhas 62–68) retorna apenas `{ detected, pattern_id }` sem o `matched` string.

**Impacto:** Quando injection é bloqueada pelo proxy sidecar, o audit trail não captura o conteúdo malicioso — impossível análise forense completa. Os dois caminhos de interceptação têm auditoria assimétrica, violando o princípio de equivalência da spec.

**Sugestão:** Sincronizar a interface de retorno da detecção em ambos os caminhos para incluir `matched?: string`. Propagar esse campo até o `AuditRecord`.

---

### 🟠 C2-03 — JWT no channel-gateway: rejeição de `alg:none` não explícita

**Evidência:** `packages/channel-gateway/src/plughub_channel_gateway/adapters/webchat.py` (linhas 337–351). O fluxo faz primeiro decode sem verificação (para extrair `tenant_id`), depois decode com verificação. O segundo decode especifica `algorithms=["HS256"]` mas sem `options={"verify_signature": True}` explícito. PyJWT >= 2.6 rejeita `alg:none` por padrão, mas versões anteriores não.

**Impacto:** Se a dependência `PyJWT` for downgraded por conflito transitivo para < 2.6, tokens sem assinatura (`alg:none`) podem ser aceitos — comprometimento total de autenticação WebChat.

**Sugestão:**
1. Fixar `PyJWT>=2.8.0` no `pyproject.toml` do channel-gateway
2. Adicionar validação explícita: verificar que `pyjwt.get_unverified_header(token)["alg"] == "HS256"` antes de qualquer decode

---

### 🟠 C2-04 — TokenVault: enumeração de tokens via timing attack

**Evidência:** `packages/mcp-server-plughub/src/lib/token-vault.ts` (linhas 110–119). O `resolve()` retorna imediatamente no hit Redis (∼1ms) ou após o miss completo (∼5–10ms). Sem rate-limit ou jitter, um atacante pode enumerar IDs de token via timing diferencial.

**Impacto:** Vazamento de token IDs válidos. Combinado com conhecimento do formato do token (`tk_XXXX`), possível reconstrução de dados mascarados (CPF, cartão, telefone).

**Sugestão:**
1. Adicionar jitter fixo de 5ms a todas as respostas de `resolve()`
2. Implementar rate-limit: máximo 20 tentativas de resolve por minuto por tenant
3. Logar tentativas de resolve com token IDs inválidos (possível ataque)

---

### 🟡 C2-05 — Audit records perdidos quando Kafka está indisponível

**Evidência:** `packages/sdk/src/mcp-interceptor.ts` — o método `_audit()` chama `this.writer.write(record)` sem try/catch. Se o Kafka writer lança exceção (broker down, buffer full), o AuditRecord é silenciosamente descartado.

**Impacto:** Em falha de Kafka, chamadas MCP ficam sem registro de auditoria — violação de conformidade LGPD. A spec define "fire-and-forget" mas não "lose-on-error".

**Sugestão:** Adicionar fallback de auditoria: escrever para `stderr`/log estruturado quando Kafka write falha, com o conteúdo completo do `AuditRecord`. Isso garante rastreabilidade mesmo em falha de infraestrutura.

---

### 🟡 C2-06 — Proxy sidecar: vulnerabilidade a slow-client DoS na acumulação de body

**Evidência:** `packages/sdk/src/proxy/server.ts` (linhas 230–237). Chunks do body são acumulados sem timeout nem limite de tamanho. Um cliente que abre conexão POST e envia dados lentamente (1 byte/segundo) mantém memória alocada indefinidamente.

**Impacto:** Memory leak por slow-client attack. Em ambientes com muitos agentes externos conectados, pode resultar em OOMKill do processo sidecar.

**Sugestão:** Adicionar timeout de 10s para acumulação do body completo, e limite de 10MB no total de chunks acumulados, retornando 408/413 respectivamente.

---

### 🟡 C2-07 — Permissões MCP: mudança silenciosa de permissions[] em reconexão

**Evidência:** `packages/ai-gateway/src/plughub_ai_gateway/inference.py` (linhas 94–105). Se um agente reconecta com token renovado contendo permissions[] reduzidos, a filtragem de tools é aplicada corretamente, mas sem nenhuma auditoria da mudança. O agente pode ter cached a lista antiga de tools.

**Impacto:** Privilege reduction silenciosa. Agente tenta chamar tool que não está mais em permissions[], recebe erro genérico sem contexto claro.

**Sugestão:** Ao detectar que permissions[] mudou entre sessões para o mesmo `instance_id`, publicar evento de auditoria e notificar o agente via mensagem `agents_only` no stream da sessão.

---

## CAMADA 3 — Routing e Orchestration

### 🔴 C3-01 — Tie-breaking de routing não-determinístico (violação da spec)

**Evidência:** `packages/routing-engine/src/plughub_routing/decide.py` (linhas 201–210). Quando múltiplos pools têm score igual, a ordenação usa apenas `sort()` na lista — Python sort é estável (preserva ordem original), mas a ordem original depende da ordem de iteração de estruturas Redis, que não é garantidamente consistente.

**Impacto:** A spec (CLAUDE.md) exige tie-breaking por menor `queue_length`. Sem isso, rotas em situações de alta carga com pools simétricos variam entre execuções. Testes determinísticos passam mas produção diverge. Impossível reproducir bugs de roteamento.

**Sugestão:** Implementar tie-breaking explícito: após sort por score, aplicar sub-sort por `queue_length` (crescente). Como sort em Python não aceita async comparators, computar `queue_length` antes da ordenação e incluí-lo na tupla: `(-score, queue_length, pool_id)`.

---

### 🔴 C3-02 — Crash detector: race condition que causa dupla execução de steps

**Evidência:** `packages/routing-engine/src/plughub_routing/crash_detector.py` (linhas 139–156). O detector re-queues conversas onde não encontra lock `{tenant_id}:pipeline:{conversation_id}:running`. Porém, um agente nativo em `BLPOP` aguardando resultado de menu step NÃO segura o lock durante a espera.

**Cenário concreto:**
1. Agente nativo está bloqueado em `BLPOP menu:result:{session_id}` (sem lock)
2. Crash detector não vê lock → re-publica para `conversations.inbound`
3. Routing Engine aloca a conversa para segundo agente
4. Agente original acorda do BLPOP e continua processando

**Impacto:** Dois agentes ativos na mesma sessão. Duplo envio de mensagens, inconsistência de `pipeline_state`, possível double-billing via usage-emitter.

**Sugestão:** Introduzir session activity flag renovável (`{tenant_id}:session:{session_id}:active_instance:{instance_id}`) com TTL de heartbeat (30s). Agente nativo renova este flag periodicamente mesmo durante BLPOP. Crash detector verifica este flag em vez do (ausente) pipeline lock.

---

### 🔴 C3-03 — InstanceBootstrap: idempotência falha em crash durante Redis write parcial

**Evidência:** `packages/orchestrator-bridge/src/plughub_orchestrator_bridge/instance_bootstrap.py` (linhas 350–420). O loop de criação de instâncias faz writes Redis sequenciais. Se o processo crasha após o write da instância N mas antes da instância N+1, o estado Redis é parcialmente atualizado. Na próxima reconciliação, o algoritmo tenta criar N+1 de novo (correto), mas o `self._registered` dict em memória foi reset (bootstrap recriado após crash), então o desired state é recomputado corretamente.

**Problema real:** O dict `draining=True` de instâncias `busy` não sobrevive ao crash. Na retomada, instâncias que estavam marcadas como `draining` são re-vistas como normais, e o bootstrap pode sobrescrever a instância Redis com status `ready` — interrompendo uma sessão em andamento.

**Impacto:** Instâncias ocupadas podem ter seu estado Redis sobrescrito durante reconciliação pós-crash. Sessões ativas perdem o agente.

**Sugestão:** Antes de sobrescrever qualquer instância existente no Redis, verificar o status atual: se `status == "busy"`, nunca sobrescrever com `ready` — apenas aplicar `pending_update=True`.

---

### 🟠 C3-04 — `pool_config_diverged()` ignora campos críticos de roteamento

**Evidência:** `packages/orchestrator-bridge/src/plughub_orchestrator_bridge/instance_bootstrap.py` (linhas 891–909). O conjunto `MANAGED` de campos validados não inclui: `routing_expression`, `competency_weights`, `aging_factor`, `breach_factor`, `remote_sites`.

**Impacto:** Operador muda `routing_expression` no Agent Registry. A pool config no Redis não é atualizada (divergência não detectada). Routing Engine continua usando pesos de score antigos indefinidamente até o próximo TTL expiration (24h).

**Sugestão:** Expandir o conjunto `MANAGED` para incluir todos os campos que alimentam `scorer.py` e `decide.py`.

---

### 🟡 C3-05 — TTL de `pool_config` excessivamente alto (24h)

**Evidência:** `packages/orchestrator-bridge/src/plughub_orchestrator_bridge/instance_bootstrap.py`, constante `_POOL_CONFIG_TTL_S = 86400`.

**Impacto:** Pool removido do Registry persiste visível no Redis (e no dashboard operacional) por até 24 horas. Afeta relatórios e alarmes operacionais.

**Sugestão:** Reduzir para 3600s (1h). Suficiente para crash recovery, curto o suficiente para cleanup operacional.

---

## CAMADA 4 — Skill Flow Engine

### 🔴 C4-01 — `suspend.ts`: idempotência falha se SaveState falhar entre as duas fases

**Evidência:** `packages/skill-flow-engine/src/steps/suspend.ts` (linhas 115–124 e 177–182). O protocolo de dois estágios usa `sentinel = "suspending"` (SAVE #1) → `persistSuspend()` → `sentinel = "suspended"` (SAVE #2). Se SAVE #2 falha (rede, Redis timeout), o estado persiste com `sentinel = "suspending"`.

**Cenário na retomada:**
1. Engine retoma com sentinel != "suspended" → não entra no caminho idempotente
2. Gera novo `resumeToken` (linha 113)
3. Chama `persistSuspend()` novamente com token diferente
4. Dois registros de suspend no workflow-api para a mesma instância
5. Ambos os tokens são válidos → comportamento undefined no resume

**Impacto:** Duplicate suspend instances no banco. Resume com token antigo pode falhar com "already resumed". Workflow em estado inconsistente.

**Sugestão:** Separar o token de sua geração: salvar o `resumeToken` no pipeline_state ANTES de chamar persistSuspend. Na retomada, se sentinel == "suspending" E token existe no state, reusar o token existente para chamar persistSuspend (que deve ser idempotente por token).

---

### 🟠 C4-02 — `menu.ts`: falha silenciosa no cleanup do waitingKey Redis

**Evidência:** `packages/skill-flow-engine/src/steps/menu.ts` (linhas 65–72 e 134–141). Tanto o `set(waitingKey)` quanto o `del(waitingKey)` estão em blocos `try/catch` com `// Non-fatal` comments e sem logging.

**Impacto:** Se `set(waitingKey)` falha, a sessão fica bloqueada no BLPOP mas o orchestrator-bridge não sabe que está esperando — mensagens de agentes humanos podem não ser roteadas corretamente para a sessão. Se `del(waitingKey)` falha, a flag persiste com TTL, marcando sessões finalizadas como "em espera".

**Sugestão:** Logar falhas com `logger.warning` no mínimo. Avaliar se `set(waitingKey)` falhar deve ser erro de passo (passo não pode garantir entrega de resposta).

---

### 🟡 C4-03 — `reason.ts`: sem timeout explícito no `aiGatewayCall`, retry sem backoff

**Evidência:** `packages/skill-flow-engine/src/steps/reason.ts` (linhas 23–63). O retry loop não inclui delay entre tentativas, nem timeout para a chamada HTTP ao AI Gateway.

**Impacto:** Em sobrecarga do AI Gateway, retries imediatos pioram a situação (thundering herd). Sessão pode ficar bloqueada por múltiplos segundos enquanto retries acumulam.

**Sugestão:** Adicionar backoff exponencial (100ms, 300ms, 900ms) e timeout configurable (padrão 30s) ao `aiGatewayCall`.

---

### 🟡 C4-04 — `collect.ts`: sem validação antecipada de campos do step

**Evidência:** `packages/skill-flow-engine/src/steps/collect.ts` (linhas 32–78). O step não valida `step.interaction`, `step.channel`, `step.target` antes de gerar o `collect_token` e chamar `persistCollect`.

**Impacto:** Se o flow YAML tem erro de digitação em `interaction` (ex: `"invalido"`), o `collect_token` é gerado e persistido, mas `persistCollect` rejeita o payload → instância collect órfã no banco.

**Sugestão:** Validar campos obrigatórios antes de gerar o token. Lançar `StepError` com mensagem clara se campos forem inválidos.

---

## CAMADA 5 — Channel Gateway

### 🔴 C5-01 — StreamSubscriber: race condition entre EXISTS e XREAD na reconexão

**Evidência:** `packages/channel-gateway/src/plughub_channel_gateway/stream_subscriber.py` (linhas 100–110). Na reconexão com cursor, o código faz `EXISTS session:{id}:stream` e, em caso de exceção Redis, assume que o stream existe (`exists = 1`). Entre o EXISTS e o primeiro XREAD, o stream pode expirar.

**Impacto:** Em falha parcial de Redis (network partition), o loop de delivery fica bloqueado em XREAD com timeout de 2s repetidos por até 20–40s antes de fechar, mantendo a conexão WebSocket ativa sem entregar mensagens.

**Sugestão:** Eliminar o check `EXISTS` separado. Tentar `XREAD` diretamente com timeout curto (500ms). Tratar erro `nil` do XREAD como `StreamExpiredError`. Isso elimina a race condition e simplifica o fluxo.

---

### 🟠 C5-02 — WebchatAdapter: backpressure descontrolado nas três tasks concorrentes

**Evidência:** `packages/channel-gateway/src/plughub_channel_gateway/adapters/webchat.py` (linhas 424–446). `_stream_delivery_loop` itera sobre `subscriber.messages()` e chama `send_json` diretamente. Sem buffer limitado entre o subscriber e o send, a fila de mensagens pendentes cresce sem limite se o cliente lê lentamente.

**Impacto:** Memory leak silencioso em sessões longas com clientes lentos (ex: browser em background). Em containers com limite de memória, pode causar OOMKill do channel-gateway, afetando todas as sessões ativas.

**Sugestão:** Introduzir `asyncio.Queue(maxsize=200)` entre `subscriber.messages()` e `send_json`. Se a fila encher, fechar a conexão WebSocket com código 1009 (message too big / going away) e logar a sessão.

---

### 🟡 C5-03 — Upload: magic bytes validation ausente (phase 2 pendente)

**Evidência:** `packages/channel-gateway/src/plughub_channel_gateway/attachment_store.py` (linhas 41–42), `upload_router.py` (linhas 22–24). Comentário `# TODO: fase 2 — validar também por magic bytes`.

**Impacto:** Cliente envia conteúdo malicioso com MIME type válido (ex: executável disfarçado de PDF). Arquivo é salvo e servido com Content-Type correto para o receptor.

**Sugestão:** Priorizar como item de segurança, não apenas "feature phase 2". Biblioteca `filetype` (PyPI) valida magic bytes em < 5 linhas. Bloquear upload se magic bytes não correspondem ao MIME type declarado.

---

### 🟡 C5-04 — Fallback textual de menu para canais não-suportados: não implementado no channel-gateway

**Evidência:** CLAUDE.md especifica que fallback (menu → texto numerado) ocorre exclusivamente no Channel Gateway. O `stream_subscriber.py` passa `interaction_request` direto do stream para o WebSocket sem normalização de canal.

**Impacto:** Quando adapters de SMS/Email forem implementados, o fallback não estará pronto — receberão menus estruturados que não conseguem renderizar. Sessões travadas.

**Sugestão:** Criar interface `ChannelAdapter.supports_interaction(type: str) -> bool` e implementar `render_fallback(menu_step) -> str` antes de implementar qualquer adapter de canal não-gráfico.

---

## CAMADA 6 — Serviços de Suporte

### 🔴 C6-01 — UsageAggregator: pipeline Redis não é transação atômica

**Evidência:** `packages/usage-aggregator/src/plughub_usage_aggregator/aggregator.py` (linhas 55–61). O código usa `redis.pipeline()` com `INCRBY`, `EXPIRE`, e `SET NX`. Redis pipeline não é MULTI/EXEC — commands são enviados em batch mas executados independentemente.

**Impacto:** Se dois workers Kafka consomem o mesmo evento simultaneamente (ex: redelivery por crash), ambos fazem `INCRBY` no mesmo key — double count. A deduplicação por `event_id` existe no PostgreSQL (PRIMARY KEY), mas o counter Redis fica inconsistente com o banco.

**Sugestão:** Usar `MULTI/EXEC` em vez de pipeline para garantir atomicidade. Ou: aceitar a inconsistência Redis e usar apenas PostgreSQL como fonte de verdade para counters, invalidando o cache Redis após INSERT.

---

### 🟠 C6-02 — PricingCalculator: ambiguidade de timezone em billing de reserve pools

**Evidência:** `packages/pricing-api/src/plughub_pricing_api/calculator.py` (linhas 225–227). `count_active_days` consulta `reserve_activation_log.activation_date` (coluna tipo `DATE`, sem timezone). A timezone efetiva depende do servidor PostgreSQL.

**Impacto:** Em deploy em fuso UTC-3 (Brasil), pool ativado às 22:00 horário local em dia D aparece como dia D+1 em UTC. Faturamento errado de ±1 dia por ativação de reserve pool.

**Sugestão:** Armazenar `activation_date` como `DATE` baseado em UTC explicitamente. Documentar no schema SQL: `-- All dates stored in UTC`. Converter ao inserir: `activation_date = datetime.now(timezone.utc).date()`.

---

### 🟠 C6-03 — Analytics dashboard: RBAC ausente nos endpoints SSE

**Evidência:** `packages/analytics-api/src/plughub_analytics_api/dashboard.py` (linhas 50–94). O endpoint `/dashboard/operational` aceita `tenant_id` como query param sem qualquer verificação de autenticação. Compare com `/admin/consolidated` que usa `Depends(require_principal)`.

**Impacto:** Qualquer cliente não autenticado pode consultar pool snapshots de qualquer tenant via SSE — exfiltração de dados operacionais. Violação de isolamento multi-tenant.

**Sugestão:** Adicionar `principal: Principal = Depends(require_principal)` a todos os endpoints de `/dashboard/` e aplicar `principal.effective_tenant(tenant_id)` para filtrar por tenant.

---

### 🟡 C6-04 — Workflow timeout_scanner: potencial double-processing com múltiplas réplicas

**Evidência:** `packages/workflow-api/src/plughub_workflow_api/timeout_job.py` (linhas 365–379). O UPDATE PostgreSQL é atômico (serializado por row lock), protegendo contra processamento duplicado. Porém, se duas réplicas rodam o scanner simultaneamente, ambas publicam `workflow.timed_out` para diferentes batches — e ambas escrevem em `collect_instances` expiradas também.

**Impacto:** Baixa probabilidade, mas Kafka recebe eventos duplicados → log spam, overhead de processing no worker.

**Sugestão:** Adicionar distributed lock Redis (`SET NX EX 70`) no início de `_scan_once()`. Se lock não obtido, a réplica pula o ciclo silenciosamente.

---

### 🟡 C6-05 — ClickHouse: endpoints de relatório sem `FINAL` para deduplicação forçada

**Evidência:** CLAUDE.md seção Analytics documenta uso de `ReplacingMergeTree` para idempotência. A deduplicação é lazy (background merge). Endpoints de relatório que não usam `SELECT ... FINAL` podem retornar dados duplicados.

**Impacto:** Relatórios exportados (CSV, XLSX) podem contabilizar a mesma sessão/evento múltiplas vezes durante períodos de alta ingestão. Erros de reporte descobertos por clientes.

**Sugestão:** Auditoria de todos os queries em `reports_query.py` e `query.py` — verificar que queries de sessões/eventos usam `FINAL`. Aceitar a penalidade de 10–20% de latência para consistência.

---

## CAMADA 7 — Manutenibilidade e Observabilidade

### 🟠 C7-01 — `docker-compose.demo.yml`: serviços Kafka consumers sem health checks

**Evidência:** `docker-compose.demo.yml` — serviços `routing-engine`, `session-replayer`, `usage-aggregator`, `skill-flow-worker` não têm `healthcheck` definido. Usam `restart: on-failure` mas sem `depends_on: condition: service_healthy`.

**Impacto:** Em startup da demo/E2E, o `e2e-runner` inicia antes dos consumers Kafka estarem prontos. Primeiros eventos publicados são perdidos — testes E2E ficam flaky (falham intermitentemente).

**Sugestão:** Adicionar health checks baseados em endpoint HTTP `/health` (FastAPI) ou verificação de connectivity Kafka (consumer group lag = 0 para tópico de controle). Wired com `depends_on: condition: service_healthy` nos serviços downstream.

---

### 🟡 C7-02 — Cobertura de testes: ausência de integração para fluxos críticos

**Achados transversais:**
- `test_attachment_store.py` (30 testes) usa mocks para asyncpg e filesystem — não cobre upload real end-to-end
- `test_calculator.py` (23 testes) não cobre cenários de timezone
- Não há cenário E2E para: usage event → Kafka → Redis counter → PostgreSQL (consistência entre os dois stores)
- Skill Flow worker não tem testes automatizados (pasta `tests/` vazia ou ausente)

**Impacto:** Bugs de integração descobertos em produção. Race conditions e bugs de timezone invisíveis em testes unitários com mocks.

**Sugestão:** Adicionar ao menos 3 cenários E2E críticos: scenario 17 (pricing full cycle), scenario 18 (usage metering end-to-end), scenario 19 (webchat upload real). Infraestrutura E2E já existe — custo marginal baixo.

---

### 🟡 C7-03 — Logs não estruturados: session_id não rastreável em produção

**Evidência transversal:** Vários serviços Python usam `logging.getLogger()` com format string padrão. Não há uso sistemático de `structlog` ou `python-json-logger`. Exemplo em `stream_subscriber.py` (linha 202): `logger.error("stream error: %s", err)` sem `session_id` no contexto.

**Impacto:** Troubleshooting de bugs em sessão específica requer grep manual em logs não-estruturados. Em ambientes com múltiplos tenants e sessões paralelas, logs são indiferenciáveis.

**Sugestão:** Adotar `structlog` ou `python-json-logger` como padrão. Criar um `LogContext` helper que injeta automaticamente `session_id`, `tenant_id`, `instance_id` em todos os logs de uma coroutine. Padronizar schema: `{"ts": "...", "level": "...", "session_id": "...", "tenant_id": "...", "msg": "..."}`.

---

### 🟡 C7-04 — CLAUDE.md: 2 seções desincronizadas com a implementação

**Desincronias identificadas:**

1. **Arc 4 — Skill Flow worker com rotas HTTP reais**: marcado como ✅ completo. Não foram encontrados testes automatizados para `mcpCall` e `aiGatewayCall` em `packages/skill-flow-worker/`. Verificar se funcionalidade foi realmente testada ou apenas implementada.

2. **Arc 3 — Dashboard SSE**: endpoint `/dashboard/operational` listado como ✅, mas conforme C6-03, está sem RBAC. A spec original provavelmente previa autenticação. O ✅ pode ser prematuro.

**Impacto:** Desenvolvedor consulta CLAUDE.md para entender o estado do sistema, integra código dependente de feature "completa", descobre em QA que está incompleta ou insegura.

**Sugestão:** Fazer auditoria de todos os ✅ nas seções Arc 3 e Arc 4. Criar distinção entre "código existe" e "feature completa e segura".

---

### 🟡 C7-05 — Dependências circulares: ausência de verificação automatizada no CI

**Evidência:** O CLAUDE.md define hierarquia estrita de dependências entre pacotes. Não foi encontrado script de CI que valide esse grafo automaticamente (ex: `npm ls` ou verificação de `tsconfig.json` paths vs. dependências permitidas).

**Impacto:** À medida que o projeto cresce (Arc 4, Arc 5), ciclos de dependência podem ser introduzidos acidentalmente. O erro se manifesta em erros de build ou runtime difíceis de rastrear.

**Sugestão:** Adicionar ao pipeline CI um step que valida o grafo de dependências contra a hierarquia definida no CLAUDE.md. Ferramentas: `madge` (TypeScript), `pipdeptree --warn` (Python).

---

## Ranking de Prioridade para Correção

### Sprint imediata — Críticos (🔴)

| ID | Componente | Risco principal |
|---|---|---|
| C2-01 | Injection Guard (unicode bypass) | Jailbreak de prompt não detectado |
| C3-02 | Crash Detector (race condition) | Dupla execução de agent steps |
| C4-01 | Suspend step (SaveState parcial) | Duplicate suspend instances |
| C6-01 | UsageAggregator (pipeline não-atômico) | Double count em usage/billing |
| C3-01 | Routing tie-break | Roteamento não-determinístico |
| C3-03 | InstanceBootstrap (crash parcial) | Sessões perdendo agentes |
| C5-01 | StreamSubscriber (EXISTS race) | WebSocket pendurado em reconexão |

### Próximo sprint — Altos (🟠)

| ID | Componente | Risco principal |
|---|---|---|
| C2-03 | JWT alg:none | Auth bypass se PyJWT downgrade |
| C2-04 | TokenVault timing | Enumeração de tokens mascarados |
| C3-04 | Pool config divergence | Configs de roteamento obsoletas |
| C4-02 | Menu cleanup flag | False waiting state |
| C5-02 | WebSocket backpressure | OOMKill em carga |
| C6-02 | Pricing timezone | Faturamento ±1 dia |
| C6-03 | RBAC em SSE | Exfiltração de dados operacionais |
| C7-01 | Health checks demo | Testes E2E flaky |

### Backlog técnico — Médios (🟡)

C1-03, C1-04, C1-05, C2-05, C2-06, C2-07, C3-05, C4-03, C4-04, C5-03, C5-04, C6-04, C6-05, C7-02, C7-03, C7-04, C7-05

---

*Relatório gerado via análise estática do codebase em 2026-04-25. Achados baseados em leitura de código — não cobre bugs descobríveis apenas por execução dinâmica.*
