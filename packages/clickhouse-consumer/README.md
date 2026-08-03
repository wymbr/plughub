# clickhouse-consumer — PACOTE FÓSSIL (quarentena, 2026-08-03)

> **Não está deployado em lugar nenhum e não pode funcionar como está.**
> Mantido no repositório em quarentena documentada — não apagado — pelo mesmo
> critério usado com a tabela `pools` fóssil: o erro fica visível e reversível.

## Por que fóssil

Medido em 2026-08-03 (`infra/test/probe_hygiene.sh`, bloco 2):

| Evidência | Resultado |
|---|---|
| Serviço em `docker-compose.demo.yml` | **não existe** |
| `Dockerfile` no pacote | **não existe** |
| Tópico consumido (`evaluation.results`) | **não existe no broker**, e não pode ser criado (`KAFKA_AUTO_CREATE_TOPICS_ENABLE: "false"`) |
| Produtor de `evaluation.results` no repo | **nenhum** — o único hit é este próprio pacote |
| Menção em `CLAUDE.md` § Repository Structure | **ausente** |
| Referência viva | só `ecosystem.config.js` (topologia PM2, anterior ao Docker) |

Os defaults confirmam a idade: `clickhouse_database: "plughub"` e
`clickhouse_user: "default"` — hoje o demo usa `plughub_demo` / `plughub`.

## O que o substituiu

O caminho vivo de avaliação → ClickHouse é o tópico **`evaluation.events`**,
consumido pela **analytics-api**. Ver `CLAUDE.md` § Kafka Topics.

## Antes de reativar ou apagar

Reativar exige, no mínimo: um produtor de `evaluation.results`, o tópico no
`kafka-init`, um `Dockerfile`, um serviço no compose, e a entrada na §
Repository Structure. Se a decisão for apagar, apagar **junto** a entrada do
`ecosystem.config.js` — deixar o PM2 apontando para um script inexistente é
trocar um fóssil silencioso por um erro de boot.

A suíte (`src/plughub_clickhouse_consumer/tests/`) foi retirada do
`infra/test/report_suite_skips.sh`, onde saía como INCONCLUSIVO e fazia o
relatório inteiro terminar com código 2.
