# Plano de Testes — Skill-Flows e Jornadas Multi-Canal

> **Versão:** 1.0 — 2026-05-22  
> **Escopo:** Validação end-to-end do ambiente docker-demo com foco em webchat  
> **Abordagem:** Determinístico primeiro → LLM → Multi-canal  
> **Avaliação:** Interação no canal · Monitoração · Relatórios e Analytics

---

## 1. Catálogo de Agentes e Workflows

### 1.1 Status atual dos artefatos

| Arquivo | Tipo | Pool | Status | Ação |
|---|---|---|---|---|
| `agente_demo_ia_v1` | Tier 3 — triagem | demo_ia | ✅ Ativo | Manter / criar v2 determinístico |
| `agente_sac_ia_v1` | Tier 3 — SAC LLM | sac_ia | ✅ Ativo | Fase 2 (LLM) |
| `agente_contexto_ia_v1` | Tier 3 — especialista contexto | contexto_ia | ✅ Ativo | Fase 2 (requer CRM mock) |
| `agente_fila_v1` | Tier 3 — agente de fila | fila_humano | ✅ Ativo | Manter |
| `agente_copilot_v1` | Tier 3 — copilot @mention | copilot_sac | ✅ Ativo | Manter |
| `agente_auth_ia_v1` | Tier 3 — auth PIN mascarado | auth_ia | ✅ Ativo | Manter |
| `agente_auth_form_v1` | Tier 3 — auth formulário | auth_form_ia | ✅ Ativo | Manter |
| `agente_nps_v1` | Tier 3 — NPS pós-atendimento | nps_ia | ✅ Ativo | Manter |
| `agente_wrapup_v1` | Tier 3 — wrap-up humano | wrapup_ia | ✅ Ativo | Manter |
| `agente_evaluador_echo_v1` | Tier 3 — debug receive step | evaluador_echo_ia | ✅ Ativo | Manter (debug) |
| `agente_avaliacao_v1` | Tier 3 — avaliador Arc 13 | avaliacao_ia | ✅ Ativo | Fase 4 (avaliação) |
| `agente_pre_revisor_v1` | Tier 3 — gate pré-publicação | avaliacao_ia | ✅ Ativo | Fase 4 |
| `agente_revisor_v1` | Tier 3 — árbitro pós-contestação | avaliacao_ia | ✅ Ativo | Fase 4 |
| `skill_portabilidade_demo_v1` | Tier 1 — Journey portabilidade | portabilidade_ia | ⚠️ Corrigir | Fix `channel:` → `requires:` |
| `skill_reembolso_demo_v1` | Tier 1 — Journey reembolso | reembolso_ia | ⚠️ Corrigir | Fix `channel:` → `requires:` |
| `skill_revisao_treplica_v1` | Tier 1 — motor revisão Arc 13 | — | ✅ Ativo | Fase 4 |
| `skill_scheduled_deploy_v1` | Tier 1 — deploy agendado | — | ✅ Ativo | Manter |
| `agente_retencao_v1` | Tier 3 — stub obsoleto | retencao_ia | ❌ Remover | Sprint 0 |
| `agente_finalizacao_v1` | Tier 3 — NPS antigo | finalizacao_ia | ❌ Remover | Sprint 0 |
| `agente_reviewer_ia_v1` | Tier 3 — reviewer Arc 6 | avaliacao_ia | ❌ Remover | Sprint 0 |
| `skill_avaliacao_padrao_v1` | Tier 1 — orquestrador Arc 6 | — | ❌ Remover | Sprint 0 |
| `skill_revisao_simples_v1` | Tier 1 — 1 round obsoleto | — | ❌ Remover | Sprint 0 |

### 1.2 Artefatos a criar (Sprints 1–3)

