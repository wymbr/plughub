# Outbound — Mailing + Campaign + Delivery (arco)

> **Status:** Fase 1 **✅ E2E**; Fase 2 **✅ API**; Fase 2b **✅ E2E**; Fase 3 (elegibilidade: 3a janela + 3b
> opt-out) **✅ API** (`smoke_outbound_fase3a.sh`/`3b.sh`) — 2026-07-21. Fases 4–5 pendentes.
> Design fechado: [`../product/outbound-mailing-campaign-design.md`](../product/outbound-mailing-campaign-design.md).
> Spec de implementação da Fase 1: [`../product/outbound-fase1-implementation-spec.md`](../product/outbound-fase1-implementation-spec.md).
> Contexto: o módulo Outbound é a **Fase 4 (opcional) do arco Scheduler** (cadência com diff zero).

Substrato **genérico** de contato ativo: `mailing` (audiência reutilizável) + `campaign` (orquestrador fino) +
governança de contato. **Survey é o primeiro consumidor (S11 agendado), não o dono.** Invariantes de modelagem:
metadado da entrada é **opaco** (contrato produtor↔consumidor), **membership (`mailing_entries`) ≠ suppression
(`campaign_deliveries`)**, a entrada é `(pessoa, contexto)`, e a unidade endereçável é o **POOL** (S4).

## Componentes

| Peça | Papel |
|---|---|
| **`mailing-api`** (Python FastAPI + asyncpg, porta **3660**) | Store canônico do domínio `outbound` (schema PG `outbound`). CRUD de mailings/campaigns, `mailing_add` (upsert por `dedup_key`), **drain** (claim atômico) e resultado de entrega. |
| **schema PG `outbound`** | `mailings`, `mailing_entries`, `campaigns`, `campaign_deliveries` (Fase 1). `contact_log`/`contact_policy` = Fase 2. |
| **tools MCP `outbound`** (`mcp-server-plughub/tools/outbound.ts`) | `mailing_add`, `campaign_drain`, `campaign_delivery_result` — wrappers finos do mailing-api, `isError` em não-2xx (degradação não silenciosa), auditados via McpInterceptor. |
| **`@plughub/schemas/outbound.ts`** | Contratos Zod (fonte canônica dos blobs JSONB + payloads REST). |
| **pool `outbound_demo` + `skill_outbound_demo_v1`** | Demo perfil workflow: drena → `loop` → `campaign_delivery_result`. Prova o substrato com diff zero no scheduler. |

## Fluxo (Fase 1)

Uma **Agenda** (scheduler) dispara `POST /v1/channels/webhook/pool/outbound_demo` com `payload {campaign_id}`; o
dispatcher entrega o payload como `context` → `@ctx.campaign_id` (mesmo caminho do promote agendado da Scheduler
Fase 2). O skill:

1. `campaign_drain(campaign_id)` → o mailing-api roda o `SELECT … FOR UPDATE SKIP LOCKED` sobre entradas elegíveis
   (active, não expiradas, casando `selection`, ainda não entregues por esta campanha **ou** falha retryável) e
   **claima** cada uma inserindo/atualizando `campaign_deliveries` (`result='claimed'`). Devolve o lote.
2. `loop` sobre `drained` → por entrada, `campaign_delivery_result` (o demo marca `contacted`; um consumidor real
   faria o `collect` antes e marcaria `responded`/`failed`).
3. `complete`.

**Pacing** = a agenda recorrente (cada tick drena ≤ `batch_size`). **Idempotência**: `UNIQUE(campaign_id,
mailing_entry_id)` + `ON CONFLICT DO UPDATE WHERE result='failed' AND attempts<max` → re-disparo não re-drena
entradas já entregues; só re-tenta falhas retryáveis.

