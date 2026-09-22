"""
test_navigation_routing.py — ORQ-14: o roteamento do orquestrador acertou?

A proposição: *`re_roteados` conta o contato cujo DESTINO não concluiu e cujo
atendimento seguiu em OUTRO pool* — e só isso. Os testes existem porque as três
exclusões são o que separa o número de uma contagem de qualquer coisa:

  · hook (NPS, wrap-up) e convidado NÃO são continuação — senão todo contato com NPS
    seria "re-roteado", e a taxa diria 100% num parque saudável;
  · a volta ao ORQUESTRADOR é o *"tenho outro assunto"* do cliente — uma decisão nova;
  · quem nunca chegou a um destino sai da BASE, nunca conta como acerto.

O que faria isto ficar vermelho: somar `sem_destino` na base (a folha abandonada
pareceria a melhor), esquecer o `FINAL` (o mesmo segmento contado duas vezes), ou
deixar o array de segmentos sem ordem (a cadeia deixa de ser cadeia).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ..reports_query import _fetch_navigation_routing, _proximos_distintos

DB     = "plughub"
TENANT = "tenant_demo"

_COLS = ["orquestrador", "destino", "contatos", "sem_destino", "re_roteados",
         "sem_cadeia", "com_renavegacao", "proximos_ref"]


def _client(rows) -> MagicMock:
    r = MagicMock()
    r.column_names = _COLS
    r.result_rows  = rows
    c = MagicMock()
    c.query = MagicMock(return_value=r)
    return c


def _rodar(rows, **kw) -> dict:
    c = _client(rows)
    out = _fetch_navigation_routing(c, DB, TENANT, "2026-09-01 00:00:00",
                                    "2026-09-22 23:59:59", kw.pop("pool_id", None),
                                    kw.pop("accessible_pools", None))
    out["_sql"] = c.query.call_args[0][0]
    out["_params"] = c.query.call_args[1]["parameters"]
    return out


class TestOSQLExecutado:
    """A regra mora no SQL, então é o SQL EXECUTADO que se asserta — `grep` no fonte
    contaria o comentário que documenta a regra."""

    def _sql(self, **kw) -> str:
        return _rodar([], **kw)["_sql"]

    def test_so_segmento_que_ATENDE_entra_na_cadeia(self):
        sql = self._sql()
        assert "role = 'primary'" in sql          # hook/convidado fora
        assert "agent_type != 'system'" in sql    # agente de fila fora

    def test_a_cadeia_e_ORDENADA_e_deduplicada(self):
        sql = self._sql()
        assert "segments FINAL" in sql            # ReplacingMergeTree: sem isto, linha dupla
        assert "ORDER BY started_at, segment_id" in sql

    def test_a_volta_ao_orquestrador_fecha_a_janela(self):
        sql = self._sql()
        assert "indexOf(resto_ref, orquestrador_ref)" in sql
        assert "arraySlice(resto_ref, 1, i_volta_ref - 1)" in sql

    def test_alias_de_agregado_nunca_repete_coluna_real(self):
        """`any(pool_id) AS pool_id` derruba a query inteira (code 184) e o wrapper
        devolve `data: []`, que se lê como 'não há dado'."""
        sql = self._sql()
        for proibido in ("AS pool_id", "AS session_id", "AS category"):
            assert proibido not in sql

    def test_escopo_de_pool_entra_no_filtro(self):
        assert "pool_id IN ('demo_llm_ia')" in self._sql(accessible_pools=["demo_llm_ia"])

    def test_recorte_por_orquestrador_e_parametro(self):
        out = _rodar([], pool_id="demo_ia")
        assert out["_params"]["pool_id"] == "demo_ia"


class TestContagem:
    def test_taxa_exclui_quem_nunca_chegou_a_um_destino(self):
        # 10 contatos, 2 sem destino, 2 re-roteados ⇒ 2/8, nunca 2/10
        out = _rodar([["demo_llm_ia", "sac.especialista", 10, 2, 2, 0, 0, ["sac_ia"]]])
        linha = out["data"][0]
        assert (linha["atendidos"], linha["re_roteados"]) == (8, 2)
        assert linha["taxa_re_roteio"] == 0.25

    def test_sem_ninguem_atendido_a_taxa_e_NAO_MEDIDA(self):
        """`None`, nunca 0.0: zero é ponto legítimo da escala e diria 'nunca erra'."""
        out = _rodar([["demo_llm_ia", "portabilidade", 3, 3, 0, 0, 0, []]])
        assert out["data"][0]["taxa_re_roteio"] is None
        assert out["meta"]["taxa_re_roteio"] is None

    def test_meta_soma_as_linhas_e_declara_o_que_o_numero_e(self):
        out = _rodar([
            ["demo_llm_ia", "sac.especialista", 10, 2, 2, 0, 1, ["sac_ia"]],
            ["demo_llm_ia", "aumento_limite",    6, 0, 3, 1, 0, ["sac_ia,retencao_humano"]],
        ])
        m = out["meta"]
        assert (m["contatos"], m["atendidos"], m["re_roteados"]) == (16, 14, 5)
        assert m["taxa_re_roteio"] == round(5 / 14, 4)
        assert m["sem_cadeia"] == 1 and m["com_renavegacao"] == 1
        assert "proxy" in m["sinal"]      # o nome do número é o que ele mede

    def test_proximos_aponta_a_folha_que_falta(self):
        """O campo acionável: um destino que sempre termina no mesmo outro pool é uma
        folha pedindo para existir — o `aumento_limite` da ORQ-11 apareceria aqui."""
        out = _rodar([["demo_llm_ia", "sac.especialista", 4, 0, 4, 0, 0,
                       ["limite_ia", "limite_ia,sac_ia"]]])
        assert out["data"][0]["proximos"] == ["limite_ia", "sac_ia"]


class TestProximosDistintos:
    def test_desdobra_a_sequencia_preservando_a_ordem_e_sem_repetir(self):
        assert _proximos_distintos(["a,b", "a", "c,b"]) == ["a", "b", "c"]

    def test_teto_de_tres_e_vazio_nao_inventa(self):
        assert _proximos_distintos(["a,b,c,d"]) == ["a", "b", "c"]
        assert _proximos_distintos(None) == [] and _proximos_distintos([]) == []