| Arquivo | Tipo | Pool | Sprint | Finalidade |
|---|---|---|---|---|
| `agente_triagem_v2` | Tier 3 | demo_ia | 1A | Substituto determinístico do demo_ia_v1 |
| `skill_atendimento_sac_v1` | Tier 2 | sac_ia | 1A | SAC determinístico com Journey |
| `agente_portabilidade_intake_v1` | Tier 3 | portabilidade_ia | 1B | Coleta dados iniciais portabilidade |
| `skill_atendimento_portabilidade_v1` | Tier 2 | portabilidade_ia | 1B | Orquestra intake + jornada |
| `agente_reembolso_intake_v1` | Tier 3 | reembolso_ia | 1C | Coleta dados iniciais reembolso |
| `skill_atendimento_reembolso_v1` | Tier 2 | reembolso_ia | 1C | Orquestra intake + jornada |
| `skill_atendimento_auth_v1` | Tier 2 | auth_form_ia | 1D | Composição auth → SAC |
| `mock-crm` (FastAPI) | Infraestrutura | — | 2 | Retorna dados fixos por customer_id |

---

## 2. Arquitetura dos Cenários de Teste

### Visão geral — Três Tiers (Arc 16)

```
Tier 1 — Business Workflow  →  Journey + workflow-api  →  multi-sessão, longa duração
Tier 2 — Execution Workflow →  Skill Flow + segmentos  →  escopo da sessão
Tier 3 — Interaction Agent  →  menus + ContextStore    →  I/O com o cliente
```

### Cenário A — Atendimento SAC com Journey simples

```
Cliente webchat
  → pool demo_ia        [Tier 3: agente_triagem_v2 — menu determinístico]
  → pool sac_ia         [Tier 2: skill_atendimento_sac_v1 — creates_journey: true]
  → pool retencao_humano [humano no Console]
       ↳ on_human_end: [wrapup_ia, nps_ia]   (hooks automáticos)
```

**Dados gerados:** `journey.events`, `conversations.participants`, `session.motivo_contato`, `caller.nome`, segmentos com `sequence_index`, NPS + wrapup, `usage.events`.

### Cenário B — Portabilidade multi-sessão (webchat → webchat)

```
Sessão 1 (webchat)
  → pool portabilidade_ia  [Tier 3: agente_portabilidade_intake_v1]
       ↳ coleta número + operadora → journey.* namespace
       ↳ journey_start(skill_jornada_portabilidade_v1)  [Tier 1]
       ↳ Tier 1 suspende (aguarda aprovação interna — simula backoffice)

[POST /v1/workflow/{id}/resume — manual ou script de teste]

Sessão 2 (collect, webchat)
  → cliente recebe confirmação personalizada
  → resposta → journey_session_linked → journey completa
```

**Dados gerados:** `journey.events` (started → suspended → session_linked → completed), `collect.events`, `journey.*` no ContextStore, instância de workflow visível.

### Cenário C — Reembolso com confirmação de pagamento

```
Sessão 1 (webchat)
  → pool reembolso_ia  [Tier 3: agente_reembolso_intake_v1]
       ↳ coleta pedido + motivo → journey.* namespace
       ↳ journey_start(skill_jornada_reembolso_v1)  [Tier 1]
       ↳ suspend 1: análise interna (72h)

[aprovação ou rejeição via POST /v1/workflow/{id}/resume]

Sessão 2 (collect, webchat)  — se aprovado
  → "Sua solicitação foi aprovada! Confirme o recebimento."
  ↳ suspend 2: aguarda confirmação de pagamento (120h)

Sessão 3 (collect, webchat)
  → "Pagamento recebido. Obrigado!"
  → journey completa
```

**Dados gerados:** Journey longa com múltiplos `suspended` + `session_linked`, múltiplos `collect.events`.

### Cenário D — Autenticação com formulário mascarado

```
Cliente webchat
  → pool auth_form_ia  [Tier 3: agente_auth_form_v1]
       ↳ begin_transaction → formulário (email + senha masked + 2FA masked)
       ↳ validate_pin → resultado
       ↳ end_transaction → escalada para sac_ia ou retencao_humano
```

**Dados gerados:** Tokens mascarados no stream, `masked_input_fields` em `mcp.audit`, begin/end_transaction no pipeline_state.

