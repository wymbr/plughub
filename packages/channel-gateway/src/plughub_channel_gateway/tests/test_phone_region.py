"""
test_phone_region.py — IDN-14 (2026-09-13)

A normalização de telefone assumia DDI presente (`"+" + dígitos`): `11 99999-0001`
virava `+11999990001` e não casava com `+55 11 99999-0001` — o mesmo celular, dois
hashes. Agora o número vira E.164 com o país padrão do TENANT (config-api
`identity.default_phone_region`), telefone sem DDI e sem país é RECUSADO, e valor com
letra não é telefone (o `contact_identifier` `cli_…` do webchat virava âncora phone).

Contra o config-api e o cadastro reais: `infra/test/probe_phone_region.sh`.
"""
from __future__ import annotations

import hashlib
import logging

import pytest

from plughub_channel_gateway.identity import region as region_mod
from plughub_channel_gateway.identity.normalize import hash_anchor, normalize_anchor
from plughub_channel_gateway.identity.region import PhoneRegionConfig, anchor_hash

SALT = "s"


class TestNormalizacao:
    @pytest.mark.parametrize("digitado", [
        "11 99999-0001", "(11) 99999-0001", "11999990001",       # nacional
        "5511999990001", "55 11 99999-0001",                     # DDI sem '+'
        "+55 11 99999-0001", "+5511999990001",                   # internacional
    ])
    def test_as_formas_do_mesmo_celular_dao_o_mesmo_e164(self, digitado):
        assert normalize_anchor("phone", digitado, "BR") == "+5511999990001"

    def test_com_mais_a_regiao_nao_entra(self):
        assert normalize_anchor("phone", "+55 11 99999-0001", "US") == "+5511999990001"
        assert normalize_anchor("phone", "+1 415 555 0100", "BR") == "+14155550100"

    def test_ddd_55_nao_e_confundido_com_o_ddi(self):
        # 11 dígitos com DDD 55 (RS) é número NACIONAL possível — o parser não o come.
        assert normalize_anchor("phone", "55 99999-0001", "BR") == "+5555999990001"

    def test_sem_ddi_e_sem_regiao_recusa(self):
        with pytest.raises(ValueError, match="no default region"):
            normalize_anchor("phone", "11 99999-0001", None)

    def test_controle_com_mais_e_sem_regiao_aceita(self):
        assert normalize_anchor("phone", "+5511999990001", None) == "+5511999990001"

    @pytest.mark.parametrize("valor", ["cli_52989317358", "11 9999-ABCD", "1-800-FLOWERS"])
    def test_valor_com_letra_nao_e_telefone(self, valor):
        with pytest.raises(ValueError, match="non-phone"):
            normalize_anchor("phone", valor, "BR")

    def test_numero_impossivel_recusa(self):
        with pytest.raises(ValueError, match="not a possible"):
            normalize_anchor("phone", "123", "BR")

    def test_regiao_desconhecida_recusa(self):
        with pytest.raises(ValueError, match="unknown phone region"):
            normalize_anchor("phone", "11999990001", "XX")

    def test_hash_de_quem_ja_tinha_ddi_nao_muda(self):
        # A regra antiga era sha256(salt + "+" + dígitos). Âncora gravada COM DDI tem de
        # manter o hash — senão a mudança órfã o cadastro inteiro, não só o legado sem DDI.
        antigo = hashlib.sha256((SALT + "+5511999990001").encode()).hexdigest()
        assert hash_anchor(SALT, "phone", "+55 (11) 99999-0001", "BR") == antigo
        assert hash_anchor(SALT, "phone", "11 99999-0001", "BR") == antigo

    def test_outros_kinds_ignoram_a_regiao(self):
        assert normalize_anchor("cpf", "123.456.789-09", None) == "12345678909"
        assert normalize_anchor("email", "A@B.com", None) == "a@b.com"


class _Resp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body


