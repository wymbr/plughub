# workflow-api — PACOTE FÓSSIL (quarentena, 2026-09-30)

> **Não está deployado em compose nenhum desde a WFL-01.** Mantido no repositório
> em quarentena documentada, não apagado, pelo mesmo critério de
> `conversation-writer` e `clickhouse-consumer`: o erro fica visível e reversível.
> **Não religar sem ler esta página.**

## Por que fóssil

Medido em 2026-09-30, antes de tirar o serviço do ar:

| Evidência | Resultado |
|---|---|
| Rotas de produto | **nenhuma** desde a AUT-64 (2026-09-29): sobravam `/v1/health` e `POST /admin/backfill-events` |
| `workflow.instances` · `workflow.collect_instances` | **0 linhas** no `plughub_demo`; nada as cria desde o Arc 19 |
| Scanner de timeout | varria as duas tabelas vazias a cada ciclo |
| `workflow.events` (produtor único era este pacote) | consumidores: `skill-flow-worker` (fóssil também) e o consumer reativo da evaluation-api, removido na REV-02 |
| `analytics.workflow_events` (ClickHouse) | **vazia** |
| Borda | `/v1/workflow` e `/v1/journeys` saíram do nginx do platform-ui na AUT-64 |

O processo custava um container, uma conexão ao PostgreSQL, ao Redis e ao Kafka,
e não servia nada.

## O que substituiu

- **Disparar ou retomar processo** é pelo POOL, no channel-gateway
  (`POST /v1/channels/webhook/...`, Arc 19). A sessão é o identificador que atravessa
  suspend/resume.
- **Revisão e contestação de avaliação** são o REST do Arc 13 na evaluation-api
  (o motor por workflow saiu na REV-02).
- **Endereço de webhook** é o `ChannelEndpoint` do agent-registry (MOD-11).

## O que ficou de pé, de propósito

- As tabelas do schema `workflow` (vazio não custa; apagar schema é outra decisão).
- O código deste pacote.
- *(Até a WFL-02, também o tópico `collect.events` e os consumidores dele no
  channel-gateway e na analytics-api: o único produtor era
  `kafka_emitter.emit_collect_requested` deste pacote, com zero chamadores. A cadeia
  inteira saiu em 2026-09-30, por decisão do dono.)*

Ver `CHANGELOG.md` § 2026-09-30 (5).
