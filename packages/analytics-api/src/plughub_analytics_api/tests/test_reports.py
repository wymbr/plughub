"""
test_reports.py
Unit tests for the Analytics API report query helpers (reports_query.py).

Strategy:
  - Each _fetch_* function is called indirectly via the async query_* wrapper.
  - ClickHouse client is mocked: .query() returns MagicMock with column_names +
    result_rows; two calls per endpoint (count + data).
  - Error paths: client raises → function returns {"data": [], "error": ...}.
  - CSV helper (_to_csv) tested separately.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from ..reports_query import (
    _apply_pool_scope,
    _apply_origin_scope,
    _apply_contact_scope,
    _attach_journey_internal_counts,
    _attach_session_journey_chip,
    _contact_only_predicate,
    _DIRECTION_EXPR,
    SESSION_DIRECTIONS,
    _mark_internal_rows,
    _sessions_meta,
    _clamp_page_size,
    _events_sql_branches,
    _to_csv,
    query_agent_availability,
    query_agent_performance_daily,
    query_agent_performance_report,
    query_contact_insights_report,
    query_agents_compare,
    query_agents_cross,
    query_evaluations_report,
    query_evaluations_summary,
    query_participation_report,
    query_quality_report,
    query_segments_report,
    query_session_complexity,
    query_sessions_report,
    query_usage_report,
)

TENANT = "tenant_telco"
DB     = "plughub"


# ── helpers ───────────────────────────────────────────────────────────────────

def _ch_result(col_names: list[str], rows: list[list]) -> MagicMock:
    r = MagicMock()
    r.column_names = col_names
    r.result_rows  = rows
    return r


def _make_client(*query_results) -> MagicMock:
    """Mock do cliente ClickHouse com resultados sequenciais.

    Esgotar a lista levanta `AssertionError` com o motivo, e não `StopIteration`.
    A diferença não é cosmética: `StopIteration` cruzando um `asyncio.to_thread`
    (que é como `_fetch_sessions` roda) **trava a suíte** em vez de falhar — foi o
    que aconteceu ao adicionar o teste de drill de journey, cujo ramo faz uma
    consulta a mais (`_journey_resolved_map` lê `journey_aliases`) que o teste não
    previa. Um teste que pendura é pior que um que reprova: não diz nada e ainda
    consome a rodada inteira.
    """
    pending = list(query_results)

    def _next(*_args, **_kwargs):
        if not pending:
            raise AssertionError(
                "mock ClickHouse esgotado: o código sob teste fez mais consultas do "
                "que os resultados fornecidos a _make_client(). Verifique se o ramo "
                "exercitado adiciona uma query (ex.: root_session_id → "
                "_journey_resolved_map antes da contagem)."
            )
        return pending.pop(0)

    client = MagicMock()
    client.query = MagicMock(side_effect=_next)
    return client


def _sessions_count_result(n: int, contacts: int | None = None) -> MagicMock:
    """Resultado da query de contagem de `/reports/sessions`.

    **Uma definição, N consumidores.** Desde o ADR §7 a contagem devolve DOIS
    agregados (`count()` + `countIf(<é contato>)`); enquanto cada classe de teste
    montava o seu próprio mock de uma coluna, mudar a query quebrava só as classes
    que alguém lembrasse de atualizar — foi assim que a
    `TestPoolScopedSessionsReport` estourou com `list index out of range`. É a
    mesma lição do `_is_workflow_dispatch_entry` no bridge: duas cópias da mesma
    forma divergem por omissão.

    Sem `contacts`, modela o escopo `contacts` (o `WHERE` já excluiu o pool
    interno, então os dois agregados coincidem)."""
    return _ch_result(
        ["count()", "countIf(contact)"],
        [[n, n if contacts is None else contacts]],
    )


# ── _to_csv ───────────────────────────────────────────────────────────────────

class TestToCsv:
    def test_empty_returns_empty_string(self):
        assert _to_csv([]) == ""

    def test_single_row_has_header(self):
        csv_str = _to_csv([{"a": 1, "b": "x"}])
        lines = csv_str.strip().split("\n")
        assert lines[0] == "a,b"
        assert lines[1] == "1,x"

    def test_multiple_rows(self):
        data = [{"col": "v1"}, {"col": "v2"}]
        csv_str = _to_csv(data)
        lines = csv_str.strip().split("\n")
        assert len(lines) == 3  # header + 2 rows

    def test_special_chars_quoted(self):
        csv_str = _to_csv([{"msg": "hello, world"}])
        assert '"hello, world"' in csv_str


# ── _clamp_page_size ──────────────────────────────────────────────────────────

class TestClampPageSize:
    def test_json_max_1000(self):
        assert _clamp_page_size(5000, False) == 1_000

    def test_csv_max_10000(self):
        assert _clamp_page_size(5000, True) == 5_000   # within csv limit
        assert _clamp_page_size(20000, True) == 10_000

    def test_minimum_is_1(self):
        assert _clamp_page_size(0, False) == 1


# ── query_sessions_report ────────────────────────────────────────────────────

class TestQuerySessionsReport:
    _COLS = ["session_id", "tenant_id", "channel", "pool_id",
             "opened_at", "closed_at", "close_reason", "outcome",
             "wait_time_ms", "handle_time_ms"]

    def _count_result(self, n: int, contacts: int | None = None) -> MagicMock:
        return _sessions_count_result(n, contacts)

    async def test_returns_required_keys(self):
        client = _make_client(
            self._count_result(0),
            _ch_result(self._COLS, []),
        )
        result = await query_sessions_report(client, DB, TENANT)
        assert "data" in result
        assert "meta" in result
        assert result["meta"]["page"] == 1
        assert result["meta"]["total"] == 0

    async def test_data_rows_mapped_correctly(self):
        now = datetime(2026, 4, 21, 12, 0, 0)
        client = _make_client(
            self._count_result(1),
            _ch_result(self._COLS, [
                ["sess-001", TENANT, "webchat", "retencao",
                 now, None, "flow_complete", "resolved", 0, 30000],
            ]),
        )
        result = await query_sessions_report(client, DB, TENANT)
        row = result["data"][0]
        assert row["session_id"]   == "sess-001"
        assert row["channel"]      == "webchat"
        assert row["outcome"]      == "resolved"
        assert row["handle_time_ms"] == 30000
        # datetime should be ISO string
        assert isinstance(row["opened_at"], str)

    async def test_meta_total_matches_count_query(self):
        client = _make_client(
            self._count_result(42),
            _ch_result(self._COLS, []),
        )
        result = await query_sessions_report(client, DB, TENANT, page=1, page_size=10)
        assert result["meta"]["total"] == 42
        assert result["meta"]["page_size"] == 10

    async def test_optional_filters_do_not_crash(self):
        client = _make_client(
            self._count_result(3),
            _ch_result(self._COLS, []),
        )
        result = await query_sessions_report(
            client, DB, TENANT,
            channel="webchat", outcome="resolved",
            close_reason="flow_complete", pool_id="retencao",
        )
        assert result["meta"]["total"] == 3

    async def test_error_returns_empty_with_error_key(self):
        client = MagicMock()
        client.query = MagicMock(side_effect=Exception("ch down"))
        result = await query_sessions_report(client, DB, TENANT)
        assert result["data"] == []
        assert result.get("error") == "data_unavailable"

    async def test_page_size_reflected_in_meta(self):
        """page_size is passed through as-is; clamping is the router's responsibility."""
        client = _make_client(
            self._count_result(0),
            _ch_result(self._COLS, []),
        )
        result = await query_sessions_report(client, DB, TENANT, page_size=50)
        assert result["meta"]["page_size"] == 50

    # ── ADR wrapup-detached-pull §7 ──────────────────────────────────────────

    async def test_default_scope_is_contacts_and_domains_coincide(self):
        """O default tem de continuar sendo o que a E2f fechou. A igualdade
        `total == total_contacts` é o que prova que nada foi relaxado sem pedido."""
        client = _make_client(
            self._count_result(7),
            _ch_result(self._COLS, []),
        )
        result = await query_sessions_report(client, DB, TENANT)
        meta = result["meta"]
        assert meta["scope"] == "contacts"
        assert meta["total"] == meta["total_contacts"] == 7
        assert meta["total_internal"] == 0

    async def test_scope_all_reports_both_domains_separately(self):
        """Com a tabela expandida, o cabeçalho ainda sabe quantos são CONTATO —
        nunca um número só (guardrail §7.2 item 2)."""
        client = _make_client(
            self._count_result(9, contacts=7),
            _ch_result(self._COLS, []),
        )
        result = await query_sessions_report(client, DB, TENANT, scope="all")
        meta = result["meta"]
        assert meta["scope"] == "all"
        assert meta["total"] == 9            # pagina o que está listado
        assert meta["total_contacts"] == 7   # cabeçalho
        assert meta["total_internal"] == 2

    async def test_scope_all_relaxes_pool_rule_in_the_sql(self):
        """A prova de que o parâmetro chega ao SQL: em `all` o `WHERE` da listagem
        não pode conter a exclusão por pool. Sem esta asserção, o teste acima
        passaria mesmo com o `scope` ignorado no caminho da query."""
        client = _make_client(
            self._count_result(1),
            _ch_result(self._COLS, []),
        )
        with patch(
            "plughub_analytics_api.reports_query._internal_pools_for",
            return_value=frozenset({"retencao_humano-int"}),
        ):
            await query_sessions_report(client, DB, TENANT, scope="all")
        listing_sql = client.query.call_args_list[-1].args[0]
        assert "NOT IN ('retencao_humano-int')" not in listing_sql
        # …e a contagem do domínio de contato SEGUE conhecendo o conjunto.
        count_sql = client.query.call_args_list[0].args[0]
        assert "countIf(s.pool_id NOT IN ('retencao_humano-int'))" in count_sql

    async def test_journey_drill_is_exempt_from_the_pool_rule(self):
        """Fatia 4 — `root_session_id` é drill de UM processo, não listagem. A
        sessão de wrap-up pertence àquele processo; escondê-la mentiria sobre a
        composição do que o operador pediu para ver."""
        client = _make_client(
            # O ramo `root_session_id` consulta journey_aliases ANTES da contagem
            # (union-find J3) — sem esta 1ª resposta o mock esgota.
            _ch_result(["source_root", "canonical_root"], []),
            self._count_result(3),
            _ch_result(self._COLS, []),
        )
        with patch(
            "plughub_analytics_api.reports_query._internal_pools_for",
            return_value=frozenset({"retencao_humano-int"}),
        ):
            await query_sessions_report(client, DB, TENANT, root_session_id="root-1")
        listing_sql = client.query.call_args_list[-1].args[0]
        assert "NOT IN ('retencao_humano-int')" not in listing_sql
        assert "root_session_id IN" in listing_sql

    async def test_plain_listing_still_applies_the_pool_rule(self):
        """Controle negativo do teste acima: sem `root_session_id`, e em `contacts`,
        a exclusão TEM de estar lá. Sem este par, a isenção poderia ter vazado para
        a listagem inteira e os dois testes passariam."""
        client = _make_client(
            self._count_result(3),
            _ch_result(self._COLS, []),
        )
        with patch(
            "plughub_analytics_api.reports_query._internal_pools_for",
            return_value=frozenset({"retencao_humano-int"}),
        ):
            await query_sessions_report(client, DB, TENANT)
        listing_sql = client.query.call_args_list[-1].args[0]
        assert "NOT IN ('retencao_humano-int')" in listing_sql

    # ── F4/F3 — direção do acesso (ADR histórico-unificado D8) ───────────────
    #
    # O que estes testes protegem NÃO é "o filtro funciona" — é que a COLUNA e o
    # FILTRO continuem sendo a mesma pergunta. Enquanto a direção foi derivada em
    # TypeScript e o filtro não existia, não havia como divergir; a partir do
    # momento em que passam a ser duas expressões, divergem em silêncio, e o
    # sintoma seria uma linha marcada `interno` aparecendo sob o filtro `inbound`.
    #
    # O teste que pega isso tem de assertar sobre o SQL EXECUTADO — no fonte,
    # `grep` contaria também o comentário que documenta a regra.

    async def test_direction_filter_and_column_are_the_same_expression(self):
        client = _make_client(
            self._count_result(2),
            _ch_result(self._COLS, []),
        )
        await query_sessions_report(client, DB, TENANT, direction="outbound")
        listing_sql = client.query.call_args_list[-1].args[0]
        # Duas ocorrências: a coluna (`AS direction`) e o predicado do WHERE.
        assert listing_sql.count(_DIRECTION_EXPR) == 2, (
            "coluna e filtro deixaram de compartilhar a expressão de direção"
        )
        assert f"{_DIRECTION_EXPR} AS direction" in listing_sql
        assert f"{_DIRECTION_EXPR} = {{direction:String}}" in listing_sql
        assert client.query.call_args_list[-1].kwargs["parameters"]["direction"] == "outbound"

    async def test_unfiltered_listing_still_returns_the_column(self):
        """Controle negativo: sem filtro, a coluna FICA (a Vista Processos separa
        acesso de etapa interna por ela) e o predicado SAI. Sem este par, o teste
        acima passaria com o filtro aplicado sempre."""
        client = _make_client(
            self._count_result(2),
            _ch_result(self._COLS, []),
        )
        await query_sessions_report(client, DB, TENANT)
        listing_sql = client.query.call_args_list[-1].args[0]
        assert f"{_DIRECTION_EXPR} AS direction" in listing_sql
        assert "{direction:String}" not in listing_sql

    async def test_count_query_joins_the_channel_recovery_when_filtering(self):
        """A contagem pagina a lista; se ela resolvesse o canal de outro jeito, o
        total e as linhas responderiam diferente para a MESMA sessão ativa (a que
        o `parse_routed` deixa com `channel=''`) — a paginação passaria a mentir
        exatamente na população que o JOIN existe para não perder."""
        client = _make_client(
            self._count_result(1),
            _ch_result(self._COLS, []),
        )
        await query_sessions_report(client, DB, TENANT, direction="internal")
        count_sql = client.query.call_args_list[0].args[0]
        assert "AS _ch ON _ch.session_id = s.session_id" in count_sql

    async def test_count_query_has_no_extra_join_without_the_filter(self):
        """…e o join NÃO entra fora do filtro: contagem é caminho quente, e um JOIN
        que só o filtro precisa não deve pesar sobre toda listagem."""
        client = _make_client(
            self._count_result(1),
            _ch_result(self._COLS, []),
        )
        await query_sessions_report(client, DB, TENANT)
        count_sql = client.query.call_args_list[0].args[0]
        assert "AS _ch" not in count_sql

    def test_unknown_spawn_reason_is_claimed_by_no_direction(self):
        """A regra que torna `Σ das três ≤ total` verdadeira — e é ela que o gate
        usa para CONTAR a população não classificada em vez de escondê-la num
        balde plausível."""
        assert "ifNull(s.spawn_reason, '') != '', ''," in _DIRECTION_EXPR
        assert set(SESSION_DIRECTIONS) == {"inbound", "outbound", "internal"}


