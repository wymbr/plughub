"""
test_aas08_a2a_interop.py — AAS-08 (2026-10-01): o que o SDK oficial do A2A exigiu da porta.

PROPOSIÇÕES (spec A2A v1.0 § 3.6 e § 14.2.1; medição com a2a-sdk 1.2.1 e @a2a-js/sdk 1.3.0):
  · `A2A-Version` é comparado por Major.Minor; AUSENTE vale 0.3 — e 0.3 não é falado aqui, logo
    é `VersionNotSupportedError` (-32009) com o `id` ecoado, ANTES de chegar ao adapter;
  · a versão também vale como parâmetro de query; patch é ignorado;
  · o endereço do agente com barra final (como o card o anuncia) chega ao MESMO adapter que o
    sem barra — o SDK JS resolve `.well-known/agent-card.json` relativo a ele e perde o slug sem
    a barra, então o card publica com ela e a porta aceita as duas formas.
"""
from __future__ import annotations

import pytest

from plughub_channel_gateway import a2a_tasks as at
from plughub_channel_gateway import main as gw_main
from plughub_channel_gateway.tests.test_aas04_a2a_principal import OK, porta  # noqa: F401 — fixture

RPC = {"jsonrpc": "2.0", "id": 9, "method": "GetTask", "params": {"id": "x"}}


@pytest.mark.parametrize("header,query", [("1.0", None), ("1.0.3", None), (" 1.0 ", None), (None, "1.0")])
def test_versao_falada_passa(header, query):
    assert at.version_error(header, query) is None


@pytest.mark.parametrize("header,query,pedida,assumida", [
    (None, None, "0.3", True),        # ausente = 0.3 (spec § 3.6), e 0.3 não é falada aqui
    ("", None, "0.3", True),
    ("0.3", None, "0.3", False),
    ("1.1", None, "1.1", False),
    ("2.0", None, "2.0", False),
    ("1", None, "1", False),          # sem Minor não casa Major.Minor
    (None, "0.3", "0.3", False),
])
def test_versao_nao_falada_e_32009_e_nomeia_o_que_foi_pedido(header, query, pedida, assumida):
    e = at.version_error(header, query)
    assert e is not None and e.code == -32009
    assert e.data == {"requested": pedida, "assumed": assumida, "supported": ["1.0"]}


class _Svc:
    def __init__(self):
        self.chamadas = 0

    async def handle(self, caller, req):
        self.chamadas += 1
        return {"jsonrpc": "2.0", "id": req.get("id"), "result": {"ok": True}}


def test_porta_sem_versao_recusa_antes_do_adapter_com_id_ecoado(porta, monkeypatch):  # noqa: F811
    c, _ = porta(OK)
    svc = _Svc()
    monkeypatch.setattr(gw_main, "_a2a_service", lambda: svc)
    r = c.post("/a2a/segunda-via", json=RPC, headers={"Authorization": "Bearer y"})
    assert r.status_code == 200
    assert r.json()["id"] == 9 and r.json()["error"]["code"] == -32009
    assert svc.chamadas == 0


@pytest.mark.parametrize("caminho,headers", [
    ("/a2a/segunda-via", {"A2A-Version": "1.0"}),
    ("/a2a/segunda-via/", {"A2A-Version": "1.0"}),          # como o card anuncia
    ("/a2a/segunda-via/?A2A-Version=1.0", {}),               # versão por query
])
def test_controle_versao_falada_chega_ao_adapter_com_ou_sem_barra(porta, monkeypatch, caminho, headers):  # noqa: F811
    c, visto = porta(OK)
    svc = _Svc()
    monkeypatch.setattr(gw_main, "_a2a_service", lambda: svc)
    r = c.post(caminho, json=RPC, headers={"Authorization": "Bearer y", **headers})
    assert r.status_code == 200 and r.json() == {"jsonrpc": "2.0", "id": 9, "result": {"ok": True}}
    assert svc.chamadas == 1 and visto["resolve"][0]["identifier"] == "segunda-via"
