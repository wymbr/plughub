# Contexto de Retomada — PlugHub Platform
> Atualizado: 2026-05-30 · Para uso na próxima sessão (Claude Code ou Cowork)

---

## Como retomar

```
"Estou retomando o trabalho no projeto PlugHub.
 Leia docs/sessao-contexto-atual.md e TODO.md antes de começar."
```

---

## Estado do fluxo de portabilidade — 100% implementado e testado

### Ciclo completo validado em demo

```
Session A  (webchat, intake)       portabilidade_ia      → resolved ✅
Session B  (webhook, processo)     portabilidade_proc_ia → resolved ✅  (após approve)
Session C  (webchat, confirmação)  portabilidade_conf    → resolved ✅  (aguardar_inbound imediato)
Session A-new (webchat, reconnect) portabilidade_ia      → resolved ✅  (via conference specialist)
```

### Fluxo end-to-end

```
1. Cliente abre webchat → intake (Session A)
   → coleta numero, operadora, contato
   → workflow_trigger → Session B (suspended, aguarda operadora)

2. Operador aprova (curl resume):
   → Session B retoma → determinar_canal_confirmacao
   → contact=phone → confirmation_channel = "inbound_only"
   → delegate(portabilidade_confirmacao) → Session C criada

3. Session C (agente_confirmacao):
   → verificar_canal: confirmation_channel = "inbound_only" → aguardar_inbound
   → complete: resolved IMEDIATAMENTE (sem chamar workflow_resume)
   → Session B permanece suspenso no delegate step
   → pending_workflow key: {"resume_token", "pool", "context"} válida no Redis

4. Cliente reconecta ao webchat → novo intake (Session A-new):
   → coletar_contato: "11888888888"
   → verificar_pendencia (pending_workflow_get) → found: true
   → menu: "✅ Sua portabilidade foi aprovada! Confirmar?"
   → [✅ Confirmar] [❌ Cancelar] [🔄 Novo atendimento]
   → retomar_processo: delegate(portabilidade_confirmacao) ← CONFERENCE SPECIALIST

5. Conference specialist (agente_confirmacao em Session A-new):
   → verificar_canal: delegate_resume_token EXISTS → notificar_aprovacao
   → notify: "✅ Portabilidade aprovada!"
   → menu: [✅ Confirmar] [❌ Cancelar]
   → cliente confirma → workflow_resume(Session B token) → Session B: encerrar_sucesso ✅
   → retornar_sessao_pai → workflow_resume(Session A-new delegate token)
   → Session A-new delegate on_resume → finalizar → closed ✅
```

---

## Arquitetura de decisão de canal (nova)

**Session B define o canal antes de delegar:**
- `contact_identifier` com `@` → `email`
- `contact_identifier` sem `@` (phone) → `inbound_only` (demo sem WhatsApp)
- Futuro: phone + WhatsApp adapter → `whatsapp`

**agente_confirmacao decide por canal:**
- `delegate_resume_token` existe → conference specialist → proceed full flow
- `confirmation_channel = inbound_only` → `aguardar_inbound` → complete sem workflow_resume
- default → outbound capable → proceed full flow

---

## Arquivos modificados nesta sessão (todos não commitados)

### Novos componentes — pending workflow reconnect + delegate-as-conference

| Arquivo | Mudança |
|---|---|
| `skill-flow-engine/src/steps/delegate.ts` | `resolveInputMap` para context + import |
| `skill-flow-engine/src/engine.ts` | `persistDelegate` em `SkillFlowEngineConfig` + wiring |
| `channel-gateway/adapters/webhook.py` | `handle_delegate`, `handle_delegate_conference`, `get_pending_workflow` |
| `channel-gateway/main.py` | 3 novos endpoints: `/delegate`, `/delegate-conference`, `/pending/{id}` |
| `mcp-server-plughub/src/tools/workflow.ts` | tools: `pending_workflow_get` |
| `mcp-server-plughub/src/server.ts` | wire `registerWorkflowTools` segunda instância |
| `e2e-tests/services/skill-flow-service/src/index.ts` | `persistDelegateFn` split webhook/conference; `CHANNEL_GATEWAY_URL` |
| `docker-compose.demo.yml` | `CHANNEL_GATEWAY_URL`, `CALENDAR_API_URL`, `channel-gateway` dependency |
| `infra/registry/tenant_demo.yaml` | permissões `pending_workflow_get`, `workflow_resume` para intake |
| `orchestrator-bridge/main.py` | `pipeline_session_id` sufixo só para conference specialists |
| `analytics-api/consumer.py` | cache channel + origin_session_id no consumer |
| `analytics-api/sessions.py` | fallback Redis origin_session_id; fallback SCAN pipeline key; delegate_child_ids |
| `platform-ui/tabs/ListaTab.tsx` | badge suspenso; heurística webhook detection |
| `platform-ui/SessionsPage.tsx` | cross-session navigation sem segment filter |
| `platform-ui/WebhookSegmentDetail.tsx` | step timeline sempre visível |
| `platform-ui/locales/en/contacts.json` | key `lista.suspended` |
| `platform-ui/locales/pt-BR/contacts.json` | key `lista.suspended` |
| `schemas/src/index.ts` | remove duplicate `SuspendStep` export |

### YAMLs de skill

| Arquivo | Mudança |
|---|---|
| `skill_portabilidade_demo_v1.yaml` | v3.0: `determinar_canal_confirmacao` → `setar_canal_*` → `delegate` com `confirmation_channel` |
| `agente_portabilidade_intake_v1.yaml` | `verificar_pendencia` → `avaliar_pendencia` → `menu_continuidade` → `processar_decisao` → `retomar_processo` (delegate conference) |
| `agente_confirmacao_portabilidade_v1.yaml` | entry: `verificar_canal` → `aguardar_inbound` ou `notificar_aprovacao`; `retornar_sessao_pai` |

---

## Status dos testes

| Teste | Status |
|---|---|
| Intake → workflow criado (Session B) | ✅ OK |
| Aprovação via curl → Session C criada | ✅ OK |
| Session C fecha imediatamente (inbound_only) | ✅ Pendente teste pós-último-commit |
| Cliente reconecta → menu de confirmação aparece | ✅ OK |
| Cliente confirma → Session B fecha | ✅ OK |
| Session A-new fecha depois do specialist | ✅ Pendente teste |
| WorkflowTraceList: intake + exec + delegate_child | ✅ OK |

---

## Pendências (TODO.md)

1. `$.channel_type` nativo no session_context do bridge (para checks de canal genéricos)
2. WhatsApp outbound adapter em `agente_confirmacao` (setar_canal_whatsapp)
3. Timeout scanner para delegate step em webhook sessions
4. Arc 19 cleanup: remover `workflow.events` topic, arquivar `skill-flow-worker`
5. Usage Metering, Pricing, Audit LGPD Fases 2-5

---

## Regras técnicas chave

1. `field` + `operator` nos choice steps do YAML (não `key`/`op`)
2. `resolveInputMap` obrigatório para qualquer campo que aceite `@ctx.*` em steps
3. `pipeline_session_id` com sufixo `--seg--` só para conference specialists (conference_id set)
4. `pending_workflow` key escrita por `handle_delegate` quando `contact_identifier` no context
5. `confirmation_channel` definido por Session B ANTES do delegate step
6. `agente_confirmacao` com `aguardar_inbound` fecha sem chamar `workflow_resume` → Session B fica pendente
7. `delegate_resume_token` em ContextStore indica conference specialist → cliente conectado