---

## 3. Configuração do Ambiente de Teste

### 3.1 Serviços necessários (docker-compose.demo.yml)

| Serviço | Porta | Função |
|---|---|---|
| `core` | 3000 | Session lifecycle, stream, mascaramento |
| `channel-gateway` | 8010 | WebSocket webchat, inbound/outbound |
| `routing-engine` | — | Alocação de agentes, filas |
| `orchestrator-bridge` | — | Lifecycle de instâncias de agentes |
| `ai-gateway` | 8000 | LLM inference (Fase 2+) |
| `mcp-server-plughub` | 3100 | Tools dos agentes, supervisor_state |
| `agent-registry` | 3300 | CRUD pools + agent_types |
| `workflow-api` | 3800 | Journey lifecycle, suspend/resume |
| `skill-flow-worker` | — | Consumidor Kafka, executa SkillFlow |
| `analytics-api` | 3500 | Reports, ClickHouse consumer |
| `auth-api` | 3200 | JWT, usuários, ABAC |
| `config-api` | 3600 | Namespaces de configuração |
| `platform-ui` | 5174 | Console, Monitor, Analytics, Sessions |
| `webchat-client` | 5173 | Simulação do cliente |
| `redis` | 6379 | ContextStore, stream, filas |
| `kafka` | 9092 | Eventos entre serviços |
| `clickhouse` | 8123 | Analytics persistido |
| `postgres` | 5432 | Agent registry, auth, workflow |

### 3.2 Dados iniciais necessários

```bash
# 1. Tenant e usuários
POST /auth/register
  { email: "supervisor@demo.com", password: "Demo@2024", role: "supervisor" }
  { email: "operador@demo.com",   password: "Demo@2024", role: "operator" }
  { email: "admin@demo.com",      password: "Demo@2024", role: "admin" }

# 2. Registry sync (automático no bootstrap via infra/registry/tenant_demo.yaml)
# Verificar: GET /v1/pools?tenant_id=tenant_demo

# 3. Campanha de avaliação (Fase 4 — não configurar inicialmente)
# POST /v1/evaluation/campaigns  { ... }
```

### 3.3 Variáveis de ambiente mínimas (.env.demo)

```env
PLUGHUB_TENANT_ID=tenant_demo
PLUGHUB_AUTH_ADMIN_TOKEN=demo-admin-token-local

# LLM — deixar vazio nas Fases 1 e 0 (agentes determinísticos)
PLUGHUB_ANTHROPIC_API_KEYS=

# Canais — apenas webchat na Fase 1
WEBCHAT_JWT_SECRET=demo-webchat-secret

# Fase 2+ (CRM mock)
# PLUGHUB_CRM_URL=http://mock-crm:8099
```

### 3.4 Como ativar/desativar funcionalidades por configuração

| Funcionalidade | Como ativar | Como desativar |
|---|---|---|
| LLM (Fase 2) | `PLUGHUB_ANTHROPIC_API_KEYS=sk-...` | Deixar vazio |
| Avaliação (Fase 4) | POST /v1/evaluation/campaigns | Não criar campanha |
| CRM mock (Fase 2) | `PLUGHUB_CRM_URL=http://mock-crm:8099` | Variável ausente |
| WhatsApp (Fase 3) | `WHATSAPP_ACCESS_TOKEN=...` + pool channels | Não configurar |
| Copilot | Pool `retencao_humano.mentionable_pools` inclui `copilot_sac` | Remover do YAML |

---

## 4. Roteiro de Testes

> **Convenção de IDs:** `TC-{Cenário}{Sequência}` — ex: TC-A1, TC-B2  
> **Interfaces acessadas durante o teste:**
> - **Webchat cliente:** http://localhost:5173/webchat-test.html
> - **Console (operador):** http://localhost:5174 → /agent-assist
> - **Monitor:** http://localhost:5174 → /monitor
> - **Analytics:** http://localhost:5174 → /contacts/sessions
> - **Processos (Journey):** http://localhost:5174 → /contacts/processos

