"""
test_contacts_series.py — F2 do `adr-relatorios-duas-superficies-e-lentes.md`.

────────────────────────────────────────────────────────────────────────────
O QUE ESTE ARQUIVO EXISTE PARA IMPEDIR
────────────────────────────────────────────────────────────────────────────
A superfície A passa a ter LENTES ao lado da lista, sob a MESMA barra de filtro.
O defeito que isso convida é barato de produzir e caro de perceber: a série
responde sobre uma população **diferente** da listada. O operador filtra
`channel=voice`, a lista mostra 12 contatos, o gráfico mostra 300 — os dois
números certos, para perguntas diferentes, sem nada na tela dizendo qual foi
respondida. Nada fica vermelho, e o número é usado.

O mecanismo contra isso é `_session_conditions`: **uma expressão, dois
consumidores**. Este arquivo é o que impede que voltem a ser dois textos de SQL.

────────────────────────────────────────────────────────────────────────────
POR QUE A ASSERÇÃO É SOBRE O SQL EXECUTADO
────────────────────────────────────────────────────────────────────────────
Mesma razão de `test_sla_reads_the_segment.py`: `grep` conta MENÇÃO, e os
docstrings desta fase citam `channel`/`pool_id` várias vezes. O que se assere
aqui é a string que a função **passou ao cliente**.

⚠️ A comparação é entre o SQL da LISTA e o SQL da SÉRIE, executados com os
MESMOS argumentos. Asserir só a presença de um literal na série provaria menos:
passaria também se a série tivesse a própria cópia do predicado — que é
exatamente o estado que se quer impedir. A cópia divergiria depois, no filtro
que alguém acrescentasse a um lado só.

────────────────────────────────────────────────────────────────────────────
O QUE ESTES TESTES **NÃO** PROVAM
────────────────────────────────────────────────────────────────────────────
Que os números batem. Um mock devolve o que se mandou; asserir sobre ele seria
medir a fixture. Quem prova a igualdade `Σ buckets == total da lista` contra
dado real é `infra/test/probe_report_surface.sh` (seção F), que consulta o
ClickHouse da instalação.
"""
from __future__ import annotations

import asyncio
import re
from unittest.mock import MagicMock

import pytest

from plughub_analytics_api import reports_query as rq
from plughub_analytics_api.contacts_series import (
    CONTACT_METRICS,
    query_contacts_series,
    query_token_breakdown,
)
from plughub_analytics_api.usage_attribution import (
    USAGE_ATTRIBUTION_EPOCH,
    USAGE_PRODUCER_EPOCH,
)


class _FakeResult:
    def __init__(self, column_names=None, result_rows=None):
        self.column_names = column_names or []
        self.result_rows = result_rows or []


def _client(*results: _FakeResult) -> MagicMock:
    c = MagicMock()
    # `side_effect` como lista se esgota; um callable serve qualquer nº de queries,
    # e o nº de queries é justamente o que muda entre as métricas.
    seq = list(results)
    c.query.side_effect = lambda *a, **k: seq.pop(0) if seq else _FakeResult()
    return c


def _sql(client: MagicMock) -> str:
    """Concatena TODA query executada, com espaço em branco normalizado.

    Normalizar não é conveniência: o predicado da lista é escrito com a indentação
    do `_fetch_sessions` e reaproveitado dentro de uma subconsulta na série, onde o
    alinhamento muda. Sem normalizar, a asserção passaria a medir espaço.
    """
    out = []
    for call in client.query.call_args_list:
        out.append(call.args[0] if call.args else call.kwargs.get("query", ""))
    return re.sub(r"\s+", " ", " ".join(out))


# Filtros usados nos dois lados. Cobrem as três formas de condição que existem:
# igualdade simples (`channel`, `outcome`), subconsulta (`pool_id` → segments) e
# expressão derivada (`direction` → `_DIRECTION_EXPR`, que exige JOIN).
_FILTERS = dict(
    channel="voice",
    outcome="resolved",
    pool_id="retencao_humano",
    entry_pool_id="sac_ia",
    direction="inbound",
    status="closed",
)


def _list_sql() -> str:
    c = _client()
    rq._fetch_sessions(
        c, "db", "tenant_demo", "2026-08-01 00:00:00", "2026-08-28 00:00:00",
        _FILTERS["channel"], _FILTERS["outcome"], None, _FILTERS["pool_id"],
        None, None, None, None, None, None, 1, 100,
        status=_FILTERS["status"],
        entry_pool_id=_FILTERS["entry_pool_id"],
        direction=_FILTERS["direction"],
    )
    return _sql(c)