# ── chip de processo: o total e a QUEBRA (2026-08-26) ────────────────────────
#
# O que estes testes protegem não é "o chip conta certo" — é que **o número que o
# operador clica e o cabeçalho aonde ele chega continuem sendo a mesma população**.
# Antes desta fatia o chip publicava `· 5` sob o rótulo "contatos" e a visão 2, para
# onde ele pivota, publicava `3 acessos · 2 etapas internas`: dois números certos,
# uma divergência criada pela F4 ao dar ao cabeçalho um domínio que o chip não tinha.
#
# A garantia é aritmética e vem da expressão ÚNICA: os três baldes saem de `countIf`
# sobre `_DIRECTION_EXPR`, cujo `multiIf` é exaustivo ⇒ `acesso + interna + não
# classificada == total`, sempre. Um teste que só olhasse os campos existirem não
# pegaria a regressão que importa (alguém recortar o total por direção, fazendo o
# chip dizer `·3` e a tela mostrar 5 linhas).

class TestJourneyChipCounts:
    _ALIAS_COLS = ["source_root", "canonical_root"]
    _CHIP_COLS  = ["jid", "n", "n_access", "n_internal", "n_unknown"]

    def _rows(self):
        return [{"session_id": "s1", "root_session_id": "r1"}]

    def test_breakdown_uses_the_same_direction_expression(self):
        """Três `countIf`, uma expressão. Se alguém reescrever a regra aqui, a
        contagem do chip e a coluna `direction` da mesma tela passam a poder
        discordar — e o sintoma seria mudo: um chip `· 3 + 2` sobre uma tabela
        que mostra 4 acessos."""
        client = _make_client(
            _ch_result(self._ALIAS_COLS, []),
            _ch_result(self._CHIP_COLS, [["r1", 5, 3, 2, 0]]),
        )
        _attach_session_journey_chip(client, DB, TENANT, self._rows(), None, None)
        chip_sql = client.query.call_args_list[-1].args[0]
        assert chip_sql.count(_DIRECTION_EXPR) == 3, (
            "a quebra do chip deixou de compartilhar a expressão de direção"
        )

    def test_breakdown_query_joins_the_channel_recovery(self):
        """`_DIRECTION_EXPR` lê `_ch.channel_v`: sem o join ela nem compila, e um
        `s.channel` cru no lugar faria a sessão ATIVA de webhook (a que o
        `parse_routed` deixa com `channel=''`) cair em `inbound` aqui e em
        `internal` na listagem."""
        client = _make_client(
            _ch_result(self._ALIAS_COLS, []),
            _ch_result(self._CHIP_COLS, [["r1", 5, 3, 2, 0]]),
        )
        _attach_session_journey_chip(client, DB, TENANT, self._rows(), None, None)
        chip_sql = client.query.call_args_list[-1].args[0]
        assert "AS _ch ON _ch.session_id = s.session_id" in chip_sql

    def test_the_three_buckets_add_up_to_the_total(self):
        """A invariante que o chip publica. Vale com a classe não classificada
        POVOADA — o ramo que hoje tem 0 sessões no tenant e por isso nunca foi
        exercido em tela."""
        client = _make_client(
            _ch_result(self._ALIAS_COLS, []),
            _ch_result(self._CHIP_COLS, [["r1", 6, 3, 2, 1]]),
        )
        rows = self._rows()
        _attach_session_journey_chip(client, DB, TENANT, rows, None, None)
        r = rows[0]
        assert r["journey_session_count"]       == 6
        assert r["journey_access_count"]        == 3
        assert r["journey_internal_step_count"] == 2
        assert r["journey_unclassified_count"]  == 1
        assert (r["journey_access_count"] + r["journey_internal_step_count"]
                + r["journey_unclassified_count"]) == r["journey_session_count"]

    def test_missing_journey_leaves_all_four_absent(self):
        """Testemunha negativa. Journey sem linha no agregado fica `None` nos
        QUATRO campos — zerar só a quebra desenharia `· 0 + 0` num chip cujo
        total ninguém sabe, que é o valor plausível de sempre."""
        client = _make_client(
            _ch_result(self._ALIAS_COLS, []),
            _ch_result(self._CHIP_COLS, []),
        )
        rows = self._rows()
        _attach_session_journey_chip(client, DB, TENANT, rows, None, None)
        assert rows[0]["journey_session_count"]       is None
        assert rows[0]["journey_access_count"]        is None
        assert rows[0]["journey_internal_step_count"] is None
        assert rows[0]["journey_unclassified_count"]  is None

    def test_query_failure_leaves_all_four_absent(self):
        """Falha da agregação não vira `1` nem `0` em campo nenhum — chip ausente
        e processo-de-um contato precisam continuar distinguíveis."""
        client = MagicMock()
        client.query.side_effect = [
            _ch_result(self._ALIAS_COLS, []),
            RuntimeError("clickhouse down"),
        ]
        rows = self._rows()
        _attach_session_journey_chip(client, DB, TENANT, rows, None, None)
        assert rows[0]["journey_session_count"]      is None
        assert rows[0]["journey_unclassified_count"] is None


# ── query_agents_report — REMOVIDO (2026-07-28) ──────────────────────────────
#
# A função e o endpoint `/reports/agents` saíram junto com a tabela `agent_events`
# (substrato derivado que duplicava `segments`; o endpoint não tinha chamadores).
# Os testes desta classe travavam campos sem equivalente — `routing_mode` era
# write-only e `event_type` virou coluna em `segments` (started_at/ended_at).
#
# Cobertura dos substitutos, no mesmo arquivo: `query_agent_performance_report`
# e `query_agent_performance_daily`, ambos sobre `segments`.


# ── query_quality_report ─────────────────────────────────────────────────────

class TestQueryQualityReport:
    # T11 — modelo Oficial×Operacional (evaluation_finalized). _fetch_quality faz
    # DUAS queries: agregação principal (group_key + N + distribuição) + a
    # distribuição por finalize_reason. Cols batem com os aliases do SELECT.
    _COLS = ["group_key", "n", "finalized_n", "provisional_n",
             "avg_score", "score_high", "score_mid", "score_low"]
    _REASON_COLS = ["reason", "n"]

    async def test_returns_required_keys(self):
        client = _make_client(
            _ch_result(self._COLS, []),
            _ch_result(self._REASON_COLS, []),
        )
        result = await query_quality_report(client, DB, TENANT)
        assert "data" in result
        assert "meta" in result
        assert result["mode"] == "oficial"   # default = invariante (só finalizadas)

    async def test_finalized_row_mapped(self):
        client = _make_client(
            _ch_result(self._COLS, [
                ["camp_sac", 10, 10, 0, 0.82, 7, 2, 1],
            ]),
            _ch_result(self._REASON_COLS, [
                ["evaluator", 8], ["review_upheld", 2],
            ]),
        )
        result = await query_quality_report(client, DB, TENANT, group_by="campaign_id")
        row = result["data"][0]
        assert row["group_key"]   == "camp_sac"
        assert row["avg_score"]   == 0.82
        assert row["finalized_n"] == 10
        assert result["finalize_reasons"] == {"evaluator": 8, "review_upheld": 2}
        assert result["meta"]["total_finalized"] == 10

    async def test_filters_accepted(self):
        client = _make_client(
            _ch_result(self._COLS, []),
            _ch_result(self._REASON_COLS, []),
        )
        result = await query_quality_report(
            client, DB, TENANT,
            mode="operacional",
            group_by="finalize_reason",
            campaign_id="camp_sac",
            finalize_reason="evaluator",
        )
        assert result["mode"] == "operacional"
        assert result["group_by"] == "finalize_reason"

    async def test_error_returns_empty(self):
        client = MagicMock()
        client.query = MagicMock(side_effect=Exception("ch error"))
        result = await query_quality_report(client, DB, TENANT)
        assert result["data"] == []
        assert result.get("error") == "data_unavailable"


# ── query_usage_report ───────────────────────────────────────────────────────

class TestQueryUsageReport:
    _COLS = ["event_id", "tenant_id", "session_id",
             "dimension", "quantity", "source_component", "timestamp"]

    def _count_result(self, n: int) -> MagicMock:
        return _ch_result(["count()"], [[n]])

    async def test_returns_required_keys(self):
        client = _make_client(
            self._count_result(0),
            _ch_result(self._COLS, []),
        )
        result = await query_usage_report(client, DB, TENANT)
        assert "data" in result
        assert "meta" in result

    async def test_usage_row_mapped(self):
        ts = datetime(2026, 4, 21, 8, 0, 0)
        client = _make_client(
            self._count_result(1),
            _ch_result(self._COLS, [
                ["ev-use-001", TENANT, "sess-001",
                 "llm_tokens_input", 1234, "ai-gateway", ts],
            ]),
        )
        result = await query_usage_report(client, DB, TENANT)
        row = result["data"][0]
        assert row["dimension"]        == "llm_tokens_input"
        assert row["quantity"]         == 1234
        assert row["source_component"] == "ai-gateway"
        assert isinstance(row["timestamp"], str)

    async def test_filters_accepted(self):
        client = _make_client(
            self._count_result(10),
            _ch_result(self._COLS, []),
        )
        result = await query_usage_report(
            client, DB, TENANT,
            dimension="llm_tokens_output",
            source_component="ai-gateway",
        )
        assert result["meta"]["total"] == 10

    async def test_error_returns_empty(self):
        client = MagicMock()
        client.query = MagicMock(side_effect=Exception("connection refused"))
        result = await query_usage_report(client, DB, TENANT)
        assert result["data"] == []
        assert result.get("error") == "data_unavailable"

    async def test_page_size_clamped_csv(self):
        client = _make_client(
            self._count_result(0),
            _ch_result(self._COLS, []),
        )
        from ..reports_query import _clamp_page_size
        assert _clamp_page_size(50000, True) == 10_000


