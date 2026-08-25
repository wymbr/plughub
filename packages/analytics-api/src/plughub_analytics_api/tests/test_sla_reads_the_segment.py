"""
test_sla_reads_the_segment.py — D14 (iii): a LEITURA do alvo de SLA.

────────────────────────────────────────────────────────────────────────────
O QUE ESTE ARQUIVO EXISTE PARA IMPEDIR
────────────────────────────────────────────────────────────────────────────
A (ii) fez `sessions.sla_target_ms` virar **projeção, nunca fonte de cálculo**.
Até aqui essa regra vivia só em PROSA — CLAUDE.md, CHANGELOG e
`conference-mechanics.md` § 41 —, que é a mesma família do DDL de
`participation_intervals`: um comentário que **afirma** um invariante sem
mecanismo que o imponha. O mecanismo é este arquivo.

────────────────────────────────────────────────────────────────────────────
POR QUE A ASSERÇÃO É SOBRE O SQL EXECUTADO, E NÃO SOBRE O FONTE
────────────────────────────────────────────────────────────────────────────
`grep` (ou `inspect.getsource`) conta a MENÇÃO, não a coisa: o comentário que
documenta a migração reescreve `sessions.sla_target_ms` várias vezes logo acima
da query, e um teste de fonte contaria essas linhas como violação — ou, pior,
passaria a contá-las como conformidade se alguém invertesse o sinal.

Estes testes chamam a função e leem a string que ela **passou ao cliente**.
É o artefato executado; comentário nenhum entra nele.

⚠️ Chamam as funções SÍNCRONAS internas (`_fetch_*`), nunca os wrappers `async`.
O wrapper de `query_pools_queue` tem `except Exception → {"error":
"data_unavailable"}`: por ele, um teste quebrado devolveria envelope vazio em
vez de estourar, e "não há dado" é indistinguível de "a query falhou" — é o
mesmo modo de falha já registrado no backlog para a tela.

────────────────────────────────────────────────────────────────────────────
O QUE ESTES TESTES **NÃO** PROVAM
────────────────────────────────────────────────────────────────────────────
Que duas esperas da mesma sessão são julgadas contra alvos DIFERENTES. Isso é
agregação, acontece dentro do ClickHouse, e um mock devolve o que se mandou —
asserir sobre ele seria medir a fixture. A população discriminante foi medida e
**não existe no ambiente** (`q_sla_source_delta.py`, 2026-08-25: `discord = 0`),
então quem prova essa metade é o gate `infra/test/gate_sla_segment_target.sh`,
que INSERE o caso no CH. Sem ele, esta suíte passaria idêntica sobre uma
implementação que lesse o alvo certo e o comparasse com a espera errada.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from plughub_analytics_api import sla_source
from plughub_analytics_api.query import _fetch_pool_sla_1h
from plughub_analytics_api.reports_query import _cv_sla_series, _fetch_pools_queue


class _FakeResult:
    def __init__(self, column_names=None, result_rows=None):
        self.column_names = column_names or []
        self.result_rows  = result_rows or []


def _client(*results: _FakeResult) -> MagicMock:
    c = MagicMock()
    c.query.side_effect = list(results) or [_FakeResult()]
    return c


def _sql(client: MagicMock) -> str:
    """Concatena TODA query executada, com o espaço em branco NORMALIZADO.

    Um leitor que 'não lê a sessão' na query principal e a lê numa subquery
    auxiliar tem de reprovar igual — daí concatenar tudo.

    A normalização não é conveniência: sem ela a asserção passa a depender do
    ALINHAMENTO do SQL. `query.py` escreve `AND role         = 'queue'`
    (colunas alinhadas) e `reports_query.py` escreve `AND role = 'queue'` — a
    mesma cláusula, e um teste literal reprova num e passa no outro. Reprovaria
    de novo no dia em que alguém rodasse um formatador, sem defeito nenhum.
    """
    raw = " ".join(str(call.args[0]) for call in client.query.call_args_list)
    return " ".join(raw.split())


# Todas as três leituras, num lugar só — assim um leitor NOVO que nasça lendo a
# sessão reprova sem que ninguém precise lembrar de adicioná-lo aqui... desde
# que seja registrado nesta lista. O guard de aridade que a (ii) deixou em
# `test_segment_sla_column.py` cobre coluna; este cobre LEITOR.
def _run_all() -> dict[str, str]:
    c1 = _client(_FakeResult())
    _fetch_pool_sla_1h(c1, "db", "t")

    c2 = _client(_FakeResult(["date", "eligible", "within_sla"], []))
    _cv_sla_series(c2, "db", "t", "2026-08-01", "2026-09-01", None, None)

    c3 = _client(_FakeResult(), _FakeResult(), _FakeResult())
    _fetch_pools_queue(c3, "db", "t", "2026-08-01", "2026-09-01",
                       None, "hour", None, "live")

    return {"pool_sla_1h": _sql(c1), "cv_sla_series": _sql(c2), "pools_queue": _sql(c3)}


# ── 1. Os três leem o SEGMENTO ────────────────────────────────────────────────

class TestTheThreeReadersMigrated:

    def test_every_reader_selects_from_segments(self):
        for name, sql in _run_all().items():
            assert ".segments" in sql, f"{name} não lê `segments`"
            assert "role = 'queue'" in sql, (
                f"{name} lê `segments` mas não filtra a espera — sem "
                f"`role='queue'` o alvo vem de um segmento qualquer"
            )

    def test_no_reader_takes_the_target_from_the_sessions_table(self):
        """A asserção discriminante da fatia.

        Sobre o código ANTERIOR os três reprovavam: `query.py` e
        `_cv_sla_series` liam `sla_target_ms` direto de `{db}.sessions`, e o
        `_per_wait` o selecionava na subquery de sessão (`SELECT session_id,
        pool_id, opened_at, sla_target_ms FROM sessions`).

        `pools_queue` e `cv_sla_series` **continuam tocando** `sessions` — o
        primeiro para `contacts`/`opened_at`, o segundo para o eixo do overlay —,
        então o teste não pode ser "não menciona sessions". Ele é: nenhuma
        projeção de `sessions` carrega o alvo.
        """
        sqls = _run_all()

        # (a) `pool_sla_1h` não precisa mais da sessão para NADA — a forma
        #     proibida ali é tocar a tabela.
        assert ".sessions" not in sqls["pool_sla_1h"], (
            "pool_sla_1h voltou a ler `sessions`; o dashboard de fila é todo "
            "derivável do segmento de espera"
        )

        # (b) os outros dois seguem tocando `sessions` por motivo legítimo
        #     (`contacts`/`opened_at`, eixo do overlay), então o teste é sobre a
        #     COLUNA na projeção de sessão, não sobre a tabela.
        for name in ("cv_sla_series", "pools_queue"):
            for fragment in ("opened_at, sla_target_ms", "s.sla_target_ms",
                             "ss.sla_target_ms"):
                assert fragment not in sqls[name], (
                    f"{name} voltou a tirar o alvo da SESSÃO ({fragment!r}). "
                    f"`sessions.sla_target_ms` é projeção desde a D14 (ii)."
                )

    def test_no_reader_uses_the_session_wait_column(self):
        """`sessions.wait_time_ms` é a espera da SESSÃO — o par do alvo antigo.

        Ler o alvo do segmento e a espera da sessão seria pior que não migrar:
        compararia a duração de uma passagem com o alvo de outra, e o número
        continuaria saindo, plausível.
        """
        for name, sql in _run_all().items():
            assert "wait_time_ms" not in sql, (
                f"{name} ainda compara contra `sessions.wait_time_ms`"
            )


# ── 2. O corte (b) está aplicado, e é a constante única ───────────────────────

class TestEpochCut:

    def test_every_reader_applies_the_declared_epoch(self):
        for name, sql in _run_all().items():
            assert sla_source.SEGMENT_SLA_EPOCH in sql, (
                f"{name} não aplica o corte da série — sem ele, espera sem alvo "
                f"posterior ao deploy se esconde dentro do histórico pré-produtor"
            )

    def test_the_epoch_has_a_single_definition(self):
        """Se a data for escrita à mão num dos leitores, este teste continua
        verde enquanto os valores coincidirem — e é exatamente aí que nasce a
        terceira cópia que diverge. A guarda real é a cláusula vir do helper."""
        clause = sla_source.segment_sla_epoch_clause("x")
        assert clause == f"x >= '{sla_source.SEGMENT_SLA_EPOCH}'"

    def test_the_epoch_is_not_a_moving_default(self):
        """A época é um INSTANTE MEDIDO (primeira espera carimbada), não
        `now()` nem a data do deploy. Um valor derivado do relógio faria a série
        se re-cortar a cada leitura."""
        assert "now(" not in sla_source.SEGMENT_SLA_EPOCH
        assert sla_source.SEGMENT_SLA_EPOCH == "2026-08-25 00:52:29"


# ── 3. Ausência de alvo é ausência, nunca zero ────────────────────────────────

class TestAbsentTargetStaysAbsent:

    def _by_pool_row(self, row: dict) -> dict:
        cols = list(row)
        c = _client(
            _FakeResult(),                       # s_series
            _FakeResult(),                       # q_series
            _FakeResult(cols, [tuple(row.values())]),   # by_pool
        )
        out = _fetch_pools_queue(c, "db", "t", "2026-08-01", "2026-09-01",
                                 None, "hour", None, "live")
        return out["data"]["by_pool"][0]

    def test_pool_without_stamped_wait_reports_none_not_zero(self):
        """`0` não é "sem alvo" — é o valor que os quatro sites de roteamento
        leem como prioridade absoluta (`sla_urgency > 1.0` sempre, ETA de 0 ms
        publicada ao cliente). O relatório não pode fabricá-lo."""
        r = self._by_pool_row({
            "pool_id": "p", "contacts": 3, "queued": 0, "waits": 0,
            "abandoned": 0, "handoff": 0, "abandon_rate": 0.0,
            "avg_wait_ms": None, "p95_wait_ms": None,
            "sla_target_max": None, "within_sla": 0,
            "sla_eligible": 0, "sla_unstamped": 0,
        })
        assert r["sla_target_ms"] is None, f"alvo ausente virou {r['sla_target_ms']!r}"
        assert r["sla_attainment"] is None, "aderência ausente virou número"

    def test_attainment_is_none_when_nothing_is_eligible(self):
        """Aderência AUSENTE ≠ aderência zero. Um pool que perdeu todas as
        elegíveis no corte tem de sumir da métrica, não aparecer com 0%."""
        r = self._by_pool_row({
            "pool_id": "p", "contacts": 9, "queued": 9, "waits": 9,
            "abandoned": 0, "handoff": 9, "abandon_rate": 0.0,
            "avg_wait_ms": 1000, "p95_wait_ms": 2000,
            "sla_target_max": None, "within_sla": 0,
            "sla_eligible": 0, "sla_unstamped": 9,
        })
        assert r["sla_attainment"] is None
        assert r["sla_unstamped"] == 9


# ── 4. O buraco do TTL vira NÚMERO, não silêncio ──────────────────────────────

class TestUnstampedWaitsAreCounted:

    def test_pools_queue_publishes_the_unstamped_counter(self):
        sql = _run_all()["pools_queue"]
        assert "sla_unstamped" in sql, (
            "sem este contador, espera fechada DEPOIS do deploy e mesmo assim "
            "sem alvo (o `pool_config` expirado) é indistinguível do histórico "
            "pré-produtor — degradação silenciosa"
        )

    def test_unstamped_and_eligible_are_complementary_not_overlapping(self):
        """Os dois predicados dividem a MESMA população (espera concluída,
        pós-época) pelo alvo: `> 0` × `= 0`. Se um dia deixarem de ser
        complementares, o operador soma dois números que se sobrepõem."""
        sql = _run_all()["pools_queue"]
        assert "coalesce(sla_target_ms, 0) > 0" in sql
        assert "coalesce(sla_target_ms, 0) = 0" in sql

    def test_nullable_target_is_compared_through_coalesce(self):
        """Sobre `Nullable`, um predicado com NULL devolve NULL e o `countIf`
        PULA a linha em vez de contá-la como falsa — o denominador se move sem
        nada ficar vermelho. `sla_target_ms > 0` cru é essa armadilha."""
        for name, sql in _run_all().items():
            if "sla_target_ms" not in sql:
                continue
            assert "coalesce(sla_target_ms, 0)" in sql or "coalesce(w.sla_target_ms, 0)" in sql, (
                f"{name} compara Nullable sem coalesce"
            )


# ── 5. O buraco que a D14-i fechou não pode voltar pelo overlay ───────────────

class TestNonWaiterIsNotCompliantByConstruction:

    def test_cv_sla_series_dropped_the_coalesce_that_made_zero_wait_compliant(self):
        """`coalesce(wait_time_ms, 0) <= sla_target_ms` dava 100% de aderência a
        pool com ZERO esperas (medido na D14-i: `limite_entrega`, 37 contatos,
        nenhuma espera, aderência 100%). Verde que não pode ficar vermelho."""
        sql = _run_all()["cv_sla_series"]
        assert "coalesce(wait_time_ms" not in sql
        assert "duration_ms IS NOT NULL" in sql, (
            "o overlay tem de exigir espera CONCLUÍDA; sem isso a espera em "
            "curso volta a ser julgada"
        )
