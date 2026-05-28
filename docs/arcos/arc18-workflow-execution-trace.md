# Arc 18 — Workflow Execution Trace

> Última atualização: 2026-05-27 · Estado: Especificação v2

## Premissa

Analytics/Journeys e Analytics/Processes mostram KPIs e listas, mas não têm drill-down de execução. Quando um operador abre uma instância de processo ou uma jornada e quer entender o que aconteceu — por que suspendeu, qual step falhou, quanto tempo ficou em cada etapa, qual canal foi usado no collect, qual sessão o cliente respondeu — a resposta atual é: não há como ver.

O objetivo do Arc 18 **não é criar nova infraestrutura**. É expor dados que **já existem**:

- `pipeline_state.transitions` — array de transições registrado pelo `PipelineStateManager.addTransition()` em cada step, gravado no JSONB `pipeline_state` da tabela `workflow.instances` ao completar.
- `workflow.collect_instances` — tabela com `send_at`, `expires_at`, `responded_at`, `channel`, `target` e `session_id` (link para a sessão de resposta).
- `origin_session_id`, `metadata`, `pipeline_state.contact_context` — origem e parâmetros de entrada já persistidos.

A hierarquia completa de observabilidade fica:

```
Journey
  └── WorkflowInstance  (processo)
        └── Step transitions  (trace)
              └── Session  (sessão de resposta para steps collect/task)
```

---

## Princípio de navegação

Seguir o padrão hierárquico já existente na plataforma (SessionsPage → detalhe de sessão): cada nível é uma **página dedicada com rota própria**, não um painel lateral. Isso dá espaço vertical para a timeline de steps e permite links diretos (deep-link por URL) entre journey → processo → sessão.

```
Rotas novas:
  /analytics/processes              → AnalyticsProcessesPage  (lista enriquecida)
  /analytics/processes/:instanceId  → ProcessDetailPage        (detalhe completo)
  /analytics/journeys               → AnalyticsJourneysPage   (lista enriquecida)
  /analytics/journeys/:journeyId    → JourneyDetailPage        (lista de processos)
```

O `ProcessDetailPage` é reaproveitado de ambas as rotas via o mesmo componente — quando chegou de uma Journey, o breadcrumb mostra o caminho completo.

---

## O que já existe (não alterar)

| Dado | Onde vive | Observação |
|------|-----------|------------|
| `pipeline_state.transitions[]` | JSONB `workflow.instances` (DB) | Para instâncias ativas: Redis `{tenant}:ctx:{origin_session_id}` |
| `workflow.collect_instances` | PostgreSQL | `session_id` populado quando channel-gateway vincula a sessão de resposta |
| `WorkflowInstance.origin_session_id` | DB | Link para sessão de origem |
| `WorkflowInstance.metadata` | DB JSONB | Contém `flow_definition`, `webhook_id` (quando applicável) |
| `WorkflowInstance.pipeline_state.contact_context` | DB JSONB | Parâmetros passados no trigger |
| `WorkflowInstance.created_at / completed_at` | DB | Já no type `WorkflowInstance` do frontend |
| Hook `useWorkflowInstancesFiltered` | `hooks.ts` | Já tem filtros status/pool/flow/from/to |
| Hook `useJourneyInstances` | `hooks.ts` | Já busca instâncias por journey_id |
| Hook `useWorkflowInstanceSessions` | `hooks.ts` | Já lista sessões de uma instância |

---

## Fase A — Endpoint `/trace` no workflow-api

**Novo endpoint**: `GET /v1/workflow/instances/{id}/trace`

Agrega três fontes e retorna um trace estruturado. Não requer nova tabela.

### Lógica de leitura