def _series_sql(metric: str = "volume", **over) -> tuple[str, dict]:
    c = _client()
    kw = dict(_FILTERS)
    kw.update(over)
    data = asyncio.run(rq.query_contacts_series_report(
        c, "db", "tenant_demo",
        from_dt="2026-08-01", to_dt="2026-08-28",
        metric=metric, interval=60, **kw,
    ))
    return _sql(c), data


@pytest.fixture(autouse=True)
def _no_registry(monkeypatch):
    """O conjunto de pools internos vem do agent-registry por HTTP. Fixo aqui: o que
    se testa é o predicado, não a resolução — e deixá-lo variar tornaria a asserção
    dependente de um serviço no ar."""
    async def _fake(_tenant):
        return frozenset({"wrapup_detached_ia"})
    monkeypatch.setattr(rq, "_internal_pools_for", _fake)


# ── 1. O predicado é COMPARTILHADO, não copiado ───────────────────────────────

def test_todo_predicado_da_lista_aparece_na_serie():
    """Cada condição que a lista aplica tem de aparecer na série.

    A asserção é por CONDIÇÃO, não pela string inteira do `WHERE`: a série embrulha
    o predicado numa subconsulta e concatena outras coisas em volta. Comparar textos
    inteiros reprovaria por formatação e ninguém confiaria no resultado.
    """
    lista = _list_sql()
    serie, _ = _series_sql()

    # As marcas de cada família de condição. Se `_session_conditions` deixar de ser
    # a fonte de um dos lados, alguma delas some.
    marcas = [
        # canal EFETIVO, não `s.channel` cru — ver `test_filtro_de_canal_nao_usa_subconsulta_correlacionada`
        "COALESCE(NULLIF(s.channel, ''), _ch.channel_v) = {channel:String}",
        "s.outcome = {outcome:String}",
        "s.pool_id = {entry_pool_id:String}",
        "FROM db.segments FINAL WHERE tenant_id = {tenant_id:String} AND pool_id = {pool_id:String}",
        "s.status = {status:String}",
    ]
    for m in marcas:
        assert m in lista, f"a LISTA deixou de aplicar: {m}"
        assert m in serie, f"a SÉRIE não aplica o filtro da lista: {m}"


def test_expressao_de_direcao_e_a_mesma_nos_dois():
    """A direção é derivada (D8) e é a condição mais fácil de duplicar errado —
    tem expressão longa e exige um JOIN. Se a série a reescrevesse, o texto
    divergiria no primeiro ajuste."""
    lista = _list_sql()
    serie, _ = _series_sql()
    expr = re.sub(r"\s+", " ", rq._DIRECTION_EXPR)
    assert expr in lista
    assert expr in serie
    # E o JOIN que a expressão exige veio junto — sem ele a query nem roda.
    assert "_ch" in serie


def test_serie_sem_filtro_de_direcao_nao_traz_a_expressao():
    """Testemunha negativa do teste acima: sem filtro de direção, a expressão de
    direção não pode aparecer. Se ela aparecesse sempre, o teste anterior passaria
    por acidente e não provaria nada."""
    serie, _ = _series_sql(direction=None)
    assert re.sub(r"\s+", " ", rq._DIRECTION_EXPR) not in serie


def test_join_de_canal_e_exigido_por_DOIS_filtros():
    """O JOIN `_ch` era anexado só com filtro de direção. Desde o conserto do canal
    (F2) ele tem dois exigentes, e esquecer um NÃO dá erro de sintaxe: dá
    `Missing columns: '_ch.channel_v'`, que o `except` do wrapper converte em
    "não há dado" — o mesmo modo de falha que este conserto acabou de fechar."""
    assert rq._needs_ch_join("inbound", None)
    assert rq._needs_ch_join(None, "webchat")
    assert not rq._needs_ch_join(None, None)
    # E o join chega mesmo à série quando só o canal está filtrado.
    serie, _ = _series_sql(direction=None)
    assert "_ch.channel_v" in serie


