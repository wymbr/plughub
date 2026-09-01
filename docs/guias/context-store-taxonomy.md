# ContextStore — Taxonomia e Controle de Visibilidade

> Última atualização: 2026-05-25 · Estado: Arc 16

> **Status:** Taxonomia ativa — o mascaramento de contexto foi implementado. As Fases A, B e C do mascaramento por variável × role (2026-05-17) já estão em produção e substituíram o `TAG_PII_CATEGORY` hardcoded por um sistema dinâmico de regras (`ContextMaskingRule`). Este documento permanece como referência da taxonomia de namespaces e do modelo de visibilidade.  
> **Documento de implementação:** ver [`docs/guias/context-masking-rules.md`](context-masking-rules.md) — sucessor que descreve o mecanismo dinâmico de mascaramento (`ContextMaskingRule`, regras configuráveis no Config API).  
> **Dependências:** `docs/guias/context-store.md`, `docs/guias/context-masking-rules.md`, `docs/adr/adr-message-masking.md`, `packages/schemas/src/audit.ts`  
> **Motivação:** a ContextoTab do Console (Arc 11 Fase 2) expunha todos os campos do ContextStore sem controle de acesso ou mascaramento, incluindo CPF, telefone e e-mail. Este documento definiu a taxonomia formal que precedeu a implementação de visibilidade e mascaramento na UI.

---

## Problema

O ContextStore surgiu organicamente: cada componente escreve o que precisa, sem schema formal, sem categoria PII declarada e sem regra de visibilidade por role. O resultado é que a ContextoTab exibe CPF e telefone em claro para o role `operator`, que não deveria ver esses dados completos.

---

## Taxonomia de Namespaces

### Visão geral

| Namespace | Propósito | PII | Visível a operator? |
|---|---|---|---|
| `caller.*` | Identidade do cliente | Sim — PII direto | Parcial (mascarado) |
| `account.*` | Dados contratuais e de conta | Sim — PII indireto | Parcial (mascarado) |
| `service.*` | Contexto operacional do atendimento | Não | Sim — completo |
| `journey.*` | Estado da jornada multi-sessão | Não | Sim — completo |
| `session.*` | Estado técnico da sessão atual | Não | Sim — completo |
| `agent.*` | Notas e observações do agente humano | Não | Apenas o próprio agente |
| `history.*` | Resumo condensado de contatos anteriores | Misto | Sim (service), Mascarado (PII) |

---

### `caller.*` — Identidade do cliente

Tags relativas à pessoa física ou jurídica que iniciou o contato.

| Tag | Tipo | PII category | Display operator | Display supervisor+ |
|---|---|---|---|---|
| `caller.nome` | string | — | Completo | Completo |
| `caller.cpf` | string | `cpf` | `***.***.***-**` + últimos 2 | Completo |
| `caller.cnpj` | string | `cnpj` *(novo)* | `**.***.***/****-**` | Completo |
| `caller.telefone` | string | `phone` | `(##) ****-####` + últimos 4 | Completo |
| `caller.email` | string | `email_addr` | `j***@empresa.com` | Completo |
| `caller.customer_id` | string | — | Completo | Completo |
| `caller.account_id` | string | — | Completo | Completo |
| `caller.motivo_contato` | string | — | Completo | Completo |
| `caller.intencao_primaria` | string | — | Completo | Completo |
| `caller.sentimento_atual` | string | — | Completo | Completo |

**Regra de mascaramento:** aplica `DEFAULT_MASKING_RULES` do tenant (`{tenant}:masking:config`) ao `display_partial` de cada tag PII. O `supervisor_state` entrega o valor mascarado para `operator` e o valor original para `supervisor`/`admin`/`evaluator`.

---

### `account.*` — Dados contratuais

Tags relativas ao contrato, produto ou conta do cliente na empresa.