```python
async def get_instance_trace(instance_id: str, tenant_id: str) -> InstanceTrace:
    instance = await db_get_instance(instance_id, tenant_id)

    # 1. pipeline_state — Redis first (instâncias ativas), fallback DB
    pipeline_state = instance.pipeline_state
    if instance.status in ("active", "suspended"):
        redis_key = f"{tenant_id}:ctx:{instance.origin_session_id or instance.id}"
        raw = await redis.hgetall(redis_key)
        if raw:
            pipeline_state = json.loads(raw.get("pipeline_state") or "{}")
            if not pipeline_state:
                pipeline_state = instance.pipeline_state  # fallback

    transitions = pipeline_state.get("transitions", [])
    results     = pipeline_state.get("results", {})

    # 2. Enriquecer com step_type e step_label do flow_definition
    flow_def   = (instance.metadata or {}).get("flow_definition", {})
    steps_by_id = {s["id"]: s for s in (flow_def.get("steps") or [])}
    enriched = []
    for t in transitions:
        step = steps_by_id.get(t["step_id"], {})
        item = {
            **t,
            "step_type":  step.get("type")  or t.get("step_type", "unknown"),
            "step_label": step.get("description") or t["step_id"],
            # Suspenção/resume — extrair das results
            "suspend_reason":   results.get(f"{t['step_id']}:__suspend_reason__"),
            "resume_decision":  results.get(f"{t['step_id']}:__resume_decision__"),
            "resume_timestamp": results.get(f"{t['step_id']}:__resume_timestamp__"),
        }
        enriched.append(item)

    # 3. Collect instances — enriquecer steps do tipo collect
    collect_step_ids = [
        s["id"] for s in (flow_def.get("steps") or [])
        if s.get("type") == "collect"
    ]
    collects_by_step: dict = {}
    if collect_step_ids:
        rows = await db_list_collect_instances_by_instance(instance_id)
        collects_by_step = {r["step_id"]: r for r in rows}

    for item in enriched:
        if item["step_id"] in collects_by_step:
            c = collects_by_step[item["step_id"]]
            item["collect"] = {
                "target":        c.get("target"),
                "channel":       c.get("channel"),
                "interaction":   c.get("interaction"),
                "status":        c.get("status"),         # pending | responded | expired
                "send_at":       c.get("send_at"),
                "expires_at":    c.get("expires_at"),
                "responded_at":  c.get("responded_at"),
                "session_id":    c.get("session_id"),     # link → SessionsPage
            }

    # 4. trigger_type — derivado da origem, sem novo campo no DB
    trigger_type = _derive_trigger_type(instance)

    return {
        "instance_id":        instance_id,
        "flow_id":            instance.flow_id,
        "status":             instance.status,
        "trigger_type":       trigger_type,   # "session" | "webhook" | "yaml_auto" | "api"
        "origin_session_id":  instance.origin_session_id,
        "journey_id":         instance.journey_id,
        "pool_id":            instance.pool_id,
        "contact_context":    pipeline_state.get("contact_context", {}),
        "outputs":            _extract_outputs(enriched, steps_by_id),
        "transitions":        enriched,
        "current_step":       instance.current_step,
        "outcome":            instance.outcome,
        "created_at":         str(instance.created_at),
        "suspended_at":       instance.suspended_at and str(instance.suspended_at),
        "completed_at":       instance.completed_at and str(instance.completed_at),
        "duration_ms":        _duration_ms(instance),
    }

def _derive_trigger_type(instance) -> str:
    """Derivar o tipo de acionamento da origem — sem campo novo no DB."""
    meta = instance.metadata or {}
    if meta.get("webhook_id"):
        return "webhook"
    if (meta.get("flow_definition") or {}).get("creates_journey") is True:
        return "yaml_auto"
    if instance.origin_session_id:
        return "session"   # acionado por agente ou humano numa sessão
    return "api"           # POST /trigger direto sem contexto de sessão

def _extract_outputs(transitions, steps_by_id) -> dict:
    """Agregar output_as de todos os steps que produziram resultado."""
    outputs = {}
    for t in transitions:
        step = steps_by_id.get(t["step_id"], {})
        output_as = step.get("output_as")
        if output_as and t.get("output_value") is not None:
            outputs[output_as] = t["output_value"]
    return outputs

def _duration_ms(instance) -> int | None:
    if not instance.created_at:
        return None
    end = instance.completed_at or instance.suspended_at
    if not end:
        return None
    return int((end - instance.created_at).total_seconds() * 1000)
```

### Resposta `InstanceTrace`

