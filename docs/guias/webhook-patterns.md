# Guia: Padrões de Webhook

> Última atualização: 2026-08-10 (§ Exposição na borda) · Estado: Arc 16 + arco de auth de endpoint

> Audience: desenvolvedores e integradores que conectam sistemas externos ao PlugHub

> ⚠️ **O "Padrão 1" abaixo está OBSOLETO.** Ele descreve `POST /v1/workflow/webhook/{id}` +
> `workflow.webhooks`, o caminho por token que a **Fase F** do ADR
> [`adr-webhook-endpoint-single-registry`](../adr/adr-webhook-endpoint-single-registry.md) §7.8 decidiu
> **aposentar** (a remoção física é arco próprio; ver `TODO.md`). O trigger canônico hoje é o
> `ChannelEndpoint` — `POST /channel/webhook/{identifier}` para sistemas externos —, com autenticação
> opcional por endpoint (mesmo header `X-Webhook-Token`, outro registro). O Padrão 2 (resume) segue válido.

Existem dois padrões distintos de uso de webhook na plataforma. Cada um serve a um caso de uso diferente e usa endpoints diferentes. Entender qual aplicar evita confusão de design.

---

## ⚠️ Exposição na borda — requisito de deploy, não recomendação

> Escrito em 2026-08-10, ao fechar o arco de autenticação de endpoint. **Até aqui isto era suposição:**
> a segurança dos endereços internos dependia de o prefixo `/v1/*` não ser público, e isso não estava
> escrito em lugar nenhum. Um ambiente que o publicasse perderia a proteção **sem nada ficar vermelho** —
> nenhum teste alcança a topologia. Registrar é a única defesa disponível.

> ⚠️ **Revisado 2026-08-10 (Fase 0 do arco de workflow), e a tabela anterior estava incompleta.** Ela
> listava **dois** prefixos, como se a borda fosse "webhook externo × resto interno". Enumerando a
> superfície real do channel-gateway (`infra/test/probe_edge_surface.sh`, duas fontes: `/openapi.json` ∪
> decoradores no fonte), são **nove** prefixos, **sete deles obrigatoriamente externos** — porque metade
> da superfície externa **não é produto, é infraestrutura de canal**. Enunciar a regra como *proibição*
> (*"não publique `/v1`"*) deixava passar um deploy que publica tudo menos `/v1` e expõe `/docs`.
> **A regra é uma ALLOWLIST.**

| Prefixo | Público | Na borda? | Por quê |
|---|---|---|---|
| `/channel/webhook/{identifier}` | sistemas de terceiros do tenant | **Sim** | porta externa; serve só `origin=external` (ADR §7.6.3) |
| `/survey/{token}` | cliente final | **Sim** | página pública, autenticada pela posse do token |
| `/webhooks/*` | Meta, Twilio | **Sim** | callback de PROVEDOR (whatsapp/email/sms/voice) |
| `/voice/*` | Twilio | **Sim** | áudio TTS que o provedor busca + stream de mídia |
| `/webrtc/*` | browser do cliente | **Sim** | emissão de token LiveKit para a webapp |
| `/ws/*` | browser do cliente | **Sim** | WebSocket do webchat e do webrtc |
| `/webchat/v1/*` | browser do cliente | **Sim** | upload/download de anexo |
| `/v1/*` | componentes internos | **Não** | rede interna — ver abaixo |
| `/health` | orquestrador | **Não** | liveness; nada a ganhar publicando |
| `/openapi.json`, `/docs`, `/redoc` | — | **Não** | implícitos do FastAPI, **respondendo 200 hoje**: publicá-los publica o MAPA das rotas internas, inclusive a porta anônima (2) |

