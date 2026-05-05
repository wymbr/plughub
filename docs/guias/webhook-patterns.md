# Guia: Padrões de Webhook

> Audience: desenvolvedores e integradores que conectam sistemas externos ao PlugHub

Existem dois padrões distintos de uso de webhook na plataforma. Cada um serve a um caso de uso diferente e usa endpoints diferentes. Entender qual aplicar evita confusão de design.

---

## Padrão 1 — Webhook inicia sessão nova (outbound / trigger)

**Caso de uso:** um sistema externo (ERP, CRM, sistema de cobrança) quer iniciar um fluxo de atendimento ou automação no PlugHub — sem que o cliente tenha iniciado o contato.

Exemplos:
- Sistema de cobrança dispara campanha de negociação quando fatura vence
- CRM solicita pesquisa NPS 24h após encerramento de chamado
- Plataforma de e-commerce aciona onboarding quando pedido é aprovado

### Como funciona

O sistema externo chama o endpoint público autenticado por token:

```
POST /v1/workflow/webhook/{webhook_id}
X-Webhook-Token: plughub_wh_<token>

{
  "customer_id":  "cust_123",
  "nome":         "João Silva",
  "valor_devido": 1500.00,
  "vencimento":   "2026-04-30"
}
```

O workflow-api:
1. Autentica o token (SHA-256 hash comparado com o registro)
2. Cria uma `WorkflowInstance` com o body como `context`
3. Emite `workflow.started` → skill-flow-worker → engine executa o Skill Flow

O sistema externo não precisa conhecer sessões, pools ou canais — apenas dispara o evento com os dados do caso.

### Registro de webhook (admin)

```
POST /v1/workflow/webhooks
X-Admin-Token: <admin_token>

{
  "flow_id":          "fluxo_cobranca_v1",
  "description":      "Disparado pelo sistema de cobrança ao vencer fatura",
  "context_override": { "tenant_id": "tenant_demo" }
}
```

Retorna o plain token **uma única vez** — armazene com segurança. Requisições subsequentes de listagem mostram apenas o `token_prefix`.

### Acesso ao contexto no Skill Flow

O body do webhook está disponível em `pipeline_state.context` e pode ser referenciado por interpolação:

```yaml
- id: contatar_cliente
  type: collect
  target:
    type: customer
    id: "{{context.customer_id}}"
  channel: whatsapp
  prompt: |
    Olá {{context.nome}}, identificamos uma fatura de
    R$ {{context.valor_devido}} vencida em {{context.vencimento}}.
    Podemos ajudar a regularizar?
  timeout_hours: 24
  business_hours: true
  on_response: { next: processar_resposta }
  on_timeout:  { next: tentar_outro_canal }
```

---

## Padrão 2 — Webhook sinaliza retorno para sessão existente (resume / callback)

**Caso de uso:** um workflow está suspenso aguardando que um sistema externo conclua um processo assíncrono — aprovação, pagamento, análise de crédito, emissão de documento — e precisa ser retomado quando isso acontecer.

Exemplos:
- Workflow aguarda aprovação de crédito no sistema bancário
- Workflow aguarda confirmação de pagamento no gateway financeiro
- Workflow aguarda análise manual de risco pelo time de fraude

### Como funciona

O Skill Flow suspende com `reason: webhook` e envia o `resume_token` ao sistema externo:

```yaml
- id: solicitar_analise
  type: invoke
  tool: sistema_credito_solicitar
  input:
    cpf:          "@ctx.caller.cpf"
    renda:        "@ctx.caller.renda_declarada"
    callback_url: "https://api.plughub.com/v1/workflow/resume"
    token:        "{{pipeline_state.resume_token}}"
  on_success: aguardar_analise

- id: aguardar_analise
  type: suspend
  reason: webhook
  timeout_hours: 4
  business_hours: true
  on_resume:  { next: processar_decisao }
  on_timeout: { next: escalar_analise_pendente }

- id: processar_decisao
  type: choice
  conditions:
    - field: "resumeContext.payload.aprovado"
      operator: eq
      value: true
      next: gerar_contrato
  default: informar_reprovacao
```

Quando o sistema externo conclui a análise, chama:

```
POST /v1/workflow/resume

{
  "token":    "<resume_token>",
  "decision": "input",
  "payload":  {
    "aprovado":  true,
    "limite":    5000.00,
    "analista":  "sistema_automatico"
  }
}
```

O workflow-api valida o token, verifica se não expirou, emite `workflow.resumed` → engine retoma em `processar_decisao` com `resumeContext.payload` disponível.

### Contexto disponível no retorno

Quando o engine retoma, tem acesso a:

| Fonte | O que contém |
|---|---|
| `pipeline_state` | Histórico completo de steps e resultados anteriores |
| ContextStore Redis | Todos os campos `caller.*`, `session.*` acumulados |
| `resumeContext.payload` | Dados enviados pelo sistema externo no resume |

