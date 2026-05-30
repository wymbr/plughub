# Delegate — I/O de Workflow via Agente Especialista

> Criado: 2026-05-30 · Status: **especificado, pendente de implementação**

## Motivação

O modelo Arc 19 trata workflows como canal `webhook` — processos de longa duração sem presença direta do cliente. A primeira implementação do fluxo de portabilidade usou steps `notify` e `collect` diretamente no workflow YAML, o que criou três problemas:

1. **Acoplamento de canal**: o workflow precisa saber como entregar mensagens e coletar respostas do cliente — trabalho do agente, não do workflow.
2. **Mecanismo incompleto**: o step `collect` em modo webhook não escrevia em `resume_tokens`, exigindo workaround manual para retomada.
3. **Fragmentação na analytics**: cada processo gerava 3 sessões independentes sem hierarquia clara, dificultando a visão consolidada do caso do cliente.

---

## Princípio arquitetural — separação de responsabilidades

```
Workflow:  orquestra, suspende, decide, delega. Nunca faz I/O.
Agente:    interage com o cliente (menu, notify, collect). Nunca chama suspend().
```

| Perfil      | Steps permitidos                                                        | Steps proibidos                                         |
|-------------|-------------------------------------------------------------------------|---------------------------------------------------------|
| `workflow`  | task, **delegate**, choice, catch, escalate, complete, invoke, reason, suspend, receive | notify, collect, menu, begin_transaction, end_transaction |
| `agent`     | notify, collect, menu, begin_transaction, end_transaction, invoke, reason | suspend, **delegate**                                   |

A validação é feita no parser do YAML e no engine ao iniciar execução.

---

## Modelo de sessões — topologia estrela

### Princípio

Toda sessão criada em serviço de um contato de cliente tem `origin_session_id` apontando para **a sessão original do cliente** (a raiz). A hierarquia é **plana (estrela)**, não recursiva (cadeia).

```
Session A  (webchat, intake)          ← raiz, sem origin_session_id
  ├── Session B  (webhook, processo)  ← origin_session_id = A
  └── Session C  (webchat, confirmação) ← origin_session_id = A
```

Session B cria Session C via `delegate()`. Ao criar Session C, o step herda e propaga `origin_session_id` da sessão raiz (`@ctx.session.origin_session_id`), não o próprio session_id. Com isso, qualquer profundidade de delegação sempre aponta para a raiz.

```python
# delegate() ao criar sessão-filho
origin_for_child = ctx.origin_session_id or session_id  # raiz ou self se raiz
```

### Por que estrela e não cadeia

| | Cadeia A→B→C | Estrela (A centro) |
|---|---|---|
| Lookup analytics | Recursivo (B→C→...) | Plano (WHERE origin = A) |
| Entrada no Analytics | 3 entradas separadas | 1 entrada por caso |
| WorkflowTraceList | Precisa percorrer cadeia | Um único SELECT |
| Semântica de origin | "quem me criou" | "caso do cliente que originou" |

### Comportamento para workflows sem intake

Workflows acionados por API, scheduled ou sem sessão de origem têm `origin_session_id = NULL`. O WorkflowTraceList não mostra nó de entrada — apenas os segmentos do webhook. Correto e esperado.

### Performance do reverse lookup

```sql
SELECT * FROM analytics.sessions FINAL
WHERE tenant_id = ? AND origin_session_id = ?
ORDER BY started_at
```

`origin_session_id` não está na ORDER BY key da tabela `sessions` (que é `(tenant_id, session_id)`). ClickHouse é columnar — o scan é aceitável dado que o número de filhos por raiz é pequeno (tipicamente 2–3). Se o volume de processos for alto, adicionar projection ou secondary index em `origin_session_id`.

---

## Mecanismo: step `delegate`

O step `delegate` combina `suspend()` com despacho de agente. É o único step onde um workflow cede controle a um agente para I/O com o cliente.

### Sequência de execução

```
WORKFLOW (Session B, webhook)            AGENTE (Session C, canal correto)

delegate()
  → gera resume_token
  → grava em {tenant}:resume_tokens
     campo:  resume_token
     valor:  "{session_B_id}:{step_id}:{expires_at}"
  → cria sessão Session C via routing engine:
       pool: declarado no step
       origin_session_id: @ctx.session.origin_session_id (= Session A)
       context: { workflow_resume_token: resume_token, ...inputs }
  → retorna __suspended__
  → workflow dorme

                                          agente alocado via routing engine
                                          agente faz I/O com o cliente:
                                            notify → menu → collect → ...
                                          agente conclui
                                          invoca workflow_resume:
                                            token:    @ctx.session.workflow_resume_token
                                            decision: "input" | "approved" | "rejected"
                                            payload:  { dados coletados }
                                          → POST /v1/channels/webhook/resume/{token}
                                          agente encerra normalmente (outcome: resolved)

channel-gateway processa resume:
  → lookup resume_token → Session B ID + step_id
  → routing engine aloca instância de portabilidade_processo_ia
  → skill-flow-engine resume: step_id = aguardar_confirmacao, decision = input
  → workflow continua
```

### YAML do workflow

```yaml
- id: aguardar_confirmacao
  type: delegate
  pool: portabilidade_confirmacao          # pool onde o agente será alocado
  context:                                 # passado ao ContextStore de Session C
    numero_atual:    "@ctx.session.numero_atual"
    contact_id:      "@ctx.session.contact_identifier"
  timeout_hours: 24
  on_resume:
    next: confirmar_portabilidade          # decision = input | approved
  on_reject:
    next: confirmacao_rejeitada            # decision = rejected
  on_timeout:
    next: timeout_cliente                  # decision = timeout (fired by engine)
```

### YAML do agente de I/O (Session C)