# ── query_participation_report ────────────────────────────────────────────────

class TestQueryParticipationReport:
    _COLS = [
        "event_id", "session_id", "tenant_id",
        "participant_id", "pool_id", "agent_type_id",
        "role", "agent_type", "conference_id",
        "joined_at", "left_at", "duration_ms",
        "timestamp",
    ]

    def _count_result(self, n: int) -> MagicMock:
        return _ch_result(["count()"], [[n]])

    async def test_returns_required_keys(self):
        client = _make_client(
            self._count_result(0),
            _ch_result(self._COLS, []),
        )
        result = await query_participation_report(client, DB, TENANT)
        assert "data" in result
        assert "meta" in result
        assert result["meta"]["total"] == 0

    async def test_row_mapped_correctly(self):
        joined = datetime(2026, 4, 21, 10, 0, 0)
        left   = datetime(2026, 4, 21, 10, 3, 0)
        client = _make_client(
            self._count_result(1),
            _ch_result(self._COLS, [[
                "evt-part-001", "sess-001", TENANT,
                "part-agent-001", "retencao_humano", "agente_retencao_v1",
                "primary", "ai", None,
                joined, left, 180000,
                left,
            ]]),
        )
        result = await query_participation_report(client, DB, TENANT)
        row = result["data"][0]
        assert row["event_id"]       == "evt-part-001"
        assert row["participant_id"] == "part-agent-001"
        assert row["role"]           == "primary"
        assert row["duration_ms"]    == 180000
        assert isinstance(row["joined_at"], str)
        assert isinstance(row["left_at"], str)

    async def test_filters_do_not_crash(self):
        client = _make_client(
            self._count_result(5),
            _ch_result(self._COLS, []),
        )
        result = await query_participation_report(
            client, DB, TENANT,
            session_id="sess-001",
            pool_id="retencao_humano",
            agent_type_id="agente_retencao_v1",
            role="primary",
        )
        assert result["meta"]["total"] == 5

    async def test_error_returns_empty_with_error_key(self):
        client = MagicMock()
        client.query = MagicMock(side_effect=Exception("ch timeout"))
        result = await query_participation_report(client, DB, TENANT)
        assert result["data"] == []
        assert result.get("error") == "data_unavailable"


# ── query_segments_report (Arc 5 — ContactSegment) ────────────────────────────

class TestQuerySegmentsReport:
    _COLS = [
        "segment_id", "session_id", "tenant_id",
        "participant_id", "pool_id", "agent_type_id",
        "instance_id", "role", "agent_type",
        "parent_segment_id", "sequence_index",
        "started_at", "ended_at", "duration_ms",
        "outcome", "close_reason", "handoff_reason",
        "issue_status", "conference_id",
    ]

    def _count_result(self, n: int) -> MagicMock:
        r = MagicMock()
        r.result_rows = [[n]]
        return r

    async def test_returns_segment_rows(self):
        from datetime import datetime
        client = _make_client(
            self._count_result(1),
            _ch_result(self._COLS, [[
                "seg-uuid-001", "sess-001", "tenant_telco",
                "agente-001", "retencao_humano", "agente_retencao_v1",
                "agente_retencao_v1-001", "primary", "ai",
                None, 0,
                datetime(2026, 1, 1, 10, 0, 0), None, None,
                "resolved", None, None, None, None,
            ]]),
        )
        result = await query_segments_report(client, DB, TENANT)
        assert result["meta"]["total"] == 1
        row = result["data"][0]
        assert row["segment_id"] == "seg-uuid-001"
        assert row["session_id"] == "sess-001"
        assert row["role"] == "primary"
        assert row["outcome"] == "resolved"
        assert row["sequence_index"] == 0

    async def test_filters_do_not_crash(self):
        client = _make_client(
            self._count_result(3),
            _ch_result(self._COLS, []),
        )
        result = await query_segments_report(
            client, DB, TENANT,
            pool_id="retencao_humano",
            agent_type_id="agente_retencao_v1",
            role="primary",
            outcome="resolved",
        )
        assert result["meta"]["total"] == 3

    async def test_error_returns_empty_with_error_key(self):
        client = MagicMock()
        client.query = MagicMock(side_effect=Exception("ch timeout"))
        result = await query_segments_report(client, DB, TENANT)
        assert result["data"] == []
        assert result.get("error") == "data_unavailable"


# ── query_agent_performance_report (Arc 5 — aggregate) ───────────────────────

class TestQueryAgentPerformanceReport:
    # Aggregate query — one call only (no separate count query)
    _COLS = [
        "agent_type_id", "pool_id", "role",
        "total_sessions", "avg_duration_ms",
        "resolved_count", "escalated_count", "transferred_count",
        "abandoned_count", "timeout_count", "handoff_count",
        "escalation_rate", "handoff_rate",
    ]

    async def test_returns_required_keys(self):
        client = _make_client(_ch_result(self._COLS, []))
        result = await query_agent_performance_report(client, DB, TENANT)
        assert "data" in result
        assert "meta" in result
        assert result["meta"]["total"] == 0

    async def test_data_row_mapped_correctly(self):
        client = _make_client(_ch_result(self._COLS, [[
            "agente_retencao_v1", "retencao_ia", "primary",
            10,       # total_sessions
            35000.0,  # avg_duration_ms
            7,        # resolved_count
            1,        # escalated_count
            1,        # transferred_count
            1,        # abandoned_count
            0,        # timeout_count
            2,        # handoff_count
            0.1,      # escalation_rate  (1/10)
            0.2,      # handoff_rate     (2/10)
        ]]))
        result = await query_agent_performance_report(client, DB, TENANT)
        assert result["meta"]["total"] == 1
        row = result["data"][0]
        assert row["agent_type_id"]   == "agente_retencao_v1"
        assert row["pool_id"]         == "retencao_ia"
        assert row["role"]            == "primary"
        assert row["total_sessions"]  == 10
        assert row["avg_duration_ms"] == 35000.0
        assert row["resolved_count"]  == 7
        assert row["escalated_count"] == 1
        assert row["handoff_count"]   == 2
        assert abs(row["escalation_rate"] - 0.1) < 1e-6
        assert abs(row["handoff_rate"]    - 0.2) < 1e-6

    async def test_multiple_groups_returned(self):
        client = _make_client(_ch_result(self._COLS, [
            ["agente_sac_v1",      "sac_ia",       "primary",   5, None, 4, 0, 0, 1, 0, 0, 0.0, 0.0],
            ["agente_retencao_v1", "retencao_ia",  "primary",  20, 60000.0, 18, 1, 1, 0, 0, 3, 0.05, 0.15],
        ]))
        result = await query_agent_performance_report(client, DB, TENANT)
        assert result["meta"]["total"] == 2
        assert result["data"][0]["agent_type_id"] == "agente_sac_v1"
        assert result["data"][0]["avg_duration_ms"] is None   # null propagated
        assert result["data"][1]["total_sessions"]  == 20

    async def test_filters_do_not_crash(self):
        client = _make_client(_ch_result(self._COLS, []))
        result = await query_agent_performance_report(
            client, DB, TENANT,
            pool_id       = "retencao_ia",
            agent_type_id = "agente_retencao_v1",
            role          = "primary",
        )
        assert result["data"] == []
        assert result["meta"]["total"] == 0

    async def test_error_returns_empty_with_error_key(self):
        client = MagicMock()
        client.query = MagicMock(side_effect=Exception("ch timeout"))
        result = await query_agent_performance_report(client, DB, TENANT)
        assert result["data"] == []
        assert result.get("error") == "data_unavailable"


# ─── query_evaluations_report ────────────────────────────────────────────────

@pytest.mark.asyncio
class TestQueryEvaluationsReport:
    _COLS = [
        "result_id", "instance_id", "session_id", "tenant_id",
        "evaluator_id", "form_id", "campaign_id",
        "overall_score", "eval_status", "locked",
        "compliance_flags", "timestamp",
    ]

    def _count_result(self, n: int) -> MagicMock:
        return _ch_result(["count()"], [[n]])

    async def test_returns_data_and_meta(self):
        client = _make_client(
            self._count_result(0),
            _ch_result(self._COLS, []),
        )
        result = await query_evaluations_report(client, DB, TENANT)
        assert "data" in result
        assert "meta" in result
        assert result["meta"]["total"] == 0

    async def test_data_row_mapped_correctly(self):
        client = _make_client(
            self._count_result(1),
            _ch_result(self._COLS, [[
                "res-001", "inst-001", "sess-001", TENANT,
                "agente_avaliacao_v1-001", "form-sac-v1", "camp-q1-2026",
                0.87, "approved", 0,
                [], "2026-04-01T10:00:00",
            ]]),
        )
        result = await query_evaluations_report(client, DB, TENANT)
        assert result["meta"]["total"] == 1
        row = result["data"][0]
        assert row["result_id"]     == "res-001"
        assert row["tenant_id"]     == TENANT
        assert row["campaign_id"]   == "camp-q1-2026"
        assert row["eval_status"]   == "approved"
        assert row["overall_score"] == pytest.approx(0.87)
        assert row["locked"]        == 0

    # ── F2 (bancada de agentes): linhas carregam o agente AVALIADO ───────────
    async def test_rows_include_evaluated_agent_attribution(self):
        cols = self._COLS + ["agent_key", "agent_type", "pool_id", "user_login"]
        client = _make_client(
            self._count_result(1),
            _ch_result(cols, [[
                "res-001", "inst-001", "sess-001", TENANT,
                "agente_avaliacao_v1-001", "form-sac-v1", "camp-q1-2026",
                0.87, "approved", 0, [], "2026-04-01T10:00:00",
                "user-123", "human", "retencao_humano", "admin@plughub.local",
            ]]),
        )
        result = await query_evaluations_report(client, DB, TENANT)
        row = result["data"][0]
        assert row["agent_key"]  == "user-123"
        assert row["agent_type"] == "human"
        assert row["pool_id"]    == "retencao_humano"
        sql = client.query.call_args_list[-1][0][0]
        assert "LEFT JOIN" in sql
        assert "agent_type != 'system'" in sql

    async def test_filters_do_not_crash(self):
        client = _make_client(
            self._count_result(0),
            _ch_result(self._COLS, []),
        )
        result = await query_evaluations_report(
            client, DB, TENANT,
            campaign_id  = "camp-q1-2026",
            form_id      = "form-sac-v1",
            evaluator_id = "agente_avaliacao_v1-001",
            eval_status  = "approved",
        )
        assert result["data"] == []
        assert result["meta"]["total"] == 0

    async def test_error_returns_empty_with_error_key(self):
        client = MagicMock()
        client.query = MagicMock(side_effect=Exception("ch timeout"))
        result = await query_evaluations_report(client, DB, TENANT)
        assert result["data"] == []
        assert result.get("error") == "data_unavailable"


# ─── query_evaluations_summary ───────────────────────────────────────────────

