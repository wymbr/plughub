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

---

## 6. MATERIALIZED VIEW sobre `ReplacingMergeTree` conta VERSÕES, não linhas (2026-09-23)

Uma MV é um gatilho de INSERT: cada bloco inserido na tabela de origem é agregado no estado
dela. O merge do RMT funde as versões na TABELA e nunca na MV, então todo segmento que é
regravado (fechamento + wrap-up) entra duas ou mais vezes. `FINAL` não se aplica à MV, e
`POPULATE` não conserta: recriada, ela reagrega a tabela já fundida e volta a contar versões
no próximo INSERT. Havia duas MVs sobre `segments`, e as duas saíram no mesmo dia.

**APF-01 — `mv_agent_performance_daily`.** Achada pela TRF-01, ao ir trocar nela a coluna de
transferência. Medido contra `segments FINAL`: `retencao_humano` **600 × 382**, `sac_ia`
**327 × 240**, `fila_humano` 70 × 59, e as transferências guardadas como `transferred`, a
versão placeholder que o wrap-up reescreveu. Único leitor no `system.query_log` de 30 dias: o
`performance_job` (4 624 SELECTs), que grava `{t}:agent_perf:*` para o score de roteamento.
Ao trocar a fonte para `segments FINAL`: 23 chaves gravadas e **5 scores mudaram** (ex.:
`human_agent_retencao_humano` 0,7656 → 0,6053). Dano no roteamento zero só porque
`routing.performance_score_weight` vivo é `0.0`.

**APF-02 — `mv_segment_summary`.** Pior: sem filtro de `ended_at`, agregava também a versão
de ABERTURA. `segment_count` medido duas vezes contra `segments FINAL`, e o excesso cresce a
cada INSERT: **8 094 × 4 406** na APF-01 (com **1 889 de 2 220** sessões divergentes) e
**8 371 × 4 406** na APF-02. Só `handoff_count` (`max`, idempotente) saía certo. O único leitor
no código, `/reports/sessions/complexity`, **nunca tinha respondido**: a subquery era
`FROM sessions FINAL WHERE s.tenant_id = …` sem o alias `s`, o ClickHouse recusava com
**code 47**, e o wrapper convertia em `data: [], error: data_unavailable` — os unitários, com
mock, não tinham como ver. Por isso a MV tinha zero leitores reais no `query_log`.

**O desenho que ficou:** agregado lido de `segments FINAL`; DROP idempotente das MVs e views em
`_MIGRATIONS` (view antes da MV), fora do `_ALL_DDL` e fora dos `dependent_views` do
`_migrate_row_version`, que as recriaria no `finally`. Agregado pré-computado sobre RMT, se um
dia for preciso, é outro desenho (refresh periódico com `FINAL`, ou agregação no consumidor
com id). Fontes: `CHANGELOG.md` § 2026-09-23 (4) e (5); `docs/arcos/arc5-segments.md`;
comentários `_DDL_AGENT_PERFORMANCE_DROP_*` e `_DDL_SEGMENT_SUMMARY_DROP_*` em `clickhouse.py`.
Gates: `probe_apf01_performance_source.sh`, `probe_apf02_session_complexity.sh` e as baterias
`mut_*` irmãs.

---

## 7. Negação sobre `Nullable` dentro de `countIf` descarta a linha (2026-09-23, TRF-01)

Em SQL, `NULL != 'x'` é NULL, não verdadeiro, e o `countIf` só conta o verdadeiro. A TRF-01
passou a marcar transferência por `close_reason = 'agent_transfer'` (fato do transporte), e o
censo antes de mexer mostrou que `close_reason` é `Nullable` e está **NULL em 3 571 de 4 406**
linhas de `segments FINAL`. Um `countIf(outcome = 'resolved' AND close_reason != 'agent_transfer')`
teria encolhido o contador de resolvidos para os segmentos que TÊM `close_reason`, sem nada
vermelho. O conserto é `coalesce(close_reason, '')`, e mora em uma casa:
`_IS_TRANSFER_SQL` / `_NOT_TRANSFER_SQL` em `packages/analytics-api/src/plughub_analytics_api/reports_query.py`
(o `performance_job` a importa). O gate `probe_trf01_transfer_marking.sh` tem controle de
população: dos 1 787 resolvidos, **1 491 têm `close_reason` NULL** — sem eles o ramo não
distinguiria o defeito; a bateria `mut_trf01_transfer_marking.sh` planta a negação sem
`coalesce` (M1) e ela é pega. Fonte: `CHANGELOG.md` § 2026-09-23 (3).
