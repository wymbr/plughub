# Kickoff — Histórico unificado, Fase F1b: `entrou por` (first-write-wins em `sessions.pool_id`)

> Cole isto no início da sessão. **Uma sessão = F1b.** Não abrir UI.
> Desenho: [`../adr/adr-historico-unificado-duas-visoes.md`](../adr/adr-historico-unificado-duas-visoes.md) §D12b ·
> Plano do arco: [`historico-unificado-plano-execucao.md`](historico-unificado-plano-execucao.md).
> Estado do arco em 2026-08-14: **F0 ✅ · F1 ✅ · F2 ✅**. F1b não depende de nenhuma delas.

---

## O que é, em uma frase

**Não é adicionar um campo — é parar de apagar um que já existe.** O channel-gateway resolve o pool
antes de publicar `conversations.inbound`, o valor viaja no evento, e `parse_inbound` já o escreve
(`analytics-api/models.py:124`). Ele é destruído depois pelo `routed`/`queued`/`closed`, porque
`sessions` é `ReplacingMergeTree` de **linha inteira**.

Por que estar em `_IDENTITY_FIELDS` (`consumer.py:112-114`) não bastou:

| Função | Linha | O que faz | Efeito em `pool_id` |
|---|---|---|---|
| `_learn_session_identity` | `:133-136` | `if value: entry[field] = value` | a cache **segue o último** |
| `_inject_session_identity` | `:152-154` | `if not row.get(field)` | só preenche **ausência**, nunca divergência |
| **`opened_at` (o padrão certo)** | `:137-139` e `:155-160` | mínimo vence, sobrescreve a linha | *"A abertura é imutável"* |

Implementar = estender a `opened_at` o mesmo tratamento. **O mecanismo já existe no mesmo arquivo.**

---

## ⚠️ O que este conserto NÃO faz — declarar antes, não descobrir depois

**É forward-only.** A correção age no **ingest**: linhas já gravadas mantêm o pool errado, porque o
`ReplacingMergeTree` conserva a última versão escrita e nada as reescreve. As sessões históricas
divergentes **não vão sarar sozinhas**.

Consequência direta para o gate: **medir sobre sessão NOVA**, criada depois do deploy. Um gate que
contasse a população histórica sairia vermelho para sempre e pareceria "o fix não funcionou" — quando
o que ele estaria medindo é o passado. Backfill, se desejado, é decisão à parte (e provavelmente não
vale: `origin`/retenção mandam não reescrever substrato).

---

## O que medir ANTES de tocar em qualquer coisa

### 1. A BASE, contada — não o delta

Em 2026-08-12 mediu-se **46 divergentes em 314**. Em 2026-08-14 o tenant já tinha **407 sessões**. O
número mudou, e *previsão de delta exige a base contada* — recontar antes de prever qualquer coisa:

```bash
DC="docker compose -f docker-compose.demo.yml"
```

```bash
$DC exec -T clickhouse clickhouse-client -q "
WITH primeiro AS (
  SELECT session_id, argMin(pool_id, started_at) AS p
  FROM plughub_demo.segments FINAL
  WHERE tenant_id='tenant_demo' AND pool_id != ''
  GROUP BY session_id)
SELECT s.pool_id AS sessions_pool, primeiro.p AS primeiro_segmento, count() AS n
FROM plughub_demo.sessions FINAL AS s
INNER JOIN primeiro ON primeiro.session_id = s.session_id
WHERE s.tenant_id='tenant_demo' AND s.pool_id != primeiro.p
GROUP BY sessions_pool, primeiro_segmento
ORDER BY n DESC FORMAT TSV"
```

**Contador-testemunha ao lado** (sem ele, um `0` pode ser "não diverge" ou "o join não casou"):

```bash
$DC exec -T clickhouse clickhouse-client -q "
SELECT count() AS sessoes, countIf(pool_id='') AS sem_pool
FROM plughub_demo.sessions FINAL WHERE tenant_id='tenant_demo' FORMAT TSV"
```

Esperado em 12/08 (para comparação, **não** como previsão): 5 pares somando exato —
`limite_processo→aprovacao_credito` 19 · `wrapup_detached_ia→retencao_humano-int` 12 ·
`sac_ia→retencao_humano` 12 · `formfill_demo_ia→formfill_demo` 2 · `gate_promocao_ia→aprovacao_deploy` 1.
Par **novo** que apareça é sinal de mecanismo novo — investigar antes de codar.

### 2. O preflight de código, casando o TOKEN

```bash
$DC exec -T analytics-api grep -n "if value:" \
  /app/src/plughub_analytics_api/consumer.py
```

> ⚠️ **Confirmar o caminho antes de acreditar no número.** No channel-gateway ele é
> `/app/packages/<pkg>/src/...`, e o kickoff de F0 apontava para `/app/src/...` — o `grep` saía com
> *No such file*, que é **inconclusivo**, não "zero ocorrências". Se der erro, achar com
> `find / -name consumer.py -path "*analytics*"`.

