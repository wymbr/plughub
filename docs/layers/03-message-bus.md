# Layer 3 — Message Bus

> Última atualização: 2026-07-28 · Reconciliado contra a infraestrutura real
> Responsabilidade: backbone de eventos assíncrono — desacopla produtores de consumidores
> Implementado por: Apache Kafka (`docker-compose.infra.yml` e derivados)
>
> **Reconciliação 2026-07-28.** A versão anterior deste arquivo (25/05, Arc 16) afirmava como **fato** sete
> características de infraestrutura que **não existem no repositório**: 3 brokers por site, MirrorMaker 2
> cross-site, Kafka Connect para Snowflake/BigQuery/S3, auto-scaling por KEDA, SLA de 99,95%, tópicos
> prefixados por tenant e retenção por compliance. Eram herança da spec v24 — **arquitetura-alvo**, não
> as-built. Foram movidas para a seção § Arquitetura-alvo, claramente separadas.
> A tabela de tópicos também estava defasada (listava `agent.done` e `journey.events`, ambos removidos) e foi
> substituída por um ponteiro para a fonte canônica.

---

## Visão geral

O Message Bus é o eixo de comunicação assíncrona da plataforma. Toda troca de informação entre componentes que
não exige resposta síncrona passa pelo Kafka — o produtor publica e segue; o consumidor processa no seu ritmo.

O princípio é **event-driven first**: nenhum componente chama outro de forma síncrona, exceto onde a latência é
crítica (AI Gateway e tools MCP em tempo real de atendimento).

**Fonte canônica de tópicos, schemas, produtores e consumidores:
[`kafka-eventos.md`](../kafka-eventos.md)** — inclui o sumário por tópico, a **matriz módulo × tópico** e os
schemas Zod. Este arquivo não duplica essa tabela; duplicá-la foi o que produziu a defasagem corrigida agora.

---

## Configuração as-built

O que existe hoje no repositório, verificado nos arquivos de infraestrutura.

| Item | Estado real |
|---|---|
| **Broker** | **1 container**, `apache/kafka:3.7.0`, modo **KRaft** (sem Zookeeper), `broker,controller` no mesmo nó, quorum de um voter |
| **Listeners** | PLAINTEXT 9092 (externo) e 29092 (interno — `PLUGHUB_KAFKA_BROKERS: kafka:29092`) |
| **Criação de tópicos** | Container one-shot `kafka-init`; `KAFKA_AUTO_CREATE_TOPICS_ENABLE: "false"` |
| **Partições / replicação** | `--partitions 3 --replication-factor 1` — sem `min.insync.replicas` |
| **Retenção** | **Não configurada** — nenhum `retention.ms`/`retention.bytes`/`cleanup.policy` em nenhum tópico; vale o default do Kafka (7 dias) |
| **Nomes de tópico** | Globais e literais (`conversations.inbound`, `agent.lifecycle`, …). **Sem prefixo de tenant** — `tenant_id` é campo do payload |
| **Inspeção** | `provectuslabs/kafka-ui` em `localhost:8080` |
| **Quantidade de tópicos criados** | 5 no `infra`, 27 no `full`, 33 no `demo` |
| **Orquestração** | Somente Docker Compose. **Não há** Kubernetes, Helm ou Terraform no repositório |

**Consequência honesta:** um broker único com fator de replicação 1 é **ponto único de falha**. A stack atual é
ambiente de desenvolvimento e demonstração — adequada ao estágio, inadequada a produção. A propriedade
"degradação graciosa" da arquitetura (estado externalizado em Redis, componentes stateless) é real e
independente disso, mas não substitui replicação de broker.

**Formato e particionamento:** JSON (`json.dumps().encode("utf-8")`); chave de partição = `session_id` quando
disponível, o que garante ordem por sessão.

**Consumer groups:** cada componente declara o seu — ver a matriz em `kafka-eventos.md`. Notar que o
`analytics-api` consome **18 tópicos num único consumer group**, e que o `mcp-server-plughub` (Core) é
**produtor puro**: não assina nenhum tópico.

**Dead-letter:** `events.dead_letter` é sink write-only, alimentado por `orchestrator-bridge`, `analytics-api` e
`skill-flow-worker`. Não há consumidor no repositório — a inspeção é por ferramenta de ops.

---

## Arquitetura-alvo (NÃO implementado)

> Tudo nesta seção é **projeto**, não configuração existente. Verificado em 2026-07-28: nenhum destes itens tem
> arquivo de infraestrutura correspondente no repositório. Não apresentar como capacidade atual.

| Item | Estado | O que falta |
|---|---|---|
| 3 brokers por site | não implementado | Hoje 1 broker; exige cluster e `replication-factor ≥ 3` |
| MirrorMaker 2 (replicação cross-site) | não implementado | Zero ocorrências de `mirrormaker`/`mm2` na infra |
| Kafka Connect → Snowflake / BigQuery / S3 Parquet | não implementado | A ingestão em ClickHouse hoje é feita pelo `analytics-api` consumindo Kafka, não por Connect |
| KEDA — auto-scaling por consumer lag | não implementado | Não há manifest K8s nem `ScaledObject`. O `routing-engine` tem uma flag lógica `alert_keda` (`saturated.py:43`) que **pressupõe** KEDA, mas nada o instancia |
| SLA de 99,95% | não sustentado | Config atual (1 broker, RF=1, sem `min.insync.replicas`) contradiz o número |
| Retenção por compliance (30d / 1a / 5a) | não implementado | Nenhuma config de retenção; default de 7 dias em todos os tópicos |
| Isolamento por prefixo de tenant no nome do tópico | não implementado | Isolamento é lógico, por campo do payload |

**Nota sobre retenção e LGPD:** a retenção default de 7 dias significa que o Kafka **não** é hoje o repositório
de longo prazo de nada. A durabilidade real vem do PostgreSQL (stream de sessão, avaliações) e do ClickHouse
(analytics, `mcp_audit_log`, `audit_access_log`). Isso é arquiteturalmente correto — o barramento não deve ser
banco — mas invalida qualquer afirmação de "retenção de audit log por 5 anos no Kafka".

---

## Dívida registrada

**Não existe módulo central de constantes de tópicos.** Os nomes são literais inline em cada call site, com
poucas constantes locais de arquivo. É a causa raiz de quatro derivas encontradas na varredura de 28/07
(dois tópicos produzidos e não documentados, duas configs mortas apontando para tópicos inexistentes). Um
`topics.ts` / `topics.py` compartilhado tornaria tópico novo ou morto visível no diff.

---

## Referências

- [`kafka-eventos.md`](../kafka-eventos.md) — **fonte canônica**: tópicos, schemas, produtores, consumidores,
  matriz módulo × tópico
- `CLAUDE.md` § Kafka Topics — índice resumido
- `docker-compose.infra.yml`, `docker-compose.full.yml`, `docker-compose.demo.yml` — configuração real