---

### 4.1 Cenário A — Atendimento SAC com Journey

#### TC-A1 — Fluxo completo (triagem → SAC → humano → NPS + wrapup)

**Objetivo:** Validar o caminho principal do atendimento e confirmar que todos os subsistemas recebem os dados corretos.

**Pré-condições:**
- docker-demo em execução com todos os serviços saudáveis
- Operador logado no Console com `agente_retencao_humano_v1` ativo
- Pool `demo_ia`, `sac_ia` e `retencao_humano` com agentes disponíveis

**Passos:**

| # | Ator | Ação | Interface |
|---|---|---|---|
| 1 | Cliente | Abre http://localhost:5173/webchat-test.html | Webchat |
| 2 | Cliente | Envia primeira mensagem (qualquer texto) | Webchat |
| 3 | Sistema | Menu de triagem aparece com 4 botões | Webchat |
| 4 | Cliente | Seleciona "Reclamação" | Webchat |
| 5 | Sistema | Mensagem "Transferindo para nosso time…" | Webchat |
| 6 | Sistema | Menu "Qual seu nome?" aparece | Webchat |
| 7 | Cliente | Digita nome (ex: "João Silva") | Webchat |
| 8 | Sistema | Mensagem "Aguarde, estamos localizando um especialista." | Webchat |
| 9 | Operador | Contato aparece na lista do Console | Console |
| 10 | Operador | Aceita o contato e inicia atendimento | Console |
| 11 | Operador | Digita algumas mensagens e encerra | Console |
| 12 | Sistema | NPS enviado ao cliente (botões 0–10) | Webchat |
| 13 | Sistema | Wrapup enviado ao operador (texto + classificação) | Console |
| 14 | Cliente | Responde NPS com nota "9" | Webchat |
| 15 | Operador | Preenche wrapup e classifica como "resolvido" | Console |

**Resultados esperados — Canal (Webchat):**
- [ ] Menu de triagem renderiza botões, não texto simples
- [ ] Após seleção, botões desaparecem / ficam desabilitados
- [ ] Mensagens do agente chegam em ordem e sem delay visível
- [ ] NPS aparece como botões numéricos (0 a 10) somente para o cliente
- [ ] Após resposta do NPS, confirmação de agradecimento aparece

**Resultados esperados — Monitor:**
- [ ] Contato aparece em "Sessões Ativas" assim que criado
- [ ] Pool `demo_ia` mostra ocupação durante triagem
- [ ] Ao escalar, contato move para pool `sac_ia` e depois `retencao_humano`
- [ ] Timer SLA incrementa em tempo real
- [ ] Após encerramento humano, sessão desaparece das ativas
- [ ] Segmentos posatt (nps, wrapup) aparecem enquanto executam

**Resultados esperados — Analytics/Sessions:**
- [ ] Sessão aparece em `/contacts/sessions` com status `closed`
- [ ] Detalhamento mostra 3+ segmentos: demo_ia (primary), sac_ia (primary), humano (primary), nps (posatt), wrapup (posatt)
- [ ] `sequence_index` correto em cada segmento (0, 1, 2…)
- [ ] `outcome` preenchido no segmento humano
- [ ] `duration_ms` presente em todos os segmentos fechados
- [ ] Transcript mostra mensagens do cliente e do agente com visibility correta
- [ ] NPS visível apenas no segmento nps (visibility array)
- [ ] Wrapup visível apenas no segmento wrapup (agents_only)

**Resultados esperados — Analytics/Processos:**
- [ ] Journey aparece em `/contacts/processos` com status `completed`
- [ ] `journey_started` e `journey_completed` visíveis no detalhe
- [ ] KPIs do ProcessosPage atualizam após conclusão

---

#### TC-A2 — Timeout NPS (cliente não responde)

**Objetivo:** Confirmar que timeout do NPS não trava o sistema e o segmento encerra corretamente.

**Passos:**
- Executar TC-A1 até passo 12 (NPS enviado)
- **Aguardar** o timeout configurado (30s) sem responder
- Verificar encerramento automático

