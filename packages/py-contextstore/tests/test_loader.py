# -*- coding: utf-8 -*-
"""Testes do carregador — ALW-02 passo 3.

── O caso que carrega o peso ────────────────────────────────────────────────────

`mapa_MALFORMADO_cai_no_fallback_inteiro`. É ele que prova que esta metade se comporta
como o `safeParse` do Zod no lado TS: mapa torto não vira índice pela metade, vira fallback
DECLARADO. Sem ele, o carregador poderia tolerar folha ruim e carimbar a partir de meio mapa
afirmando `fallback: false` — dizendo *"o tipo é o que o tenant declarou"* sobre folhas
descartadas em silêncio. Um índice com 40 das 94 folhas funciona perfeitamente para as 40.

── E o segundo: o carregador NUNCA levanta ─────────────────────────────────────

Uma escrita no ContextStore não pode ficar refém do config-api. Se este arquivo deixar de
provar isso, a próxima queda de config leva junto o caminho de escrita de cinco serviços.
"""
import asyncio

import pytest

from plughub_contextstore import resolve_context_tag
from plughub_contextstore.default_map import DEFAULT_CONTEXT_MAP
from plughub_contextstore.loader import (
    context_map_url,
    get_context_map,
    invalidate_context_map_cache,
    set_context_map_fetcher,
)

MAPA_TENANT = {
    "mode": "audit",
    "dynamic_prefixes": ["segment."],
    "contexto": {"session": {"proprio": {"campo": {"tipo": "cpf_br"}}}},
}


def _corpo(mapa):
    """Envelope como o config-api o devolve."""
    return {"entries": {"context_map": {"value": mapa}}}


def _fetcher(resultado=None, erro=None, registro=None):
    async def f(url):
        if registro is not None:
            registro.append(url)
        if erro is not None:
            raise erro
        return resultado
    return f


@pytest.fixture(autouse=True)
def _limpa_cache():
    invalidate_context_map_cache()
    set_context_map_fetcher(None)
    yield
    invalidate_context_map_cache()
    set_context_map_fetcher(None)


def _run(coro):
    return asyncio.run(coro)


class TestCaminhoFeliz:
    def test_le_o_mapa_do_tenant_e_NAO_marca_fallback(self) -> None:
        idx, fb = _run(get_context_map("t1", _fetcher(_corpo(MAPA_TENANT))))
        assert fb is False
        assert resolve_context_tag("session.proprio.campo", idx).tipo == "cpf_br"

    def test_aceita_o_valor_DIRETO_sem_envelope(self) -> None:
        # As duas formas já apareceram; o gêmeo TS também tolera as duas.
        idx, fb = _run(get_context_map("t1", _fetcher({"context_map": MAPA_TENANT})))
        assert fb is False
        assert resolve_context_tag("session.proprio.campo", idx).tipo == "cpf_br"

    def test_a_url_carrega_o_tenant_id(self) -> None:
        # Sem `?tenant_id=` o config-api devolve 422 — foi uma das três causas
        # empilhadas que deixaram o namespace `session` do bridge inerte.
        reg: list[str] = []
        _run(get_context_map("t 1/x", _fetcher(_corpo(MAPA_TENANT), registro=reg)))
        assert "tenant_id=t%201%2Fx" in reg[0]
        assert "/config/masking" in reg[0]

    def test_a_porta_default_e_3600(self) -> None:
        # O `CLAUDE.md` registra um default hardcoded apontando para :3500
        # (analytics-api) que deixou um namespace inteiro inerte.
        assert ":3600/config/masking" in context_map_url("t1")


class TestFallback:
    @pytest.mark.parametrize("resposta, porque", [
        (None,                      "corpo nulo"),
        ({},                        "sem a chave"),
        ({"entries": {}},           "envelope sem a chave"),
        ("texto",                   "corpo não é objeto"),
        (_corpo({"contexto": {"s": {"d": {"c": {}}}}}), "folha sem tipo"),
        (_corpo({"contexto": "x"}), "contexto não é objeto"),
    ])
    def test_mapa_MALFORMADO_cai_no_fallback_inteiro(self, resposta, porque) -> None:
        idx, fb = _run(get_context_map("t1", _fetcher(resposta)))
        assert fb is True, porque
        # E o índice é o EMBUTIDO INTEIRO, nunca um pedaço do que veio torto.
        assert resolve_context_tag("core.contact.close_origin", idx).origin == "canonical"
        assert resolve_context_tag("session.proprio.campo", idx).origin == "unknown"

    def test_falha_de_REDE_cai_no_fallback_e_NAO_levanta(self) -> None:
        # Escrita no ContextStore não pode ficar refém do config-api.
        idx, fb = _run(get_context_map("t1", _fetcher(erro=OSError("ECONNREFUSED"))))
        assert fb is True
        assert resolve_context_tag("core.contact.close_origin", idx).origin == "canonical"

    def test_o_aviso_NOMEIA_o_que_deixa_de_valer(self, caplog) -> None:
        # `logger.warning("using default values")` existiu no bridge e ninguém leu por
        # meses. O texto tem de dizer QUAIS fatos param de ser verdadeiros.
        with caplog.at_level("WARNING"):
            _run(get_context_map("t1", _fetcher(erro=OSError("x"))))
        texto = caplog.text
        assert "atributo.fallback" in texto
        assert "unknown" in texto
        assert "t1" in texto

    def test_TESTEMUNHA_mapa_bom_nao_cai_no_fallback(self) -> None:
        # Sem esta, um carregador que caísse SEMPRE passaria em todos os casos acima.
        _, fb = _run(get_context_map("t1", _fetcher(_corpo(MAPA_TENANT))))
        assert fb is False