@pytest.mark.asyncio
class TestQueryEvaluationsSummary:
    _COLS = [
        "group_key",
        "total_evaluated",
        "count_submitted", "count_approved", "count_rejected",
        "count_contested", "count_locked", "count_locked_flag",
        "avg_score", "min_score", "max_score",
        "score_excellent", "score_good", "score_fair", "score_poor",
        "with_compliance_flags",
    ]

    async def test_returns_data_and_meta(self):
        client = _make_client(_ch_result(self._COLS, []))
        result = await query_evaluations_summary(client, DB, TENANT)
        assert "data" in result
        assert "meta" in result
        assert "group_by" in result
        assert result["group_by"] == "campaign_id"  # default

    async def test_summary_row_mapped_correctly(self):
        client = _make_client(_ch_result(self._COLS, [[
            "camp-q1-2026",
            20,      # total_evaluated
            5,       # count_submitted
            12,      # count_approved
            2,       # count_rejected
            1,       # count_contested
            0,       # count_locked
            0,       # count_locked_flag
            0.82,    # avg_score
            0.55,    # min_score
            0.98,    # max_score
            8,       # score_excellent
            6,       # score_good
            4,       # score_fair
            2,       # score_poor
            3,       # with_compliance_flags
        ]]))
        result = await query_evaluations_summary(client, DB, TENANT)
        assert result["meta"]["total"] == 1
        row = result["data"][0]
        assert row["group_key"]             == "camp-q1-2026"
        assert row["total_evaluated"]       == 20
        assert row["count_approved"]        == 12
        assert row["avg_score"]             == pytest.approx(0.82)
        assert row["score_excellent"]       == 8
        assert row["with_compliance_flags"] == 3

    async def test_invalid_group_by_defaults_to_campaign_id(self):
        client = _make_client(_ch_result(self._COLS, []))
        result = await query_evaluations_summary(client, DB, TENANT, group_by="injection; DROP")
        assert result["group_by"] == "campaign_id"

    # ── F2 (bancada de agentes): agrupamento pelo agente AVALIADO ────────────
    _AGENT_COLS = [
        "group_key", "agent_type", "pool_id", "user_login",
        "total_evaluated",
        "count_submitted", "count_approved", "count_rejected",
        "count_contested", "count_locked", "count_locked_flag",
        "avg_score", "min_score", "max_score",
        "score_excellent", "score_good", "score_fair", "score_poor",
        "with_compliance_flags",
    ]

    async def test_group_by_agent_key_joins_segments(self):
        client = _make_client(_ch_result(self._AGENT_COLS, [[
            "bef14526-b2be-4261-b115-2a765d2da381", "human", "retencao_humano",
            "admin@plughub.local",
            5, 1, 4, 0, 0, 0, 0, 0.85, 0.70, 0.95, 2, 2, 1, 0, 0,
        ]]))
        result = await query_evaluations_summary(client, DB, TENANT, group_by="agent_key")
        assert result["group_by"] == "agent_key"
        row = result["data"][0]
        assert row["agent_type"] == "human"
        assert row["pool_id"]    == "retencao_humano"
        # SQL gerado deve conter o join de atribuição com segments
        sql = client.query.call_args_list[-1][0][0]
        assert "LEFT JOIN" in sql
        assert "attr.agent_key" in sql
        assert "agent_type != 'system'" in sql
        assert "role = 'primary'" in sql

    async def test_group_by_pool_id_accepted(self):
        client = _make_client(_ch_result(self._COLS, []))
        result = await query_evaluations_summary(client, DB, TENANT, group_by="pool_id")
        assert result["group_by"] == "pool_id"
        sql = client.query.call_args_list[-1][0][0]
        assert "LEFT JOIN" in sql

    async def test_error_returns_empty_with_error_key(self):
        client = MagicMock()
        client.query = MagicMock(side_effect=Exception("ch timeout"))
        result = await query_evaluations_summary(client, DB, TENANT)
        assert result["data"] == []
        assert result.get("error") == "data_unavailable"


# ─── query_agents_compare — F3 bancada de agentes ────────────────────────────

@pytest.mark.asyncio
class TestQueryAgentsCompare:
    _SEG_COLS = [
        "agent_key", "agent_type", "label", "bucket",
        "sessions", "resolved", "escalated", "aht_ms",
    ]

    async def test_invalid_lens_rejected(self):
        client = MagicMock()
        result = await query_agents_compare(client, DB, TENANT, lens="banana")
        assert result["error"] == "invalid_lens"
        assert "resolution" in result["allowed_lenses"]
        client.query.assert_not_called()

    async def test_quality_criteria_lens_dimensions_in_summary(self):
        # F8: nota por dimensão em summary.dimensions[], comparável por form.
        cols = ["agent_key", "agent_type", "label", "dimension_id",
                "dimension_name", "form_id", "n", "avg_score"]
        client = _make_client(_ch_result(cols, [
            ["A", "human", "a@x", "empatia",      "Empatia",      "form1", 3, 9.0],
            ["A", "human", "a@x", "conformidade", "Conformidade", "form1", 3, 7.0],
        ]))
        result = await query_agents_compare(
            client, DB, TENANT, lens="quality_criteria", entities=["A"],
        )
        assert result["meta"]["lens"] == "quality_criteria"
        assert "error" not in result
        sql = client.query.call_args_list[-1][0][0]
        assert "evaluation_dimension_scores" in sql
        ent = result["data"]["entities"][0]
        assert ent["summary"]["form_id"] == "form1"
        assert ent["summary"]["n_evaluations"] == 3
        dims = {d["dimension_id"]: d for d in ent["summary"]["dimensions"]}
        assert dims["empatia"]["avg_score"] == pytest.approx(9.0)
        assert dims["empatia"]["dimension_label"] == "Empatia"
        assert dims["conformidade"]["avg_score"] == pytest.approx(7.0)

    async def test_nps_lens_reads_session_signal(self):
        # F10.3b: a lente nps (grão segmento) lê de session_signal (grain=segment),
        # join a segments por segment_id p/ agent_type/label. Cutover unificado.
        cols = ["agent_key", "agent_type", "label", "bucket",
                "n", "avg_nps", "promoters", "detractors"]
        client = _make_client(_ch_result(cols, [
            ["A", "human", "a@x", "2026-06-07", 4, 8.0, 2, 1],
        ]))
        result = await query_agents_compare(client, DB, TENANT, lens="nps", entities=["A"])
        assert result["meta"]["lens"] == "nps"
        sql = client.query.call_args_list[-1][0][0]
        assert "session_signal" in sql
        assert "grain = 'segment'" in sql
        assert "nps_score" not in sql
        ent = result["data"]["entities"][0]
        assert ent["summary"]["n_responses"] == 4
        assert ent["summary"]["avg_nps"] == pytest.approx(8.0)
        # NPS = (promoters - detractors)/n * 100 = (2-1)/4*100 = 25.0
        assert ent["summary"]["nps"] == pytest.approx(25.0)

    async def test_session_nps_lens_reads_session_signal(self):
        # F10.3a: NPS de sessão (grão session) cruzado ao agente via atribuição.
        cols = ["agent_key", "agent_type", "label", "bucket",
                "n", "avg_nps", "promoters", "detractors"]
        client = _make_client(_ch_result(cols, [
            ["A", "human", "a@x", "2026-06-10", 5, 9.0, 3, 1],
        ]))
        result = await query_agents_compare(
            client, DB, TENANT, lens="session_nps", entities=["A"],
        )
        assert result["meta"]["lens"] == "session_nps"
        assert "error" not in result
        sql = client.query.call_args_list[-1][0][0]
        assert "session_signal" in sql
        assert "grain = 'session'" in sql
        ent = result["data"]["entities"][0]
        assert ent["summary"]["n_responses"] == 5
        assert ent["summary"]["avg_nps"] == pytest.approx(9.0)
        # NPS = (3-1)/5*100 = 40.0
        assert ent["summary"]["nps"] == pytest.approx(40.0)

    async def test_wrapup_lens_distribution_in_summary(self):
        cols = ["agent_key", "agent_type", "label", "outcome", "issue_status", "cnt"]
        client = _make_client(_ch_result(cols, [
            ["A", "human", "a@x", "resolved",  "resolvido", 5],
            ["A", "human", "a@x", "escalated", "escalado",  2],
        ]))
        result = await query_agents_compare(client, DB, TENANT, lens="wrapup", entities=["A"])
        assert result["meta"]["lens"] == "wrapup"
        sql = client.query.call_args_list[-1][0][0]
        assert "issue_status != ''" in sql
        ent = result["data"]["entities"][0]
        assert ent["summary"]["total"] == 7
        disps = {d["issue_status"]: d["count"] for d in ent["summary"]["dispositions"]}
        assert disps == {"resolvido": 5, "escalado": 2}

    async def test_pool_pseudo_entity_aggregates_pool_average(self):
        # F9: entity "pool:<id>" → série de média aritmética escopada ao pool.
        # 1ª query = escopo principal (per_agent/average); 2ª = escopo do pool.
        main = _ch_result(self._SEG_COLS, [
            ["A", "human", "a@x", "2026-06-01", 2, 2, 0, 1000.0],
        ])
        pool = _ch_result(self._SEG_COLS, [
            ["A", "human",  "a@x", "2026-06-01", 2, 2, 0, 1000.0],   # res 1.0
            ["B", "native", "B",   "2026-06-01", 2, 1, 1, 500.0],    # res 0.5
        ])
        client = _make_client(main, pool)
        result = await query_agents_compare(
            client, DB, TENANT, lens="resolution", entities=["pool:retencao_humano"],
        )
        ent = result["data"]["entities"][0]
        assert ent["agent_key"]  == "pool:retencao_humano"
        assert ent["agent_type"] == "__pool__"
        assert ent["pool_id"]    == "retencao_humano"
        assert ent["n"] == 2
        d1 = next(p for p in ent["series"] if p["date"] == "2026-06-01")
        assert d1["resolution_rate"] == pytest.approx(0.75)   # (1.0 + 0.5) / 2
        assert d1["n"] == 2
        # summary escalar = média aritmética dos summaries dos agentes do pool
        assert ent["summary"]["resolution_rate"] == pytest.approx(0.75)
        assert ent["summary"]["sessions"]        == pytest.approx(2.0)

    async def test_escalation_reason_lens_distribution_in_summary(self):
        # F7: distribuição de motivos de escalação em summary.reasons[].
        cols = ["agent_key", "agent_type", "label", "reason_id", "cnt"]
        client = _make_client(_ch_result(cols, [
            ["A", "human", "a@x", "needs_authorization", 4],
            ["A", "human", "a@x", "out_of_scope",        2],
        ]))
        result = await query_agents_compare(
            client, DB, TENANT, lens="escalation_reason", entities=["A"],
        )
        assert result["meta"]["lens"] == "escalation_reason"
        assert "error" not in result
        sql = client.query.call_args_list[-1][0][0]
        assert "escalation_reason" in sql
        assert "outcome IN" in sql
        ent = result["data"]["entities"][0]
        assert ent["summary"]["total"] == 6
        reasons = {r["reason_id"]: r["count"] for r in ent["summary"]["reasons"]}
        assert reasons == {"needs_authorization": 4, "out_of_scope": 2}

    async def test_resolution_average_is_arithmetic_with_gaps(self):
        # Dia 1: A=2/2 (1.0), B=1/2 (0.5) → média 0.75 (n=2)
        # Dia 2: só A=0/1 (0.0)           → média 0.0  (n=1 — B é GAP, não zero)
        client = _make_client(_ch_result(self._SEG_COLS, [
            ["A", "human",  "a@x", "2026-06-01", 2, 2, 0, 1000.0],
            ["A", "human",  "a@x", "2026-06-02", 1, 0, 1, 2000.0],
            ["B", "native", "B",   "2026-06-01", 2, 1, 1, 500.0],
        ]))
        result = await query_agents_compare(
            client, DB, TENANT, lens="resolution", entities=["A"],
        )
        avg = result["data"]["average"]
        assert avg["label"] == "média dos agentes"
        assert avg["n"] == 2
        d1 = next(p for p in avg["series"] if p["date"] == "2026-06-01")
        d2 = next(p for p in avg["series"] if p["date"] == "2026-06-02")
        assert d1["resolution_rate"] == pytest.approx(0.75)
        assert d1["n"] == 2
        assert d2["resolution_rate"] == pytest.approx(0.0)
        assert d2["n"] == 1   # gap do B — fora do denominador

        # Entidade selecionada
        assert len(result["data"]["entities"]) == 1
        ent = result["data"]["entities"][0]
        assert ent["agent_key"] == "A"
        assert ent["agent_type"] == "human"
        assert ent["summary"]["sessions"] == 3
        assert ent["summary"]["resolution_rate"] == pytest.approx(2 / 3, abs=1e-3)

    async def test_escalate_family_folded_in_sql(self):
        client = _make_client(_ch_result(self._SEG_COLS, []))
        await query_agents_compare(client, DB, TENANT, lens="resolution")
        sql = client.query.call_args_list[-1][0][0]
        for value in ("'escalated'", "'escalated_human'", "'escalated_ai'", "'transferred'"):
            assert value in sql
        assert "agent_type != 'system'" in sql
        assert "role = 'primary'" in sql

    async def test_entities_missing_key_marked(self):
        client = _make_client(_ch_result(self._SEG_COLS, []))
        result = await query_agents_compare(
            client, DB, TENANT, lens="sessions_aht", entities=["nao_existe"],
        )
        ent = result["data"]["entities"][0]
        assert ent["missing"] is True
        assert ent["series"] == []

    async def test_quality_buckets_by_session_started_at(self):
        cols = ["agent_key", "agent_type", "label", "bucket", "n", "avg_score"]
        client = _make_client(_ch_result(cols, [
            ["A", "human", "a@x", "2026-06-01", 3, 0.8],
        ]))
        result = await query_agents_compare(
            client, DB, TENANT, lens="quality", entities=["A"],
        )
        sql = client.query.call_args_list[-1][0][0]
        assert "session_started_at" in sql            # regra de ouro §7
        assert "JOIN" in sql
        ent = result["data"]["entities"][0]
        assert ent["summary"]["n_evaluations"] == 3   # N visível (amostral)
        assert ent["summary"]["avg_score"] == pytest.approx(0.8)

    async def test_quality_exposes_form_ids_union(self):
        # item 3 (follow-ups A): a lente quality expõe os form_ids distintos por
        # agente (regra de comparabilidade: cross-agente exige mesmo form;
        # cross-form só p/ um único agente — guard/ressalva na UI).
        cols = ["agent_key", "agent_type", "label", "bucket", "n", "avg_score", "form_ids"]
        client = _make_client(_ch_result(cols, [
            ["A", "human", "a@x", "2026-06-01", 2, 0.8, ["form_sac"]],
            ["A", "human", "a@x", "2026-06-02", 1, 0.6, ["form_sac", "form_qa"]],
        ]))
        result = await query_agents_compare(
            client, DB, TENANT, lens="quality", entities=["A"],
        )
        assert "groupUniqArray(er.form_id)" in client.query.call_args_list[-1][0][0]
        ent = result["data"]["entities"][0]
        assert ent["summary"]["form_ids"] == ["form_qa", "form_sac"]   # união ordenada

    async def test_error_returns_empty_with_error_key(self):
        client = MagicMock()
        client.query = MagicMock(side_effect=Exception("ch down"))
        result = await query_agents_compare(client, DB, TENANT, lens="resolution")
        assert result["error"] == "data_unavailable"
        assert result["data"]["entities"] == []


