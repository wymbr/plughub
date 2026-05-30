# Delegate — I/O de Workflow via Agente Especialista

> Criado: 2026-05-30 · Status: **especificado, pendente de implementação**

## Motivação

O modelo Arc 19 trata workflows como canal `webhook` — processos de longa duração sem presença direta do cliente. A primeira implementação do fluxo de portabilidade usou steps `notify` e `collect` diretamente no workflow YAML, o que criou dois problemas:

1. **Acoplamento de canal**: o workflow precisa saber como entregar mensagens e coletar respostas do cliente — trabalho do agente, não do workflow.
2. **Mecanismo incompleto**: o step `collect` em modo webhook não escrevia em `resume_tokens`, exigindo workaround manual para retomada.

## Princípio arquitetural estabelecido

```
Workflow:  orquestra, suspende, decide, delega. Nunca faz I/O.
Agente:    interage com o cliente (menu, notify, collect). Nunca chama suspend().
```

| Perfil   | Steps permitidos                                                    | Steps proibidos                            |
|----------|---------------------------------------------------------------------|--------------------------------------------|
| workflow | task, delegate, choice, catch, escalate, complete, invoke, reason, suspend, receive | notify, collect, menu, begin_transaction, end_transaction |
| agent    | notify, collect, menu, begin_transaction, end_transaction, invoke, reason | suspend, delegate                          |

## Mecanismo: step `delegate`

O step `delegate` combina `suspend()` com despacho de agente. É o único ponto onde um workflow interrompe sua execução e cede o controle a um agente para fazer I/O.

### Sequência de execução

```
WORKFLOW (webhook session)          AGENTE (nova sessão no canal correto)

delegate()
  → gera resume_token
  → grava em {tenant}:resume_tokens
  → escreve session.workflow_resume_token no ContextStore da sessão-filho
  → despacha agente via routing engine (pool declarado no step)
  → retorna __suspended__
  → workflow dorme

                                    agente alocado pelo routing engine
                                    agente faz I/O com o cliente:
                                      notify → menu → collect → ...
                                    agente conclui
                                    invoca workflow_resume:
                                      decision: "input" | "approved" | "rejected" | "timeout"
                                      payload:  { dados coletados }
                                    → POST /v1/channels/webhook/resume/{resume_token}
                                    agente encerra normalmente

channel-gateway processa resume
  → routing engine aloca nova instância do webhook pool
  → skill-flow-engine retoma do step seguinte ao delegate
  → workflow continua com payload do agente
```

### YAML do workflow

```yaml
- id: aguardar_confirmacao
  type: delegate
  pool: portabilidade_confirmacao       # pool onde o agente será alocado
  context:                              # passado ao ContextStore da sessão-filho
    numero_atual:   "@ctx.session.numero_atual"
    contact_id:     "@ctx.session.contact_identifier"
  timeout_hours: 24
  on_resume:
    next: confirmar_portabilidade       # decision = input/approved
  on_reject:
    next: confirmacao_rejeitada         # decision = rejected
  on_timeout:
    next: timeout_cliente               # decision = timeout
```

### YAML do agente de I/O

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

O agente obtém o `resume_token` automaticamente via `@ctx.session.workflow_resume_token` — escrito pelo engine ao despachar a sessão-filho.

## MCP tool: `workflow_resume`

Nova tool registrada em `mcp-server-plughub/tools/workflow.ts`.

```typescript
server.tool("workflow_resume", {
  decision: z.enum(["input", "approved", "rejected", "timeout"]),
  payload:  z.record(z.unknown()).optional(),
}, async (input) => {
  const resume_token = await ctx.getContextTag("session.workflow_resume_token")
  await fetch(`${CHANNEL_GATEWAY_URL}/v1/channels/webhook/resume/${resume_token}`, {
    method: "POST",
    body: JSON.stringify({
      tenant_id: ctx.tenantId,
      payload: { decision: input.decision, ...input.payload },
    }),
  })
  return ok({ resumed: true })
})
```

## Refactoring de `agent_delegate`

O model de polling (`agent_delegate` → `agent_delegate_status`) é eliminado para o caso de I/O assíncrono. A `agent_delegate` atual (polling) permanece para o step `task` — despacho de especialistas IA de curta duração (segundos/minutos) em modo conferência dentro da mesma sessão.

| Step    | Duração esperada | Mecanismo          | Uso                            |
|---------|------------------|--------------------|--------------------------------|
| `task`  | Segundos-minutos | polling síncrono   | IA especialista, conferência   |
| `delegate` | Minutos-dias  | suspend + resume   | I/O com cliente, aprovações    |

## Diferença de relacionamento no Analytics

Com `delegate`, a sessão de I/O do agente é uma sessão normal no pool dela. Não cria hierarquia parent/child de sessões. O `resume_token` é o elo entre as sessões — rastreável no pipeline_state do workflow e nos eventos de resume.

```
Analytics/Sessions:
  Session A (webhook)   — portabilidade_processo_ia — closed
  Session B (webchat)   — portabilidade_confirmacao — closed  [agente de confirmação]

  Relacionamento: Session B chamou resume em Session A (via resume_token)
  Rastreabilidade: pipeline_state.transitions mostra delegate → resumed
```

O `WorkflowTraceList` não precisa de lookup reverso — o step `delegate` aparece como um ponto `suspend → resumed` na timeline, que já está implementada.

## Impacto no demo de portabilidade

`skill_portabilidade_demo_v1.yaml` atual viola o princípio (usa `notify` e `collect`). Após implementação:

```yaml
# ANTES (violação)
- id: coletar_dados
  type: notify
  message: "🔄 Processo iniciado..."

- id: notificar_aprovado
  type: notify
  message: "✅ Aprovado!"

- id: aguardar_confirmacao
  type: collect
  target: { type: customer, id: "@ctx.session.contact_identifier" }

# DEPOIS (correto)
- id: notificar_inicio
  type: delegate
  pool: portabilidade_notificacao    # agente envia mensagem de início e encerra
  context: { contact_id: "@ctx.session.contact_identifier" }
  on_resume: { next: solicitar_operadora }

- id: notificar_aprovado_e_confirmar
  type: delegate
  pool: portabilidade_confirmacao    # agente notifica + coleta confirmação
  context:
    contact_id: "@ctx.session.contact_identifier"
    numero_atual: "@ctx.session.numero_atual"
  timeout_hours: 24
  on_resume:   { next: confirmar_portabilidade }
  on_timeout:  { next: timeout_cliente }
```

## Arquivos a criar/modificar

| Arquivo | Ação |
|---|---|
| `skill-flow-engine/src/steps/delegate.ts` | Criar — executor do step delegate |
| `skill-flow-engine/src/executor.ts` | Adicionar delegate ao dispatcher |
| `mcp-server-plughub/src/tools/workflow.ts` | Adicionar tool `workflow_resume` |
| `mcp-server-plughub/src/tools/delegation.ts` | Simplificar `agent_delegate` (remover polling) |
| `@plughub/schemas` | Adicionar `DelegateStep` ao `SkillFlowSchema` |
| `skill_portabilidade_demo_v1.yaml` | Refatorar — substituir notify/collect por delegate |
| `agente_portabilidade_intake_v1.yaml` | Criar agente de notificação de início |
| `agente_confirmacao_portabilidade_v1.yaml` | Criar — novo agente de I/O de confirmação |