---

## O trabalho

### F1b.1 — o carimbo imutável

Estender a `pool_id`, em `_learn_session_identity` e `_inject_session_identity`, o tratamento que
`opened_at` recebe. Duas decisões de desenho a tomar **explicitamente**, não por acidente:

1. **Qual é o critério de "primeiro"?** `opened_at` usa *o menor timestamp conhecido*. Para o pool não
   há timestamp na entrada da cache — o critério natural é *o primeiro não-vazio aprendido*, o que
   torna a regra **dependente da ordem de chegada dos eventos**. Se isso não bastar, a alternativa é
   carimbar só a partir de `parse_inbound` (o único produtor que sabe que é entrada). **Escolher e
   escrever o porquê.**
2. **`contact_open` escreve `"pool_id": ""` literal** (`models.py:319`). Hoje é inócuo (`if value`
   descarta vazio). Confirmar que continua inócuo sob a regra nova — um "primeiro valor" que aceitasse
   vazio congelaria a sessão sem pool para sempre.

### F1b.2 — a decisão que NÃO é código: quem é a fonte

**Dois leitores já não confiam na coluna e derivam do `segments`:**

| Leitor | O que faz hoje |
|---|---|
| `reports_query.py:707-714` + `:779` | `_fetch_sessions` recupera `argMin(pool_id, started_at)` de `segments` e devolve `COALESCE(NULLIF(s.pool_id,''), _pool.pool_v)` — **a API já não devolve `sessions.pool_id` puro** |
| `reports_query.py:5408` + `:5416` | `_fetch_pools_queue` faz `if(ss.pool_id != '', ss.pool_id, segs.q_pool)` |

Com first-write-wins, esses fallbacks viram **redundantes ou concorrentes**. Deixar os dois é manter
duas fontes para o mesmo fato — o defeito que a fase existe para fechar, um nível acima. **Decidir
qual vence e podar a outra.**

Cuidado registrado: **não derivar do primeiro segmento como alternativa geral** — 5 sessões do
ambiente têm pool e **nenhum** segmento (abandono antes de qualquer agente entrar), que é exatamente
o caso que um relatório de fila precisa ver.

### F1b.3 — o inventário de leitores (o custo real da fase)

`pool_id` de `sessions` é lido em ~40 pontos. Os que **mudam de valor** com o conserto, e portanto
precisam de julgamento explícito antes do deploy:

- `timeseries_query.py:26` — `pool_id` é **dimensão de breakdown** (`_VALID_BREAKDOWN`): a série
  temporal muda de significado. É o efeito desejado, mas tem de ser declarado.
- `display_formatters.py:239-245`, `:292-300`, `:536-543`, `:559-567` · `admin_query.py:166-174` ·
  `sessions.py:108,234,412` · `query.py:237-250` · `dashboard.py:47-52` — agregam/filtram por pool.
  **É aqui que a atribuição de volume por pool passa a mudar de número**: hoje `limite_processo`
  aparece com 1 sessão tendo sido a porta de entrada de 20.
- ABAC: `reports_query.py:601`, `:1001`, `:1157`, `:1277` usam `(pool_id IN (…) OR pool_id = '')` —
  conferir que a mudança não altera **quem vê o quê** (um contato pode passar a cair no escopo de
  outro supervisor).

---

## Gate de aceite

**Sobre sessão NOVA** (ver a nota forward-only). Sugestão: rodar
`infra/test/smoke_limite_tres_acessos.sh`, que cria um processo com transição de pool, e então:

```bash
# a sessão criada agora tem `entrou por` = o pool de entrada, não o último?
```

Asserção diferencial, **as duas leituras lado a lado**: para a sessão nova,
`sessions.pool_id == argMin(segments.pool_id, started_at)`; para uma sessão histórica divergente
conhecida, a divergência **permanece** (é o comportamento declarado, não regressão).

E os gates que não podem regredir:

```bash
bash infra/test/probe_journey_limite.sh                    # 5/0
bash infra/test/smoke_limite_tres_acessos.sh               # 18/0
bash infra/test/probe_segments_journey_window.sh           # 6/0
```

---

## Fora de escopo nesta sessão

- F3/F4 (UI) · F5 (`ContextStorePersister`).
- Backfill das linhas históricas.
- `journey_merge` no intake de portabilidade.
- A dívida do `/reports/journeys` sem `window_applied` (herdada de F2, item próprio).

## Armadilhas de ambiente

- **e2e apaga `tenant_demo:*` e `session:*` antes de CADA cenário** — não rodar no meio da medição da
  base; ele muda a população que se está contando.
- `up -d --force-recreate` apaga o `pip install` do pytest — reinstalar antes de medir.
- `docker cp` sobrevive a `restart`, não a `up -d`; mudança de código = `build`.
- ClickHouse db = `plughub_demo` · analytics-api na porta **3500**.
- **`jq '.campo // x'` trata `false` como ausente** — mordeu em F2, dentro do próprio gate.
