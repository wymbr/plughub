# -*- coding: utf-8 -*-
"""Testes do funil de escrita Python — ALW-02 passo 3.

── Dois casos carregam o peso ───────────────────────────────────────────────────

**`recusa_ANTES_de_escrever_qualquer_uma`.** Uma chamada meio aplicada é pior que uma
recusada: metade das tags gravadas e a outra não, sem nada dizendo quais. A guarda roda
sobre o conjunto inteiro antes do primeiro `hset`.

**`tag_de_journey_e_RECUSADA`.** É o contrato estreito desta metade, e a recusa é decisão:
cair no hash da sessão perderia a tag em 4 h sem erro em lugar nenhum — o defeito exato que
o `writeContextTag` do TS existe para impedir, e que a ALW-02 acabou de consertar no
`bpm.ts`, a OUTRA casa do mesmo `mention_command`. Se este teste virar verde por o funil
passar a rotear, ótimo — mas aí ele tem de mudar junto, deliberadamente.
"""
import asyncio
import json

import pytest

from plughub_contextstore.loader import invalidate_context_map_cache
from plughub_contextstore.writer import ContextScopeRefused, write_context_tags

MAPA = {
    "mode": "audit",
    "dynamic_prefixes": ["segment."],
    "contexto": {"session": {"cliente": {"cpf": {"tipo": "cpf_br", "legado": ["caller.cpf"]}}}},
}
AGORA = "2026-09-02T00:00:00.000Z"


class FakeRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.expires: dict[str, int] = {}

    async def hset(self, key, mapping=None, **kw):
        self.hashes.setdefault(key, {}).update(mapping or {})
        return len(mapping or {})

    async def expire(self, key, ttl):
        self.expires[key] = ttl
        return 1


async def _fetch_ok(url):
    return {"entries": {"context_map": {"value": MAPA}}}


async def _fetch_fora(url):
    raise OSError("ECONNREFUSED")


@pytest.fixture(autouse=True)
def _limpa():
    invalidate_context_map_cache()
    yield
    invalidate_context_map_cache()


def _run(c):
    return asyncio.run(c)


def _lido(r: FakeRedis, tenant: str, sid: str, tag: str) -> dict:
    return json.loads(r.hashes[f"{tenant}:ctx:{sid}"][tag])


class TestCarimbo:
    def test_grava_carimbado_no_hash_da_sessao(self) -> None:
        r = FakeRedis()
        res = _run(write_context_tags(
            r, "t1", "s1", {"session.cliente.cpf": "123"},
            fetch_json=_fetch_ok, source="teste", updated_at=AGORA,
        ))
        assert res["written"] == ["session.cliente.cpf"]
        assert _lido(r, "t1", "s1", "session.cliente.cpf")["atributo"] == {
            "origem": "canonical", "tipo": "cpf_br",
        }

    def test_alias_carimba_a_canonica_e_grava_sob_a_grafia_LEGADA(self) -> None:
        # Renomear no caminho de escrita quebraria todo leitor da grafia velha.
        r = FakeRedis()
        _run(write_context_tags(
            r, "t1", "s1", {"caller.cpf": "123"},
            fetch_json=_fetch_ok, source="teste", updated_at=AGORA,
        ))
        assert "caller.cpf" in r.hashes["t1:ctx:s1"]
        assert _lido(r, "t1", "s1", "caller.cpf")["atributo"] == {
            "origem": "alias", "tipo": "cpf_br", "canonica": "session.cliente.cpf",
        }

    def test_N_tags_num_hset_so_e_cada_uma_com_o_SEU_carimbo(self) -> None:
        # O modo de falha aqui é a segunda tag herdar o carimbo da primeira — é por isso
        # que `stamp_context_entry` não muta a entrada recebida.
        r = FakeRedis()
        _run(write_context_tags(
            r, "t1", "s1",
            {"session.cliente.cpf": "1", "session.nao.cadastrado": "2", "segment.a.b": "3"},
            fetch_json=_fetch_ok, source="teste", updated_at=AGORA,
        ))
        assert _lido(r, "t1", "s1", "session.cliente.cpf")["atributo"]["origem"] == "canonical"
        assert _lido(r, "t1", "s1", "session.nao.cadastrado")["atributo"] == {"origem": "unknown"}
        assert _lido(r, "t1", "s1", "segment.a.b")["atributo"] == {"origem": "dynamic"}

    def test_config_api_fora_grava_do_mesmo_jeito_marcado(self) -> None:
        r = FakeRedis()
        res = _run(write_context_tags(
            r, "t1", "s1", {"core.contact.close_origin": "x"},
            fetch_json=_fetch_fora, source="teste", updated_at=AGORA,
        ))
        assert res["fallback"] is True
        e = _lido(r, "t1", "s1", "core.contact.close_origin")
        assert e["value"] == "x"                          # a escrita ACONTECEU
        assert e["atributo"]["fallback"] is True

    def test_os_campos_do_escritor_chegam_intactos(self) -> None:
        r = FakeRedis()
        _run(write_context_tags(
            r, "t1", "s1", {"session.cliente.cpf": {"aninhado": [1, 2]}},
            fetch_json=_fetch_ok, source="bridge:teste",
            confidence=0.75, visibility=["part_a"], updated_at=AGORA,
        ))
        e = _lido(r, "t1", "s1", "session.cliente.cpf")
        assert e["value"] == {"aninhado": [1, 2]}
        assert e["confidence"] == 0.75
        assert e["source"] == "bridge:teste"
        assert e["visibility"] == ["part_a"]
        assert e["updated_at"] == AGORA


