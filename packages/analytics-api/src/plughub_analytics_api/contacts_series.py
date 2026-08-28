"""
contacts_series.py — a SÉRIE da superfície A (F2 do
`adr-relatorios-duas-superficies-e-lentes.md`).

POR QUE UM ENDPOINT NOVO, E NÃO `/reports/timeseries/*`
-------------------------------------------------------
Os dois `/reports/timeseries/{volume,handle_time}` já existem e são consumidos pelos
dashboards e pela página de avaliação. Eles aceitam **um** filtro de recorte:
`pool_id` — que é a coluna da SESSÃO, isto é, o pool de ENTRADA (D10).

A barra de filtro da superfície A tem doze campos (canal, outcome, status, direção,
`entrou por` × `atendido por`, ANI/DNIS, categoria e tags de insight, agente…). Pendurar
uma lente naqueles endpoints faria o gráfico responder sobre uma população **diferente
da listada**, e sem nada na tela dizendo qual foi respondida: o operador filtra
`channel=voice`, a lista mostra 12 contatos e o gráfico mostra 300. Os dois números
estão certos — para perguntas diferentes. É a família *valor plausível*, na variante
mais cara: nada fica vermelho e o número é usado.

A saída não foi dar filtros próprios à série (isso são dois textos de SQL que divergem
justamente onde o filtro importa), e sim **uma expressão, dois consumidores** —
`_session_conditions`, extraída de `_fetch_sessions` nesta mesma fase. É a forma que a
F4 do `adr-historico-unificado-duas-visoes` deu à direção do acesso.

Os endpoints antigos **ficam**: têm chamadores vivos e um contrato mais simples.

O QUE CADA MÉTRICA MEDE
-----------------------
  `volume`     count() de contatos por bucket. `Σ buckets` = o total da lista sob os
               MESMOS filtros, e essa igualdade é o que o gate assere.
  `duration`   avg(`sessions.handle_time_ms`) — do PRÓPRIO elemento, nunca da soma dos
               segmentos (eles se sobrepõem: `Σ ≥ wall-clock` com conferência,
               `Σ ≤` com lacunas — ver D9 do Arc 19).
  `resources`  os DOIS números da D4 mais o pico simultâneo. Não é uma métrica: são
               três grandezas que respondem à mesma pergunta e não se derivam umas das
               outras — ver `_RESOURCE_SERIES`.

`sample` POR BUCKET, SEMPRE
---------------------------
É o campo `evidence` do contrato de lente virado dado. Sem ele, um bucket de média
0 e um bucket **sem contato nenhum** chegam à tela idênticos, e a série desenha uma
queda que nunca houve. Toda métrica devolve `sample`; a de duração devolve também
quantos contatos da janela ficaram FORA por não terem duração.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from .usage_attribution import USAGE_PRODUCER_EPOCH, attribution_where

logger = logging.getLogger("plughub.analytics.contacts_series")

# ─── vocabulário ──────────────────────────────────────────────────────────────

#: Séries devolvidas por métrica. É a declaração que o `meta` ecoa — a resposta diz o
#: que computou, em vez de deixar a UI adivinhar pelo nome da chave. A seção E de
#: `infra/test/probe_report_surface.sh` compara estas chaves com o contrato de lente
#: em TypeScript; divergir é erro de compilação do gate, não descoberta em produção.
_SERIES: dict[str, list[dict]] = {
    "volume": [
        {"key": "contacts", "format": "count", "aggregation": "sum"},
    ],
    "duration": [
        {"key": "handle_time_ms", "format": "time", "aggregation": "avg"},
    ],
    # D4 — "recursos por contato" são DOIS números, e nenhum deles é o outro:
    #   `resources` (instâncias DISTINTAS)  → o custo: quantos agentes o contato gastou.
    #   `handoffs`  (contagem de segmentos) → as trocas de mão: quantas vezes mudou.
    # Um contato com 2 recursos e 5 segmentos passou pelo mesmo agente mais de uma vez;
    # publicar um só número apagaria essa distinção — que é justamente a que o operador
    # usa para decidir se o problema é volume de agentes ou repique de transferência.
    # `peak` é a terceira: a sobreposição que torna `Σ duration` inválido É a métrica.
    "resources": [
        {"key": "resources", "format": "count", "aggregation": "avg"},
        {"key": "handoffs",  "format": "count", "aggregation": "avg"},
        {"key": "peak",      "format": "count", "aggregation": "avg"},
        {"key": "peak_max",  "format": "count", "aggregation": "max"},
    ],
    # T3 — consumo de LLM atribuído ao CONTATO.
    #
    # `tokens_in`/`tokens_out` são SOMA do bucket (a grandeza é aditiva, ao contrário
    # de duração e recursos); `tokens_per_contact` é a média, e responde outra
    # pergunta: um bucket pode ter o dobro de tokens por ter o dobro de contatos, ou
    # por os contatos custarem o dobro — e só os dois números juntos separam os casos.
    #
    # ⚠️ Entrada e saída NÃO se somam num total: são preços diferentes em todo
    # provedor. Um `tokens_total` seria o número mais fácil de publicar e o menos
    # utilizável — mesma família da soma de licença humana com licença de IA que a
    # admissão recusa.
    "tokens": [
        {"key": "tokens_in",          "format": "count", "aggregation": "sum"},
        {"key": "tokens_out",         "format": "count", "aggregation": "sum"},
        {"key": "tokens_per_contact", "format": "count", "aggregation": "avg"},
    ],
}

CONTACT_METRICS = tuple(_SERIES.keys())


def _default_from() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")


def _default_to() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _clamp_interval(minutes: int) -> int:
    return max(1, min(int(minutes or 60), 1440))


def _meta(metric: str, interval: int, since: str, until: str, **extra) -> dict:
    m = {
        "metric":           metric,
        "interval_minutes": interval,
        "from":             since,
        "to":               until,
        "series":           _SERIES[metric],
    }
    m.update(extra)
    return m


# ─── SQL ──────────────────────────────────────────────────────────────────────

def _selected_sessions_sql(db: str, joins: str, where: str) -> str:
    """
    O conjunto de contatos que a lista mostraria, como subconsulta.

    ⚠️ É o MESMO `where` de `_fetch_sessions` — vem de `_session_conditions`, não de
    uma cópia. O alias `s.` é exigência das condições, que o usam por toda parte.
    """
    return f"""
        SELECT s.session_id AS session_id, s.opened_at AS opened_at,
               s.closed_at AS closed_at, s.handle_time_ms AS handle_time_ms
        FROM {db}.sessions AS s FINAL
        {joins}
        WHERE {where}
    """


def _fetch_volume(client, db, joins, where, params, interval) -> list[dict]:
    sql = f"""
        SELECT toStartOfInterval(opened_at, toIntervalMinute({interval})) AS bucket,
               count() AS contacts
        FROM ({_selected_sessions_sql(db, joins, where)})
        GROUP BY bucket
        ORDER BY bucket ASC
    """
    r = client.query(sql, parameters=params)
    return [dict(zip(r.column_names, row)) for row in r.result_rows]


#: Sufixo dos aliases de agregado. **Não é estilo.**
#:
#: `avg(handle_time_ms) AS handle_time_ms` faz o alias SOMBREAR a coluna que o próprio
#: `avg` lê, e o ClickHouse recusa a query inteira com `ILLEGAL_AGGREGATION` (código
#: 184) — não a coluna, a query. É a regra já escrita no CLAUDE.md (§ Postura de
#: Engenharia), e ela mordeu aqui **as duas** métricas de agregado na primeira
#: execução contra dado real. O modo de falha é o que a torna cara: o `except` do
#: wrapper devolve `data_unavailable` com `buckets: []`, indistinguível de "não há
#: contato" para quem só olha a tela.
#:
#: O nome de contrato é restaurado em Python, onde ele é definido.
_A = "_v"


def _rename(rows: list[dict]) -> list[dict]:
    """Tira o sufixo dos aliases: o SQL precisa dele, o contrato da API não."""
    return [
        {(k[: -len(_A)] if k.endswith(_A) else k): v for k, v in row.items()}
        for row in rows
    ]


def _fetch_duration(client, db, joins, where, params, interval) -> list[dict]:
    # `handle_time_ms > 0` recorta os que não têm duração — contato ainda aberto, e o
    # que a coluna nunca recebeu. Eles NÃO viram zero (isso puxaria a média para baixo
    # com um valor inventado); saem da amostra e são CONTADOS no `meta`.
    sql = f"""
        SELECT toStartOfInterval(opened_at, toIntervalMinute({interval})) AS bucket,
               avg(handle_time_ms) AS handle_time_ms{_A},
               count() AS sample
        FROM ({_selected_sessions_sql(db, joins, where)})
        WHERE handle_time_ms > 0
        GROUP BY bucket
        ORDER BY bucket ASC
    """
    r = client.query(sql, parameters=params)
    return _rename([dict(zip(r.column_names, row)) for row in r.result_rows])


def _fetch_duration_excluded(client, db, joins, where, params) -> int:
    sql = f"""
        SELECT count() FROM ({_selected_sessions_sql(db, joins, where)})
        WHERE handle_time_ms IS NULL OR handle_time_ms <= 0
    """
    r = client.query(sql, parameters=params)
    return int(r.result_rows[0][0]) if r.result_rows else 0


def _fetch_resources(client, db, joins, where, params, interval, tenant_id) -> list[dict]:
    """
    Os três números da D4, por bucket do `opened_at` do contato.

    O pico é uma VARREDURA, não uma contagem: cada segmento vira dois eventos
    (+1 no início, −1 no fim), a soma corrente ordenada dá a ocupação instantânea e o
    máximo dela é o pico. O empate `ts` é ordenado por `delta ASC` de propósito — o −1
    antes do +1 —, senão uma passagem de bastão (A termina no instante em que B começa)
    seria contada como dois agentes simultâneos, e o relatório de simultaneidade
    passaria a medir transferência.

    **Segmento sem fim é fechado no fim da SESSÃO**, não em `now()`. Há 4 deles em
    contato já encerrado nesta instalação (é o defeito de segmento-aberto-para-sempre
    do `conference-mechanics.md` § Problema 34); com `now()` o pico desses contatos
    cresceria **todo dia, sem que nenhum evento acontecesse** — um número que muda
    sozinho é pior que um ausente. Em contato ABERTO o `now()` é o certo: aquele
    recurso está ocupado agora. Os clampados são contados no `meta`.
    """
    sel = _selected_sessions_sql(db, joins, where)
    sql = f"""
        SELECT bucket,
               avg(resources) AS resources{_A},
               avg(handoffs)  AS handoffs{_A},
               avg(peak)      AS peak{_A},
               max(peak)      AS peak_max{_A},
               count()        AS sample
        FROM (
            SELECT c.bucket AS bucket, c.resources AS resources,
                   c.handoffs AS handoffs, p.peak AS peak
            FROM (
                SELECT toStartOfInterval(sel.opened_at, toIntervalMinute({interval})) AS bucket,
                       sel.session_id AS session_id,
                       uniqExact(g.instance_id) AS resources,
                       count() AS handoffs
                FROM ({sel}) AS sel
                INNER JOIN {db}.segments AS g FINAL ON g.session_id = sel.session_id
                WHERE g.tenant_id = {{tenant_id:String}}
                GROUP BY bucket, session_id
            ) AS c
            INNER JOIN (
                SELECT session_id, max(running) AS peak FROM (
                    SELECT session_id, ts, delta,
                           sum(delta) OVER (PARTITION BY session_id
                                            ORDER BY ts ASC, delta ASC
                                            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                                           ) AS running
                    FROM (
                        SELECT sel.session_id AS session_id,
                               g.started_at AS ts, toInt8(1) AS delta
                        FROM ({sel}) AS sel
                        INNER JOIN {db}.segments AS g FINAL ON g.session_id = sel.session_id
                        WHERE g.tenant_id = {{tenant_id:String}}
                        UNION ALL
                        SELECT sel.session_id AS session_id,
                               coalesce(g.ended_at, sel.closed_at, now64(3)) AS ts,
                               toInt8(-1) AS delta
                        FROM ({sel}) AS sel
                        INNER JOIN {db}.segments AS g FINAL ON g.session_id = sel.session_id
                        WHERE g.tenant_id = {{tenant_id:String}}
                    )
                ) GROUP BY session_id
            ) AS p ON p.session_id = c.session_id
        )
        GROUP BY bucket
        ORDER BY bucket ASC
    """
    q = dict(params)
    q["tenant_id"] = tenant_id
    r = client.query(sql, parameters=q)
    return _rename([dict(zip(r.column_names, row)) for row in r.result_rows])


def _fetch_tokens(client, db, joins, where, params, interval, tenant_id) -> list[dict]:
    """
    Consumo de LLM por bucket, atribuído ao CONTATO.

    O join é por `session_id`, e não por `segment_id`, **de propósito**: no nível do
    contato a pergunta é *"quanto este contato custou"*, e todo evento tem sessão
    desde que o produtor existe. O `segment_id` responde *"qual agente gastou"* — é a
    chave da D1 e é ela que o BREAKDOWN usa; exigi-la aqui descartaria em silêncio
    todo evento de caminho que ainda não a propaga, subestimando o custo do contato.

    Sem corte de época: `session_id` sempre viajou. O que a época protege é o
    agrupamento por ATRIBUIÇÃO — ver `usage_attribution`. Aqui ela é RÓTULO, e vai no
    `meta` para a tela poder dizer onde a série começa.
    """
    sel = _selected_sessions_sql(db, joins, where)
    sql = f"""
        SELECT bucket,
               sum(tin)          AS tokens_in{_A},
               sum(tout)         AS tokens_out{_A},
               avg(tin + tout)   AS tokens_per_contact{_A},
               count()           AS sample
        FROM (
            SELECT toStartOfInterval(sel.opened_at, toIntervalMinute({interval})) AS bucket,
                   sel.session_id AS session_id,
                   sumIf(u.quantity, u.dimension = 'llm_tokens_input')  AS tin,
                   sumIf(u.quantity, u.dimension = 'llm_tokens_output') AS tout
            FROM ({sel}) AS sel
            INNER JOIN {db}.usage_events AS u ON u.session_id = sel.session_id
            WHERE u.tenant_id = {{tenant_id:String}}
              AND u.dimension IN ('llm_tokens_input', 'llm_tokens_output')
            GROUP BY bucket, session_id
        )
        GROUP BY bucket
        ORDER BY bucket ASC
    """
    q = dict(params)
    q["tenant_id"] = tenant_id
    r = client.query(sql, parameters=q)
    return _rename([dict(zip(r.column_names, row)) for row in r.result_rows])


def _fetch_tokens_without(client, db, joins, where, params, tenant_id) -> int:
    """
    Contatos da janela **sem nenhum evento de token**.

    É o número que faz a época aparecer sem ela precisar ser explicada: enquanto o
    produtor for novo, quase toda a população cai aqui, e a tela diz *"N de M contatos
    sem consumo registrado"* em vez de desenhar uma linha rasa que se lê como
    "gastamos pouco". Depois que a série amadurecer, o mesmo número passa a significar
    outra coisa — contato que de fato não usou IA —, e continua sendo o certo a mostrar.
    """
    sql = f"""
        SELECT count() FROM ({_selected_sessions_sql(db, joins, where)}) AS sel
        WHERE sel.session_id NOT IN (
            SELECT session_id FROM {db}.usage_events
            WHERE tenant_id = {{tenant_id:String}}
              AND dimension IN ('llm_tokens_input', 'llm_tokens_output')
        )
    """
    q = dict(params)
    q["tenant_id"] = tenant_id
    r = client.query(sql, parameters=q)
    return int(r.result_rows[0][0]) if r.result_rows else 0


def _fetch_tokens_unattributed(client, db, params, tenant_id) -> tuple[int, bool]:
    """
    Eventos **pós-época** sem `segment_id` — a segunda ausência que a
    `usage_attribution` existe para separar da primeira.

    Pré-época é história e não se conserta. Pós-época é DEFEITO de chamador (um
    caminho novo que não propaga a chave), e sem este contador ele fica invisível
    dentro da história — exatamente como o `sla_target_ms` mascarava o `pool_config`
    expirado até a D14 (iii) separar os dois.

    Deliberadamente **não** recortado pelos filtros da tela: é saúde do PRODUTOR, não
    do recorte que o operador pediu. Recortá-lo faria o defeito sumir ao filtrar.

    ⚠️ **A CONTAGEM SOZINHA MENTE NO DIA DA ÉPOCA**, e isto foi medido: a época tem
    granularidade de DIA, então os eventos emitidos algumas horas antes de a coluna
    existir são "pós-época" pelo calendário e história pelos fatos. Na primeira
    execução ela acusou **8 defeitos que não existem**.

    O discriminador é ORDEM, não data — a mesma correção que a seção C do
    `probe_llm_call_paths.sh` já tinha exigido na T2, pelo mesmo motivo: se o evento
    sem chave mais recente é ANTERIOR ao atribuído mais recente, o produtor está
    carimbando e o que sobrou é história. Devolve, então, DOIS fatos: quantos são, e
    se ainda estão chegando. Publicar só o primeiro seria um número que parece
    resultado — a família que este arco inteiro persegue.
    """
    sql = f"""
        SELECT countIf(segment_id = '')                            AS n_bad,
               toUnixTimestamp(maxIf(timestamp, segment_id =  '')) AS last_bad,
               toUnixTimestamp(maxIf(timestamp, segment_id != '')) AS last_ok
        FROM {db}.usage_events
        WHERE tenant_id = {{tenant_id:String}}
          AND dimension IN ('llm_tokens_input', 'llm_tokens_output')
          AND {attribution_where()}
    """
    r = client.query(sql, parameters={"tenant_id": tenant_id})
    if not r.result_rows:
        return 0, False
    n_bad, last_bad, last_ok = (int(v or 0) for v in r.result_rows[0])
    return n_bad, bool(n_bad and last_bad > last_ok)


def _fetch_resources_without_segments(client, db, joins, where, params, tenant_id) -> int:
    """Contatos da janela que **não têm segmento nenhum** — abandonados antes de
    qualquer agente atender.

    Eles saem da média de propósito: um contato que não consumiu recurso não é uma
    amostra de "quantos recursos um contato consome", e incluí-lo como zero puxaria a
    média para baixo por uma razão que não é sobre alocação. Mas a diferença precisa
    aparecer: medido nesta instalação, `volume` = 881 e `resources` = 861. Sem este
    número, os 20 somem, e dois totais diferentes na mesma tela viram bug aparente.
    """
    sql = f"""
        SELECT count() FROM ({_selected_sessions_sql(db, joins, where)}) AS sel
        WHERE sel.session_id NOT IN (
            SELECT session_id FROM {db}.segments FINAL WHERE tenant_id = {{tenant_id:String}}
        )
    """
    q = dict(params)
    q["tenant_id"] = tenant_id
    r = client.query(sql, parameters=q)
    return int(r.result_rows[0][0]) if r.result_rows else 0


def _fetch_resources_clamped(client, db, joins, where, params, tenant_id) -> int:
    sql = f"""
        SELECT count()
        FROM ({_selected_sessions_sql(db, joins, where)}) AS sel
        INNER JOIN {db}.segments AS g FINAL ON g.session_id = sel.session_id
        WHERE g.tenant_id = {{tenant_id:String}}
          AND g.ended_at IS NULL AND sel.closed_at IS NOT NULL
    """
    q = dict(params)
    q["tenant_id"] = tenant_id
    r = client.query(sql, parameters=q)
    return int(r.result_rows[0][0]) if r.result_rows else 0


# ─── breakdown: quem gastou, de qual conta, com qual modelo (T3) ─────────────

def _fetch_token_breakdown(client, db, joins, where, params, tenant_id, limit) -> list[dict]:
    """
    A metade da pergunta que a SÉRIE não responde.

    A série diz *quanto* e *quando*. Esta diz **quem** (o pool que atendeu o segmento),
    **de qual conta** e **como** (modelo). São as três dimensões que a revisão pediu
    nominalmente — *"quem usou quanto de qual conta, quando e como"* — e nenhuma delas
    é derivável das outras.

    ⚠️ **Aqui o join É por `segment_id`** (D1), ao contrário da série. É a diferença
    entre as duas perguntas: o custo pertence ao contato, mas *quem gastou* é fato do
    segmento — e o `pool_id` da SESSÃO é o de ENTRADA (D10), então creditar por ele
    daria o gasto do especialista de IA ao pool onde o contato começou.

    ⚠️ **Corta na época de atribuição.** Sem o corte, todo evento pré-T2 entraria num
    balde `''` indistinguível do defeito que o `unattributed_events` conta.

    `account_config_id` × `account_key_id`: o primeiro sobrevive à rotação de chave, o
    segundo não. Os dois viajam porque respondem a perguntas diferentes (custo por
    conta × depuração de rate-limit). `model_profile` × `model_id` é o par que
    diagnostica fallback — *"pedi `balanced` e veio outro"*; só um dos dois não diz nada.
    """
    sel = _selected_sessions_sql(db, joins, where)
    sql = f"""
        SELECT
            ifNull(any(g.pool_id), '')       AS pool_id{_A},
            u.account_config_id              AS account_config_id{_A},
            u.model_id                       AS model_id{_A},
            u.model_profile                  AS model_profile{_A},
            u.source                         AS source{_A},
            sumIf(u.quantity, u.dimension = 'llm_tokens_input')  AS tokens_in{_A},
            sumIf(u.quantity, u.dimension = 'llm_tokens_output') AS tokens_out{_A},
            uniqExact(u.session_id)          AS sessions{_A},
            uniqExact(u.event_id)            AS events{_A}
        FROM {db}.usage_events AS u
        INNER JOIN ({sel}) AS sel ON sel.session_id = u.session_id
        LEFT JOIN (
            SELECT segment_id, pool_id FROM {db}.segments FINAL
            WHERE tenant_id = {{tenant_id:String}}
        ) AS g ON g.segment_id = u.segment_id
        WHERE u.tenant_id = {{tenant_id:String}}
          AND u.dimension IN ('llm_tokens_input', 'llm_tokens_output')
          AND {attribution_where('u.timestamp')}
        GROUP BY account_config_id{_A}, model_id{_A}, model_profile{_A}, source{_A}
        ORDER BY tokens_in{_A} DESC
        LIMIT {int(limit)}
    """
    q = dict(params)
    q["tenant_id"] = tenant_id
    r = client.query(sql, parameters=q)
    return _rename([dict(zip(r.column_names, row)) for row in r.result_rows])


async def query_token_breakdown(
    client: Any, db: str, tenant_id: str, *,
    since: str, until: str, joins: str, where: str, params: dict, limit: int = 100,
) -> dict:
    try:
        rows = await asyncio.to_thread(
            _fetch_token_breakdown, client, db, joins, where, params, tenant_id, limit
        )
        return {
            "data": rows,
            "meta": {
                "from": since, "to": until,
                "attribution_epoch": USAGE_PRODUCER_EPOCH,
                # Linha com `pool_id` vazio = evento cujo `segment_id` não casou com
                # segmento nenhum. Nomeado, e não escondido num "—": é sintoma de
                # chave não propagada, que é o mesmo defeito do `unattributed_events`
                # visto do outro lado.
                "rows_without_pool": sum(1 for r in rows if not r.get("pool_id")),
            },
        }
    except Exception as exc:
        logger.warning("query_token_breakdown failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "meta": {"from": since, "to": until}, "error": "data_unavailable"}


# ─── entrada ──────────────────────────────────────────────────────────────────

def _to_buckets(rows: list[dict], metric: str) -> list[dict]:
    keys = [s["key"] for s in _SERIES[metric]]
    out = []
    for row in rows:
        bucket = row.get("bucket")
        values = {k: (float(row[k]) if row.get(k) is not None else None) for k in keys if k in row}
        out.append({
            "bucket": bucket.isoformat() if hasattr(bucket, "isoformat") else str(bucket),
            "values": values,
            # `volume` não tem amostra separada do próprio valor: a contagem É a
            # amostra. Repeti-la evita que a UI precise de um caso especial.
            "sample": int(row.get("sample", row.get("contacts", 0)) or 0),
        })
    return out


async def query_contacts_series(
    client:    Any,
    db:        str,
    tenant_id: str,
    *,
    metric:   str,
    interval: int = 60,
    since:    str,
    until:    str,
    joins:    str,
    where:    str,
    params:   dict,
) -> dict:
    """
    Série da superfície A sobre a população que a lista mostraria.

    Recebe `joins`/`where`/`params` JÁ construídos pelo chamador a partir de
    `_session_conditions` — não os reconstrói. É o que garante que a lente e a lista
    respondam sobre o mesmo conjunto; deixá-la montar o próprio `where` reabriria
    exatamente a divergência que ela existe para fechar.
    """
    iv = _clamp_interval(interval)
    if metric not in _SERIES:
        return {"buckets": [], "meta": _meta("volume", iv, since, until), "error": "unknown_metric"}

    try:
        if metric == "volume":
            rows = await asyncio.to_thread(_fetch_volume, client, db, joins, where, params, iv)
            buckets = _to_buckets(rows, metric)
            total = sum(b["sample"] for b in buckets)
            return {"buckets": buckets, "meta": _meta(metric, iv, since, until, total=total)}

        if metric == "duration":
            # ⚠️ SEQUENCIAL, e não `asyncio.gather`. O cliente do ClickHouse é UM por
            # request (`store.new_client()`) e recusa consultas concorrentes na mesma
            # sessão: *"Attempt to execute concurrent queries within the same session"*.
            # A primeira versão usava `gather` e as duas métricas de duas queries
            # devolviam `total: 0` — que na tela é indistinguível de "não há contato".
            # Paralelizar exigiria um cliente por thread, e o ganho não paga o risco:
            # são duas queries curtas sobre a mesma janela.
            rows = await asyncio.to_thread(_fetch_duration, client, db, joins, where, params, iv)
            excluded = await asyncio.to_thread(
                _fetch_duration_excluded, client, db, joins, where, params
            )
            buckets = _to_buckets(rows, metric)
            return {
                "buckets": buckets,
                "meta": _meta(
                    metric, iv, since, until,
                    total=sum(b["sample"] for b in buckets),
                    # Nomeado, não omitido: sem este número, "a média caiu" e "metade
                    # dos contatos ainda está aberta" são a mesma tela.
                    without_duration=excluded,
                ),
            }

        if metric == "tokens":
            rows = await asyncio.to_thread(
                _fetch_tokens, client, db, joins, where, params, iv, tenant_id
            )
            without = await asyncio.to_thread(
                _fetch_tokens_without, client, db, joins, where, params, tenant_id
            )
            unattributed, in_flight = await asyncio.to_thread(
                _fetch_tokens_unattributed, client, db, params, tenant_id
            )
            buckets = _to_buckets(rows, metric)
            return {
                "buckets": buckets,
                "meta": _meta(
                    metric, iv, since, until,
                    total=sum(b["sample"] for b in buckets),
                    # Enquanto o produtor for novo, quase toda a população cai aqui — e
                    # é isso que impede a série rasa de ser lida como "gastamos pouco".
                    without_tokens=without,
                    # RÓTULO, não predicado: a série não é cortada aqui (não há nada
                    # antes), mas a tela precisa poder dizer onde ela começa.
                    series_starts_at=USAGE_PRODUCER_EPOCH,
                    # Saúde do PRODUTOR: evento pós-época sem chave de atribuição é
                    # defeito de chamador, e some dentro da história se não for contado.
                    unattributed_events=unattributed,
                    # ⚠️ E a CONTAGEM sozinha mente no dia da época — o corte é por DIA
                    # e não separa o que veio horas antes da coluna existir. Só a ORDEM
                    # separa: `true` = ainda estão chegando (defeito EM CURSO);
                    # `false` com contagem > 0 = história que não se conserta.
                    unattributed_in_flight=in_flight,
                ),
            }

        # Sequencial pela mesma razão do ramo de duração acima.
        rows = await asyncio.to_thread(
            _fetch_resources, client, db, joins, where, params, iv, tenant_id
        )
        clamped = await asyncio.to_thread(
            _fetch_resources_clamped, client, db, joins, where, params, tenant_id
        )
        no_seg = await asyncio.to_thread(
            _fetch_resources_without_segments, client, db, joins, where, params, tenant_id
        )
        buckets = _to_buckets(rows, metric)
        return {
            "buckets": buckets,
            "meta": _meta(
                metric, iv, since, until,
                total=sum(b["sample"] for b in buckets),
                # A diferença para o total de `volume`: contato abandonado antes de
                # qualquer agente. Nomeado, senão dois totais na mesma tela viram bug.
                without_segments=no_seg,
                # Segmento que nunca fechou em contato já encerrado. > 0 é defeito de
                # ingestão (Problema 34), não ruído desta lente — e é por estar contado
                # aqui que ele deixa de ser invisível.
                clamped_segments=clamped,
            ),
        }
    except Exception as exc:
        logger.warning(
            "query_contacts_series failed tenant=%s metric=%s: %s", tenant_id, metric, exc
        )
        return {
            "buckets": [],
            "meta": _meta(metric, iv, since, until, total=0),
            "error": "data_unavailable",
        }