**Resultados esperados:**
- [ ] Canal: Nenhuma mensagem adicional para o cliente após timeout
- [ ] Analytics: Segmento `nps_ia` fecha com `outcome: timeout` ou `close_reason: session_timeout`
- [ ] Monitor: Sessão desaparece das ativas após timeout do último hook

---

#### TC-A3 — Supervisão em tempo real

**Objetivo:** Validar que supervisor consegue entrar em sessão ativa e ver o stream.

**Pré-condições:** TC-A1 em andamento (atendimento humano ativo)

**Passos:**
- Supervisor acessa Analytics → Sessions → abre sessão ativa → seleciona segmento ativo → clica "Entrar como supervisor"
- Supervisor digita mensagem `agents_only`
- Operador verifica se mensagem aparece no Console

**Resultados esperados:**
- [ ] Botão "Entrar como supervisor" aparece **somente** em segmentos com `ended_at = null`
- [ ] Botão **desabilitado** em segmentos já encerrados (regressão do bug corrigido)
- [ ] Mensagem do supervisor chega ao Console com badge "Internal"
- [ ] Mensagem do supervisor **não aparece** no webchat do cliente
- [ ] Monitor: segmento do supervisor aparece como `role: supervisor`

---

#### TC-A4 — Copilot via @mention

**Objetivo:** Validar invocação do copilot e recebimento de sugestão durante atendimento ativo.

**Pré-condições:** Atendimento humano ativo (passo 10 do TC-A1)

**Passos:**
- Operador digita `@copilot ativa` no Console
- Sistema invoca `agente_copilot_v1`
- Agente copilot analisa contexto e envia sugestão (agents_only)
- Operador digita `@copilot para` para encerrar

**Resultados esperados:**
- [ ] Canal: nenhuma mensagem visível ao cliente durante ação do copilot
- [ ] Console: mensagem de análise aparece com indicação "agents_only"
- [ ] Monitor: segmento `copilot_sac` aparece com `role: specialist`, `parent_segment_id` preenchido
- [ ] Analytics: transcript do segmento specialist mostra apenas as mensagens do contexto desse agente

---

### 4.2 Cenário B — Portabilidade multi-sessão

#### TC-B1 — Fluxo completo (intake + suspend + resume + collect + complete)

**Objetivo:** Validar o ciclo completo de uma Journey multi-sessão com webchat.

**Pré-condições:**
- Pools `portabilidade_ia` configurado no registry demo
- `agente_portabilidade_intake_v1` e `skill_jornada_portabilidade_v1` deployados

**Passos — Sessão 1 (intake):**

| # | Ator | Ação | Interface |
|---|---|---|---|
| 1 | Cliente | Abre webchat e envia primeira mensagem | Webchat |
| 2 | Sistema | "Vou registrar sua solicitação de portabilidade." | Webchat |
| 3 | Sistema | "Qual é seu número atual?" | Webchat |
| 4 | Cliente | Digita "11999990000" | Webchat |
| 5 | Sistema | Menu de operadoras: [Claro, TIM, Vivo, Oi, Outra] | Webchat |
| 6 | Cliente | Seleciona "TIM" | Webchat |
| 7 | Sistema | "Dados registrados! Vou abrir seu processo." | Webchat |
| 8 | Sistema | Sessão 1 encerra (journey criada, suspensa) | Webchat |

**Passos — Aprovação (simula backoffice):**

| # | Ator | Ação | Interface |
|---|---|---|---|
| 9 | Admin | GET /v1/workflow/instances?tenant_id=tenant_demo | API / browser |
| 10 | Admin | Copia `instance_id` da instância com status `suspended` | API |
| 11 | Admin | POST /v1/workflow/{id}/resume { decision: "approved" } | API |

**Passos — Sessão 2 (collect):**