Nada precisa ser reconstruído — o estado está integralmente preservado.

### Importante: quem envia o token ao sistema externo?

O PlugHub **não empurra o token automaticamente** para o sistema externo. É o Skill Flow que entrega o token via `invoke` (MCP tool que chama a API do sistema externo) antes de suspender. Isso é por design — garante que apenas o sistema que recebeu o token pode retomar o workflow.

```yaml
# Padrão correto: invocar antes de suspender
steps:
  - id: enviar_token_ao_erp
    type: invoke
    tool: erp_registrar_callback
    input:
      processo_id: "{{context.processo_id}}"
      resume_url:  "https://api.plughub.com/v1/workflow/resume"
      token:       "{{pipeline_state.resume_token}}"
    on_success: aguardar_erp

  - id: aguardar_erp
    type: suspend
    reason: webhook
    timeout_hours: 48
    on_resume:  { next: processar_resultado }
    on_timeout: { next: escalar }
```

---

## Comportamento do step `collect`

Uma dúvida frequente: o `collect` encerra a sessão após coletar a resposta?

**Não.** O `collect` abre uma sessão de canal com o cliente, coleta a resposta e fecha **essa sessão de canal** — mas o workflow continua vivo e segue para o próximo step.

```
collect step executa:
  ├── Abre sessão WhatsApp com o cliente
  ├── Envia mensagem/formulário
  ├── Suspende o workflow
  │
  Cliente responde:
  ├── Sessão WhatsApp fecha   ← apenas o canal fecha
  ├── workflow.resumed (Kafka)
  └── Engine continua no on_response → próximo step
```

O workflow só encerra quando um step `complete` ou `escalate` é executado. Isso permite fluxos com múltiplas coletas, validações e chamadas a sistemas externos ao longo do mesmo workflow:

```yaml
steps:
  - id: coletar_cpf
    type: collect
    channel: whatsapp
    output_as: dados_identificacao
    on_response: { next: validar_cpf }
    on_timeout:  { next: tentar_sms }       # fallback para outro canal

  - id: validar_cpf
    type: invoke
    tool: crm_validar_cpf
    input:
      cpf: "{{pipeline_state.results.dados_identificacao.cpf}}"
    on_success: calcular_proposta
    on_failure: coletar_cpf                 # nova coleta se inválido

  - id: calcular_proposta
    type: reason                            # LLM calcula proposta
    on_success: apresentar_proposta

  - id: apresentar_proposta
    type: collect                           # segundo collect no mesmo fluxo
    channel: whatsapp
    prompt: "Proposta: {{pipeline_state.results.proposta}}. Aceita? (sim/não)"
    output_as: decisao_cliente
    on_response: { next: registrar_acordo }
    on_timeout:  { next: escalar_sem_decisao }

  - id: registrar_acordo
    type: invoke
    tool: crm_registrar_acordo
    on_success: encerrar

  - id: encerrar
    type: complete
    outcome: resolved                       # workflow fecha apenas aqui
```

Cada `collect` abre e fecha sua própria sessão de canal de forma independente. O ContextStore preserva todo o estado entre eles.

---

## Comparativo dos dois padrões

| | Padrão 1 — Trigger | Padrão 2 — Resume |
|---|---|---|
| **Direção** | Sistema externo → PlugHub inicia | Sistema externo → PlugHub retoma |
| **Endpoint** | `POST /v1/workflow/webhook/{id}` | `POST /v1/workflow/resume` |
| **Autenticação** | Token de webhook registrado | `resume_token` gerado por suspensão |
| **Sessão do cliente** | Criada pelo step `collect` | Já existia; preservada no ContextStore |
| **Quem conhece o token** | Admin (registra o webhook) | Apenas o sistema externo que recebeu via MCP call |
| **Cria WorkflowInstance** | Sim | Não (retoma existente) |

---

## Quando usar cada padrão

Use **Padrão 1** quando o sistema externo é a origem do evento — ele sabe que algo aconteceu e quer que o PlugHub aja. O PlugHub não estava esperando nada.

Use **Padrão 2** quando o PlugHub iniciou um processo assíncrono num sistema externo e está aguardando a conclusão. O PlugHub delegou e está esperando a resposta.

Se o sistema externo empurra eventos sem que o PlugHub tenha solicitado e sem ter um token, o tratamento correto é o Padrão 1: um novo webhook trigger que cria um workflow de notificação, que pode interagir com sessões ativas via `@mention` ou step `task`.

---

## Referências

- Webhook CRUD: `packages/workflow-api/src/plughub_workflow_api/webhooks.py`
- Endpoints: `packages/workflow-api/src/plughub_workflow_api/router.py`
- Schema `suspend` e `collect`: `packages/schemas/src/skill.ts`
- Executor `collect`: `packages/skill-flow-engine/src/steps/collect.ts`
- Guia relacionado: `docs/guias/timeouts-e-deteccao-de-falhas.md`