class TestCache:
    def test_a_segunda_chamada_NAO_vai_a_rede(self) -> None:
        reg: list[str] = []
        f = _fetcher(_corpo(MAPA_TENANT), registro=reg)
        _run(get_context_map("t1", f))
        _run(get_context_map("t1", f))
        assert len(reg) == 1

    def test_o_cache_e_POR_TENANT(self) -> None:
        reg: list[str] = []
        f = _fetcher(_corpo(MAPA_TENANT), registro=reg)
        _run(get_context_map("t1", f))
        _run(get_context_map("t2", f))
        assert len(reg) == 2

    def test_o_FALLBACK_tambem_e_cacheado(self) -> None:
        # Sem isto, config-api fora significaria uma tentativa de rede POR ESCRITA no
        # caminho quente — e com timeout, não `ECONNREFUSED` imediato.
        reg: list[str] = []
        f = _fetcher(erro=OSError("x"), registro=reg)
        _run(get_context_map("t1", f))
        _, fb = _run(get_context_map("t1", f))
        assert len(reg) == 1
        assert fb is True

    def test_invalidar_forca_nova_leitura(self) -> None:
        reg: list[str] = []
        f = _fetcher(_corpo(MAPA_TENANT), registro=reg)
        _run(get_context_map("t1", f))
        invalidate_context_map_cache("t1")
        _run(get_context_map("t1", f))
        assert len(reg) == 2

    def test_invalidar_SEM_tenant_limpa_tudo(self) -> None:
        # É o que o consumidor de `config.changed` chama quando não sabe o afetado.
        reg: list[str] = []
        f = _fetcher(_corpo(MAPA_TENANT), registro=reg)
        _run(get_context_map("t1", f))
        _run(get_context_map("t2", f))
        invalidate_context_map_cache()
        _run(get_context_map("t1", f))
        _run(get_context_map("t2", f))
        assert len(reg) == 4


class TestMapaEmbutido:
    def test_o_embutido_e_valido_pelo_construtor_ESTRITO(self) -> None:
        # Se este falhar, o pacote está quebrado na origem: o fallback não funcionaria
        # justamente no momento em que é preciso.
        from plughub_contextstore import build_context_tag_index
        idx = build_context_tag_index(DEFAULT_CONTEXT_MAP)
        assert len(idx.canonical) >= 90     # 94 na medição de 2026-08-30
        assert len(idx.alias) >= 80         # 82 na mesma medição
        assert "core.segment." in idx.dynamic_prefixes


class TestTransporteRegistrado:
    """O serviço registra o transporte uma vez no boot; os escritores fire-and-forget não
    têm o cliente HTTP à mão e enfiá-lo por dez assinaturas seria pior."""

    def test_usa_o_fetcher_registrado_quando_nenhum_e_passado(self) -> None:
        reg: list[str] = []
        set_context_map_fetcher(_fetcher(_corpo(MAPA_TENANT), registro=reg))
        idx, fb = _run(get_context_map("t1"))
        assert fb is False
        assert len(reg) == 1
        assert resolve_context_tag("session.proprio.campo", idx).tipo == "cpf_br"

    def test_o_argumento_VENCE_o_registrado(self) -> None:
        set_context_map_fetcher(_fetcher(erro=OSError("registrado")))
        _, fb = _run(get_context_map("t1", _fetcher(_corpo(MAPA_TENANT))))
        assert fb is False

    def test_SEM_transporte_cai_no_fallback_e_diz_por_que(self, caplog) -> None:
        # Esquecer o registro é degradação diagnosticável, nunca crash — mas o aviso
        # tem de nomear a causa, senão parece queda de rede.
        with caplog.at_level("WARNING"):
            _, fb = _run(get_context_map("t1"))
        assert fb is True
        assert "set_context_map_fetcher" in caplog.text