```typescript
export interface InstanceTrace {
  instance_id:       string
  flow_id:           string
  status:            WorkflowStatus
  trigger_type:      "session" | "webhook" | "yaml_auto" | "api"
  origin_session_id: string | null
  journey_id:        string | null
  pool_id:           string | null
  contact_context:   Record<string, unknown>   // parâmetros de entrada
  outputs:           Record<string, unknown>   // outputs acumulados pelos steps
  transitions:       StepTransition[]
  current_step:      string | null
  outcome:           string | null
  created_at:        string
  suspended_at:      string | null
  completed_at:      string | null
  duration_ms:       number | null
}

export interface StepTransition {
  step_id:           string
  step_type:         string
  step_label:        string
  timestamp:         string
  reason:            "completed" | "suspended" | "resumed" | "on_failure" | "skipped" | "escalated"
  next_step:         string
  suspend_reason?:   "approval" | "input" | "webhook" | "timer"
  resume_decision?:  "approved" | "rejected" | "input" | "timeout"
  resume_timestamp?: string
  collect?: {
    target:         { type: string; id: string }
    channel?:       string
    interaction?:   string
    status:         "pending" | "responded" | "expired"
    send_at?:       string
    expires_at?:    string
    responded_at?:  string
    session_id?:    string   // link direto para SessionsPage
  }
}
```

---

## Fase B — Enriquecimento das listas

### B.1 — Lista de instâncias (`AnalyticsProcessesPage`)

Adicionar colunas `started_at`, `ended_at` e `duration` na tabela, que hoje só mostra `flow_id`, `status` e `created_at` (relativo). O tipo `WorkflowInstance` já tem todos os campos necessários.

| Coluna | Campo | Formato |
|--------|-------|---------|
| **Processo** | `flow_id` + `id.slice(0,8)` | Já existe |
| **Status** | `status` | Badge colorido — já existe |
| **Início** | `created_at` | `toLocaleString()` |
| **Fim** | `completed_at ?? suspended_at ?? '—'` | `toLocaleString()` |
| **Duração** | `(completed_at ?? suspended_at) - created_at` | `fmtDuration(ms)` |
| **Step atual** | `current_step` | `<code>` — já existe como tooltip |
| **Pool** | `pool_id` | Nova coluna, opcional |

Ao **clicar em uma linha**, navegar para `/analytics/processes/:instanceId` (React Router `useNavigate`).

### B.2 — Lista de jornadas (`AnalyticsJourneysPage`)

Adicionar `completed_at` e duração. O tipo `Journey` atual tem `created_at` e `last_event_at`. A API de journeys retorna `completed_at` quando o status é final — verificar se o campo está sendo propagado no response ou adicionar ao tipo.

| Coluna | Campo | Formato |
|--------|-------|---------|
| **Jornada** | `journey_id.slice(0,8)` + `skill_id` | Já existe |
| **Tipo** | `journey_type_id` | Badge roxo — já existe |
| **Status** | `status` | Badge colorido — já existe |
| **Início** | `created_at` | `toLocaleString()` |
| **Fim** | `completed_at ?? last_event_at ?? '—'` | `toLocaleString()` |
| **Duração** | calculada | `fmtDuration(ms)` |
| **Processos** | `session_count` (proxy) | numérico |

Ao **clicar em uma linha**, navegar para `/analytics/journeys/:journeyId`.

---

## Fase C — ProcessDetailPage

Rota `/analytics/processes/:instanceId`. Breadcrumb: `Analytics > Processos > {flow_id} {instanceId.slice(0,8)}` (ou `Analytics > Jornadas > {journeyId} > {flow_id}` quando acessada a partir de uma Journey).

Página de largura total com quatro seções verticais:

### Seção 1 — Header com status

```
┌──────────────────────────────────────────────────────────┐
│  skill_portabilidade_demo_v1           [COMPLETED ✓]      │
│  fd0f2c0a-...                          Duração: 1h 30min  │
│  Início: 27/05/2026 10:00  Fim: 27/05/2026 11:30          │
└──────────────────────────────────────────────────────────┘
```

### Seção 2 — Origem

Exibe como o processo foi iniciado. `trigger_type` é derivado pelo backend:

| trigger_type | Rótulo | Ícone | Link adicional |
|---|---|---|---|
| `session` | Sessão de atendimento | 💬 | Link para `origin_session_id` em SessionsPage |
| `webhook` | Webhook externo | 🔗 | — (sem link, por segurança) |
| `yaml_auto` | Automático pelo Skill YAML | ⚡ | `creates_journey: true` badge |
| `api` | Chamada de API direta | 🔧 | — |

Quando `journey_id` presente: exibir link para `/analytics/journeys/:journeyId` com badge do `journey_type_id`.

### Seção 3 — Parâmetros

Duas colunas lado a lado:

**Entrada** (`contact_context` do `pipeline_state`): pares chave-valor do contexto passado no trigger. Campos vazios ou `{}` mostram "Nenhum parâmetro de entrada".

**Saída** (`outputs` do trace): pares chave-valor dos `output_as` acumulados pelos steps. Mostra o estado final de cada variável de saída do fluxo.

```tsx
<div className="grid grid-cols-2 gap-4">
  <ParamsTable title={t('trace.inputs')}  data={trace.contact_context} />
  <ParamsTable title={t('trace.outputs')} data={trace.outputs} />
</div>
```

### Seção 4 — Execução (step trace)

`ProcessStepTimeline` — timeline vertical com um item por transição registrada em `transitions[]`:

```
◉ verificar_elegibilidade          [task]          10:00:01  ✓ completed
│
◉ solicitar_operadora              [suspend]       10:00:02  ⏸ suspended
│   Motivo: aprovação · Retomado: 10:15:00 · Decisão: aprovado
│
◉ aguardar_confirmacao             [collect]       10:15:01  ⏸ suspended
│   ┌──────────────────────────────────────────────┐
│   │ Canal: whatsapp  Status: responded            │
│   │ Enviado: 10:15:02  Respondido: 11:30:00       │
│   │ ↗ Ver sessão de resposta …collect_abc        │
│   └──────────────────────────────────────────────┘
│
◉ finalizar                        [complete]      11:30:01  ✓ completed
```

Cores de badge por tipo de step (igual ao Arc 18 v1). Itens em que `reason !== "completed"` exibem o reason como badge secundário cinza.

**Steps collect** ganham um card expandido com:
- Canal utilizado, tipo de interação, status (pending/responded/expired)
- Timestamps `send_at` e `responded_at`
- Link `↗ Ver sessão de resposta …{session_id.slice(-10)}` navegando para `/contacts/sessions?sessionId={session_id}`

**Polling**: para instâncias `active` ou `suspended`, `useInstanceTrace` poleia a cada 5 segundos.

---

## Fase D — JourneyDetailPage

Rota `/analytics/journeys/:journeyId`. Breadcrumb: `Analytics > Jornadas > {journeyId.slice(0,8)}`.

Substitui o painel lateral atual do `MonitorJourneysPage` por uma página dedicada de largura total com três seções:

### Seção 1 — Header

Status, `journey_type_id`, `pool_id`, `origin_session_id` (link para SessionsPage), botões Merge e Split (que hoje estão no painel lateral — migrar os drawers para cá).

### Seção 2 — Timeline de eventos

Os eventos da Journey (`journey.events` Kafka já consumido pelo analytics-api) como timeline simples: `journey_started` → `journey_session_linked` × N → `journey_suspended` / `journey_resumed` / `journey_completed`.

### Seção 3 — Instâncias de processo

Lista de `WorkflowInstance` desta Journey (via `useJourneyInstances`). Mesmas colunas enriquecidas da Fase B: status, início, fim, duração, step atual.

Ao **clicar em uma linha**, navegar para `/analytics/processes/:instanceId` com state `{ fromJourney: journeyId }` para o breadcrumb mostrar o caminho completo.

---

## Fase E — Analytics por step (deferred)

Fase opcional, requer nova infraestrutura:

- Kafka consumer em `analytics-api` consumindo `workflow.events`
- ClickHouse table `workflow_step_events` alimentada pelas transições
- Endpoint `GET /reports/workflow-steps` com agregação por `flow_id + step_id`
- Heatmap de steps na SummaryTab do `ProcessosPage` (taxa de falha, tempo médio, taxa de timeout nos collect)