def test_filtro_de_canal_nao_usa_subconsulta_correlacionada():
    """O defeito que a F2 encontrou vivo, e que 679 testes não pegavam.

    A condição de canal era `EXISTS (SELECT 1 ... WHERE tenant_id = s.tenant_id ...)`
    — subconsulta CORRELACIONADA, que o ClickHouse 23.8 recusa com o código 47. A
    query inteira levantava, o `except` do wrapper devolvia `data_unavailable` com
    `data: []`, e o endpoint respondia **200 com zero linhas**: o seletor de canal
    nunca filtrou, ele ESVAZIAVA. Medido na instalação: 398 sessões `webchat`,
    `channel=webchat` devolvia 0.

    A asserção é sobre a FORMA (nenhum `s.tenant_id` dentro de subconsulta), e não
    sobre o resultado, porque um mock devolve o que se mandou — só o SQL executado
    denuncia a construção. Quem prova o número contra dado real é a seção F de
    `infra/test/probe_report_surface.sh`.
    """
    for sql in (_list_sql(), _series_sql()[0]):
        assert "WHERE tenant_id = s.tenant_id" not in sql
        assert "COALESCE(NULLIF(s.channel, ''), _ch.channel_v) = {channel:String}" in sql


# ── 2. Testemunhas negativas: o filtro FILTRA ─────────────────────────────────

def test_filtro_ausente_nao_vaza_condicao():
    """Sem canal, a condição de canal não pode existir — um filtro que aparece
    quando não foi pedido recorta a população em silêncio."""
    serie, _ = _series_sql(channel=None)
    assert "s.channel = {channel:String}" not in serie


def test_escopo_vazio_devolve_vazio_e_nao_tudo():
    """`accessible_pools=[]` é "nenhum pool", não "sem restrição". A inversão aqui
    é a diferença entre negar tudo e liberar tudo — e ela degrada MUDA."""
    c = _client()
    data = asyncio.run(rq.query_contacts_series_report(
        c, "db", "tenant_demo", metric="volume", accessible_pools=[],
    ))
    assert data["buckets"] == []
    assert c.query.call_count == 0, "consultou o CH com escopo vazio"


def test_scope_all_nao_conta_pool_interno():
    """Agregado nunca conta pool interno (§7.2 do ADR wrap-up). O parâmetro existe
    na barra de filtro da tela e não pode vazar para a contagem: se vazasse, trocar
    de aba mudaria o total sem o operador pedir nada."""
    serie, _ = _series_sql()
    assert "wrapup_detached_ia" in serie, "o escopo de contato não foi aplicado"


# ── 3. `sample` e as ausências nomeadas ───────────────────────────────────────

def test_volume_devolve_sample_por_bucket():
    c = _client(_FakeResult(["bucket", "contacts"], [("2026-08-01T00:00:00", 7)]))
    out = asyncio.run(query_contacts_series(
        c, "db", "t", metric="volume", since="a", until="b",
        joins="", where="1", params={},
    ))
    assert out["buckets"][0]["sample"] == 7
    assert out["buckets"][0]["values"]["contacts"] == 7.0
    assert out["meta"]["total"] == 7


def test_duration_conta_quem_ficou_de_fora():
    """Contato sem duração sai da média (virar zero puxaria a média com um valor
    inventado) — e é CONTADO. Sem o número, "a média caiu" e "metade dos contatos
    ainda está aberta" são a mesma tela."""
    c = _client(
        _FakeResult(["bucket", "handle_time_ms", "sample"], [("2026-08-01T00:00:00", 4200.0, 3)]),
        _FakeResult(["count()"], [(11,)]),
    )
    out = asyncio.run(query_contacts_series(
        c, "db", "t", metric="duration", since="a", until="b",
        joins="", where="1", params={},
    ))
    assert out["meta"]["without_duration"] == 11
    assert out["buckets"][0]["sample"] == 3


def test_resources_conta_segmento_clampado():
    """Segmento sem fim em contato JÁ ENCERRADO é o defeito de ingestão do
    Problema 34. O pico dele é fechado no fim da sessão — com `now()` cresceria
    todo dia sem nenhum evento —, e o número de clampados vai ao `meta` para que o
    defeito deixe de ser invisível."""
    c = _client(
        _FakeResult(
            ["bucket", "resources", "handoffs", "peak", "peak_max", "sample"],
            [("2026-08-01T00:00:00", 1.43, 1.91, 1.23, 3, 932)],
        ),
        _FakeResult(["count()"], [(4,)]),
        _FakeResult(["count()"], [(20,)]),
    )
    out = asyncio.run(query_contacts_series(
        c, "db", "t", metric="resources", since="a", until="b",
        joins="", where="1", params={},
    ))
    assert out["meta"]["clamped_segments"] == 4
    # Contato abandonado antes de qualquer agente não é amostra de "recursos usados",
    # mas a diferença para o total de `volume` tem de aparecer: medido, 881 × 861.
    # Sem o número, dois totais na mesma tela viram bug aparente.
    assert out["meta"]["without_segments"] == 20
    v = out["buckets"][0]["values"]
    assert v["resources"] == 1.43 and v["handoffs"] == 1.91 and v["peak"] == 1.23