⚠️ **A distinção NÃO é topológica hoje.** `/channel/webhook/{slug}` e `/v1/channels/webhook/{skill_id}` são
rotas do **mesmo app FastAPI na mesma porta**; o que as separa é o filtro `allowed_origins={"external"}`.
Não existe borda versionada no repositório (sem `nginx.conf`; `vite.config.ts` e `Dockerfile` não publicam
`/channel`). Esta tabela é **requisito para quem publica**, não descrição do que está publicado.

### Por que `/v1/*` não pode ser público

Dentro dele vivem **duas** portas de disparo, e a segunda não tem como ser protegida por credencial:

1. `POST /v1/channels/webhook/{identifier}` — resolve pelo registro; **pode** exigir token por endpoint.
2. `POST /v1/channels/webhook/pool/{pool_id}` — **anônima por construção**. Não passa pelo registro
   (ADR §7.6.1: uma linha `identifier = pool_id → pool_id` é a função identidade do pool, um inventário
   incapaz de discordar da fonte), logo **não tem onde pendurar credencial**.

Todo pool webhook é acionável pela porta (2). Publicar `/v1/*` torna disparável, por qualquer um,
**todo pool webhook do tenant** — inclusive os que promovem deploy (`deploy_promote_ia`) e os que
contatam clientes (`outbound_*`). Nenhuma configuração de `auth_required` muda isso; é por isso que a
fatia 3 do arco de auth foi **cancelada** em vez de implementada (§7.9).

E o mesmo prefixo abriga RPC interno que nunca foi endpoint de tenant: `/v1/channels/webhook/delegate`,
`…/collect`, `…/resume/{token}`, `…/identity/*`. O nome é infeliz (anotado no §8 do ADR), mas a
consequência é concreta — publicar o prefixo publica isso junto.

### O que verificar num ambiente

- Os **sete** prefixos da allowlist → alcançáveis de fora. ✅ esperado.
- `/v1/*`, `/health`, `/openapi.json`, `/docs`, `/redoc` do channel-gateway (porta 8010) → **não**
  alcançáveis de fora.
- Endpoint `external` que precise de proteção adicional → gere token pela UI
  (`/config/channels` › Webhook). Endpoint `internal` **não recebe token**: o servidor recusa (422), e a
  razão é a porta (2) acima.

**Nenhum gate cobre a exposição real, e dois probes dizem isso por escrito.**
`probe_webhook_endpoint_inventory.sh` § F6 lê o *store* e declara que não verificou a borda.
`probe_edge_surface.sh` (2026-08-10) vai um passo além: **classifica** cada prefixo e reprova se um
prefixo novo aparecer sem linha na tabela — mas imprime, toda execução, que é uma DECLARAÇÃO e que nada
verifica o que o deploy publica. Rodá-lo depois de acrescentar rota ao channel-gateway é o hábito que
mantém esta seção viva; ausência de vermelho continua não sendo prefixo fechado.

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

### `requires` — negociação de canal por capacidade (Arc 16 Fase D)

A partir do Arc 16, o campo `channel` no step `collect` passou a ser **opcional**.
Em vez de fixar o canal, o `collect` pode declarar quais capacidades são necessárias
via `requires`, e o Channel Gateway seleciona o canal outbound com base na matriz de
capacidades por canal e no contexto da Journey (`journey.available_channels`,
`journey.canal_preferido`):

```yaml
- id: coletar_documento
  type: collect
  requires: [file_upload, text]    # canal escolhido pelo Channel Gateway
  output_as: anexo_cliente
  on_response: { next: validar_documento }
  on_timeout:  { next: escalar }
```

Valores aceitos em `requires`: `text`, `audio`, `video`, `file_upload`,
`masked_input`, `rich_menu`. Usar `channel` explícito continua válido para forçar
um canal específico.

### Nota: `notify` como step type está depreciado (Arc 16)

O step type `notify` foi **depreciado** no Arc 16 — use `invoke: notification_send`
para enviar mensagens unidirecionais ao cliente. O sub-campo `notify` dentro do step
`suspend` **permanece válido** (preservado por atomicidade) e não é afetado por esta
depreciação.

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