| # | Ator | Ação | Interface |
|---|---|---|---|
| 12 | Sistema | Webchat recebe "Sua portabilidade do número 11999990000 para TIM foi aprovada!" | Webchat |
| 13 | Sistema | "Confirme para prosseguir com a portabilidade" (menu sim/não) | Webchat |
| 14 | Cliente | Seleciona "Sim, confirmar" | Webchat |
| 15 | Sistema | "Portabilidade confirmada! Prazo: 3 dias úteis." | Webchat |
| 16 | Sistema | Journey completa | — |

**Resultados esperados — Canal:**
- [ ] Sessão 1 encerra normalmente após coleta dos dados
- [ ] Sessão 2 chega como nova conversa webchat (sem o cliente ter iniciado)
- [ ] Mensagem da sessão 2 usa `journey.numero_atual` e `journey.operadora_destino` do ContextStore (personalizada)
- [ ] Menu de confirmação aparece como botões

**Resultados esperados — Monitor:**
- [ ] Durante suspensão: Journey aparece em Processos com status `suspended`
- [ ] Após resume: nova sessão aparece em Sessões Ativas brevemente
- [ ] Após conclusão: Journey status muda para `completed`

**Resultados esperados — Analytics/Processos:**
- [ ] Journey detalhe mostra: `journey_started` → `journey_suspended` → `journey_session_linked` → `journey_completed`
- [ ] Duas sessões vinculadas à Journey (origin_session_id + collect session)
- [ ] `journey.numero_atual` e `journey.operadora_destino` visíveis no ContextStore (via Console inject-context ou audit)

**Resultados esperados — Analytics/Sessions:**
- [ ] Ambas as sessões aparecem em `/contacts/sessions`
- [ ] Sessão 2 (collect) tem `journey_id` preenchido
- [ ] Segmentos de cada sessão visíveis no drilldown

---

#### TC-B2 — Timeout de suspensão (backoffice não aprova)

**Objetivo:** Confirmar comportamento quando a aprovação não chega dentro do prazo.

**Passos:** Executar TC-B1 até passo 8, aguardar timeout configurado (simular com valor baixo, ex: 2 min em teste).

**Resultados esperados:**
- [ ] Canal: cliente recebe mensagem de timeout (definida no step `complete: outcome: timeout`)
- [ ] Analytics/Processos: Journey com status `failed` ou `cancelled` com close_reason `timeout`
- [ ] Monitor: sem sessão ativa vinculada

---

#### TC-B3 — Cliente não responde ao collect

**Objetivo:** Validar timeout do collect step quando cliente não interage.

**Passos:** Executar até passo 12 (sessão 2 chega), aguardar timeout sem resposta do cliente.

**Resultados esperados:**
- [ ] Journey fecha com `outcome: timeout` na sessão de collect
- [ ] Journey event `journey_completed` com `close_reason` indicando abandono do collect

---

### 4.3 Cenário C — Reembolso com múltiplas suspensões

#### TC-C1 — Fluxo completo (intake + análise + aprovação + confirmação)

**Objetivo:** Validar Journey com 2 ciclos de suspend/collect e múltiplos `journey_session_linked`.

**Passos — Sessão 1 (intake):**
- Cliente acessa webchat → pool reembolso_ia
- Sistema coleta: número do pedido + motivo (via menu button)
- Journey criada e suspensa (análise interna 72h)

**Passos — Aprovação + Sessão 2 (collect pagamento):**
- Admin: POST /v1/workflow/{id}/resume { decision: "approved" }
- Sistema envia collect: "Pedido {numero} aprovado. Informe conta bancária para crédito."
- Cliente responde com dados bancários (texto livre)
- Journey suspende novamente (aguarda pagamento)

**Passos — Confirmação + Sessão 3:**
- Admin: POST /v1/workflow/{id}/resume { decision: "payment_sent" }
- Sistema envia collect: "Pagamento enviado! Confirme o recebimento."
- Cliente confirma → Journey completa

**Resultados esperados:**
- [ ] Analytics/Processos: 3 eventos `journey_session_linked` no detalhe
- [ ] 3 sessões vinculadas à Journey na listagem
- [ ] ContextStore `journey.*` acumulando dados entre sessões (pedido, decisão, banco)

