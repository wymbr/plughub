# -*- coding: utf-8 -*-
"""Testes da metade Python — ALW-02 passo 2.

── O que é testado AQUI e o que é testado no GATE ───────────────────────────────

O gate de paridade (`infra/test/probe_context_stamp_parity.sh`) já compara as duas
implementações caso a caso sobre uma fixture única. Repetir aquilo aqui seria medir a mesma
proposição duas vezes.

Este arquivo mede o que o gate **deliberadamente não compara**, e por isso os casos são
outros:

  1. **A ESTRITUDE do construtor do índice.** Ela existe justamente porque as duas metades
     tratam mapa malformado em bordas diferentes — o TS pelo Zod (`getContextMap` descarta e
     cai no embutido, marcando `fallback: true`), esta pelo `ValueError` que o carregador vai
     capturar. Comparar o MEIO desse caminho mediria a implementação, não o contrato.
  2. **A rota de retenção** (`resolve_context_store`), que não entra no carimbo mas viaja no
     mesmo pacote e será usada pelos escritores Python no passo 3.

── Por que a estritude tem tantos casos ─────────────────────────────────────────

Porque o modo de falha que ela impede é mudo e caro: sem ela, um mapa pela metade produziria
um índice pela metade, e o carimbo sairia afirmando `fallback: false` — isto é, dizendo *"o
tipo é o que o tenant declarou"* sobre folhas que foram descartadas em silêncio. É a família
do valor plausível: um índice com 40 das 94 folhas funciona perfeitamente para as 40.
"""
import pytest

from plughub_contextstore import (
    DEFAULT_DYNAMIC_PREFIXES,
    build_context_tag_index,
    resolve_context_store,
    resolve_context_tag,
    stamp_context_entry,
)

MAPA_OK = {
    "mode": "audit",
    "dynamic_prefixes": ["segment."],
    "contexto": {
        "session": {"cliente": {"cpf": {"tipo": "cpf_br", "legado": ["caller.cpf"]}}},
    },
}


class TestEstritude:
    """Mapa malformado LEVANTA — o carregador captura e cai no embutido, como o Zod."""

    @pytest.mark.parametrize("mapa, porque", [
        ({},                                    "sem `contexto`"),
        ({"contexto": None},                    "`contexto` nulo"),
        ({"contexto": []},                      "`contexto` não é objeto"),
        ({"contexto": {"s": "x"}},              "escopo não é objeto"),
        ({"contexto": {"s": {"d": "x"}}},       "domínio não é objeto"),
        ({"contexto": {"s": {"d": {"c": 1}}}},  "folha não é objeto"),
        ({"contexto": {"s": {"d": {"c": {}}}}}, "folha sem `tipo`"),
        ({"contexto": {"s": {"d": {"c": {"tipo": ""}}}}},          "`tipo` vazio"),
        ({"contexto": {"s": {"d": {"c": {"tipo": 7}}}}},           "`tipo` não é string"),
        ({"contexto": {"s": {"d": {"c": {"tipo": "t", "legado": "a"}}}}},  "`legado` é string"),
        ({"contexto": {"s": {"d": {"c": {"tipo": "t", "legado": [1]}}}}},  "alias não é string"),
        ({"contexto": {}, "dynamic_prefixes": "segment."},         "prefixos é string"),
        ({"contexto": {}, "dynamic_prefixes": [1]},                "prefixo não é string"),
    ])
    def test_mapa_malformado_levanta(self, mapa, porque) -> None:
        with pytest.raises(ValueError):
            build_context_tag_index(mapa)

    def test_a_mensagem_NOMEIA_a_folha(self) -> None:
        # Recusa muda manda quem opera adivinhar qual das 94 folhas está torta.
        with pytest.raises(ValueError, match=r"session\.cliente\.cpf"):
            build_context_tag_index(
                {"contexto": {"session": {"cliente": {"cpf": {"tipo": None}}}}},
            )

    def test_TESTEMUNHA_o_mapa_bom_passa(self) -> None:
        # Sem esta, um construtor que levantasse SEMPRE passaria em todos os casos acima.
        idx = build_context_tag_index(MAPA_OK)
        assert idx.canonical == {"session.cliente.cpf": "cpf_br"}
        assert idx.alias == {"caller.cpf": "session.cliente.cpf"}

    def test_legado_ausente_e_valido_e_nao_e_o_mesmo_que_vazio(self) -> None:
        # `legado` é opcional no schema — ausência não pode virar recusa.
        idx = build_context_tag_index(
            {"contexto": {"s": {"d": {"c": {"tipo": "t"}}}}, "dynamic_prefixes": []},
        )
        assert idx.alias == {}


class TestPrefixosDinamicos:
    def test_ausente_vira_o_DEFAULT_do_schema(self) -> None:
        # Cair em lista vazia faria toda tag `segment.*` virar `unknown` e inflar a
        # população que a V4 conta. Ausente é mapa VÁLIDO no Zod, com `.default()`.
        idx = build_context_tag_index({"contexto": {}})
        assert tuple(idx.dynamic_prefixes) == tuple(DEFAULT_DYNAMIC_PREFIXES)
        assert resolve_context_tag("segment.x.y", idx).origin == "dynamic"

    def test_VAZIO_declarado_e_diferente_de_ausente(self) -> None:
        idx = build_context_tag_index({"contexto": {}, "dynamic_prefixes": []})
        assert resolve_context_tag("segment.x.y", idx).origin == "unknown"


class TestRotaDeRetencao:
    """`resolve_context_store` — não entra no carimbo, mas roteia hash e TTL."""

    @pytest.mark.parametrize("tag, store", [
        ("session.cliente.cpf",        "session"),
        ("core.contact.close_origin",  "session"),
        ("qualquer.coisa.do.tenant",   "session"),
        ("journey.pedido.id",          "journey"),
        ("core.journey.pedido.id",     "journey"),
        ("insight.historico.x",        "customer"),
        ("pricing.plano",              "customer"),
        ("core.customer.nome",         "customer"),
    ])
    def test_rotas(self, tag, store) -> None:
        assert resolve_context_store(tag) == store

    def test_customer_PONTO_nao_roteia_para_o_hash_do_cliente(self) -> None:
        # O nome do STORE e o prefixo que roteia para ele não são a mesma string, e
        # confundi-los moveria dado de retenção trimestral para um hash de 4 h sem erro
        # em lugar nenhum. O oráculo do mapa acusa isso como `mismatched_retention`.
        assert resolve_context_store("customer.nome") == "session"


class TestCarimboNaoMuta:
    def test_a_entrada_recebida_fica_intacta(self) -> None:
        # O escritor reusa o objeto num `mapping=` de N tags; mutar faria a segunda tag
        # herdar o carimbo da primeira.
        idx = build_context_tag_index(MAPA_OK)
        entrada = {"value": "x", "confidence": 1.0}
        stamp_context_entry(entrada, "session.cliente.cpf", idx, False)
        assert "atributo" not in entrada
