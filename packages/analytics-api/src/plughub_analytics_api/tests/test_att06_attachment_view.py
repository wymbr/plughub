"""test_att06_attachment_view.py — ATT-06 (2026-10-01): nítido para quem atende, borrado para os
demais, e o "revelar" na trilha.

PROPOSIÇÃO: o original de um anexo só sai para quem ATENDE o contato (`human-{sub}` no roster da
sessão com papel de atendimento) ou para serviço; supervisor, avaliador e replay recebem a prévia
borrada, e o original só com `reveal`, que a trilha distingue (`revealed`).

Os dois lados de cada ramo: o atendente recebe o original (o portão deixa passar), o vizinho que
não atende não recebe — inclusive quando o roster não responde.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi import HTTPException

from plughub_analytics_api.tests.test_att02_attachment_door import (  # noqa: F401 — fixtures
    ATENDE,
    GRANT,
    SID,
    _Gateway,
    _user,
    _ver,
    pools_da_sessao,
    trilha,
)

SUPERVISOR = [{"participant_id": "human-u1", "role": "supervisor"}]
OUTRO = [{"participant_id": "human-u2", "role": "primary"}]


class _GatewayPdf(_Gateway):
    async def __call__(self, path, tenant_id, variant=None):
        r = await super().__call__(path, tenant_id, variant)
        if path.endswith("/meta"):
            return httpx.Response(200, json={"session_id": SID, "expired": False,
                                             "mime_type": "application/pdf"})
        return r


@pytest.mark.asyncio
async def test_quem_atende_recebe_o_original(monkeypatch, trilha, pools_da_sessao):
    gw = _Gateway()
    r = await _ver(_user(["sac_ia"], GRANT), gw, monkeypatch, roster=ATENDE)
    assert r.headers["x-attachment-view"] == "original" and gw.variantes[-1] is None
    assert [l["result"] for l in trilha] == ["ok"]


@pytest.mark.asyncio
async def test_especialista_tambem_atende(monkeypatch, trilha, pools_da_sessao):
    r = await _ver(_user(["sac_ia"], GRANT), _Gateway(), monkeypatch,
                   roster=[{"participant_id": "human-u1", "role": "specialist"}])
    assert r.headers["x-attachment-view"] == "original"


@pytest.mark.asyncio
@pytest.mark.parametrize("roster", [OUTRO, SUPERVISOR, [], None], ids=["outro", "supervisor", "vazio", "sem-roster"])
async def test_quem_nao_atende_recebe_a_previa_borrada(monkeypatch, trilha, pools_da_sessao, roster):
    gw = _Gateway()
    r = await _ver(_user(["sac_ia"], GRANT), gw, monkeypatch, roster=roster)
    assert r.headers["x-attachment-view"] == "blurred"
    assert gw.variantes[-1] == "blurred", "pediu o ORIGINAL ao gateway para quem não atende"
    assert [l["result"] for l in trilha] == ["ok_blurred"]


@pytest.mark.asyncio
async def test_roster_ilegivel_e_borrado_nunca_original(monkeypatch, trilha, pools_da_sessao):
    gw = _Gateway()
    r = await _ver(_user(["sac_ia"], GRANT), gw, monkeypatch, roster=RuntimeError("redis fora"))
    assert r.headers["x-attachment-view"] == "blurred" and gw.variantes[-1] == "blurred"


@pytest.mark.asyncio
async def test_revelar_entrega_o_original_e_a_trilha_diz(monkeypatch, trilha, pools_da_sessao):
    gw = _Gateway()
    r = await _ver(_user(["sac_ia"], GRANT), gw, monkeypatch, roster=SUPERVISOR, reveal=True)
    assert r.headers["x-attachment-view"] == "revealed" and gw.variantes[-1] is None
    assert [l["result"] for l in trilha] == ["revealed"]


@pytest.mark.asyncio
async def test_revelar_nao_fura_o_escopo(monkeypatch, trilha, pools_da_sessao):
    pools_da_sessao["pools"] = {"retencao_humano"}
    gw = _Gateway()
    with pytest.raises(HTTPException) as e:
        await _ver(_user(["sac_ia"], GRANT), gw, monkeypatch, roster=SUPERVISOR, reveal=True)
    assert e.value.status_code == 403
    assert all(not c.endswith("/content") for c in gw.chamadas)


@pytest.mark.asyncio
async def test_nao_imagem_sem_revelar_pede_revelar_e_trilha(monkeypatch, trilha, pools_da_sessao):
    gw = _GatewayPdf()
    with pytest.raises(HTTPException) as e:
        await _ver(_user(["sac_ia"], GRANT), gw, monkeypatch, roster=SUPERVISOR)
    assert e.value.status_code == 409 and e.value.detail == "reveal_required"
    assert all(not c.endswith("/content") for c in gw.chamadas), "buscou bytes sem revelar"
    assert [l["result"] for l in trilha] == ["reveal_required"]


@pytest.mark.asyncio
async def test_nao_imagem_para_quem_atende_sai_direto(monkeypatch, trilha, pools_da_sessao):
    r = await _ver(_user(["sac_ia"], GRANT), _GatewayPdf(), monkeypatch, roster=ATENDE)
    assert r.headers["x-attachment-view"] == "original"


# ── a transcrição (SSE) leva o anexo, sem o link da porta pública ─────────────

def _entrada_com_anexo():
    import json as _json
    att = {"media_type": "image", "file_id": "f-9", "mime_type": "image/jpeg",
           "url": "http://gw/webchat/v1/attachments/f-9?exp=1&sig=x"}
    return {"type": "message", "author_id": "c-1", "author_role": "customer",
            "visibility": '"all"', "timestamp": "2026-10-01T10:00:00Z",
            "payload": _json.dumps({"message_id": "m-1", "text": "[Anexo: foto.jpg]",
                                    "content": {"type": "text", "text": "[Anexo: foto.jpg]",
                                                "attachment": att}})}


def test_transcricao_leva_o_anexo_pelo_file_id():
    from plughub_analytics_api.sessions import _parse_entry
    r = _parse_entry("1-0", _entrada_com_anexo())
    assert r["content"]["attachment"] == {"media_type": "image", "file_id": "f-9", "mime_type": "image/jpeg"}


def test_o_link_da_porta_publica_nao_sai_em_lugar_nenhum():
    import json as _json
    from plughub_analytics_api.sessions import _parse_entry
    r = _parse_entry("1-0", _entrada_com_anexo())
    assert "sig=" not in _json.dumps(r), "o link assinado abriria o original fora da porta que borra"


def test_controle_mensagem_sem_anexo_nao_ganha_o_campo():
    import json as _json
    from plughub_analytics_api.sessions import _parse_entry
    r = _parse_entry("1-0", {"type": "message", "author_id": "c-1", "visibility": '"all"',
                             "payload": _json.dumps({"text": "oi", "content": {"type": "text", "text": "oi"}})})
    assert r["content"] == {"text": "oi"}