---

#### TC-C2 — Rejeição da solicitação

**Objetivo:** Validar caminho negativo — analista rejeita e cliente recebe notificação.

**Passos:** Após suspensão 1, POST /v1/workflow/{id}/resume { decision: "rejected", reason: "Produto fora de garantia" }

**Resultados esperados:**
- [ ] Canal: collect envia mensagem de rejeição personalizada com motivo
- [ ] Journey fecha com status `completed`, outcome `rejected`
- [ ] Nenhuma sessão adicional criada após a rejeição

---

### 4.4 Cenário D — Autenticação com formulário mascarado

#### TC-D1 — Autenticação bem-sucedida

**Objetivo:** Validar begin_transaction, campos mascarados e escalada pós-auth.

**Passos:**
- Cliente acessa webchat → pool `auth_form_ia`
- Sistema apresenta aviso de início (agents_only ao operador)
- Sistema envia formulário com 3 campos: e-mail + senha (masked) + código 2FA (masked)
- Cliente preenche e submete
- Sistema valida e escala para `sac_ia` ou `retencao_humano`

**Resultados esperados — Canal:**
- [ ] Formulário renderiza campo de senha e 2FA como `<input type="password">` (overlay)
- [ ] Após submit, os campos mascarados não aparecem no histórico do chat

**Resultados esperados — Monitor/Analytics:**
- [ ] Stream mostra tokens mascarados `[password:tk_xxx:***]` e `[otp:tk_yyy:***]`
- [ ] `original_content` **não visível** no transcript padrão (apenas para role evaluator/reviewer via audit)
- [ ] `mcp.audit` contém entrada com `masked_input_fields: ["senha", "codigo_2fa"]`
- [ ] begin_transaction e end_transaction no pipeline_state (não visíveis no stream)

---

#### TC-D2 — Tentativa inválida e retry

**Objetivo:** Validar fluxo de retry até escalada por falha.

**Passos:**
- Executar TC-D1 mas informar senha incorreta 2x
- Na 2ª falha, sistema escala para `retencao_humano` com motivo `auth_failed`

**Resultados esperados:**
- [ ] Canal: mensagem de erro após cada tentativa inválida
- [ ] No máximo 2 tentativas (conforme YAML) antes de escalar
- [ ] Segmento `auth_form_ia` fecha com `outcome: escalated`
- [ ] Próximo segmento: `retencao_humano` com `sequence_index: 1`

---

## 5. Matriz de Avaliação por Frente

Para cada item, avaliar: **✅ Funciona** · **⚠️ Parcial** · **❌ Problema** · **➕ Oportunidade de melhoria**

### 5.1 Frente: Interação no Canal (Webchat)

| Aspecto | O que observar | Resultado esperado |
|---|---|---|
| Renderização de menus | Botões vs texto | Botões com clique único |
| Estado pós-seleção | Botões ficam desabilitados? | Não permite clique duplo |
| Formulários mascarados | Campo tipo password | Overlay correto |
| Collect inbound | Sessão 2 chega ao cliente | Sem necessidade de ação do cliente |
| Continuidade de conversa | Histórico preservado entre sessões | Sessões separadas mas journey visível |
| Latência percebida | Tempo entre ação e resposta | < 2s para fluxos determinísticos |
| Mensagens de erro | Timeout, falha de auth | Texto amigável, sem stack trace |
| Indicador de digitação | Agente "está digitando…" | Presente enquanto agente processa |

### 5.2 Frente: Monitoração (Monitor + Console)

| Aspecto | O que observar | Resultado esperado |
|---|---|---|
| Lista de sessões ativas | Atualização em tempo real | Sem necessidade de F5 |
| SLA timer | Incremento por sessão | Timer visível, alerta ao exceder |
| Pool snapshot | Ocupação por pool | Números corretos após cada roteamento |
| Segmentos ativos | Participantes na aba Agentes | Todos os participantes listados |
| Orchestration tab | Steps do Skill-Flow | Step atual visível para supervisor |
| Journey ativa | Indicação no Console | Aba Journey com status da jornada |
| Supervisão | Botão habilitado/desabilitado | Somente em segmentos `ended_at = null` |
| Copilot | Invocação via @mention | Resposta aparece sem interrupção ao cliente |