class _Client:
    """Substitui `httpx.AsyncClient`, contando as chamadas."""
    respostas: list = []
    chamadas: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None):
        _Client.chamadas.append((url, params))
        r = _Client.respostas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def cliente(monkeypatch):
    # O alvo existe no módulo — o mock não o cria (VOZ-03).
    assert hasattr(region_mod.httpx, "AsyncClient")
    monkeypatch.setattr(region_mod.httpx, "AsyncClient", _Client)
    _Client.respostas, _Client.chamadas = [], []
    return _Client


class TestPhoneRegionConfig:
    async def test_le_a_chave_do_tenant_e_guarda_em_cache(self, cliente):
        cliente.respostas = [_Resp(200, {"value": "br"})]
        cfg = PhoneRegionConfig("http://cfg")
        assert await cfg("t1") == "BR"
        assert await cfg("t1") == "BR"
        assert len(cliente.chamadas) == 1
        url, params = cliente.chamadas[0]
        assert url == "http://cfg/config/identity/default_phone_region" and params == {"tenant_id": "t1"}

    async def test_invalidate_rele(self, cliente):
        cliente.respostas = [_Resp(200, {"value": "BR"}), _Resp(200, {"value": "PT"})]
        cfg = PhoneRegionConfig("http://cfg")
        assert await cfg("t1") == "BR"
        cfg.invalidate("t1")
        assert await cfg("t1") == "PT"

    async def test_mudanca_do_default_global_invalida_todo_tenant(self, cliente):
        # o config.changed do global chega com tenant_id="__global__"
        cliente.respostas = [_Resp(200, {"value": "BR"}), _Resp(200, {"value": "BR"}),
                             _Resp(200, {"value": "PT"}), _Resp(200, {"value": "PT"})]
        cfg = PhoneRegionConfig("http://cfg")
        await cfg("t1"), await cfg("t2")
        cfg.invalidate("__global__")
        assert (await cfg("t1"), await cfg("t2")) == ("PT", "PT")

    async def test_nao_configurado_e_none_e_avisa_o_que_deixa_de_valer(self, cliente, caplog):
        cliente.respostas = [_Resp(404)]
        with caplog.at_level(logging.WARNING):
            assert await PhoneRegionConfig("http://cfg")("t1") is None
        assert "RECUSADO" in caplog.text

    async def test_valor_invalido_e_none_com_erro(self, cliente, caplog):
        cliente.respostas = [_Resp(200, {"value": "Brasil"})]
        with caplog.at_level(logging.ERROR):
            assert await PhoneRegionConfig("http://cfg")("t1") is None
        assert "Brasil" in caplog.text

    async def test_falha_de_leitura_mantem_o_ultimo_valor_e_retenta(self, cliente):
        cliente.respostas = [_Resp(200, {"value": "BR"}), RuntimeError("down"), _Resp(200, {"value": "PT"})]
        cfg = PhoneRegionConfig("http://cfg", retry_s=0.0)
        assert await cfg("t1") == "BR"
        cfg.invalidate("t1")
        # a invalidação apaga o cache: a falha seguinte não tem o que manter
        assert await cfg("t1") is None
        assert await cfg("t1") == "PT", "falha não é definitiva — retenta"

    async def test_falha_nao_derruba_o_valor_bom_sem_invalidate(self, cliente):
        cliente.respostas = [RuntimeError("down"), _Resp(200, {"value": "BR"})]
        cfg = PhoneRegionConfig("http://cfg", retry_s=0.0)
        assert await cfg("t1") is None
        assert await cfg("t1") == "BR"


class TestAnchorHash:
    async def test_so_telefone_consulta_a_regiao(self):
        chamados = []

        async def fonte(tenant):
            chamados.append(tenant)
            return "BR"
        await anchor_hash(SALT, fonte, "t1", "cpf", "12345678909")
        assert chamados == []
        await anchor_hash(SALT, fonte, "t1", "phone", "11999990001")
        assert chamados == ["t1"]

    async def test_recusa_de_telefone_loga_o_porque(self, caplog):
        with caplog.at_level(logging.WARNING), pytest.raises(ValueError):
            await anchor_hash(SALT, None, "t1", "phone", "11999990001")
        assert "ancora phone recusada" in caplog.text