```yaml
id: skill_agente_confirmacao_portabilidade_v1

steps:
  - id: notificar_aprovado
    type: notify
    message: "✅ Portabilidade do número {{@ctx.session.numero_atual}} foi aprovada! Confirme para prosseguir."
    on_success: coletar_resposta

  - id: coletar_resposta
    type: menu
    prompt: "Deseja confirmar a portabilidade?"
    interaction: button
    options:
      - id: sim
        label: "✅ Sim, confirmar"
      - id: nao
        label: "❌ Cancelar"
    output_as: resposta_cliente
    timeout_s: 86400
    on_success: retornar_ao_workflow
    on_timeout: retornar_timeout

  - id: retornar_ao_workflow
    type: invoke
    tool: workflow_resume
    input:
      decision: "input"
      payload:
        resposta: "$.pipeline_state.resposta_cliente"
    on_success: finalizar

  - id: retornar_timeout
    type: invoke
    tool: workflow_resume
    input:
      decision: "timeout"
    on_success: finalizar

  - id: finalizar
    type: complete
    outcome: resolved
```

O agente obtém o `resume_token` de `@ctx.session.workflow_resume_token` — escrito pelo engine ao criar Session C via `delegate()`.

---

## MCP tool: `workflow_resume`

Nova tool em `mcp-server-plughub/tools/workflow.ts`. Chamada pelo agente ao concluir o I/O.

```typescript
server.tool("workflow_resume", {
  decision: z.enum(["input", "approved", "rejected", "timeout"]),
  payload:  z.record(z.unknown()).optional(),
}, async (input, ctx) => {
  const token = await ctx.redis.hget(
    `${ctx.tenantId}:ctx:${ctx.sessionId}`,
    "session.workflow_resume_token"
  )
  if (!token) return mcpError("token_not_found", "workflow_resume_token missing from context")
  const parsed = JSON.parse(token as string)
  const resume_token = parsed.value ?? token

  await fetch(`${CHANNEL_GATEWAY_URL}/v1/channels/webhook/resume/${resume_token}`, {
    method: "POST",
    body: JSON.stringify({
      tenant_id: ctx.tenantId,
      payload: { decision: input.decision, ...(input.payload ?? {}) },
    }),
    headers: { "Content-Type": "application/json" },
  })
  return ok({ resumed: true, decision: input.decision })
})
```

---

## Impacto em `agent_delegate` — eliminação do polling

O modelo de polling (`agent_delegate` → `agent_delegate_status`) é eliminado para I/O assíncrono. A distinção entre step `task` e step `delegate` é:

| Step       | Duração       | Mecanismo               | Uso                                  |
|------------|---------------|-------------------------|--------------------------------------|
| `task`     | Segundos-min  | polling síncrono        | IA especialista, conferência interna |
| `delegate` | Min-dias      | suspend + resume_token  | I/O com cliente, aprovações externas |

`agent_delegate_status` pode ser removido quando o `task` step migrar para usar `delegate` internamente (fase futura). Por ora, o step `task` continua usando o mecanismo de polling existente para não quebrar YAMLs vigentes.

---

## Analytics/Sessions — visualização unificada

Com a topologia estrela, a WorkflowTraceList de Session A mostra:

```
Session A (webchat, intake)
  STEP TIMELINE: agente portabilidade intake
    coletar_numero    success → coletar_operadora
    coletar_operadora success → coletar_contato
    coletar_contato   success → disparar_workflow
    disparar_workflow success → finalizar (workflow triggered)

  PROCESSOS RELACIONADOS:
    Session B (webhook) — processo assíncrono          [closed · resolved]
      Step timeline: suspend → resumed → delegate → resumed → complete

    Session C (webchat) — confirmação do cliente       [closed · resolved]
      Transcript: mensagem de aprovação + resposta do cliente
```

O endpoint `GET /sessions/{A}/workflow-trace` faz dois queries paralelos:
1. Segmentos de Session A (próprio intake)
2. Reverse lookup: `WHERE origin_session_id = A` → retorna B e C, ordenados por `started_at`

---

## Monitor/Sessions

Session B e C continuam visíveis nos pools respectivos. Com `origin_session_id`, a coluna ORIGIN no Monitor pode exibir o ID de Session A como contexto. Quando `delegate()` estiver em execução em Session B, o bridge pode escrever `session.delegate_active_pool` no ContextStore para que o supervisor_state exponha qual pool está tratando o I/O no momento.

---

## Arquivos a criar/modificar

| Arquivo | Ação |
|---|---|
| `skill-flow-engine/src/steps/delegate.ts` | Criar — executor do step delegate |
| `skill-flow-engine/src/executor.ts` | Adicionar `delegate` ao dispatcher de steps |
| `@plughub/schemas` (`skill-flow.ts`) | Adicionar `DelegateStep` ao `SkillStepSchema` |
| `mcp-server-plughub/src/tools/workflow.ts` | Adicionar MCP tool `workflow_resume` |
| `mcp-server-plughub/src/tools/delegation.ts` | Simplificar `agent_delegate` — remover polling |
| `channel-gateway/adapters/webhook.py` | Garantir que `handle_trigger` aceita `origin_session_id` para sessões-filho de delegate |
| `analytics-api/sessions.py` | Endpoint `workflow-trace`: adicionar reverse lookup para sessões-filho |
| `skill_portabilidade_demo_v1.yaml` | Refatorar — substituir `notify`/`collect` por `delegate` |
| `agente_portabilidade_intake_v1.yaml` | Remover `notify` de início (ou manter por UX) |
| `agente_confirmacao_portabilidade_v1.yaml` | Criar — agente de I/O de confirmação |
| `infra/registry/tenant_demo.yaml` | Adicionar pool `portabilidade_confirmacao` + agente |
