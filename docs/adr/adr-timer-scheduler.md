# ADR — Central Timer/Scheduler vs. Timers Espalhados

> Status: **Aceito (norte arquitetural)** · Implementação: **diferida** (primeiro corte
> em produção é o scanner do channel-gateway). Criado: 2026-05-31.

## Contexto

A plataforma tem vários mecanismos de "tempo" espalhados por componentes diferentes:

| Timer | Onde vive hoje | Natureza |
|---|---|---|
| Timeout de `suspend`/`delegate` (webhook) | channel-gateway — `run_timeout_scanner` varre `*:resume_tokens` | durável (horas/dias) |
| `_hook_timeout_guard` (NPS/wrap-up não completam) | orchestrator-bridge | médio (segundos/min) |
| Timeout de `collect` | workflow / channel-gateway | durável |
| Timeout de `menu`/`timeout_s` | inline no skill-flow-engine (BLPOP com timeout) | curto, síncrono |
| SLA de fila | routing-engine (lazy eval no head da fila) | curto |

Além disso, o **calendar-api** (porta 3700) é um **engine puro de prazo**
(`is_open`, `add_business_duration`, `business_duration`) — calcula *quando* um prazo
vence respeitando horário comercial, mas **não tem estado nem dispara timers**. O
timeout scanner do antigo **workflow-api** foi deprecado no Arc 19.

Resultado: lógica de agendamento fragmentada, cada serviço com seu próprio loop de
polling, sem um lugar único para observar/testar/raciocinar sobre disparos por tempo.

## Decisão

Adotar como norte um **módulo único de scheduling/timer**, separando claramente dois
conceitos que não devem ser fundidos:

- **"Quando" (cálculo de prazo)** — responsabilidade do **calendar-api**, que **permanece
  um engine puro**. Calcula o `deadline` (ciente de horário comercial). NÃO dispara nada.
- **"Disparar no quando" (firing)** — responsabilidade do **scheduler**. Mantém os timers
  pendentes, detecta os vencidos e dispara.

> Invariante: o firing **nunca** entra no calendar-api. Misturar transformaria um engine
> stateless de horário num agendador com estado e loop — responsabilidades distintas.

### Mecanismo proposto

- **Armazenamento**: Redis **sorted-set** por tenant — `ZADD {tenant}:timers
  score=deadline_epoch member={timer_id}` + um hash `{tenant}:timer:{id}` com o payload
  (`kind`, `session_id`, `resume_token`, `owner`, …). O `deadline` vem do calendar-api.
- **Poller único**: a cada N segundos faz `ZRANGEBYSCORE {tenant}:timers -inf {now}` para
  pegar os vencidos (O(log n) + tamanho do batch), em vez de varrer hashes inteiros.
- **Reação por dono**: ao vencer, o scheduler **emite um evento genérico**
  `timer.fired { kind, session_id, resume_token, payload }`. O **dono daquele tipo reage**:
  - `kind=webhook_resume` → channel-gateway chama `handle_resume(decision="timeout")`
  - `kind=hook_guard` → orchestrator-bridge fecha a conferência
  - `kind=collect` → workflow trata o timeout do collect
  - Ou seja: scheduler decide o *quando-disparar*; calendar-api calcula o *quando*; a
    *ação* fica com quem é dono dela.

### O que NÃO entra no scheduler

Timeouts **curtos e síncronos** que já são resolvidos inline e bloqueiam o próprio fluxo
— ex.: `menu`/`timeout_s` via `BLPOP` no engine. Centralizá-los só adicionaria latência e
um hop de evento sem ganho.

## Estado atual (primeiro corte) e migração

O timeout de `resume_tokens` (suspend + delegate) está implementado como
`WebhookAdapter.run_timeout_scanner` no **channel-gateway** (varredura `scan_iter` sobre
`*:resume_tokens`, intervalo 60s). É um lar **defensável** para esse timer específico,
porque o channel-gateway é dono do `resume_tokens` e do `handle_resume`.

Migração para o scheduler central, quando priorizada:

1. Trocar a varredura por um sorted-set de deadlines (mesma estrutura do scheduler).
2. Extrair o poller + o evento `timer.fired` para o módulo dedicado.
3. Migrar `_hook_timeout_guard` (bridge) e o timeout de `collect` para o mesmo módulo.

## Consequências

**Prós**: um lugar só para observar/testar agendamento; `ZRANGEBYSCORE` é eficiente; semântica
de timeout consistente entre suspend/delegate/collect/hooks; elimina N loops de polling.

**Contras**: mais um módulo/serviço; um hop de evento (`timer.fired`) entre vencer e agir
(vs. o scan in-process atual); esforço de migração atravessa channel-gateway + bridge +
workflow. Por isso a implementação fica diferida até haver prioridade — o scanner atual
cobre a necessidade funcional (Fase D) enquanto isso.