# ─── query_agents_cross — F6 cruzamentos ─────────────────────────────────────

@pytest.mark.asyncio
class TestQueryAgentsCross:
    # item 5: NPS sai de segments.nps_score e passa a vir de session_signal —
    # query separada (seg → nps → eval). seg não traz mais colunas de NPS.
    _SEG_COLS = ["agent_key", "agent_type", "label", "sessions", "resolved", "escalated"]
    _NPS_COLS = ["agent_key", "nps_n", "nps_sum", "promoters", "detractors"]
    _EVAL_COLS = ["agent_key", "n_evals", "avg_score"]

    async def test_combines_segments_and_eval_per_agent(self):
        client = _make_client(
            _ch_result(self._SEG_COLS, [
                ["A", "human", "a@x", 10, 7, 2],
            ]),
            _ch_result(self._NPS_COLS, [
                ["A", 4, 32.0, 2, 1],
            ]),
            _ch_result(self._EVAL_COLS, [
                ["A", 3, 0.82],
            ]),
        )
        result = await query_agents_cross(client, DB, TENANT)
        assert "data" in result
        # o NPS lê de session_signal, não de segments.nps_score
        nps_sql = client.query.call_args_list[1][0][0]
        assert "session_signal" in nps_sql and "nps_score" not in nps_sql
        row = result["data"][0]
        assert row["agent_key"] == "A"
        assert row["sessions"] == 10
        assert row["resolution_rate"] == pytest.approx(0.7)
        assert row["escalation_rate"] == pytest.approx(0.2)
        assert row["quality_score"] == pytest.approx(0.82)
        assert row["quality_n"] == 3
        # NPS = (2-1)/4*100 = 25.0 ; avg = 32/4 = 8.0
        assert row["nps"] == pytest.approx(25.0)
        assert row["avg_nps"] == pytest.approx(8.0)
        assert row["nps_n"] == 4

    async def test_agent_without_eval_has_null_quality(self):
        client = _make_client(
            _ch_result(self._SEG_COLS, [
                ["B", "native", "skill_x", 5, 5, 0],
            ]),
            _ch_result(self._NPS_COLS, []),
            _ch_result(self._EVAL_COLS, []),
        )
        result = await query_agents_cross(client, DB, TENANT)
        row = result["data"][0]
        assert row["quality_score"] is None
        assert row["quality_n"] == 0
        assert row["nps"] is None       # nps_n = 0
        assert row["resolution_rate"] == pytest.approx(1.0)

    async def test_error_returns_empty(self):
        client = MagicMock()
        client.query = MagicMock(side_effect=Exception("ch down"))
        result = await query_agents_cross(client, DB, TENANT)
        assert result["data"] == []
        assert result.get("error") == "data_unavailable"


# ── Arc 7c — pool-scoped visibility ───────────────────────────────────────────

class TestApplyPoolScope:
    """Unit tests for the _apply_pool_scope helper (pure, no async)."""

    def test_none_pools_noop_returns_true(self):
        conditions: list = ["tenant_id = 'x'"]
        result = _apply_pool_scope(conditions, None)
        assert result is True
        assert len(conditions) == 1   # no extra condition added

    def test_empty_list_returns_false(self):
        conditions: list = ["tenant_id = 'x'"]
        result = _apply_pool_scope(conditions, [])
        assert result is False
        assert len(conditions) == 1   # no condition added (caller short-circuits)

    def test_single_pool_appends_in_clause(self):
        conditions: list = []
        _apply_pool_scope(conditions, ["pool_sac"])
        assert len(conditions) == 1
        assert "pool_id IN ('pool_sac')" in conditions[0]

    def test_multiple_pools_joined_correctly(self):
        conditions: list = []
        _apply_pool_scope(conditions, ["sac", "retencao", "billing"])
        clause = conditions[0]
        assert "pool_id IN" in clause
        assert "'sac'" in clause
        assert "'retencao'" in clause
        assert "'billing'" in clause


# ── Substrate isolation (ADR adr-quality-substrate-isolation) — origin filter ──

class TestApplyOriginScope:
    """Unit tests for the _apply_origin_scope helper (pure, no async)."""

    def test_default_live(self):
        conds: list = []
        _apply_origin_scope(conds)
        assert conds == ["origin IN ('live')"]

    def test_explicit_reeval(self):
        conds: list = []
        _apply_origin_scope(conds, "reeval")
        assert conds == ["origin IN ('reeval')"]

    def test_list_of_origins(self):
        conds: list = []
        _apply_origin_scope(conds, ["live", "import"])
        assert conds[0] == "origin IN ('live', 'import')"

    def test_invalid_falls_back_to_live(self):
        conds: list = []
        _apply_origin_scope(conds, "bogus")
        assert conds == ["origin IN ('live')"]

    def test_empty_list_falls_back_to_live(self):
        conds: list = []
        _apply_origin_scope(conds, [])
        assert conds == ["origin IN ('live')"]

    def test_alias_prefix(self):
        conds: list = []
        _apply_origin_scope(conds, "live", alias="s.")
        assert conds == ["s.origin IN ('live')"]


# ── E2f — escopo de CONTATO (sessão de hook sem canal + pool interno) ─────────

class TestApplyContactScope:
    """Unit tests do _apply_contact_scope (puro, sem async).

    A regra tem duas metades que respondem à MESMA pergunta ("isto é um contato?")
    e viviam separadas: a de hook-sem-canal (copiada em 3 queries) e a de pool
    interno (E2f, inexistente até então).
    """

    def test_hook_clause_always_present(self):
        conds: list = []
        _apply_contact_scope(conds)
        assert conds == ["(channel != '' OR closed_at IS NULL)"]

    def test_no_internal_pools_adds_nothing_extra(self):
        """Sem conjunto resolvido o helper NÃO inventa exclusão — quem loga a
        degradação é o pools_client, não este helper."""
        conds: list = []
        _apply_contact_scope(conds, frozenset())
        assert len(conds) == 1

    def test_internal_pools_excluded(self):
        conds: list = []
        _apply_contact_scope(conds, frozenset({"wrapup_detached_ia"}))
        assert conds[1] == "pool_id NOT IN ('wrapup_detached_ia')"

    def test_internal_pools_sorted_for_determinism(self):
        conds: list = []
        _apply_contact_scope(conds, frozenset({"z_pool", "a_pool"}))
        assert conds[1] == "pool_id NOT IN ('a_pool', 'z_pool')"

    def test_alias_prefix_applies_to_all_columns(self):
        conds: list = []
        _apply_contact_scope(conds, frozenset({"wrapup_detached_ia"}), alias="s.")
        assert conds[0] == "(s.channel != '' OR s.closed_at IS NULL)"
        assert conds[1] == "s.pool_id NOT IN ('wrapup_detached_ia')"


# ── ADR wrapup-detached-pull §7 — scope=contacts|all na LISTAGEM ─────────────

class TestScopeAllRelaxesOnlyThePoolRule:
    """O que `scope=all` pode e o que NÃO pode relaxar.

    A passagem de `None` no lugar do conjunto é o mecanismo inteiro — estes testes
    fixam que ela relaxa a regra do POOL e **mantém** a do CANAL. Relaxar a segunda
    faria sessão ATIVA duplicar na tela (a linha `channel=''` do `parse_routed`
    sobrescreve a do `parse_inbound` no ReplacingMergeTree), que é um defeito de
    dado, não de visibilidade.
    """

    INTERNAL = frozenset({"retencao_humano-int", "wrapup_detached_ia"})

    def test_scope_contacts_keeps_both_rules(self):
        conds: list = []
        _apply_contact_scope(conds, self.INTERNAL, alias="s.")
        assert len(conds) == 2
        assert "NOT IN" in conds[1]

    def test_scope_all_drops_pool_rule_keeps_channel_rule(self):
        conds: list = []
        _apply_contact_scope(conds, None, alias="s.")
        assert conds == ["(s.channel != '' OR s.closed_at IS NULL)"]


class TestContactOnlyPredicate:
    """A regra do pool como EXPRESSÃO — é o que permite contar o domínio de
    contato dentro de uma listagem que o relaxou (guardrail §7.2 item 2)."""

    def test_no_internal_pools_is_always_true(self):
        """Sem conjunto resolvido, `countIf` conta tudo — e aí `total_contacts ==
        total`. Nunca inventa exclusão, igual ao `_apply_contact_scope`."""
        assert _contact_only_predicate(frozenset()) == "1"
        assert _contact_only_predicate(None) == "1"

    def test_negates_membership_with_alias_and_sorted(self):
        assert _contact_only_predicate(
            frozenset({"z_pool", "a_pool"}), alias="s."
        ) == "s.pool_id NOT IN ('a_pool', 'z_pool')"

    def test_is_the_exact_negation_of_the_where_clause(self):
        """O invariante que impede as duas metades de divergirem: a expressão
        contada e a condição filtrada têm de ser a MESMA string."""
        pools = frozenset({"w_int", "a_int"})
        conds: list = []
        _apply_contact_scope(conds, pools, alias="s.")
        assert conds[1] == _contact_only_predicate(pools, alias="s.")


