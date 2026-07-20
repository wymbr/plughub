# Scheduler / Agenda — Spec (Fases 1–3)

> **Status:** Proposto (desenho fechado em discussão 2026-07-20). Pré-código.
> **Serviço novo:** `scheduler-api` (porta **3650** — 3600 já é do config-api).
> **Norte arquitetural:** [`docs/adr/adr-timer-scheduler.md`](../adr/adr-timer-scheduler.md) (Aceito) — esta spec
> **eleva** o ADR de um substrato de disparo efêmero para um domínio de **agendas de primeira classe**
> (duráveis, observáveis, operáveis), mantendo os invariantes do ADR.

---

## 1. Conceito

Uma **Agenda** é um recurso genérico que, num *quando* e *modo* configurados, **aciona um pool** da plataforma
via webhook (Arc 19). A agenda é agnóstica de domínio — por acaso usada por **deploy** (promover um slot) e
**outbound** (ex. campanhas de survey); ela não sabe o que o pool faz.

**Responsabilidade da agenda = disparar o pool, não executar.** O status da agenda é *"acionou ou não o pool"*
(produziu uma sessão admitida?). O status de **execução** é da sessão disparada — problema já resolvido pela
máquina de sessão/segmento/Monitor. A agenda guarda a **referência** (`session_id`/`root_session_id`) e o Monitor
faz drill-through; **nunca espelha** o status de execução (uma fonte de verdade por fato).

### Duas camadas (do ADR, estendido)

| Camada | O quê | Onde |
|---|---|---|
| **1 — substrato de timer** | "dispare em T": sorted-set de deadlines + poller único + evento genérico | Redis + scheduler-api |
| **2 — entidade de agenda** | registro durável: descritor, estado, ledger de disparos, próxima ocorrência | Postgres + scheduler-api |

Um recorrente = agenda ativa (Camada 2) que **re-arma** um timer (Camada 1) a cada disparo. O scheduler calcula
**só a próxima ocorrência** (não materializa a série); `validity.ends_at` é a condição de parada.

---

## 2. Invariantes

- **calendar-api só calcula o *quando/aberto*** (`is_open`, `next_open_slot`), nunca dispara. (ADR)
- **O disparo é o webhook de um POOL — nunca um `skill_id`.** (invariante S4 do CLAUDE.md; rota já existe:
  `POST /v1/channels/webhook/pool/{pool_id}`.) O skill do slot `current` do pool é o corpo do job.
- **O scheduler é domain-agnostic** — dispara o webhook com um payload genérico; quem interpreta é o skill do pool.
  Nenhuma lógica de deploy/campanha entra no scheduler.
- **Dispatch = "o gateway criou uma sessão"** (devolveu `session_id`); 500/timeout/unreachable = disparo
  **falho**, registrado com o motivo (degradação nunca silenciosa; sem retry no v1). **Nota as-built:** a
  channel-gateway devolve `201 + session_id` mesmo para pool inexistente/sem slot — ela cria a sessão e deixa o
  routing resolver. Logo `dispatched` NÃO garante admissão pelo pool; falhas de capacidade/admissão aparecem no
  **ciclo da sessão** (drill-through via `session_id`), não no dispatch. Isso é coerente com a camada: a agenda
  dispara; admitir/servir é da sessão.
- **Nunca espelhar status de execução na agenda** — guardar a referência da sessão e fazer drill-through.
- **Nunca redefinir tipos do `@plughub/schemas`** — importar `DayOfWeekSchema`, reusar o regex `HH:MM`.

---

## 3. Modelo de dados

### 3.1 Descritor da Agenda (`@plughub/schemas/scheduler.ts`, novo)

