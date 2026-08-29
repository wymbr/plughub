"""
test_customer_voice_rollup.py — Customer Surveys S1 (roll-up por instrumento).

Antes do S1, `query_customer_voice` aplicava `avg(value_num)` a tudo que não fosse
NPS. Isso é numericamente inválido para dois dos cinco instrumentos:
  · FCR é binário 0/1  → "média 0,62" no lugar de "62% resolvido";
  · PMF é categórico 1–3 com a direção INVERTIDA (1 = "very_disappointed" = melhor)
    → "média 1,8", e o indicador que a spec define (% very_disappointed, alvo Sean
    Ellis ≥ 40%) simplesmente não existia.

Estes testes travam o roll-up correto por instrumento e a derivação do catálogo
único (`survey_catalog`), que também corrige a divergência do CES.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from plughub_analytics_api.reports_query import CV_INSTRUMENTS, query_customer_voice


class _FakeResult:
    def __init__(self, column_names, result_rows):
        self.column_names = column_names
        self.result_rows = result_rows


_SIGNAL_COLS = ["date", "n", "avg_value", "promoters", "detractors", "hits"]
_SLA_COLS    = ["date", "eligible", "within_sla"]


def _client(signal_rows) -> MagicMock:
    """Query 1 = série do instrumento; query 2 = overlay de SLA (sempre vazio aqui)."""
    c = MagicMock()
    c.query.side_effect = [
        _FakeResult(_SIGNAL_COLS, signal_rows),
        _FakeResult(_SLA_COLS, []),
    ]
    return c


def _sql(client) -> str:
    return " ".join(str(call.args[0]) for call in client.query.call_args_list)


def _run(metric: str, rows, grain: str = "session") -> dict:
    return query_customer_voice(
        _client(rows), "db", "t", grain, metric, "2026-07-01", "2026-07-31",
    )


# ── FCR: % resolvido, não média ───────────────────────────────────────────────

def test_fcr_rolls_up_as_percent_resolved():
    # 8 respostas, 5 com value_num >= 1 → 62.5% resolvido (não "média 0.62")
    out = _run("fcr", [("2026-07-10", 8, 0.625, 0, 8, 5)])
    assert out["series"][0]["value"] == 62.5
    assert out["summary"]["value"] == 62.5
    assert out["instrument"]["rollup"] == "pct"


# ── PMF: % very_disappointed (Sean Ellis), não média de escala categórica ─────

def test_pmf_rolls_up_as_percent_very_disappointed():
    # 10 respostas, 4 com value_num <= 1 → 40% (o corte da spec)
    out = _run("pmf", [("2026-07-10", 10, 1.8, 0, 10, 4)])
    assert out["series"][0]["value"] == 40.0
    assert out["summary"]["value"] == 40.0
    # o indicador EXIBIDO é "% muito decepcionado": maior é melhor, ainda que o
    # número cru seja invertido (1 = melhor).
    assert out["instrument"]["higher_is_better"] is True


def test_pmf_weights_summary_by_volume_not_by_day():
    # dia 1: 1/1 = 100% · dia 2: 1/9 ≈ 11% → agregado = 2/10 = 20% (não 55%)
    out = _run("pmf", [
        ("2026-07-10", 1, 1.0, 0, 1, 1),
        ("2026-07-11", 9, 2.6, 0, 9, 1),
    ])
    assert out["summary"]["value"] == 20.0
    assert out["summary"]["n"] == 10


# ── NPS e CSAT: comportamento preservado ─────────────────────────────────────

def test_nps_index_unchanged():
    # 10 respostas: 6 promotores, 2 detratores → índice 40
    out = _run("nps", [("2026-07-10", 10, 8.1, 6, 2, 0)])
    assert out["series"][0]["value"] == 40.0
    assert out["instrument"]["rollup"] == "nps_index"


def test_csat_still_averages():
    out = _run("csat", [("2026-07-10", 4, 4.25, 0, 0, 0)])
    assert out["series"][0]["value"] == 4.25
    assert out["instrument"]["rollup"] == "avg"


# ── Catálogo ─────────────────────────────────────────────────────────────────

def test_ces_direction_matches_spec():
    """Nota alta = bom (baixo esforço). O catálogo do relatório dizia o contrário."""
    assert CV_INSTRUMENTS["ces"]["higher_is_better"] is True


def test_pct_condition_comes_from_catalog_and_reaches_the_sql():
    client = _client([])
    query_customer_voice(client, "db", "t", "session", "fcr", "2026-07-01", "2026-07-31")
    sql = _sql(client)
    assert "countIf(value_num >= 1)" in sql
    assert "AS hits" in sql


def test_metrics_without_pct_rollup_get_an_inert_hits_expression():
    # `avg`/`nps_index` não usam `hits`; a expressão precisa ser válida e nunca contar.
    client = _client([])
    query_customer_voice(client, "db", "t", "session", "csat", "2026-07-01", "2026-07-31")
    assert "countIf(1 = 0)" in _sql(client)


def test_unknown_metric_raises():
    try:
        _run("inexistente", [])
    except ValueError as exc:
        assert "unknown metric" in str(exc)
    else:
        raise AssertionError("esperava ValueError para métrica fora do catálogo")


# ══════════════════════════════════════════════════════════════════════════════
# F4 — filtro de pool: UM, VÁRIOS, ou nenhum
# ══════════════════════════════════════════════════════════════════════════════
#
# O parâmetro virou lista quando `/analise/surveys` foi absorvido como o nível de
# RESPOSTAS desta superfície: aquela tela tinha `PoolMultiSelect` e esta aceitava um
# pool só. Unificar a barra com o escalar teria REDUZIDO uma capacidade que funcionava.
#
# Estes testes assertam sobre o SQL EXECUTADO, não sobre o fonte — mesmo mecanismo do
# `test_sla_reads_the_segment.py`, e pela mesma razão: um `grep` no arquivo contaria o
# comentário que documenta a decisão.
#
# ⚠️ O caso do OVERLAY é o que importa mais e é o menos óbvio: a série de survey e a de
# SLA precisam do MESMO recorte. Se só uma filtrasse, o gráfico compararia populações
# diferentes no mesmo eixo — a forma mais convincente de publicar uma correlação que
# não existe. Por isso a asserção é sobre as DUAS queries.


def _run_pools(pool_id, metric: str = "nps") -> MagicMock:
    c = _client([])
    query_customer_voice(
        c, "db", "t", "session", metric, "2026-07-01", "2026-07-31", pool_id=pool_id,
    )
    return c


def test_sem_pool_nao_filtra():
    sql = _sql(_run_pools(None))
    assert "pool_id = {pool_id:String}" not in sql
    assert "pool_ids:Array(String)" not in sql


def test_pool_unico_usa_igualdade():
    c = _run_pools("sac_ia")
    sql = _sql(c)
    assert "pool_id = {pool_id:String}" in sql
    assert "pool_ids:Array(String)" not in sql
    params = [call.kwargs.get("parameters", {}) for call in c.query.call_args_list]
    assert any(p.get("pool_id") == "sac_ia" for p in params)


def test_varios_pools_usam_in():
    c = _run_pools(["sac_ia", "retencao_humano"])
    sql = _sql(c)
    assert "pool_ids:Array(String)" in sql
    params = [call.kwargs.get("parameters", {}) for call in c.query.call_args_list]
    assert any(p.get("pool_ids") == ["sac_ia", "retencao_humano"] for p in params)


def test_lista_vazia_e_o_mesmo_que_sem_filtro():
    """Quem limpou a seleção e quem nunca escolheu querem a mesma resposta.

    Sem isto, `pool_ids=[]` viraria `IN []` — que em ClickHouse não casa com nada, e a
    tela devolveria ZERO sinais para "todos os pools". Um vazio que parece resultado.
    """
    assert "pool_id" not in _sql(_run_pools([]))


def test_string_vazia_nao_vira_pool():
    """`?pool_id=` sem valor é "sem filtro", não "o pool cujo id é a string vazia"."""
    assert "pool_id = {pool_id:String}" not in _sql(_run_pools(""))


def test_overlay_de_sla_recebe_o_mesmo_recorte():
    """As DUAS queries filtram — senão o overlay compara populações diferentes.

    A coluna difere de propósito: a série de survey filtra `pool_id` de
    `session_signal`; o overlay filtra `w.pool_id`, o pool do SEGMENTO onde se esperou
    (D10 — o pool da sessão é o de ENTRADA).
    """
    c = _run_pools(["a", "b"])
    sqls = [str(call.args[0]) for call in c.query.call_args_list]
    assert len(sqls) == 2, "esperado série + overlay"
    assert "pool_id IN {pool_ids:Array(String)}" in sqls[0]
    assert "w.pool_id IN {pool_ids:Array(String)}" in sqls[1]