def test_pico_fecha_segmento_aberto_no_fim_da_sessao_nao_em_now():
    """A regra acima, no SQL: `coalesce(ended_at, closed_at, now())` — nessa ordem.
    Inverter (ou omitir o `closed_at`) devolve um pico que muda sozinho."""
    c = _client()
    asyncio.run(query_contacts_series(
        c, "db", "t", metric="resources", since="a", until="b",
        joins="", where="1", params={},
    ))
    sql = _sql(c)
    assert "coalesce(g.ended_at, sel.closed_at, now64(3))" in sql


def test_pico_desempata_fim_antes_de_inicio():
    """Passagem de bastão (A termina quando B começa) é UM agente, não dois.
    A ordem `delta ASC` no empate de `ts` é o que garante isso; sem ela a lente de
    simultaneidade passaria a medir transferência."""
    c = _client()
    asyncio.run(query_contacts_series(
        c, "db", "t", metric="resources", since="a", until="b",
        joins="", where="1", params={},
    ))
    assert "ORDER BY ts ASC, delta ASC" in _sql(c)


def test_nenhum_alias_de_agregado_repete_nome_de_coluna():
    """A regra do CLAUDE.md § Postura de Engenharia, virada mecanismo.

    `avg(peak) AS peak` faz o alias SOMBREAR a coluna que o próprio `avg` lê, e o
    ClickHouse recusa a query INTEIRA com `ILLEGAL_AGGREGATION` (código 184). Mordeu
    aqui as duas métricas de agregado na primeira execução contra dado real.

    ⚠️ Este teste existe porque **nenhum mock reproduz o 184**: o fake devolve os
    nomes de coluna que a fixture mandou, então a suíte inteira passava verde sobre
    a query quebrada. Um teste que não pode reprovar compra confiança sem dar nada —
    e este arquivo tinha exatamente isso até esta função. A asserção é sobre a FORMA
    do SQL, que é o único artefato onde a colisão é visível sem um servidor.

    ⚠️⚠️ E a primeira versão desta função media a proposição ERRADA: exigia sufixo em
    TODO alias de agregado, e reprovou `count() AS handoffs` — que não sombreia nada
    (define um nome novo). *Exigir sufixo* e *não sombrear* são fatos diferentes; só
    o segundo é o defeito. O predicado certo é o abaixo: o alias não pode aparecer
    DENTRO da expressão que o agregado lê.
    """
    for metric in ("duration", "resources"):
        c = _client()
        asyncio.run(query_contacts_series(
            c, "db", "t", metric=metric, since="a", until="b",
            joins="", where="1", params={},
        ))
        sql = _sql(c)
        for func, expr, alias in re.findall(
            r"\b(avg|max|min|sum|any|anyIf|uniqExact|count)\(([^)]*)\)\s+AS\s+(\w+)", sql
        ):
            assert not re.search(rf"\b{re.escape(alias)}\b", expr), (
                f"{metric}: `{func}({expr}) AS {alias}` — o alias sombreia a coluna que "
                f"o próprio agregado lê. O ClickHouse recusa a QUERY INTEIRA com "
                f"ILLEGAL_AGGREGATION (184) e o wrapper devolve 'não há dado'"
            )


# ── 3b. Token (T3) — as duas perguntas, e as duas ausências ───────────────────

def test_serie_de_token_atribui_por_SESSAO_e_o_breakdown_por_SEGMENTO():
    """A diferença que define a T3.

    A SÉRIE responde *"quanto este contato custou"* — todo evento tem `session_id`, e
    exigir `segment_id` ali descartaria em silêncio todo evento de caminho que ainda
    não propaga a chave, **subestimando o custo do contato**.

    O BREAKDOWN responde *"quem gastou"* — e aí o `segment_id` é obrigatório (D1),
    porque o `pool_id` da SESSÃO é o de ENTRADA (D10): creditar por ele daria o gasto
    do especialista de IA ao pool onde o contato começou.

    Trocar os dois joins é o erro mais fácil desta fase e o mais silencioso: os dois
    devolvem número, e nenhum fica vermelho.
    """
    c = _client()
    asyncio.run(query_contacts_series(
        c, "db", "t", metric="tokens", since="a", until="b",
        joins="", where="1", params={},
    ))
    serie = _sql(c)
    assert "INNER JOIN db.usage_events AS u ON u.session_id = sel.session_id" in serie
    assert "u.segment_id" not in serie, "a SÉRIE não pode exigir a chave de segmento"

    c2 = _client()
    asyncio.run(query_token_breakdown(
        c2, "db", "t", since="a", until="b", joins="", where="1", params={},
    ))
    bd = _sql(c2)
    assert "g.segment_id = u.segment_id" in bd, "o BREAKDOWN tem de atribuir por segmento"
    assert "any(g.pool_id)" in bd, "o pool vem do SEGMENTO, nunca da sessão"


