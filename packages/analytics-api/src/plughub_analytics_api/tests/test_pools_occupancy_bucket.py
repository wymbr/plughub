"""
test_pools_occupancy_bucket.py — P3 (`bucket=15min`) + o vazamento de marcadores.

**P3 é leitura pura.** O grão gravado em `pool_occupancy_peaks` é de 1 MINUTO, então
qualquer agregação maior é retroativa e não exige escritor novo. `max` re-agrega picos
corretamente — máximo de máximos É o máximo. O que nunca seria válido é SOMAR buckets,
pela mesma razão que somar pools não é: pico não é grandeza aditiva.

**E um defeito que o P3 encontrou.** O `WHERE` excluía os marcadores por LISTA
(`'__total__','__reserved__','__shared__','__buffer__'`). A F4c acrescentou linhas
`__capacity_{kind}__` e elas passaram a entrar em `series`/`by_pool` **como se fossem
pools** — a Analytics exibiria um pool chamado `__capacity_human__`, com o `headroom` e
a `utilization` calculados em cima. Ninguém viu porque nenhum teste olhava a lista de
pools do relatório, e na tela um pool a mais entre 44 não salta aos olhos.

Testes de unidade sobre `_fetch_pools_occupancy` com um cliente ClickHouse FALSO que
captura o SQL. O que se julga é a query construída — não há Redis nem CH aqui.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from plughub_analytics_api.reports_query import _fetch_pools_occupancy


class _FakeResult:
    def __init__(self, rows, cols):
        self.result_rows   = rows
        self.column_names  = cols


class _FakeClient:
    """Captura todo SQL executado e devolve uma linha plausível para cada consulta."""
    def __init__(self) -> None:
        self.queries: list[str] = []

    def query(self, sql: str, parameters=None):
        self.queries.append(sql)
        if "GROUP BY bucket, pool_id" in sql and "admitted" in sql:
            return _FakeResult(
                [[datetime(2026, 8, 2, 19, 15), "retencao_humano", 2, 3, 0]],
                ["bucket", "pool_id", "peak_concurrency", "capacity", "admitted"],
            )
        if "GROUP BY pool_id" in sql:
            return _FakeResult(
                [["retencao_humano", 2, 3]],
                ["pool_id", "peak_concurrency", "capacity"],
            )
        if "GROUP BY bucket, pool_id" in sql:      # admissão (7b)
            return _FakeResult([], ["bucket", "pool_id", "used", "cap"])
        if "GROUP BY bucket" in sql:               # total_series
            return _FakeResult(
                [[datetime(2026, 8, 2, 19, 15), 2, 356]],
                ["bucket", "peak_concurrency", "capacity"],
            )
        return _FakeResult([[2, 356]], ["peak_concurrency", "capacity"])


def _run(bucket: str, accessible=None) -> _FakeClient:
    client = _FakeClient()
    _fetch_pools_occupancy(
        client, "plughub_demo", "tenant_demo",
        "2026-08-02 00:00:00", "2026-08-03 00:00:00",
        None, bucket, accessible,
    )
    return client


# ── 1. P3 — o bucket de 15 minutos ────────────────────────────────────────────

@pytest.mark.parametrize("bucket, esperado", [
    ("15min", "toStartOfInterval(minute, INTERVAL 15 MINUTE)"),
    ("hour",  "toStartOfHour(minute)"),
    ("day",   "toStartOfDay(minute)"),
])
def test_bucket_function_matches_the_requested_granularity(bucket, esperado):
    """Cada bucket produz a função de agregação correspondente, em TODAS as séries."""
    client = _run(bucket)
    series_qs = [q for q in client.queries if "AS bucket" in q]
    assert series_qs, "nenhuma query de série foi construída"
    for q in series_qs:
        assert esperado in q, (
            f"bucket={bucket}: esperava `{esperado}` na query, veio:\n{q}"
        )


def test_unknown_bucket_falls_back_to_hour_instead_of_breaking():
    """Valor não previsto degrada para `hour` — e não monta SQL inválido.

    A rota já valida com `pattern`, mas esta função é chamada direto por testes e
    poderia sê-lo por outro caller; interpolar o valor cru no SQL seria injeção de
    identificador, não degradação.
    """
    client = _run("xyz")
    for q in [q for q in client.queries if "AS bucket" in q]:
        assert "toStartOfHour(minute)" in q
        assert "xyz" not in q


# ── 2. Os marcadores não são pools ────────────────────────────────────────────

def test_capacity_marker_rows_are_excluded_from_the_pool_series():
    """As linhas `__capacity_{kind}__` (F4c) não podem aparecer como POOL.

    Com a exclusão por LISTA elas entravam em `series`/`by_pool`: a Analytics mostraria
    um "pool" `__capacity_human__` com headroom e utilization calculados. A exclusão por
    PREFIXO cobre também o próximo marcador que alguém adicionar sem lembrar do `WHERE`.
    """
    client = _run("hour")
    pool_qs = [q for q in client.queries
               if "AS peak_concurrency" in q and "'__total__'" not in q
               and "'__reserved__'" not in q]
    assert pool_qs, "nenhuma query por pool foi construída"
    for q in pool_qs:
        assert "NOT startsWith(pool_id, '__')" in q, (
            "a série por pool não exclui os marcadores por prefixo — linhas agregadas "
            f"vão aparecer como pools:\n{q}"
        )


def test_marker_series_still_read_their_own_rows():
    """A exclusão por prefixo vale para a série de POOLS, não para quem lê marcador.

    O simétrico importa: se o filtro vazasse para as queries de `__total__` e de
    admissão, elas parariam de devolver qualquer coisa — e o gráfico ficaria vazio sem
    ninguém entender por quê.
    """
    client = _run("hour")
    total_qs = [q for q in client.queries if "'__total__'" in q]
    assert total_qs, "a query do `__total__` sumiu"
    for q in total_qs:
        assert "NOT startsWith(pool_id, '__')" not in q, (
            "o filtro de marcadores vazou para a query que LÊ um marcador — ela nunca "
            f"devolverá linha:\n{q}"
        )


# ── 3. O rótulo descreve o que a query fez ────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("pedido, aplicado", [
    ("15min", "15min"),
    ("xyz",   "hour"),    # inválido → degrada, e o RÓTULO acompanha
    (None,    "hour"),
])
async def test_meta_reports_the_bucket_actually_used(pedido, aplicado):
    """`meta.bucket` é o bucket APLICADO, nunca o pedido.

    Antes o meta ecoava o parâmetro cru: pedir `bucket=xyz` respondia
    `meta.bucket: "xyz"` sobre dados agregados por hora — o rótulo descrevendo algo que
    a query não fez, que é a forma mais barata de um relatório mentir. O caso `15min`
    entra junto para garantir que a correção não passou a devolver `hour` sempre, que
    faria o teste do valor inválido passar por motivo errado.

    Testado em `query_pools_occupancy` (quem valida), não em `_fetch_pools_occupancy`
    (que recebe o valor já resolvido) — asserção no nível de baixo seria tautologia.
    """
    from plughub_analytics_api.reports_query import query_pools_occupancy

    out = await query_pools_occupancy(
        _FakeClient(), "plughub_demo", "tenant_demo",
        from_dt="2026-08-02T00:00:00", to_dt="2026-08-03T00:00:00",
        bucket=pedido,
    )
    assert out["meta"]["bucket"] == aplicado, (
        f"pediu bucket={pedido!r}: os dados foram agregados por {aplicado!r}, mas o "
        f"meta diz {out['meta']['bucket']!r}"
    )