| Tag | Tipo | PII category | Display operator | Display supervisor+ |
|---|---|---|---|---|
| `account.plano_atual` | string | — | Completo | Completo |
| `account.status_conta` | string | — | Completo | Completo |
| `account.numero_contrato` | string | `financial` | `****-####` + últimos 4 | Completo |
| `account.valor_fatura` | number | `financial` | `R$ ****,**` | Completo |
| `account.limite_credito` | number | `financial` | Oculto | Completo |
| `account.data_vencimento` | string | — | Completo | Completo |
| `account.inadimplente` | boolean | — | Completo | Completo |

---

### `service.*` — Contexto operacional do atendimento *(novo)*

Tags sobre o que foi feito ou solicitado **nesta sessão ou jornada**. Não contêm PII — são dados operacionais seguros para exibição ao `operator`.

| Tag | Tipo | Escrito por | Descrição |
|---|---|---|---|
| `service.solicitacao_atual` | string | agent, reason step | Serviço ou produto solicitado pelo cliente |
| `service.servicos_prestados` | string[] | agent, invoke step | Lista de serviços executados na sessão |
| `service.pendencias` | string[] | agent, reason step | Serviços solicitados ainda não resolvidos |
| `service.motivo_escalacao` | string | routing_engine, agent | Motivo declarado da escalação para humano |
| `service.protocolo` | string | mcp_call:crm | Número de protocolo gerado no CRM |
| `service.resolucao` | string | agent | Resolução registrada pelo agente humano |
| `service.categoria` | string | agent, ai_inferred | Categoria do serviço (ex: "portabilidade", "cancelamento") |
| `service.subcategoria` | string | agent, ai_inferred | Subcategoria (ex: "portabilidade.saída") |

---

### `journey.*` — Estado da jornada multi-sessão *(novo)*

Tags que representam o progresso e estado da Journey que agrupa esta e outras sessões. Complementa `session.*` para fluxos multi-contato.

| Tag | Tipo | Escrito por | Descrição |
|---|---|---|---|
| `journey.id` | string | workflow-api, mcp_call | UUID da Journey ativa |
| `journey.skill_id` | string | workflow-api | Skill que gerou a Journey |
| `journey.status` | string | workflow-api | active / suspended / completed |
| `journey.etapa_atual` | string | skill-flow-worker | ID do step atual no fluxo |
| `journey.etapa_descricao` | string | skill-flow-worker | Descrição legível do step atual |
| `journey.sessoes_anteriores` | number | workflow-api | Quantos contatos já ocorreram nesta jornada |
| `journey.proximo_passo` | string | reason step | Próximo passo esperado (gerado por IA) |

---

### `session.*` — Estado técnico da sessão

Tags de infraestrutura e estado da sessão atual. Sem PII — produzidas por componentes de sistema.

| Tag | Tipo | Escrito por | Display |
|---|---|---|---|
| `core.pool.id` | string | routing_engine | Completo |
| `core.pool.channels` | string[] | routing_engine | Completo |
| `core.pool.mentionable_pools` | object | routing_engine | Completo |
| `session.pool.mentionable_journeys` | object | routing_engine | Completo |
| `core.pool.max_reply_time_ms` | number | routing_engine | Completo |
| `core.pool.agent_groups` | string[] | routing_engine | Completo |
| `core.sentiment.current` | number | ai_inferred | Completo |
| `core.sentiment.category` | string | ai_inferred | Completo |
| `core.contact.close_origin` | string | routing_engine | Completo |
| `core.contact.customer_participant_id` | string | orchestrator-bridge | Completo |
| `core.contact.human_agent_participant_id` | string | orchestrator-bridge | Completo |
| `session.copilot.*` | object | ai_inferred:copilot | Completo |
| `session.escalar_solicitado` | boolean | reason step | Completo |
| `session.ultima_resposta` | string | reason step | Completo |
| `session.pergunta_coleta` | string | reason step | Completo |

---

### `agent.*` — Notas do agente humano *(novo)*

Tags escritas manualmente pelo agente humano via ManualTagForm. Visíveis apenas ao próprio agente na sessão — não propagadas para outros participantes nem para relatórios.

| Tag | Tipo | Descrição |
|---|---|---|
| `agent.observacao` | string | Nota livre sobre o cliente ou a sessão |
| `agent.alerta` | string | Flag de alerta para próximo atendimento |
| `agent.qualidade_percebida` | string | Avaliação subjetiva do agente (uso interno) |

