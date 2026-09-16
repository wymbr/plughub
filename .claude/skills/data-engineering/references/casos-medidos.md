# Casos medidos — o porquê de cada regra da skill `data-engineering`

> Texto movido **integralmente** do `CLAUDE.md` § *Postura de Engenharia* em 2026-09-16. Lá
> ficou a regra em uma ou duas linhas; aqui fica a medição que a justifica. Não resuma.

---

## 1. `ReplacingMergeTree` substitui a LINHA INTEIRA — não faz merge por coluna

Todo writer de
`sessions` ou manda a linha completa, ou é reidratado antes da escrita (cache de identidade no
consumer + carimbo no close, que é a linha sobrevivente). Três bugs de `sessions` num dia só vieram
disto. Vale para qualquer tabela RMT nova. **Regra derivada (2026-08-18): versão de RMT é fato do
EVENTO, nunca da inserção, e precisa da RESOLUÇÃO do fenômeno.** `segments` e
`participation_intervals` foram migradas para `ReplacingMergeTree(row_version)` com
`coalesce(<fim>, <início>)` em `DateTime64(3)`, como `sessions` e `session_transitions` — as duas
versões anteriores (`ingested_at` em segundo; e nenhuma coluna) perdiam o fechamento de segmento.
**Resíduo que a migração NÃO cobre:** `participation_intervals` continua
`ORDER BY (tenant, session, participant)`, então dois segmentos do mesmo participante na mesma sessão
(caso do resume) colidem numa linha só — ela **não** serve de testemunha por-segmento, e agora vence
o de evento mais recente em vez do último inserido. Use `segments`.

---

## 2. Ordem no Kafka é por PARTIÇÃO — logo publish sem `key` não tem ordem nenhuma

Qualquer par de
eventos que descreva o MESMO objeto (abre/fecha, cria/atualiza) tem de viajar com chave que os
coloque na mesma partição; sem ela o particionador espalha e o segundo evento pode ser consumido
antes do primeiro. Custou o defeito mais caro deste repositório até hoje: `conversations.participants`
publicava sem chave em tópico de 3 partições, o `participant_joined` vencia o `participant_left` na
dedup, e o segmento ficava aberto **para sempre, sem erro em lugar nenhum** — cinco rodadas de
investigação em três hipóteses erradas (transporte, controle, GC de task). Pior: o DDL de
`participation_intervals` **afirmava em prosa** a garantia que ninguém impunha (*"the 'left' event is
always inserted after 'joined' (Kafka ordering)"*). Comentário que promete invariante sem produtor é
a mesma família de "valor plausível". Ver `CHANGELOG.md` 2026-08-18 e
`docs/guias/conference-mechanics.md` § Problema 34.

O conserto vive em `orchestrator-bridge/main.py`, no publish de `conversations.participants`
(`key=session_id.encode("utf-8")`, com o comentário *"NÃO é otimização, é correção"*).

---

## 3. Identidade DERIVADA tem de conter o discriminador do FENÔMENO, não o do contêiner dele

Id
determinístico (`uuid5`) é a forma correta de tornar emissão repetida inócua — mas só se a chave
descrever a coisa que se quer contar. `queue_wait_segment_id` era `uuid5(tenant, session_id)`:
identificava a SESSÃO, enquanto o fato registrado é a PASSAGEM pela fila. Medido em 2026-08-24 num
contato real — espera de 24 118 ms num pool, transferência, espera de 85 009 ms noutro, **duas
emissões, uma linha**, e a primeira espera **deixou de existir** (o `ReplacingMergeTree` não funde,
substitui). Não é defeito de exibição: o carimbo da passagem perdida é apagado na saída, logo
nenhuma migração a alcança depois. **Escolha do discriminador é escolha de escopo**: o
`first_queued_ms` serviu porque seu ciclo de vida (NX na entrada, DELETE na saída) *já significa* uma
passagem; o `pool_id` foi recusado porque é fato do CALL SITE (o emissor passa `event.pool_id or ""`)
e daria dois ids para uma passagem. **Agravante que é a lição de método:** a premissa falsa
(*"uma sessão tem UMA passagem pela fila"*) vivia no **docstring da própria função** — comentário que
promete invariante sem mecanismo que a imponha, exatamente como o DDL de `participation_intervals`.
Ver `CHANGELOG.md` 2026-08-24 e `conference-mechanics.md` § Mudança 38.

---

## 4. Em ClickHouse, alias de agregado NUNCA repete nome de coluna real da tabela

`any(pool_id) AS
pool_id` faz o alias sombrear a coluna que o `WHERE` usa, e a query inteira falha
(`ILLEGAL_AGGREGATION`, code 184) — não a coluna, a query. Já aconteceu duas vezes: `any(attr.agent_type)`
na lente `deploy` e `any(pool_id)`/`any(user_id)` no `wrapup-summary`. Sufixe o alias (`_ref`) e renomeie
na camada Python, onde o contrato da API é definido. O modo de falha agrava a regra: o wrapper devolve
`data_unavailable` com `data: []`, indistinguível de "não há dado" para quem só olha a tela — só se
diagnostica se o `except` logar o texto da exceção.

---

## 5. O banco do demo é `plughub_demo`, não `analytics` (2026-09-09)

`CLAUDE.md` e os docs citam `analytics.sessions`, `analytics.segments`. Ao vivo o banco é
`plughub_demo` (`CLICKHOUSE_DB` no `docker-compose.demo.yml`) e `analytics` não existe —
a query falha com `UNKNOWN_DATABASE` (code 81). Com `2>/dev/null` no script, o erro some e a
saída vazia parece *"não há linha"*: três consultas seguidas "responderam" nada sobre pools
que tinham 93 e 4 segmentos. `session_timeline` está **vazia** neste deploy; o stream durável
é `session_stream_events`, no **Postgres**, com colunas `event_type`/`payload`.