class TestSessionsMeta:
    """Dois domínios, dois números — nunca um total somado (guardrail §7.2 item 2)."""

    def test_contacts_scope_has_zero_internal(self):
        m = _sessions_meta(1, 50, 7, 7, "f", "t", "contacts")
        assert (m["total"], m["total_contacts"], m["total_internal"]) == (7, 7, 0)

    def test_all_scope_splits_the_domains(self):
        m = _sessions_meta(1, 50, 9, 7, "f", "t", "all")
        assert m["total_contacts"] == 7
        assert m["total_internal"] == 2
        assert m["scope"] == "all"

    def test_total_never_below_contacts(self):
        """Contagem impossível não vira negativo silencioso: `total_internal` é
        clampado em 0. Se isto disparar na prática, o defeito é a query de
        contagem, não o meta."""
        m = _sessions_meta(1, 50, 3, 5, "f", "t", "all")
        assert m["total_internal"] == 0

    def test_keeps_base_meta_fields(self):
        m = _sessions_meta(2, 25, 9, 7, "2026-01-01", "2026-01-02", "all")
        assert m["page"] == 2 and m["page_size"] == 25
        assert m["from_dt"] == "2026-01-01" and m["to_dt"] == "2026-01-02"

    def test_internal_pools_known_is_a_count_not_a_health_flag(self):
        """Deliberadamente um número: `frozenset()` vazio significa tanto registry
        fora do ar quanto tenant sem pool interno, e o cliente não distingue. Um
        booleano "resolvido" mentiria num dos dois casos."""
        assert _sessions_meta(1, 50, 0, 0, "f", "t")["internal_pools_known"] == 0
        assert _sessions_meta(
            1, 50, 9, 7, "f", "t", "all", internal_pools_known=2,
        )["internal_pools_known"] == 2


class TestAttachJourneyInternalCounts:
    """Fatia 4b — o segundo número do card, por pós-passe.

    A escolha de desenho que estes testes protegem: contar as internas DENTRO do
    `GROUP BY` principal as faria entrar no `WHERE` e contaminar `channels`,
    `pool_ids`, `open_count` e o wall-clock do processo — o G1 reaberto um nível
    acima. Se alguém "simplificar" movendo a contagem para lá, é aqui que dói.
    """

    def _rows(self):
        return [{"journey_id": "j1"}, {"journey_id": "j2"}]

    def test_zero_on_every_row_when_no_internal_pools(self):
        client = MagicMock()
        rows = self._rows()
        _attach_journey_internal_counts(client, DB, TENANT, {}, rows, frozenset())
        assert [r["internal_session_count"] for r in rows] == [0, 0]
        client.query.assert_not_called()

    def test_maps_counts_by_journey(self):
        client = _make_client(_ch_result(["jid", "internal_count"], [["j1", 2]]))
        rows = self._rows()
        _attach_journey_internal_counts(
            client, DB, TENANT, {}, rows, frozenset({"w_int"}),
        )
        assert rows[0]["internal_session_count"] == 2
        assert rows[1]["internal_session_count"] == 0   # ausente = zero, não None

    def test_alias_is_jid_not_journey_id(self):
        """`sessions` TEM coluna `journey_id` (cache dormente da raiz canônica);
        alias que sombreia coluna real já derrubou query inteira neste projeto."""
        client = _make_client(_ch_result(["jid", "internal_count"], []))
        _attach_journey_internal_counts(
            client, DB, TENANT, {}, self._rows(), frozenset({"w_int"}),
        )
        sql = client.query.call_args_list[0].args[0]
        assert "AS jid" in sql
        assert "AS journey_id" not in sql

    def test_applies_accessible_pools_scope(self):
        """Sem isto o card anunciaria "1 interna" a um supervisor cujo drill não
        mostra nenhuma — card e drill precisam contar o mesmo universo."""
        client = _make_client(_ch_result(["jid", "internal_count"], []))
        _attach_journey_internal_counts(
            client, DB, TENANT, {}, self._rows(), frozenset({"w_int"}),
            accessible_pools=["retencao_humano"],
        )
        sql = client.query.call_args_list[0].args[0]
        assert "'retencao_humano'" in sql

    def test_query_failure_degrades_to_zero_without_raising(self):
        client = MagicMock()
        client.query = MagicMock(side_effect=Exception("ch down"))
        rows = self._rows()
        _attach_journey_internal_counts(
            client, DB, TENANT, {}, rows, frozenset({"w_int"}),
        )
        assert [r["internal_session_count"] for r in rows] == [0, 0]


class TestMarkInternalRows:
    """Fatia 1b — o veredicto atravessa a fronteira, não o conjunto bruto."""

    def test_marks_only_rows_of_internal_pools(self):
        rows = [
            {"session_id": "s1", "pool_id": "retencao_humano"},
            {"session_id": "s2", "pool_id": "wrapup_detached_ia"},
        ]
        _mark_internal_rows(rows, frozenset({"wrapup_detached_ia"}))
        assert rows[0]["is_internal"] is False
        assert rows[1]["is_internal"] is True

    def test_field_present_on_every_row_even_when_set_is_empty(self):
        """A chave nunca falta: coluna ausente em metade das linhas quebraria o
        CSV (`DictWriter` tira o cabeçalho da 1ª linha) e faria a UI ler
        `undefined` como 'não interno' sem saber que não perguntou."""
        rows = [{"session_id": "s1", "pool_id": "retencao_humano"}]
        _mark_internal_rows(rows, frozenset())
        assert rows[0]["is_internal"] is False

    def test_row_without_pool_id_is_not_internal(self):
        rows = [{"session_id": "s1"}]
        _mark_internal_rows(rows, frozenset({"w"}))
        assert rows[0]["is_internal"] is False

    async def test_reaches_the_payload_of_the_report(self):
        """Prova de ponta: a classificação chega ao `data`, não só ao helper."""
        client = _make_client(
            _ch_result(["count()", "countIf(contact)"], [[2, 1]]),
            _ch_result(
                ["session_id", "pool_id"],
                [["s-contact", "retencao_humano"], ["s-wrap", "wrapup_detached_ia"]],
            ),
        )
        with patch(
            "plughub_analytics_api.reports_query._internal_pools_for",
            return_value=frozenset({"wrapup_detached_ia"}),
        ):
            result = await query_sessions_report(client, DB, TENANT, scope="all")
        assert [r["is_internal"] for r in result["data"]] == [False, True]
        assert result["meta"]["internal_pools_known"] == 1

    def test_active_sessions_survive_empty_channel(self):
        """Guarda contra a regressão que o comentário do _fetch_sessions descreve:
        parse_routed escreve channel='' e sobrescreve a linha do inbound no
        ReplacingMergeTree — sessão ATIVA some se a condição for só channel != ''."""
        conds: list = []
        _apply_contact_scope(conds)
        assert "closed_at IS NULL" in conds[0]


class TestOriginScopeInReports:
    """Default origin='live' reaches the generated SQL; override changes it."""

    _SEG_COLS = [
        "segment_id", "session_id", "tenant_id", "participant_id", "pool_id",
        "agent_type_id", "flow_id", "user_id", "user_login", "instance_id",
        "role", "agent_type", "parent_segment_id", "sequence_index",
        "started_at", "ended_at", "duration_ms", "outcome", "close_reason",
        "handoff_reason", "issue_status", "conference_id",
    ]

    async def test_segments_default_is_live(self):
        client = _make_client(_ch_result(["count()"], [[0]]), _ch_result(self._SEG_COLS, []))
        await query_segments_report(client, DB, TENANT)
        for call in client.query.call_args_list:
            assert "origin IN ('live')" in call[0][0]

    async def test_segments_override_reeval(self):
        client = _make_client(_ch_result(["count()"], [[0]]), _ch_result(self._SEG_COLS, []))
        await query_segments_report(client, DB, TENANT, origin="reeval")
        for call in client.query.call_args_list:
            assert "origin IN ('reeval')" in call[0][0]
            assert "origin IN ('live')" not in call[0][0]


class TestEventsSqlBranches:
    """
    `_events_sql_branches` monta o UNION ALL de /reports/events. Não tinha NENHUM
    teste até 2026-07-28 — e foi exatamente a função reescrita quando `agent_events`
    foi descontinuada (routed/agent_done passaram a sair de `segments`).

    O risco desta função é estrutural, não de valor: todas as branches do UNION
    precisam ter o MESMO número e ordem de colunas, senão o ClickHouse rejeita a
    query inteira e o endpoint devolve `data_unavailable` — falha global, não
    parcial. Por isso os asserts são sobre o SQL gerado.
    """

    def _branches(self, **kw):
        return _events_sql_branches(
            db=DB, tenant_id=TENANT,
            since="2026-07-01 00:00:00", until="2026-07-28 23:59:59",
            **kw,
        )[0]

    @staticmethod
    def _projected_columns(branch: str) -> int:
        """
        Conta as colunas projetadas pelo SELECT — só as vírgulas de NÍVEL SUPERIOR.

        Contar `,` cru não serve: `concat(a, b)`, `if(a, b, c)` e `coalesce(a, b, c)`
        somam vírgulas internas e cada branch usa um conjunto diferente delas.
        """
        head  = branch.split("FROM")[0]
        depth = 0
        cols  = 1
        for ch in head:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                cols += 1
        return cols

    def test_all_branches_project_the_same_columns(self):
        # Se uma branch divergir em número de colunas, o UNION quebra em runtime e
        # o endpoint inteiro devolve data_unavailable — falha global, não parcial.
        branches = self._branches(event_type=None, session_id=None, accessible_pools=None)
        assert len(branches) >= 6
        widths = {self._projected_columns(b) for b in branches}
        assert widths == {10}, f"branches com larguras diferentes: {widths}"

    def test_routed_and_done_come_from_segments(self):
        branches = self._branches(event_type=None, session_id=None, accessible_pools=None)
        routed = [b for b in branches if "'routed'" in b]
        done   = [b for b in branches if "'agent_done'" in b]
        assert len(routed) == 1 and len(done) == 1
        assert f"FROM {DB}.segments" in routed[0]
        assert f"FROM {DB}.segments" in done[0]
        assert f"{DB}.agent_events" not in "".join(branches)
        # agent_done só conta segmento fechado
        assert "ended_at IS NOT NULL" in done[0]

    def test_event_type_prunes_to_single_branch(self):
        # Antes as duas saíam juntas (eram uma branch só sobre agent_events), e a
        # de agent_done era escaneada à toa quando se filtrava por routed.
        only_routed = self._branches(event_type="routed", session_id=None, accessible_pools=None)
        assert len(only_routed) == 1
        assert "'routed'" in only_routed[0]

        only_done = self._branches(event_type="agent_done", session_id=None, accessible_pools=None)
        assert len(only_done) == 1
        assert "'agent_done'" in only_done[0]

    def test_origin_scope_is_uniform_across_substrate_branches(self):
        # Assimetria aqui produz stream incoerente: sessão importada apareceria
        # aberta e com mensagens, mas sem nenhum agente.
        branches = self._branches(event_type=None, session_id=None, accessible_pools=None)
        for b in branches:
            if f"{DB}.sessions FINAL" in b or f"{DB}.segments FINAL" in b or f"{DB}.messages FINAL" in b:
                assert "origin = 'live'" in b

    def test_session_filter_has_no_stale_alias(self):
        # As branches de segmento perderam o JOIN com sessions; um `ae.` remanescente
        # viraria "unknown identifier" em runtime.
        branches = self._branches(event_type=None, session_id="sess-1", accessible_pools=None)
        for b in branches:
            assert "ae." not in b

    def test_pool_scope_applied(self):
        branches = self._branches(event_type=None, session_id=None, accessible_pools=["retencao"])
        seg = [b for b in branches if f"FROM {DB}.segments" in b]
        assert seg and all("pool_id IN ('retencao')" in b for b in seg)


