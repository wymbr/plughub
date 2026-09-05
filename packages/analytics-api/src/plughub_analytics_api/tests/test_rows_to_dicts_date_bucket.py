"""
test_rows_to_dicts_date_bucket.py

`/reports/agent-events/series` devolvia **500 com dado e 200 sem dado**.

A causa e uma assimetria de `datetime`: `date` NAO e subclasse de `datetime` — e o
contrario, `datetime` herda de `date`. `_rows_to_dicts` so tratava `datetime`, entao
a coluna `toDate(emitted_at) AS period` chegava ao `JSONResponse` como objeto `date`
cru e o encoder levantava `TypeError`. As irmas (`/summary`, `/categories`) usam
`DateTime64` e por isso nunca mostraram o defeito.

**O que faz este teste valer:** o modo de falha e invisivel em ambiente vazio — um
smoke test numa instalacao limpa devolve 200 e passa. Por isso cada caso aqui carrega
uma LINHA, e o veredicto e a serializacao de verdade (`json.dumps`), nao a inspecao
do tipo: asserir `isinstance(..., str)` deixaria passar um `repr` qualquer.

Controle positivo obrigatorio: o caso `datetime` continua carimbando UTC. Sem ele,
uma "correcao" que trocasse o ramo de lugar — capturando todo `datetime` no ramo de
`date` — passaria neste arquivo enquanto **apaga a hora** de todo relatorio da casa.
"""
import json
from datetime import date, datetime, timezone

from plughub_analytics_api.reports_query import _rows_to_dicts


class _FakeResult:
    """Minimo do que `clickhouse_connect` entrega: nomes de coluna + linhas."""

    def __init__(self, column_names, result_rows):
        self.column_names = column_names
        self.result_rows = result_rows


def test_date_bucket_survives_json_serialization():
    """`toDate(...)` — o caso que produzia 500. Veredicto = `json.dumps` nao levanta."""
    res = _FakeResult(
        ["period", "category", "count"],
        [[date(2026, 9, 5), "retencao_humano.wrapup.motivo.financeiro", 3]],
    )
    rows = _rows_to_dicts(res)

    # A serializacao E o veredicto: e exatamente o que o JSONResponse faz.
    json.dumps(rows)

    assert rows[0]["period"] == "2026-09-05"
    # Bucket de DIA nao ganha hora inventada: acrescentar "T00:00:00+00:00" faria
    # o eixo do grafico afirmar meia-noite UTC, que e um instante que ninguem mediu.
    assert "T" not in rows[0]["period"]


def test_datetime_ainda_carimba_utc():
    """CONTROLE POSITIVO — sem ele, inverter a ordem dos ramos passaria despercebido."""
    res = _FakeResult(
        ["last_seen"],
        [[datetime(2026, 9, 5, 17, 20, 5)]],  # naive, como o ClickHouse entrega
    )
    rows = _rows_to_dicts(res)

    json.dumps(rows)
    assert rows[0]["last_seen"] == "2026-09-05T17:20:05+00:00"


def test_datetime_com_tz_nao_e_deslocado():
    """Ja aware ⇒ preserva o offset; `replace` cego mudaria o instante."""
    res = _FakeResult(
        ["last_seen"],
        [[datetime(2026, 9, 5, 17, 20, 5, tzinfo=timezone.utc)]],
    )
    rows = _rows_to_dicts(res)

    json.dumps(rows)
    assert rows[0]["last_seen"] == "2026-09-05T17:20:05+00:00"


def test_linha_mista_de_serie_inteira():
    """
    A forma REAL de uma linha de `/agent-events/series`: bucket de dia (`date`) ao
    lado de agregados. Um caso so, com as duas familias juntas, porque o defeito
    aparecia justamente na combinacao — e nao em `date` isolado.
    """
    res = _FakeResult(
        ["period", "category", "category_l1", "count", "total_value", "avg_value"],
        [[date(2026, 9, 5), "retencao_humano.wrapup.servico.cadastro.troca_titularidade",
          "retencao_humano", 1, 1.0, 1.0]],
    )
    rows = _rows_to_dicts(res)

    body = json.dumps({"data": rows})
    assert '"period": "2026-09-05"' in body
    assert rows[0]["count"] == 1