```
Agenda {
  id
  tenant_id
  name
  target_pool_id            // pool webhook (validado: channel_types ∋ "webhook")
  payload                   // JSON genérico entregue ao webhook (interpretado pelo skill do pool)
  timezone                  // default = timezone do calendar associado
  calendar_id?              // referencia calendar-api (traz semana/feriados/exceções). Padrão evaluation_calendar_id.
  status                    // active | paused | completed | expired | cancelled  (ciclo de vida da agenda)

  validity {
    starts_at               // quando entra em vigor
    ends_at?                // null = aberta (só recorrente)
  }

  schedule:
    | { mode: "once",      fire_at }              // uma data/hora
    | { mode: "recurring", rule }

  rule {
    frequency:  daily | weekly | monthly
    interval:   N                                  // a cada N (default 1) → quinzenal/bimestral etc.
    weekdays?:  [DayOfWeek]                         // weekly (importa DayOfWeekSchema)
    month_by?:  { days: [1..31 | "last"] }          // mensal por data
              | { nth: 1..5 | "last", weekday }     // mensal por posição ("última sexta")
    times:      ["HH:MM", ...]                       // ≥1 INSTANTE de disparo (≠ TimeSlot open/close do calendar)
    business_day_policy: ignore | only_business_days | shift_next | shift_previous
    month_overflow:      clamp | skip               // dia 31 em fevereiro
  }

  misfire_policy: fire_late | skip | fire_all_missed  // serviço fora do ar quando venceu

  // derivados / runtime
  next_fire_at?             // próxima ocorrência calculada
  last_fired_at?
}
```

**Conjunto de disparos** de um recorrente = produto cartesiano *(dias selecionados × `times`)*. Deslocamento por
`shift_*` move o **dia inteiro** (com todos os seus `times`).

### 3.2 Ledger de disparos (`AgendaDispatch`)

Um registro **por ocorrência** — a régua que o Monitor mostra por baixo da agenda.

```
AgendaDispatch {
  id
  agenda_id
  scheduled_for             // instante planejado (pós-shift de calendário)
  fired_at                  // instante real do POST
  result:  dispatched | failed | skipped        // skipped = misfire/only_business_days
  session_id?               // correlação com a sessão criada (drill-through)
  root_session_id?
  error?                    // motivo quando failed (5xx, no_capacity, unreachable)
}
```

---

## 4. Arquitetura do scheduler-api

- **Camada 1 (Redis):** `ZADD {tenant}:agenda_timers score=deadline_epoch member={agenda_id}:{occurrence}` +
  hash com o payload de disparo. Poller único: `ZRANGEBYSCORE … -inf now` a cada N s → para cada vencido, dispara.
- **Camada 2 (Postgres):** tabelas `agendas` + `agenda_dispatches`. Fonte de verdade; sobrevive a restart;
  re-hidrata a Camada 1 no boot (re-arma timers de agendas `active`).
- **Cálculo do *quando*:** avaliador da `rule` gera o instante-candidato; se `calendar_id` setado e
  `business_day_policy ≠ ignore`, chama **calendar-api** (`is_open`/`next_open_slot`) para validar/deslocar.
- **Disparo:** `POST /v1/channels/webhook/pool/{target_pool_id}` com o `payload`; captura a resposta síncrona
  (session_id / erro) → grava `AgendaDispatch`. Recorrente: recalcula e re-arma a próxima; respeita `validity`.
- **Correlação de status:** o `AgendaDispatch.session_id` é o link; o Monitor lê a sessão pela máquina existente.
  (Não assina `session_closed` para espelhar — drill-through.)

### Novos artefatos de plataforma
- Topic/evento (opcional se tudo síncrono no v1): manter disparo síncrono via HTTP ao webhook; `timer.fired`
  interno ao serviço. (Reavaliar evento Kafka quando a migração dos timers legados entrar — follow-up do ADR.)
- Config API namespace `scheduler`: `poll_interval_s`, `dispatch_timeout_s`, defaults de `misfire_policy`.
- ABAC: campo `scheduler` em `infra/modules.yaml` (`operacao` = ver/operar; `configurar` = criar/editar).
- i18n namespace `scheduler` (en + pt-BR).
- `docker-compose.demo.yml`: serviço `scheduler-api` + proxy `/v1/scheduler` no platform-ui (nginx+vite).

---

## 5. Pontos de reuso (calendar / schemas / UI)

| Reuso | O quê | Como |
|---|---|---|
| **Por referência (ganho maior)** | semana útil + feriados + exceções + timezone | `calendar_id` + chamadas ao calendar-api. A agenda **não** remodela feriado nem horário — `business_day_policy` pergunta ao calendário associado. |
| **Por import (schemas)** | `DayOfWeekSchema`, regex `HH:MM` | `weekdays[]` importa o enum; nunca redefinir. |
| **NÃO reusar (conceito ≠)** | `TimeSlot {open,close}` (intervalo) ≠ `times[]` (instantes) | manter fire-times como lista de instantes própria. |
| **Padrão de UI** | `WeeklyEditor` (toggle dia + lista de horários), `Modal`, `ConfirmDelete`, cliente `/v1/` | adaptar o `WeeklyEditor` para `weekdays[]`+`times[]`; reusar shell CRUD/Modal direto. |
| **Precedente de campo** | `evaluation_calendar_id` (calendar_id direto, sem `CalendarAssociation`) | seguir o mesmo padrão no v1. |