> **Achado na validação (fix de engine):** `invoke` dentro de `loop` só rodava a 1ª iteração — o `loop` limpava o
> sentinel de idempotência `:__notified__` (notify) a cada volta mas **não** o `:__invoked__` (invoke), então o
> invoke retornava o resultado cacheado ("completed") sem re-chamar a tool. Corrigido em `skill-flow-engine/steps/
> loop.ts` (clear simétrico). **Ops:** recriar o `skill-flow-service` exige recriar o `orchestrator-bridge` em
> seguida (o bridge mantém conexão viva ao container antigo → `Cannot connect to host skill-flow-service:3460`).

## Drain / claim / retry (semântica)

- **Claim atômico**: `FOR UPDATE OF e SKIP LOCKED` no `SELECT`; `INSERT … ON CONFLICT DO NOTHING/UPDATE` serializa
  drains concorrentes (nunca dois claims da mesma entrada).
- **Retry por campanha**: `campaign.retry.max_attempts` + `delivery.attempts`. `campaign_delivery_result('failed')`
  incrementa `attempts`; o drain re-elege a falha enquanto `attempts < max_attempts` (via `ON CONFLICT DO UPDATE`
  reabrindo a entrega para `claimed`).
- **`dedup_key`**: `customer` → `cust:{customer_id}`; `customer_context` → `ctx:{sha256(customer_id+metadata)}`;
  `none` → sempre distinto. `mailing_add` faz upsert por `(mailing_id, dedup_key)`.

## Fase 2 — governança de contato (fato × regra × decisão)

Motor **agnóstico** de fadiga. Três peças no schema `outbound` + duas tools:

- **`contact_log`** (fato): todo contato outbound é registrado — `customer_id`, `channel`, `campaign_id`,
  `contacted_at`, `result`. Índices por `(tenant, customer, contacted_at)` e `(tenant, customer, channel,
  contacted_at)`.
- **`contact_policy`** (regra, em camadas): `scope` `tenant`|`campaign` (UNIQUE por `(tenant, scope, scope_id)`);
  `frequency_caps` (`[{window, max, per_channel}]`), `quarantine_after`, `channel_caps` (`{channel:{window,max}}`).
  Janela = `"24h"|"7d"|"60m"|"30s"` ou inteiro em segundos. Efetiva = policy da campanha se existir, senão a do
  tenant, senão **sem regra** (allowed).
- **`contact_eligibility_check(customer_id, channel, campaign_id?, claim=true, at?)`** (decisão): avalia a policy
  efetiva contra o `contact_log` (quarentena → frequency_caps → channel_caps; qualquer cap barra). Se permitido e
  `claim=true`, **grava o fato** na mesma transação (a janela começa no envio). Retorna `{allowed, reason,
  retry_after, claimed}`. `reason` sempre nomeia a regra que barrou (nunca silencioso).
- **`mailing_unsubscribe(customer_id, mailing_id?, channel?)`**: supressão mailing-scoped → `entry.status=
  'unsubscribed'` (o drain já exclui não-`active`). `mailing_id` omisso = todas as listas do cliente.

