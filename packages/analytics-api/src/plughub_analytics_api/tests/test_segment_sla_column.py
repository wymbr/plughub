"""
test_segment_sla_column.py — D14 (ii): a coluna `segments.sla_target_ms`.

Duas proposições, e elas são independentes:

  1. **Alinhamento** — `_SEGMENT_COLS` e `_segment_row` descrevem a MESMA linha.
     O modo de falha de acrescentar uma coluna é inserir o nome numa lista e o
     valor na outra em posições diferentes: o ClickHouse aceita a linha e todos
     os campos a partir dali ficam deslocados. `close_reason` passa a conter o
     `handoff_reason`, e nada fica vermelho. Este guard é barato e cobre qualquer
     coluna futura, não só esta.

  2. **Normalização** — `_wait_sla_target` é o funil por onde TODO escritor de
     `segments` passa, e ele recusa o não-alvo com log em vez de deixá-lo virar
     `NULL` no meio de milhares. Zero, booleano e ilegível têm causas diferentes
     do `None` legítimo, e é por isso que o funil não é um `or None`.
"""
from __future__ import annotations

from ..clickhouse import AnalyticsStore, _segment_row, _wait_sla_target

# A lista de colunas é atributo de classe (`AnalyticsStore._SEGMENT_COLS`), lida
# aqui pelo nome real — nunca copiada para o teste. Uma cópia local passaria a
# concordar consigo mesma e o guard de alinhamento perderia o sentido.
_SEGMENT_COLS_REF = AnalyticsStore._SEGMENT_COLS


def _minimal_segment(**extra) -> dict:
    d = {
        "segment_id":     "seg-1",
        "session_id":     "ses-1",
        "tenant_id":      "tenant_demo",
        "participant_id": "system-queue",
        "pool_id":        "retencao_humano",
        "role":           "queue",
        "agent_type":     "system",
        "joined_at":      "2026-01-01T10:00:00+00:00",
        "timestamp":      "2026-01-01T10:05:00+00:00",
        "type":           "participant_left",
        "duration_ms":    300_000,
    }
    d.update(extra)
    return d


class TestSegmentRowAlignment:
    def test_cols_and_row_have_the_same_arity(self):
        row = _segment_row(_minimal_segment())
        assert len(row) == len(_SEGMENT_COLS_REF), (
            f"_segment_row devolve {len(row)} valores para "
            f"{len(_SEGMENT_COLS_REF)} colunas — a linha inteira desloca a partir "
            "do ponto de divergência, e o ClickHouse aceita sem reclamar"
        )

    def test_sla_target_lands_on_its_own_column(self):
        """
        Alinhamento POSICIONAL, não só de contagem: duas colunas trocadas entre
        si mantêm a aridade e passam no teste acima.
        """
        row = _segment_row(_minimal_segment(sla_target_ms=300_000))
        idx = _SEGMENT_COLS_REF.index("sla_target_ms")
        assert row[idx] == 300_000, (
            f"posição {idx} (sla_target_ms) contém {row[idx]!r} — as listas "
            "divergiram de ordem"
        )


class TestWaitSlaTargetNormalization:
    def test_positive_target_passes_through(self):
        """TESTEMUNHA DE PRESENÇA — sem ela, um funil que devolvesse sempre
        `None` passaria em todos os testes de recusa abaixo."""
        assert _wait_sla_target({"sla_target_ms": 300_000}) == 300_000

    def test_absent_is_none(self):
        assert _wait_sla_target({}) is None

    def test_zero_is_refused(self):
        """`0` não é alvo instantâneo — o contrato do pool é `.positive()`."""
        assert _wait_sla_target({"sla_target_ms": 0}) is None

    def test_negative_is_refused(self):
        assert _wait_sla_target({"sla_target_ms": -1}) is None

    def test_boolean_is_refused(self):
        """`isinstance(True, int)` é `True`: sem a guarda, viraria alvo de 1 ms."""
        assert _wait_sla_target({"sla_target_ms": True}) is None

    def test_unreadable_is_refused(self):
        assert _wait_sla_target({"sla_target_ms": "trezentos mil"}) is None

    def test_numeric_string_is_accepted(self):
        """
        JSON de produtores externos (quality-ingest) pode trazer o número como
        string. Recusá-lo seria perder um alvo legítimo — e a recusa é silenciosa
        do ponto de vista de quem lê a tabela.
        """
        assert _wait_sla_target({"sla_target_ms": "300000"}) == 300_000