### 5.3 Frente: Relatórios e Analytics

| Aspecto | O que observar | Resultado esperado |
|---|---|---|
| Sessions list | Filtros funcionando | Filtrar por canal, status, data |
| Drilldown de sessão | Lista de segmentos | Todos com role, timing, outcome |
| Transcript | Mensagens com visibility correta | NPS/wrapup separados por segmento |
| Journey list | Status e KPIs | Journey com todas as sessões vinculadas |
| Journey events | Timeline de eventos | 4+ eventos em ordem cronológica |
| ContextStore viewer | Namespace journey.* | Dados do intake visíveis entre sessões |
| Usage metering | sessions + messages | Contadores incrementando |
| Audit (LGPD) | Tokens mascarados | original_content apenas para auditores |
| Sentiment | Score por sessão | Timeline após Fase 2 (LLM) |
| Agent performance | Métricas por pool | Disponível após volume mínimo |

---

## 6. Critérios de Avanço entre Fases

### Fase 1 → Fase 2 (LLM)

Todos os itens abaixo ✅ antes de ativar `PLUGHUB_ANTHROPIC_API_KEYS`:

- [ ] TC-A1 completo sem erros nos logs
- [ ] TC-A3 (supervisão) funcionando — botão desabilitado em segmentos fechados
- [ ] TC-B1 completo — Journey com 2 sessões visível em Analytics/Processos
- [ ] TC-D1 — formulário mascarado sem vazamento de dados no stream
- [ ] Usage: `sessions` e `messages` incrementando no Redis
- [ ] Segments: `sequence_index` correto em todos os handoffs

### Fase 2 → Fase 3 (Multi-canal)

- [ ] Fase 1 + LLM validado
- [ ] `llm_tokens_input/output` visíveis no Usage Metering
- [ ] Sentiment timeline aparecendo em Analytics após sessões LLM
- [ ] `agente_contexto_ia_v1` funcionando (requer mock-crm)

### Fase 3 → Fase 4 (Avaliação Arc 13)

- [ ] Fase 2 + Multi-canal validado
- [ ] Collect funcionando em canal diferente do webchat (ex: WhatsApp mock)
- [ ] `inbound_journey_resume` testado (cliente retorna em canal diferente)
- [ ] Volume de sessões suficiente para calibrar avaliador (mín. 10 sessões)

---

## 7. Roadmap de Sprints

| Sprint | Foco | Duração estimada | Pré-requisito |
|---|---|---|---|
| **0 — Limpeza** | Remover 5 YAMLs + corrigir requires: + registry | ½ dia | — |
| **1A — SAC Journey** | agente_triagem_v2 + skill_atendimento_sac_v1 + testes TC-A1..A4 | 2 dias | Sprint 0 |
| **1B — Portabilidade** | intake + Tier 2 + fix Tier 1 + testes TC-B1..B3 | 2 dias | Sprint 1A |
| **1C — Reembolso** | intake + Tier 2 + fix Tier 1 + testes TC-C1..C2 | 1 dia | Sprint 1B |
| **1D — Auth** | skill_atendimento_auth_v1 + testes TC-D1..D2 | 1 dia | Sprint 1A |
| **2 — LLM** | mock-crm + ativar agentes LLM + validar sentiment + tokens | 2 dias | Sprint 1 completo |
| **3 — Multi-canal** | WhatsApp mock + capability negotiation + inbound resume | 3 dias | Sprint 2 |
| **4 — Avaliação** | Campanha + calibração + curadoria + Calibration Dashboard | 3 dias | Sprint 3 |

---

*Documento mantido em `docs/skill-flows-test-plan.md`. Atualizar com resultados reais de cada sprint.*