Tools MCP (`tools/outbound.ts`): `contact_eligibility_check`, `mailing_unsubscribe` — wrappers finos, `isError` em
não-2xx, auditados. Endpoints REST: `POST/GET/PATCH/DELETE /v1/contact-policies`, `POST /v1/contact/eligibility`,
`POST /v1/unsubscribe`. Validação (Fase 2, decisão): **motor via API** (`smoke_outbound_fase2.sh` ✅ — 2 checks no
mesmo cliente/janela → 2º negado por `frequency_cap` + `retry_after`; unsubscribe → drain exclui). **Fase 2b ✅
E2E:** o gate roda **dentro** do `skill_outbound_demo_v1` (loop → `verificar_elegibilidade` → `decidir` →
`registrar_ok`|`registrar_skip`), provado por `smoke_outbound_fase2b.sh` (fadiga cross-campanha no fluxo real).
**Lição de deploy (pool com slot):** editar o skill NÃO propaga só republicando `skill.flow`/reconcile — o bridge
roda o **snapshot do slot `current`**; é preciso `PUT /v1/pools/{id}/slots/next {skill_id}` (auto-snapshot do
`skill.flow`) → `POST /promote` (ambos com `x-service-token`), que publica `registry.changed(pool)` e invalida o
cache do bridge.
*(Achados na validação da Fase 2: `$n` não-referenciado no count por-ramo — Postgres "could not determine
data type"; e `datetime` faltando no import do `router.py` sob `from __future__ import annotations`.)*

**Fora da Fase 2** (Fase 3): opt-out **global** (`do_not_contact` no cadastro do cliente), janela de calendário
(`is_open`), preferência soft (canal/horário), pacing por `pool_status_get`.

## Fase 3 — portões (desenho fechado, 2026-07-21)

O `contact_eligibility_check` é o **ponto de decisão unificado** (a Fase 2 já fez a fadiga). Dos quatro "portões"
do design (§6), **dois são código novo no outbound** (elegibilidade) e **dois colapsam em subsistemas que já
existem** (routing/collect) — viram configuração, não build.

| Portão | Natureza | Como fecha |
|---|---|---|
| **Janela de contato** | elegibilidade (tempo) | **3a ✅ via API** — `contact_eligibility_check` resolve `campaign.contact_calendar_id` → `calendar-api is_open`; fechado → `outside_window` (sem claim) + `retry_after` até abrir. Aplica só se configurado. Degrada gracioso→ABERTO em erro do calendar. Smoke `smoke_outbound_fase3a.sh`. |
| **Opt-out global** | elegibilidade (veto) | **3b ✅ via API** — `contact_eligibility_check` consulta o cadastro (`identity.customers.attributes.do_not_contact` `{all?, channels?}`) via channel-gateway `GET …/identity/customers/{id}`; **maior precedência** (antes de calendar/fadiga), veta `opt_out` salvo `campaign.transactional`. `mailing_unsubscribe scope=global` escreve o atributo. Degrada→ALLOW barulhento em erro. Smoke `smoke_outbound_fase3b.sh`. |
| **Capacidade** | routing | **config, sem código** — não se consulta para reservar; **cria-se o contato roteado** (o `collect`) e o `Router.route()` **aloca-ou-enfileira** (invariante "árbitro único"). Teto de espera = `pool.queue_config.max_wait_s` (`min 0`, default 1800; `0` = falha rápido). Estourou → `close_reason=max_wait_exceeded`. Throttle grosso = `campaign.batch_size` por tick da agenda. |
| **Canal** | collect | **reuso, sem código** — `collect.channel_policy` **já** aceita `{channels: {canal→pool}, exclude, preferred_order}`; a campanha passa a sua `channel_policy` ao `collect`; o **N2** (`_negotiate_channel`, cego ao processo) resolve `(canal, pool)`. Falta só `_reachable_channels` sair do stub (consultar o resolver p/ `possessed_only`) — é do channel-gateway, Fase 5. |

**O `collect` unifica capacidade + canal:** ele cria a **sessão-filho de contato** no `(canal, pool)` negociado →
esse contato entra no `Router.route()` → aloca-ou-enfileira. Ou seja, **seleção de canal (N2) + alocação de
recurso (routing) num passo só**. Nada disso é build novo no outbound — o outbound só **passa** `channel_policy` +
`customer_id`; capacidade e canal fecham na **Fase 5** (quando houver `collect` real).

**Pacing é por-canal, não global — dois regimes** (`campaign.pacing.mode`):
- **`reactive`** (default; baixa latência: webchat/whatsapp/`collect`): **sem consulta** — dispara e o routing
  aloca-ou-enfileira (`max_wait_s`). Simples e previsível.
- **`look_ahead`** (alta latência: **discador de voz** — progressivo/preditivo): a **consulta de disponibilidade
  volta a ser necessária** — não para reservar, mas como **input de pacing e critério de início**. Setup de voz é
  caro (alocar tronco, rede, discar, tocar, detectar): discar 1:1 deixa o agente ocioso durante o setup; discar
  demais gera **abandono** (regulado). Então consulta-se `pool_status_get` (`available`/`queue_length`) — + **taxa
  de conexão** (histórico) e **alvo de abandono** no preditivo — para calcular *quantos discar à frente* e *se
  começa*. `session_reservation`/`max_session_total` (schema do pool) são o gancho de reserva. Eventos de
  disponibilidade = otimização futura do preditivo apertado (o poll do snapshot é o piso). **Desenho fechado
  agora; construído junto com o canal de voz outbound (Fase 5+, "discador = dependência futura", §19).**

**Escopo da Fase 3 (agora):** só **3a (calendar)** + **3b (opt-out)** — portões de elegibilidade, ortogonais a
routing/collect. Capacidade e canal são config/reuso que fecham na Fase 5.

## Execução paralela (fan-out) — desenho fechado (Fase 5)

Hoje o `skill_outbound_demo_v1` processa o lote **inline** num `loop` (sequencial). Para **paralelizar** os
contatos, o unit de paralelismo é a **sessão**, não o step — e o mecanismo é **fan-out**, não `delegate`.

**Por que não `delegate`/`collect`:** ambos **suspendem** a sessão (`delegate.ts` → `__suspended__`; `collect`
idem) e são **nível único** (um `session.delegate_resume_token` por sessão — concorrentes colidem). Num loop,
delegam/coletam **um de cada vez** → sequencial. Uma sessão é single-threaded.

**Mecanismo de paralelismo — `workflow_trigger` (fire-and-forget):** é uma **tool MCP** (`mcp-server-plughub/
tools/workflow.ts`), chamada de qualquer skill pelo passo **`invoke`** (não é step type nem SDK-only). Cria uma
sessão webhook nova (`pool_id`|`skill_id`, `customer_id`, `origin_session_id`, `journey`, `context_json`), devolve
`{workflow_session_id}` e **a sessão chamadora CONTINUA** (não espera). É o mesmo mecanismo do gatilho de survey.

**Decomposição dispatcher/worker:**
```
sessão-dispatcher (1 por tick da agenda):
  drenar (campaign_drain — claim do lote)        → drained[N]
  loop: invoke workflow_trigger(pool=worker,      → dispara N sessões INDEPENDENTES
        customer_id, context={delivery_id,...})     (NÃO espera cada uma)
  complete                                         → termina sem bloquear
sessão-worker (1 por contato, em PARALELO):
  eligibility → collect/contato → campaign_delivery_result
```

**Grau de paralelismo = capacidade do pool worker** (`max_concurrent_sessions`) + o routing `allocate-or-queue`
(`queue_config.max_wait_s`): disparar mais workers que instâncias → o excedente **enfileira** (bounded por
`max_wait_s`) — a "aloca-ou-enfileira" da Fase 3, agora automática por-sessão. **Pacing = `campaign.batch_size`**
por tick (quantos o dispatcher espalha; varia por campanha, já que é campo da campanha). Cada worker marca a sua
própria `delivery` — o dispatcher não acompanha resultado.

**Accounting — duas variantes:** (a) o dispatcher drena+claima o lote e passa `delivery_id` no `context` de cada
worker (explícito); (b) o dispatcher só dispara K workers e **cada worker drena `limit=1`** (claima o seu —
auto-pacing). Ambas válidas; (a) casa melhor com a contabilidade por-entrada.

**Push vs. pull:** o pull (worker chama `campaign_drain`) permanece a fronteira de reserva/claim (invariante
"agentes só via MCP"); o fan-out é uma **camada de distribuição por cima**, não substitui o pull. Um discador
preditivo (push da próxima chamada ao agente livre) seria essa camada com pacing `look_ahead`.

## Ordenação e seleção do lote (no claim) — desenho fechado + backend ✅

> **Backend ✅ via API (`smoke_outbound_ordering.sh`):** `campaign.ordering` (`[{path,dir,type}]`) traduzido em
> `ORDER BY` no drain (`_build_order_by`: path sanitizado, `type=number` com cast guardado, `added_at` desempate).
> UI (lista de campos) = fatia 1b.

O claim **é** a seleção — ordenação e corte de lote moram na **própria query do drain** (não num passo à parte:
`FOR UPDATE SKIP LOCKED` + ordem têm que ser atômicos). Três peças query-side, **duas já existem**:

- `campaign.selection` (existe) — predicado sobre `metadata` → **fatia** o mailing (`WHERE e.metadata @> selection`).
- `campaign.batch_size` (existe) — teto por tick → `LIMIT` (varia por campanha, resolve "tamanho do lote").
- **`campaign.ordering` (NOVO)** — sort **declarativo** → `ORDER BY`. Lista ordenada de `{path, dir}` (a ordem da
  lista = precedência); o backend **sempre** anexa `added_at` como último desempate (determinismo). Ex.:
  `[{path:"priority", dir:"desc"}]` → `ORDER BY (e.metadata->>'priority')::int DESC NULLS LAST, e.added_at`.

**Opacidade (exceção controlada):** o `metadata` continua **opaco por padrão**; `ordering`/`selection` são
**janelas declaradas pela campanha** (que conhece o `metadata_contract`) — a plataforma lê **só os paths nomeados**,
nunca assume schema. Opt-in por-campanha, não interpretação geral.

**Índice:** `ORDER BY (metadata->>'x')` não usa o índice atual — em volume pede um **índice de expressão** por path
(`CREATE INDEX … ON mailing_entries (mailing_id, ((metadata->>'x')))`), criado sob demanda. Demo/pequeno = seq scan.

**UI (fatia 1b):** editor "lista de campos" reordenável — cada linha `{campo, direção}`, precedência = ordem da
lista. Campo = **path livre** (metadata opaco → sem dropdown; o autor conhece o contrato) + `added_at` embutido e
como fallback final garantido. `metadata_contract` versionado futuro → autocomplete dos paths.

**Cota-por-segmento** (`ROW_NUMBER() OVER (PARTITION BY metadata->>'seg')`) = refinamento posterior, fora do 1º corte.

## Consumidor: Customer Surveys (S11) — decisões de fronteira

O survey (a implementar **depois** do outbound) é cliente do **caminho batelado/agendado** (NPS/PMF relacional).
O retorno por-resposta (§19 de `customer-surveys.md`) usa **pull inbox**, não mailing/campaign. Decisões fechadas
(2026-07-21): **`contact_eligibility_check` (Fase 2) SUBSTITUI `survey_eligibility_check`** — motor de
elegibilidade único e genérico, sem tool/ledger de survey; a quarentena por tipo de instrumento vira
`contact_policy`. A pertença à journey de um survey batelado (via `journey_merge` a partir de
`metadata.origin_session_id`) é concern de Fase 5.

## Invariantes

- **One source per domain** — `outbound` tem um store (`mailing-api`); sem duplicar scheduler/agent-registry.
- **Agentes só via MCP** — o skill drena via `campaign_drain`, nunca SQL direto.
- **POOL é a unidade endereçável** — `campaign.pool_id` / `agenda.target_pool_id`; nunca skill.
- **Degradação nunca silenciosa** — tools retornam `isError`; `collect` falho → `failed` com motivo.
- **UI-editable** ⚠️ dívida: mailing/campaign são config de tenant → exigem tela (fatia 1b; Fase 1 é backend).

## Pendente (próximas fases)

- **Fase 3 de elegibilidade ✅** (3a calendar + 3b opt-out `do_not_contact`). Capacidade (routing `max_wait_s`) e
  canal (`collect.channel_policy`) NÃO são build novo — fecham na Fase 5. Pacing `look_ahead` (discador) = desenho
  fechado, Fase 5+.
- **Fase 4** — importador anti-corrupção (CSV/xlsx → `mailing_add`).
- **Fase 5** — survey outbound e2e (`journey_complete` no `complete` do processo; `survey_record`→`session_signal`).
- **UI (fatia 1b)** — telas de mailings/campaigns/deliveries no platform-ui.