def test_breakdown_corta_na_epoca_e_a_serie_NAO():
    """As duas posturas, e a razão de serem diferentes.

    Agrupar por ATRIBUIÇÃO não pode misturar *"não media"* (pré-T2, coluna inexistente)
    com *"não informado"* (pós-T2, chamador que esqueceu a chave) — a segunda é defeito
    e sumiria dentro da primeira. Já a série é por sessão, e `session_id` sempre viajou:
    cortá-la ali descartaria consumo real sem ganhar distinção nenhuma.
    """
    c = _client()
    asyncio.run(query_token_breakdown(
        c, "db", "t", since="a", until="b", joins="", where="1", params={},
    ))
    assert USAGE_ATTRIBUTION_EPOCH in _sql(c)

    c2 = _client()
    asyncio.run(query_contacts_series(
        c2, "db", "t", metric="tokens", since="a", until="b",
        joins="", where="1", params={},
    ))
    serie = _sql(c2)
    # A época aparece SÓ na query de saúde do produtor (`unattributed_events`), nunca
    # no recorte da série. Contar as ocorrências separa os dois casos.
    assert serie.count(USAGE_ATTRIBUTION_EPOCH) == 1, (
        "a época entrou no recorte da série — ela é RÓTULO ali, não predicado"
    )


def test_token_conta_as_duas_ausencias_com_nomes_diferentes():
    """`without_tokens` (contato sem consumo) × `unattributed_events` (evento sem
    chave) são fatos de escopos diferentes, e fundi-los num "desconhecido" apagaria o
    único sintoma de chave não propagada que existe."""
    c = _client(
        _FakeResult(
            ["bucket", "tokens_in_v", "tokens_out_v", "tokens_per_contact_v", "sample"],
            [("2026-08-28T00:00:00", 968.0, 78.0, 523.0, 2)],
        ),
        _FakeResult(["count()"], [(879,)]),
        #                    n_bad, last_bad, last_ok  → sem chave é ANTERIOR ⇒ história
        _FakeResult(["n_bad", "last_bad", "last_ok"], [(4, 100, 200)]),
    )
    out = asyncio.run(query_contacts_series(
        c, "db", "t", metric="tokens", since="a", until="b",
        joins="", where="1", params={},
    ))
    assert out["meta"]["without_tokens"] == 879
    assert out["meta"]["unattributed_events"] == 4
    assert out["meta"]["series_starts_at"] == USAGE_PRODUCER_EPOCH
    v = out["buckets"][0]["values"]
    assert v["tokens_in"] == 968.0 and v["tokens_out"] == 78.0


def test_evento_sem_chave_so_e_DEFEITO_se_ainda_estiver_chegando():
    """A contagem sozinha mente no dia da época, e isso foi MEDIDO.

    O corte é por DIA. Os eventos emitidos algumas horas antes de a coluna existir são
    "pós-época" pelo calendário e história pelos fatos — na primeira execução real, a
    contagem acusou **8 defeitos que não existem**.

    Quem separa é a ORDEM, não a data: se o evento sem chave mais recente é ANTERIOR
    ao atribuído mais recente, o produtor está carimbando. É a mesma correção que a
    seção C do `probe_llm_call_paths.sh` já precisara fazer na T2, pelo mesmo motivo —
    e é por isso que os dois fatos viajam separados.
    """
    def _meta_for(last_bad, last_ok):
        c = _client(
            _FakeResult(["bucket", "tokens_in_v", "tokens_out_v",
                         "tokens_per_contact_v", "sample"],
                        [("2026-08-28T00:00:00", 1.0, 1.0, 2.0, 1)]),
            _FakeResult(["count()"], [(0,)]),
            _FakeResult(["n_bad", "last_bad", "last_ok"], [(8, last_bad, last_ok)]),
        )
        return asyncio.run(query_contacts_series(
            c, "db", "t", metric="tokens", since="a", until="b",
            joins="", where="1", params={},
        ))["meta"]

    historia = _meta_for(100, 200)
    assert historia["unattributed_events"] == 8
    assert historia["unattributed_in_flight"] is False, "história virou defeito em curso"

    em_curso = _meta_for(300, 200)
    assert em_curso["unattributed_in_flight"] is True, "defeito em curso passou por história"

    # E zero sem-chave nunca é "em curso", mesmo com timestamps degenerados.
    assert _meta_for(0, 0)["unattributed_events"] == 8