**Regra:** tags `agent.*` têm `visibility: ["<participant_id do agente>"]` — só aparecem para o próprio agente que escreveu. O `supervisor_state` filtra por `participant_id` do JWT antes de entregar.

---

### `history.*` — Resumo de contatos anteriores *(novo)*

Tags que condensam o histórico de atendimentos anteriores deste cliente. Preenchidas por `agente_contexto_ia_v1` ou `insight.historico.*` via Kafka. Regras mistas: tags de serviço são seguras; tags com dados pessoais seguem mascaramento.

| Tag | Tipo | PII | Descrição |
|---|---|---|---|
| `history.ultimo_contato_at` | string | — | Data do último atendimento |
| `history.ultimo_motivo` | string | — | Motivo do último contato |
| `history.ultima_resolucao` | string | — | Como foi resolvido no último contato |
| `history.nps_score` | number | — | Último NPS registrado |
| `history.contatos_30d` | number | — | Quantidade de contatos nos últimos 30 dias |
| `history.servicos_recorrentes` | string[] | — | Serviços solicitados com frequência |
| `history.reclamacoes_abertas` | number | — | Reclamações não resolvidas |

---

## Modelo de Visibilidade por Role

### Tabela de acesso

| Namespace | `operator` | `supervisor` | `admin` | `evaluator` / `reviewer` |
|---|---|---|---|---|
| `caller.*` não-PII | ✅ completo | ✅ completo | ✅ completo | ✅ completo |
| `caller.*` PII | 🔶 mascarado | ✅ completo | ✅ completo | ✅ completo (audit) |
| `account.*` não-PII | ✅ completo | ✅ completo | ✅ completo | ✅ completo |
| `account.*` PII | 🔶 mascarado | ✅ completo | ✅ completo | ✅ completo (audit) |
| `service.*` | ✅ completo | ✅ completo | ✅ completo | ✅ completo |
| `journey.*` | ✅ completo | ✅ completo | ✅ completo | ✅ completo |
| `session.*` | ✅ completo | ✅ completo | ✅ completo | ✅ completo |
| `agent.*` | 🔒 só próprio | ✅ completo | ✅ completo | 🔒 só próprio |
| `history.*` não-PII | ✅ completo | ✅ completo | ✅ completo | ✅ completo |
| `history.*` PII | 🔶 mascarado | ✅ completo | ✅ completo | ✅ completo (audit) |

### Legenda
- ✅ **completo** — valor original entregue ao frontend
- 🔶 **mascarado** — `display_partial` derivado da `MaskingRule` da categoria PII
- 🔒 **restrito** — filtrado por `participant_id` antes de entregar

---

## "Set Mínimo" Configurável por Pool

A ideia é que cada pool declare quais namespaces são exibidos na ContextoTab para o `operator` por padrão. O default conservador exibe apenas dados operacionais; um pool de cobrança pode optar por exibir também `account.*` mascarado.

### Campo proposto em `PoolRegistrationSchema`

```typescript
context_visibility?: {
  /**
   * Namespaces visíveis ao operator na ContextoTab.
   * Default: ["service", "journey", "session"]
   * Namespaces PII (caller, account, history) exibem valores mascarados quando incluídos.
   */
  operator_namespaces: string[]
}
```

### Exemplos de configuração

```yaml
# Pool de SAC genérico — mínimo necessário
context_visibility:
  operator_namespaces: [service, journey, session]

# Pool de cobrança — precisa ver dados contratuais (mascarados)
context_visibility:
  operator_namespaces: [service, journey, session, account, history]

# Pool de identidade verificada — agente pode ver CPF completo (supervisor apenas)
context_visibility:
  operator_namespaces: [service, journey, session]
  # caller.* aparece para supervisor+ automaticamente
```

---

## Integração com Masking Config

O mascaramento de valores PII na ContextoTab deve reutilizar as `DEFAULT_MASKING_RULES` e as regras customizadas do tenant (`{tenant}:masking:config`), aplicando o `display_partial` já definido em cada `MaskingRule`.

