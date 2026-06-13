# ADR — Identidade de participante: fonte única no ContextStore (escopo correto)

> Status: aceito · 2026-06-12 · Contexto: G7 Slice A (wrap-up multi-humano)

## Contexto

O wrap-up pós-atendimento (`agente_wrapup_v1`, hook `on_human_end` side=agent) isolava sua
conversa ao humano que encerrou usando um único campo de **sessão** no ContextStore:
`session.human_agent_participant_id`. Esse campo tinha **quatro** leitores que dependiam do mesmo
valor:

1. `orchestrator-bridge` `fire_pool_hooks` — `_fixed_pid` do participants SET (posatt).
2. `mcp-server` `menu_submit` — resolução do remetente do reply (botão).
3. `mcp-server` handler de texto WS — resolução do remetente do reply (texto).
4. `agente_wrapup_v1.yaml` — visibility de **todas** as 8 mensagens.

O campo é sobrescrito por `_write_pre_hook_context` a cada humano que sai. Com **1 humano**
(segmento final do contato) tudo concorda por cardinalidade 1. Com **≥2 humanos** (humano convidado
como specialist, ou origem+destino num transfer) o campo guarda **uma** identidade → o wrap-up de um
humano não-final fica mal-endereçado na **saída** (visibility errada) e na **entrada** (o reply
resolve o humano errado e não casa o `menu:result`). É o report "wrap-up só funciona quando o humano
é o segmento final".

## Decisão

**Identidade de participante é um fato por-escopo e mora numa fonte única no ContextStore. Nenhum
componente deriva nem duplica essa identidade num campo de escopo mais amplo.**

- Fatos de **contato** (cliente) → `session.*` (ex.: `session.customer_participant_id`).
- Fatos de **segmento** (o humano que ESTE hook serve) → `segment.{segId}.*`
  (ex.: `segment.{segId}.served_human_participant_id`), namespace já isolado por agente
  (`@segment.*` → `resolveSegmentRef` auto-prefixa com `ctx.segmentId`).

O wrap-up passa a endereçar via `@segment.served_human_participant_id` (auto-resolve para
`segment.{wrapupSegId}.served_human_participant_id`). O bridge grava esse campo no **join** do
wrap-up (espelhando o padrão `inviter_participant_id`), a partir do humano que aquele hook serve.
A entrega e a entrada passam a usar a identidade da própria conexão / o instance de origem do menu
(ver Slice A (b)/(c)), não o campo global.

`session.human_agent_participant_id` permanece **apenas** como fallback de sessão single-humano
(leitores legados) até ser aposentado; é proibido criar novos leitores dele.

## Consequências

- Wrap-up correto com N humanos por contato (transfer, convite de humano, handoff sequencial).
- Categoria de bug eliminada: "mesmo fato derivado em campo de escopo mais amplo".
- Regra geral (ver CLAUDE.md § invariante): estado **compartilhado para leitura** mora no
  ContextStore (`ContextEntry` uniforme, namespaces por escopo). Estado de **controle de ciclo de
  vida** (contadores posatt/hook_pending, guards NX, filas BLPOP) e **histórico** (canonical stream)
  são categorias distintas e não migram para cá — a regra é "uma fonte por fato, no escopo certo",
  não "uma chave para tudo".

## Alternativas descartadas

- **Coletar o motivo num modal síncrono no transfer** (sem agente wrap-up): contraria o modelo do
  produto — o wrap-up é um agente especialista conversacional por-pool, acionado no fim de segmento.
- **Manter o campo de sessão e desambiguar só na entrada**: insuficiente — a **saída** (visibility da
  YAML) também sai do campo global; o endereço continuaria errado em multi-humano.