class TestOriginScopeInBench:
    """Substrate isolation reaches the bench (compare/cross) — default live, override works."""

    _CMP_COLS = ["agent_key", "agent_type", "label", "bucket",
                 "sessions", "resolved", "escalated", "aht_ms"]

    async def test_compare_resolution_default_live(self):
        client = _make_client(_ch_result(self._CMP_COLS, []))
        await query_agents_compare(client, DB, TENANT, lens="resolution", include_average=False)
        joined = " ".join(c[0][0] for c in client.query.call_args_list)
        assert "origin IN ('live')" in joined

    async def test_compare_resolution_override_reeval(self):
        client = _make_client(_ch_result(self._CMP_COLS, []))
        await query_agents_compare(client, DB, TENANT, lens="resolution",
                                   include_average=False, origin="reeval")
        joined = " ".join(c[0][0] for c in client.query.call_args_list)
        assert "origin IN ('reeval')" in joined
        assert "origin IN ('live')" not in joined

    async def test_cross_default_live(self):
        seg_cols  = ["agent_key", "agent_type", "label", "sessions", "resolved", "escalated"]
        nps_cols  = ["agent_key", "nps_n", "nps_sum", "promoters", "detractors"]
        eval_cols = ["agent_key", "n_evals", "avg_score"]
        client = _make_client(
            _ch_result(seg_cols, []), _ch_result(nps_cols, []), _ch_result(eval_cols, []),
        )
        await query_agents_cross(client, DB, TENANT)
        joined = " ".join(c[0][0] for c in client.query.call_args_list)
        # segments aggregate + NPS join + attribution all carry the live filter
        assert "origin IN ('live')" in joined


class TestPoolScopedSessionsReport:
    """Arc 7c — accessible_pools filtering in query_sessions_report."""

    _COLS = ["session_id", "tenant_id", "channel", "pool_id",
             "opened_at", "closed_at", "close_reason", "outcome",
             "wait_time_ms", "handle_time_ms"]

    def _count_result(self, n: int) -> MagicMock:
        return _sessions_count_result(n)

    async def test_none_accessible_pools_passes_through(self):
        """accessible_pools=None (unrestricted) — ClickHouse is still called."""
        client = _make_client(
            self._count_result(5),
            _ch_result(self._COLS, []),
        )
        result = await query_sessions_report(
            client, DB, TENANT, accessible_pools=None
        )
        assert result["meta"]["total"] == 5
        assert client.query.call_count == 2   # count + data

    async def test_empty_accessible_pools_short_circuits(self):
        """accessible_pools=[] (no access) — ClickHouse never called."""
        client = MagicMock()
        result = await query_sessions_report(
            client, DB, TENANT, accessible_pools=[]
        )
        assert result["data"] == []
        assert result["meta"]["total"] == 0
        client.query.assert_not_called()

    async def test_pool_list_injects_in_clause(self):
        """accessible_pools=['sac'] — WHERE clause contains pool_id IN (...)."""
        client = _make_client(
            self._count_result(2),
            _ch_result(self._COLS, []),
        )
        await query_sessions_report(
            client, DB, TENANT, accessible_pools=["sac"]
        )
        # Both calls (count + data) should contain the IN clause
        for call in client.query.call_args_list:
            sql = call[0][0]
            assert "pool_id IN ('sac')" in sql


# TestPoolScopedAgentsReport removida com `query_agents_report` (2026-07-28).
# O invariante Arc 7c que ela cobria — short-circuit de `accessible_pools=[]` e
# `pool_id IN (...)` em todas as queries — segue coberto pela classe acima, sobre
# `query_sessions_report`.


class TestPoolPrincipalAuth:
    """Unit tests for pool_auth.PoolPrincipal and optional_pool_principal."""

    def test_is_unrestricted_when_none(self):
        from ..pool_auth import PoolPrincipal
        p = PoolPrincipal(accessible_pools=None, tenant_id="t", sub="u")
        assert p.is_unrestricted is True

    def test_is_not_unrestricted_when_list(self):
        from ..pool_auth import PoolPrincipal
        p = PoolPrincipal(accessible_pools=["pool_a"], tenant_id="t", sub="u")
        assert p.is_unrestricted is False

    async def test_open_access_does_not_bypass_pool_scoping(self):
        # Segurança Fase A/E: pool-scoping é DESACOPLADO do open_access. Mesmo com
        # open_access=True (bypass amplo de audit/admin/transcript no demo), um token
        # RESTRITO válido enforça accessible_pools em /reports/*.
        import jwt as _jwt
        from unittest.mock import patch
        from fastapi.security import HTTPAuthorizationCredentials
        from ..pool_auth import optional_pool_principal
        tok = _jwt.encode(
            {"sub": "u", "tenant_id": "t", "accessible_pools": ["pool_a"]},
            "secret", algorithm="HS256",
        )
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=tok)
        with patch("plughub_analytics_api.pool_auth.get_settings") as m:
            m.return_value.analytics_open_access = True
            m.return_value.auth_jwt_secret = "secret"
            principal = await optional_pool_principal(credentials=creds)
        # O espelho `-int` entra por DERIVAÇÃO (`_with_internal_mirrors`, ADR
        # author-bound D2): quem alcança `p` alcança `p-int`. O teste fixava a forma
        # anterior à I5. Ver `test_internal_mirrors_are_derived` abaixo para o porquê.
        assert set(principal.accessible_pools) == {"pool_a", "pool_a-int"}

    async def test_open_access_no_token_still_unrestricted(self):
        # Sem token (dashboards/embeds) segue irrestrito — nada quebra.
        from unittest.mock import patch
        from ..pool_auth import optional_pool_principal
        with patch("plughub_analytics_api.pool_auth.get_settings") as m:
            m.return_value.analytics_open_access = True
            m.return_value.auth_jwt_secret = "secret"
            principal = await optional_pool_principal(credentials=None)
        assert principal.accessible_pools is None

    async def test_no_secret_returns_unrestricted(self):
        from unittest.mock import patch
        from ..pool_auth import optional_pool_principal
        with patch("plughub_analytics_api.pool_auth.get_settings") as m:
            m.return_value.analytics_open_access = False
            m.return_value.auth_jwt_secret = ""
            principal = await optional_pool_principal(credentials=None)
        assert principal.accessible_pools is None

    async def test_no_token_returns_unrestricted(self):
        from unittest.mock import patch
        from ..pool_auth import optional_pool_principal
        with patch("plughub_analytics_api.pool_auth.get_settings") as m:
            m.return_value.analytics_open_access = False
            m.return_value.auth_jwt_secret = "mysecret"
            principal = await optional_pool_principal(credentials=None)
        assert principal.accessible_pools is None

    async def test_valid_jwt_empty_pools_returns_unrestricted(self):
        """JWT with accessible_pools=[] means all pools (admin convention)."""
        import jwt as pyjwt
        from unittest.mock import MagicMock, patch
        from ..pool_auth import optional_pool_principal
        secret = "testsecret"
        token = pyjwt.encode(
            {"sub": "u1", "tenant_id": "t1", "accessible_pools": []},
            secret, algorithm="HS256",
        )
        creds = MagicMock()
        creds.credentials = token
        with patch("plughub_analytics_api.pool_auth.get_settings") as m:
            m.return_value.analytics_open_access = False
            m.return_value.auth_jwt_secret = secret
            principal = await optional_pool_principal(credentials=creds)
        assert principal.accessible_pools is None   # [] → all pools

    async def test_valid_jwt_with_pools_restricts(self):
        import jwt as pyjwt
        from unittest.mock import MagicMock, patch
        from ..pool_auth import optional_pool_principal
        secret = "testsecret"
        token = pyjwt.encode(
            {"sub": "u2", "tenant_id": "t1", "accessible_pools": ["sac", "retencao"]},
            secret, algorithm="HS256",
        )
        creds = MagicMock()
        creds.credentials = token
        with patch("plughub_analytics_api.pool_auth.get_settings") as m:
            m.return_value.analytics_open_access = False
            m.return_value.auth_jwt_secret = secret
            principal = await optional_pool_principal(credentials=creds)
        assert set(principal.accessible_pools) == {"sac", "sac-int", "retencao", "retencao-int"}

    async def test_internal_mirrors_are_derived(self):
        """Quem alcança `p` alcança `p-int` — e o espelho não duplica se já veio no token.

        Diagnóstico (2026-08-03): estes dois testes reprovavam desde a I5 e o `TODO.md`
        registrava a hipótese de que era `PLUGHUB_AUTH_JWT_SECRET` ausente no container.
        **A variável está definida** — a suposição foi conferida e derrubada. A causa é
        `_with_internal_mirrors` (ADR author-bound, D2), acrescentado depois que os
        testes foram escritos.

        Vale um teste próprio porque o modo de falha desta função é **ausência**: um pool
        a menos no relatório, não erro. Se a derivação sumisse, o supervisor com acesso a
        `retencao_humano` deixaria de enxergar o ACW de `retencao_humano-int` — a I5
        teria tornado o pós-atendimento mensurável e o escondido de quem precisa dele.
        Nada ficaria vermelho, e a tela pareceria certa.
        """
        import jwt as pyjwt
        from unittest.mock import MagicMock, patch
        from ..pool_auth import optional_pool_principal
        secret = "testsecret"

        async def _resolve(pools: list[str]) -> list[str]:
            token = pyjwt.encode(
                {"sub": "u", "tenant_id": "t1", "accessible_pools": pools},
                secret, algorithm="HS256",
            )
            creds = MagicMock()
            creds.credentials = token
            with patch("plughub_analytics_api.pool_auth.get_settings") as m:
                m.return_value.analytics_open_access = False
                m.return_value.auth_jwt_secret = secret
                p = await optional_pool_principal(credentials=creds)
            return p.accessible_pools

        # espelho já presente no token → não duplica
        got = await _resolve(["retencao_humano", "retencao_humano-int"])
        assert got.count("retencao_humano-int") == 1
        assert set(got) == {"retencao_humano", "retencao_humano-int"}

        # o espelho de um espelho não é criado (`-int-int` não existe)
        got = await _resolve(["sac-int"])
        assert got == ["sac-int"]

    async def test_invalid_jwt_raises_401(self):
        from unittest.mock import MagicMock, patch
        from fastapi import HTTPException
        from ..pool_auth import optional_pool_principal
        creds = MagicMock()
        creds.credentials = "not.a.valid.jwt"
        with patch("plughub_analytics_api.pool_auth.get_settings") as m:
            m.return_value.analytics_open_access = False
            m.return_value.auth_jwt_secret = "secret"
            with pytest.raises(HTTPException) as exc_info:
                await optional_pool_principal(credentials=creds)
        assert exc_info.value.status_code == 401


# ── query_contact_insights_report ─────────────────────────────────────────────