### DataCategory existentes (schemas/audit.ts)

| Categoria | Exemplos de dados | Regra default |
|---|---|---|
| `cpf` | 123.456.789-00 | `***.***.***-**` + últimos 2 |
| `credit_card` | 4539 1234 5678 1234 | `**** **** **** ****` + últimos 4 |
| `phone` | (11) 98765-4321 | `(##) ****-####` + últimos 4 |
| `email_addr` | joao@empresa.com | `j***@empresa.com` (preserva domínio) |
| `health` | dados de saúde | Oculto total |
| `financial` | valor de fatura, limite | `R$ ****,**` |

### Categorias a adicionar

| Categoria | Dados cobertos |
|---|---|
| `cnpj` | CNPJ de pessoa jurídica |
| `address` | Endereço residencial/comercial completo |
| `account_number` | Número de conta bancária |

---

## Mapeamento de Tags PII por Categoria

> **Nota (2026-05-17):** o mapa `TAG_PII_CATEGORY` hardcoded abaixo foi **removido**. Toda nova variável PII exigia alteração de código. Foi substituído pelo sistema dinâmico `ContextMaskingRule` — regras configuráveis por variável × role, vivas no Config API e editáveis pela UI. Ver [`docs/guias/context-masking-rules.md`](context-masking-rules.md). O bloco abaixo é mantido apenas como referência histórica.

Tabela de referência (histórica) para o `supervisor_state` decidir qual `MaskingRule` aplicar a cada tag:

```typescript
const TAG_PII_CATEGORY: Record<string, DataCategory> = {
  "caller.cpf":               "cpf",
  "caller.cnpj":              "cnpj",
  "caller.telefone":          "phone",
  "caller.email":             "email_addr",
  "account.numero_contrato":  "financial",
  "account.valor_fatura":     "financial",
  "account.limite_credito":   "financial",
}
```

Tags não listadas aqui são consideradas não-PII e entregues em claro para qualquer role autorizado.

---

## Plano de Implementação

### Fase 1 — Filtro e mascaramento no backend (supervisor_state)
- `supervisor.ts`: ler `pool.context_visibility.operator_namespaces` do pool_config Redis
- Filtrar `context_snapshot` pelo namespace permitido para o role do JWT
- Aplicar `display_partial` nas tags PII com base em `TAG_PII_CATEGORY`
- Entregar `context_snapshot` filtrado + mascarado

### Fase 2 — Controle de escrita (inject-context)
- `POST /api/inject-context/:sessionId`: validar que o role pode escrever no namespace da key
- `operator` pode escrever apenas em `agent.*` e `service.*`
- `supervisor+` pode escrever em qualquer namespace

### Fase 3 — Tipagem no pool config
- Adicionar `context_visibility` ao `PoolRegistrationSchema` (schemas)
- Adicionar coluna `context_visibility Json?` ao Pool no Prisma
- Propagar via `pool.registered` Kafka → routing-engine pool_config Redis

### Fase 4 — UI
- `ContextoTab`: renderizar badge "🔒 PII" em tags mascaradas
- `ContextoTab`: respeitar `context_visibility` do pool para ordem e visibilidade de seções
- `ManualTagForm`: limitar namespaces disponíveis no datalist por role

---

## O que Enriquecer a Seguir

Tags úteis que ainda não existem e que agentes/fluxos deveriam começar a escrever:

| Tag | Quando escrever | Escrito por |
|---|---|---|
| `service.solicitacao_atual` | Ao classificar o motivo do contato | reason step / agente |
| `service.motivo_escalacao` | Ao escalar para humano | routing_engine / agent |
| `service.protocolo` | Ao registrar no CRM | invoke step (mcp_call) |
| `journey.etapa_atual` | A cada step do skill-flow | skill-flow-worker |
| `history.ultimo_motivo` | Ao carregar histórico do cliente | agente_contexto_ia_v1 |
| `history.nps_score` | Ao carregar NPS do CRM | invoke step (mcp_call) |
| `service.pendencias` | Ao identificar pendências abertas | reason step |