def test_saude_do_produtor_NAO_e_recortada_pelos_filtros_da_tela():
    """`unattributed_events` mede o PRODUTOR, não o recorte pedido. Recortá-lo faria o
    defeito sumir ao filtrar — e quem filtra não está procurando por ele."""
    c = _client()
    asyncio.run(query_contacts_series(
        c, "db", "t", metric="tokens", since="a", until="b",
        joins="", where="s.channel = {channel:String}", params={"channel": "voice"},
    ))
    # A última query executada é a de saúde; ela não pode carregar o predicado da tela.
    ultima = re.sub(r"\s+", " ", c.query.call_args_list[-1].args[0])
    assert "segment_id = ''" in ultima
    assert "s.channel" not in ultima


def test_entrada_e_saida_nao_viram_um_total():
    """Token de entrada e de saída têm preços diferentes em todo provedor. Um
    `tokens_total` seria o número mais fácil de publicar e o menos utilizável — mesma
    família da soma de licença humana com licença de IA que a admissão recusa."""
    from plughub_analytics_api.contacts_series import _SERIES
    chaves = {m["key"] for m in _SERIES["tokens"]}
    assert "tokens_total" not in chaves
    assert {"tokens_in", "tokens_out"} <= chaves


def test_breakdown_conta_linha_sem_pool():
    """`segment_id` que não casa com segmento algum vira `pool_id` vazio — sintoma de
    chave não propagada, visto do lado do breakdown. Nomeado, não escondido num "—"."""
    c = _client(_FakeResult(
        ["pool_id_v", "account_config_id_v", "model_id_v", "model_profile_v",
         "source_v", "tokens_in_v", "tokens_out_v", "sessions_v", "events_v"],
        [("", "acc-1", "claude-sonnet-4-6", "balanced", "reason", 95, 8, 1, 1),
         ("sac_ia", "acc-1", "claude-haiku-4-5", "fast", "sentiment", 226, 18, 1, 1)],
    ))
    out = asyncio.run(query_token_breakdown(
        c, "db", "t", since="a", until="b", joins="", where="1", params={},
    ))
    assert out["meta"]["rows_without_pool"] == 1
    assert out["data"][1]["pool_id"] == "sac_ia"
    assert out["data"][0]["model_profile"] == "balanced"


# ── 4. A borda ────────────────────────────────────────────────────────────────

def test_metrica_desconhecida_nao_vira_volume_em_silencio():
    c = _client()
    out = asyncio.run(query_contacts_series(
        c, "db", "t", metric="custo_em_reais", since="a", until="b",
        joins="", where="1", params={},
    ))
    assert out["error"] == "unknown_metric"
    assert c.query.call_count == 0


def test_meta_declara_o_que_computou():
    """`meta.series` é o contrato da resposta. A UI plota pelo que ele diz, não
    pelo nome da chave — e a seção E do probe compara esta lista com o contrato de
    lente em TypeScript."""
    for metric in CONTACT_METRICS:
        c = _client()
        out = asyncio.run(query_contacts_series(
            c, "db", "t", metric=metric, since="a", until="b",
            joins="", where="1", params={},
        ))
        assert out["meta"]["metric"] == metric
        assert out["meta"]["series"], f"{metric} não declara as próprias séries"
        for s in out["meta"]["series"]:
            assert {"key", "format", "aggregation"} <= set(s)


def test_falha_do_clickhouse_nao_vira_serie_vazia_sem_marcador():
    c = MagicMock()
    c.query.side_effect = RuntimeError("boom")
    out = asyncio.run(query_contacts_series(
        c, "db", "t", metric="volume", since="a", until="b",
        joins="", where="1", params={},
    ))
    assert out["error"] == "data_unavailable"
    assert out["buckets"] == []