Não bloqueia as Fases A–D.

---

## Diagrama de navegação completo

```
Analytics / Journeys  [lista enriquecida — Fase B.2]
  │  Colunas: journey_id · tipo · status · início · fim · duração · processos
  │
  └── /analytics/journeys/:journeyId  [JourneyDetailPage — Fase D]
        ├── Header (status, tipo, pool, sessão origem, Merge, Split)
        ├── Timeline de eventos da Journey
        └── Lista de processos [enriquecida]
              │
              └── /analytics/processes/:instanceId  [ProcessDetailPage — Fase C]
                    ├── Header (skill, status, início, fim, duração)
                    ├── Origem (trigger_type + links)
                    ├── Parâmetros (inputs ↔ outputs)
                    └── ProcessStepTimeline
                          └── collect step → /contacts/sessions?sessionId=...

Analytics / Processes  [lista enriquecida — Fase B.1]
  │  Colunas: processo · status · início · fim · duração · pool
  │
  └── /analytics/processes/:instanceId  [mesmo ProcessDetailPage — Fase C]
```

---

## Novos arquivos / mudanças por fase

| Fase | Arquivos | Tipo de mudança |
|------|----------|----------------|
| A | `workflow-api/router.py` | Novo handler `GET /instances/{id}/trace` |
| B.1 | `ProcessosPage.tsx` | Adicionar colunas + `useNavigate` nas linhas |
| B.2 | `MonitorJourneysPage.tsx` | Adicionar colunas + `useNavigate` nas linhas |
| C | `ProcessDetailPage.tsx` (novo) | Página completa com 4 seções |
| C | `ProcessStepTimeline.tsx` (novo) | Componente reutilizável de timeline |
| C | `hooks.ts` | Novo `useInstanceTrace` |
| D | `JourneyDetailPage.tsx` (novo) | Substitui painel lateral atual |
| D | `routes.tsx` | 4 novas rotas |
| A–D | `contacts.json` (en + pt-BR) | Novas chaves i18n |

---

## Chaves de i18n novas (namespace `contacts`)

```json
{
  "trace": {
    "loading":           "Loading execution trace…",
    "empty":             "No steps recorded yet.",
    "stepCount":         "{{count}} steps",
    "trigger": {
      "session":         "Triggered from session",
      "webhook":         "Triggered by webhook",
      "yaml_auto":       "Auto-triggered by skill YAML",
      "api":             "Triggered via API"
    },
    "sections": {
      "origin":          "Origin",
      "inputs":          "Input parameters",
      "outputs":         "Output values",
      "execution":       "Execution trace"
    },
    "reason": {
      "completed":       "completed",
      "suspended":       "suspended",
      "resumed":         "resumed",
      "on_failure":      "failed",
      "skipped":         "skipped",
      "escalated":       "escalated"
    },
    "decision": {
      "approved":        "approved",
      "rejected":        "rejected",
      "input":           "input received",
      "timeout":         "timed out"
    },
    "collect": {
      "channel":         "Channel",
      "status":          "Status",
      "sentAt":          "Sent",
      "respondedAt":     "Responded",
      "expiresAt":       "Expires",
      "viewSession":     "View response session"
    },
    "noParams":          "No parameters",
    "currentStep":       "Current step"
  },
  "instanceList": {
    "columns": {
      "startedAt":       "Started",
      "endedAt":         "Ended",
      "duration":        "Duration",
      "pool":            "Pool"
    }
  }
}
```

---

## Invariantes

- O endpoint `/trace` é **read-only** — não modifica estado.
- Para instâncias `active`/`suspended`, lê Redis primeiro e usa DB como fallback.
- `trigger_type` é **derivado** no servidor — nunca persiste como campo na tabela, evitando migration.
- `ProcessDetailPage` e `ProcessStepTimeline` são os mesmos componentes independentemente da rota de origem (processos diretos ou dentro de jornada) — não duplicar.
- Links de collect step para SessionsPage só aparecem quando `session_id` está populado no `collect_instance`.
- Fases A–D não requerem nova tabela ClickHouse nem consumidor Kafka.
- Fase E é deferred e não bloqueia as anteriores.