---

## 6. Fases

### Fase 1 — Core scheduler-api (backend)
Schemas (`scheduler.ts`: Agenda + AgendaDispatch + eventos) · scaffold do serviço · Camada 1 (sorted-set + poller)
· Camada 2 (Postgres + CRUD REST) · avaliador da `rule` + integração calendar-api para `business_day_policy` ·
disparo webhook→pool com captura da resposta (AgendaDispatch) · re-arme de recorrente + re-hidratação no boot.
**Gate:** criar agenda via REST (once + recurring) contra um pool webhook sintético → sessão admitida, ledger
gravado, próxima ocorrência calculada; caso `only_business_days`/feriado → `skipped`; caso 5xx → `failed`.

### Fase 2 — Consumidor deploy (promote agendado)
Corpo do job = pool webhook cujo skill faz `invoke POST /v1/pools/:id/promote` **com `on_failure`** (409 de
capacidade / `next` vazio não some). Autora-se uma Agenda apontando esse pool, `payload = { target_pool,
action: "promote" }` — **sem versão** (ver §7: pin descartado; promote = "vira o `next` vigente em T", concern
do pool). **Gate:** agenda dispara em T → promove o slot `next` do pool-alvo → `SkillDeployment` gravado; `next`
vazio ou falha de capacidade vira `AgendaDispatch.failed` com motivo, sem promover em silêncio.

### Fase 3 — UI (autoria + Monitor)
- **`/config/schedules`** (grupo Configuração, ABAC `scheduler.configurar`): lista + drawer de criação/edição
  reusando os padrões do CalendarsPage; **seletor de pool filtrado a `channel_types ∋ webhook`** (avisa se vazio);
  editor de `rule` (frequency/weekdays/monthly/times/business_day_policy) + `validity` + `calendar_id` + payload
  (JsonParamInput). Rota/navKey em inglês, rótulo "Agendas" só no pt-BR.
- **Monitor** (aba nova): agendas vivas + régua de disparos (AgendaDispatch com link pra sessão) + operações
  **disparar agora / reagendar próxima ocorrência / pausar-retomar / cancelar**. Config define a *política*;
  Monitor opera as *instâncias vivas* (não duplicar reagendamento nos dois).

### Fora do primeiro arco (follow-ups)
- **Fase 4 — outbound (campanhas/survey):** 2º consumidor recorrente; prova a abstração; liga ao outbound do
  Customer Surveys.
- **Migração dos timers legados (ADR §60–71):** dobrar `run_timeout_scanner` (channel-gateway),
  `_hook_timeout_guard` (bridge) e timeout de `collect` no substrato. Arriscado, não-bloqueante.

---

## 7. Decisões — FECHADAS (2026-07-20)
- **Pin de versão no promote → DESCARTADO.** A agenda endereça um **pool**, não um skill (invariante S4): versão
  é concern do pool, não da agenda. Promote = "vira o `next` vigente no instante T" (semântica honesta da
  operação); quem encenou o `next` é o dono do que vai ao ar. `next` vazio em T → 409 → `AgendaDispatch.failed`.
  O pin só re-emerge na composição **aprovação + agendamento** ("promover exatamente o que foi aprovado") e, aí,
  o binding é do **domínio de aprovação** (congela/encena o snapshot lá), nunca do scheduler.
- **Retry de dispatch falho → SEM retry no v1.** Falha (5xx/unreachable/sem capacidade/`next` vazio) grava
  `AgendaDispatch.failed` com motivo e aparece no Monitor; operador re-dispara. Retry automático esconderia a
  falha (contra "degradação nunca silenciosa"). Backoff = follow-up se um consumidor pedir.
- **Overlap → PERMITIR.** Cada disparo é sessão própria; sobreposição é inócua. Flag `singleton` reservada, só
  implementada sob consumidor que exija exclusão.
- **Porta + transporte → 3650 + HTTP síncrono no v1.** O disparo já devolve `session_id` síncrono; `timer.fired`
  como evento Kafka só entra na migração dos timers legados (pub/sub pros donos reagirem).
- **`misfire_policy` default → `skip` (recorrente) / `fire_late` (one-shot).** Campo já no schema; só o default.