class TestQueryContactInsightsReport:
    """Tests for the _fetch_contact_insights path via query_contact_insights_report."""

    COLS = ["insight_id", "tenant_id", "session_id",
            "insight_type", "category", "value", "tags", "agent_id", "timestamp"]

    def _insight_row(self, **overrides):
        base = [
            "ins-001", TENANT, "sess-001",
            "insight.registered", "cancelamento", "produto_x",
            ["churn", "vip"], "agente_sac_v1-001", "2026-01-15T12:00:00",
        ]
        return base

    @pytest.mark.asyncio
    async def test_returns_data_rows(self):
        count_r = _ch_result(["count()"], [[3]])
        data_r  = _ch_result(self.COLS, [self._insight_row()])
        client  = _make_client(count_r, data_r)
        result  = await query_contact_insights_report(client, DB, TENANT)
        assert result["meta"]["total"] == 3
        assert len(result["data"]) == 1
        assert result["data"][0]["insight_id"] == "ins-001"

    @pytest.mark.asyncio
    async def test_category_filter_appends_condition(self):
        count_r = _ch_result(["count()"], [[1]])
        data_r  = _ch_result(self.COLS, [self._insight_row()])
        client  = _make_client(count_r, data_r)
        await query_contact_insights_report(client, DB, TENANT, category="cancelamento")
        # Verify both queries (count + data) were called
        assert client.query.call_count == 2
        # The count query SQL should contain the category parameter
        count_sql = client.query.call_args_list[0][0][0]
        assert "category" in count_sql

    @pytest.mark.asyncio
    async def test_tags_filter_appends_has_condition(self):
        count_r = _ch_result(["count()"], [[0]])
        data_r  = _ch_result(self.COLS, [])
        client  = _make_client(count_r, data_r)
        await query_contact_insights_report(client, DB, TENANT, tags=["churn", "vip"])
        count_sql = client.query.call_args_list[0][0][0]
        assert "has" in count_sql

    @pytest.mark.asyncio
    async def test_error_returns_empty_with_error_key(self):
        client = MagicMock()
        client.query = MagicMock(side_effect=RuntimeError("CH timeout"))
        result = await query_contact_insights_report(client, DB, TENANT)
        assert result["data"] == []
        assert "error" in result


# ─── query_agent_performance_daily (Arc 5 MV — v_agent_performance) ──────────

@pytest.mark.asyncio
class TestQueryAgentPerformanceDaily:
    """Tests for the daily MV-backed performance endpoint (v_agent_performance view)."""

    _COLS = [
        "agent_type_id", "pool_id", "period_date",
        "total_sessions", "avg_duration_ms",
        "resolution_rate", "escalation_rate", "transfer_rate", "human_rate",
    ]

    async def test_returns_data_and_meta(self):
        """Empty result set still returns data + meta with date keys."""
        client = _make_client(_ch_result(self._COLS, []))
        result = await query_agent_performance_daily(client, DB, TENANT)
        assert "data" in result
        assert "meta" in result
        assert "from_date" in result["meta"]
        assert "to_date" in result["meta"]
        assert result["meta"]["total"] == 0

    async def test_data_row_mapped_correctly(self):
        """Each row contains all expected columns with correct values."""
        from datetime import date
        client = _make_client(_ch_result(self._COLS, [[
            "agente_sac_v1", "sac_ia", date(2026, 4, 28),
            42,         # total_sessions
            28500.0,    # avg_duration_ms
            0.857143,   # resolution_rate
            0.095238,   # escalation_rate
            0.047619,   # transfer_rate
            0.0,        # human_rate
        ]]))
        result = await query_agent_performance_daily(client, DB, TENANT)
        assert result["meta"]["total"] == 1
        row = result["data"][0]
        assert row["agent_type_id"]   == "agente_sac_v1"
        assert row["pool_id"]         == "sac_ia"
        assert row["total_sessions"]  == 42
        assert row["avg_duration_ms"] == pytest.approx(28500.0)
        assert row["resolution_rate"] == pytest.approx(0.857143)
        assert row["escalation_rate"] == pytest.approx(0.095238)

    async def test_filters_do_not_crash(self):
        """Passing pool_id and agent_type_id filters runs without error."""
        client = _make_client(_ch_result(self._COLS, []))
        result = await query_agent_performance_daily(
            client, DB, TENANT,
            pool_id       = "sac_ia",
            agent_type_id = "agente_sac_v1",
        )
        assert result["data"] == []
        assert result["meta"]["total"] == 0

    async def test_empty_accessible_pools_short_circuits(self):
        """accessible_pools=[] returns empty immediately without hitting ClickHouse."""
        client = MagicMock()
        result = await query_agent_performance_daily(
            client, DB, TENANT, accessible_pools=[]
        )
        assert result["data"] == []
        assert result["meta"]["total"] == 0
        client.query.assert_not_called()

    async def test_error_returns_empty_with_error_key(self):
        """ClickHouse error returns graceful fallback with error key."""
        client = MagicMock()
        client.query = MagicMock(side_effect=Exception("CH timeout"))
        result = await query_agent_performance_daily(client, DB, TENANT)
        assert result["data"] == []
        assert result.get("error") == "data_unavailable"


# ─── query_session_complexity (Arc 5 MV — v_segment_summary) ─────────────────

@pytest.mark.asyncio
class TestQuerySessionComplexity:
    """Tests for the session-complexity MV-backed endpoint (v_segment_summary view)."""

    _COLS = [
        "session_id", "pool_id",
        "segment_count", "primary_segments", "specialist_segments", "human_segments",
        "total_duration_ms", "handoff_count", "escalation_count", "resolved_count",
    ]

    def _count_result(self, n: int) -> MagicMock:
        r = MagicMock()
        r.result_rows = [[n]]
        return r

    async def test_returns_data_and_meta(self):
        """Empty result set still returns data + meta."""
        client = _make_client(
            self._count_result(0),
            _ch_result(self._COLS, []),
        )
        result = await query_session_complexity(client, DB, TENANT)
        assert "data" in result
        assert "meta" in result
        assert result["meta"]["total"] == 0

    async def test_data_row_mapped_correctly(self):
        """Each row maps all expected columns correctly."""
        client = _make_client(
            self._count_result(1),
            _ch_result(self._COLS, [[
                "sess-complex-001", "retencao_humano",
                3,       # segment_count
                2,       # primary_segments
                1,       # specialist_segments
                1,       # human_segments
                125000,  # total_duration_ms
                2,       # handoff_count
                1,       # escalation_count
                0,       # resolved_count
            ]]),
        )
        result = await query_session_complexity(client, DB, TENANT)
        assert result["meta"]["total"] == 1
        row = result["data"][0]
        assert row["session_id"]         == "sess-complex-001"
        assert row["pool_id"]            == "retencao_humano"
        assert row["segment_count"]      == 3
        assert row["handoff_count"]      == 2
        assert row["escalation_count"]   == 1
        assert row["total_duration_ms"]  == 125000

    async def test_min_handoffs_filter(self):
        """min_handoffs parameter is accepted without crashing."""
        client = _make_client(
            self._count_result(0),
            _ch_result(self._COLS, []),
        )
        result = await query_session_complexity(
            client, DB, TENANT, min_handoffs=2
        )
        assert result["data"] == []
        # SQL sent to ClickHouse should reference min_handoffs
        for call in client.query.call_args_list:
            sql = call[0][0]
            assert "handoff_count" in sql

    async def test_empty_accessible_pools_short_circuits(self):
        """accessible_pools=[] returns empty immediately without hitting ClickHouse."""
        client = MagicMock()
        result = await query_session_complexity(
            client, DB, TENANT, accessible_pools=[]
        )
        assert result["data"] == []
        assert result["meta"]["total"] == 0
        client.query.assert_not_called()

    async def test_error_returns_empty_with_error_key(self):
        """ClickHouse error returns graceful fallback with error key."""
        client = MagicMock()
        client.query = MagicMock(side_effect=Exception("CH timeout"))
        result = await query_session_complexity(client, DB, TENANT)
        assert result["data"] == []
        assert result.get("error") == "data_unavailable"


# ── query_agent_availability (Arc 8) ─────────────────────────────────────────

@pytest.mark.asyncio
class TestQueryAgentAvailabilityReport:
    """Unit tests for Arc 8 agent availability report (Fase 1b — login/pause model)."""

    # _fetch_agent_availability (Fase 1b) faz 4 queries, nesta ordem, agrupadas por
    # instance_id (identidade humana). Colunas de cada uma:
    _LOGIN_COLS  = ["instance_id", "pool_id", "period_date", "user_login", "user_id",
                    "agent_type_id", "logged_ms", "total_logins"]
    _PAUSE_COLS  = ["instance_id", "pool_id", "period_date", "agent_type_id",
                    "total_pauses", "total_pause_ms"]
    _REASON_COLS = ["instance_id", "pool_id", "period_date", "reason_id", "reason_label",
                    "cnt", "total_ms"]
    _BUSY_COLS   = ["instance_id", "pool_id", "period_date", "busy_ms"]

    def _make_client_availability(self, login_rows=None, pause_rows=None,
                                  reason_rows=None, busy_rows=None):
        """
        query_agent_availability takes (client, database, tenant_id, ...).
        _fetch_agent_availability faz 4 queries em ordem: login → pause → reason → busy
        (login_intervals, pause_intervals, pause reason breakdown, segments busy).
        Fornecer os 4 resultados — senão o side_effect esgota e o asyncio.to_thread trava.
        """
        return _make_client(
            _ch_result(self._LOGIN_COLS,  login_rows  or []),
            _ch_result(self._PAUSE_COLS,  pause_rows  or []),
            _ch_result(self._REASON_COLS, reason_rows or []),
            _ch_result(self._BUSY_COLS,   busy_rows   or []),
        )

    async def test_returns_data_and_meta(self):
        """Successful call returns data list and meta dict."""
        client = self._make_client_availability()
        result = await query_agent_availability(client, DB, TENANT)
        assert "data" in result
        assert "meta" in result
        assert result["data"] == []
        assert result["meta"]["total"] == 0

    async def test_row_merged_with_reason_breakdown(self):
        """login + pause + busy merge into one row per identity; reasons attached."""
        from datetime import date
        period = date(2026, 5, 1)
        client = self._make_client_availability(
            login_rows=[["human-u1", "retencao_humano", period, "ana@x", "u1",
                         "human_agent_retencao_humano", 5400000, 2]],
            pause_rows=[["human-u1", "retencao_humano", period,
                         "human_agent_retencao_humano", 3, 1800000]],
            reason_rows=[
                ["human-u1", "retencao_humano", period, "intervalo", "Intervalo", 2, 1200000],
                ["human-u1", "retencao_humano", period, "almoco",    "Almoço",    1,  600000],
            ],
            busy_rows=[["human-u1", "retencao_humano", period, 2400000]],
        )
        result = await query_agent_availability(client, DB, TENANT)
        assert result["meta"]["total"] == 1
        row = result["data"][0]
        assert row["instance_id"]    == "human-u1"
        assert row["user_login"]     == "ana@x"
        assert row["pool_id"]        == "retencao_humano"
        assert row["logged_ms"]      == 5400000
        assert row["total_pauses"]   == 3
        assert row["total_pause_ms"] == 1800000
        assert row["busy_ms"]        == 2400000
        # available = logged − paused (clamped at 0)
        assert row["available_ms"]   == 5400000 - 1800000
        breakdown = row["reason_breakdown"]
        assert len(breakdown) == 2
        reasons = {r["reason_id"] for r in breakdown}
        assert "intervalo" in reasons
        assert "almoco"    in reasons

    async def test_empty_accessible_pools_short_circuits(self):
        """accessible_pools=[] returns empty immediately without hitting ClickHouse."""
        client = MagicMock()
        result = await query_agent_availability(
            client, DB, TENANT, accessible_pools=[]
        )
        assert result["data"] == []
        assert result["meta"]["total"] == 0
        # short-circuit: client.query must never be called
        client.query.assert_not_called()

    async def test_none_accessible_pools_calls_ch(self):
        """accessible_pools=None (unrestricted) — ClickHouse is queried normally."""
        client = self._make_client_availability()
        result = await query_agent_availability(
            client, DB, TENANT, accessible_pools=None
        )
        assert client.query.call_count == 4  # login + pause + reason + busy
        assert result["data"] == []

    async def test_pool_filter_injects_in_clause(self):
        """accessible_pools=['retencao_humano'] → IN clause in all queries."""
        client = self._make_client_availability()
        await query_agent_availability(
            client, DB, TENANT, accessible_pools=["retencao_humano"]
        )
        for call in client.query.call_args_list:
            sql = call[0][0]
            assert "pool_id IN ('retencao_humano')" in sql

    async def test_error_returns_empty_with_error_key(self):
        """ClickHouse error returns graceful fallback with error key."""
        client = MagicMock()
        client.query = MagicMock(side_effect=Exception("CH timeout"))
        result = await query_agent_availability(client, DB, TENANT)
        assert result["data"] == []
        assert result.get("error") == "data_unavailable"