class TestEscopo:
    @pytest.mark.parametrize("tag", [
        "journey.pedido.id", "core.journey.pedido.id",
        "core.customer.nome", "insight.historico.x", "pricing.plano",
    ])
    def test_tag_de_escopo_nao_sessao_e_RECUSADA(self, tag) -> None:
        r = FakeRedis()
        with pytest.raises(ContextScopeRefused, match="nao-sessao"):
            _run(write_context_tags(
                r, "t1", "s1", {tag: "x"},
                fetch_json=_fetch_ok, source="teste", updated_at=AGORA,
            ))

    def test_recusa_ANTES_de_escrever_qualquer_uma(self) -> None:
        # Chamada meio aplicada é pior que recusada: metade gravada, metade não, e nada
        # dizendo quais.
        r = FakeRedis()
        with pytest.raises(ContextScopeRefused):
            _run(write_context_tags(
                r, "t1", "s1",
                {"session.cliente.cpf": "1", "journey.pedido.id": "2"},
                fetch_json=_fetch_ok, source="teste", updated_at=AGORA,
            ))
        assert r.hashes == {}

    def test_a_recusa_NOMEIA_as_tags_e_a_saida(self) -> None:
        r = FakeRedis()
        with pytest.raises(ContextScopeRefused) as e:
            _run(write_context_tags(
                r, "t1", "s1", {"journey.pedido.id": "x"},
                fetch_json=_fetch_ok, source="teste", updated_at=AGORA,
            ))
        assert "journey.pedido.id" in str(e.value)
        assert "context_set" in str(e.value)      # diz para onde ir

    def test_TESTEMUNHA_customer_PONTO_nao_e_recusada(self) -> None:
        # `customer.` NÃO roteia para o hash do cliente — o nome do store e o prefixo que
        # roteia para ele não são a mesma string. Sem esta testemunha, uma guarda que
        # recusasse por SUBSTRING passaria em todos os casos acima.
        r = FakeRedis()
        _run(write_context_tags(
            r, "t1", "s1", {"customer.nome": "x"},
            fetch_json=_fetch_ok, source="teste", updated_at=AGORA,
        ))
        assert "customer.nome" in r.hashes["t1:ctx:s1"]


class TestTtl:
    def test_ttl_aplicado_quando_pedido(self) -> None:
        r = FakeRedis()
        _run(write_context_tags(
            r, "t1", "s1", {"session.cliente.cpf": "1"},
            fetch_json=_fetch_ok, source="t", updated_at=AGORA, ttl_s=86_400,
        ))
        assert r.expires["t1:ctx:s1"] == 86_400

    def test_sem_ttl_NAO_toca_na_expiracao(self) -> None:
        # Encurtar um TTL que outro componente já pôs é o defeito que o `EXPIRE ... NX`
        # do routing-engine existe para evitar; escrever um TTL não pedido seria pior.
        r = FakeRedis()
        _run(write_context_tags(
            r, "t1", "s1", {"session.cliente.cpf": "1"},
            fetch_json=_fetch_ok, source="t", updated_at=AGORA,
        ))
        assert r.expires == {}


class TestVazio:
    def test_mapa_de_tags_vazio_nao_escreve_nem_vai_a_rede(self) -> None:
        r = FakeRedis()
        chamadas = []

        async def f(url):
            chamadas.append(url)
            return {"entries": {"context_map": {"value": MAPA}}}

        res = _run(write_context_tags(
            r, "t1", "s1", {}, fetch_json=f, source="t", updated_at=AGORA,
        ))
        assert res["written"] == []
        assert r.hashes == {}
        assert chamadas == []
