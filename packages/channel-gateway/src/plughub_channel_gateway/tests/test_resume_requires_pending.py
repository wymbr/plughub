"""PID-06 — a exigência de retomada viaja do step até a pendência e volta na leitura.

O gateway não JULGA a exigência (quem julga é o `pending_workflow_get`, contra a evidência
da sessão que pede); ele só não pode perdê-la. Os três pontos em que ela se perderia calada:
o registro serializado (`PendingEntry`), a leitura que o intake consome, e o corpo do
collect, que é dict cru — foi assim que `customer_resumable` passou meses descartado.
"""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from plughub_channel_gateway import main as cg_main
from plughub_channel_gateway.adapters.webhook import WebhookAdapter
from plughub_channel_gateway.identity.index import PendingEntry


def _entry(**kw) -> PendingEntry:
    base = dict(session_id="proc", customer_id="cus_1", resume_token="tk", pool="aprovacao")
    base.update(kw)
    return PendingEntry(**base)


def test_pending_entry_carrega_a_exigencia_e_le_registro_antigo():
    e = _entry(resume_requires=["otp"])
    assert PendingEntry.from_json(e.to_json()).resume_requires == ["otp"]
    # registro gravado antes da PID-06 (sem a chave) continua legível, sem exigência
    antigo = json.loads(_entry().to_json())
    antigo.pop("resume_requires")
    assert PendingEntry.from_json(json.dumps(antigo)).resume_requires is None


@pytest.mark.asyncio
async def test_find_pending_by_customer_devolve_a_exigencia_na_lista_e_na_vista_achatada():
    adapter = WebhookAdapter.__new__(WebhookAdapter)
    adapter._identity = MagicMock()
    adapter._identity.find_pending = AsyncMock(return_value=[
        _entry(resume_token="tk1", resume_requires=["otp"]),
        _entry(resume_token="tk2", session_id="proc2"),
    ])
    r = await adapter.find_pending_by_customer("t", "cus_1")
    assert [p["resume_requires"] for p in r["pendings"]] == [["otp"], None]
    assert r["resume_requires"] == ["otp"]


def test_corpo_do_collect_recusa_exigencia_malformada_e_distingue_ausente():
    assert cg_main._resume_requires_of({}) is None
    assert cg_main._resume_requires_of({"resume_requires": None}) is None
    assert cg_main._resume_requires_of({"resume_requires": []}) == []
    assert cg_main._resume_requires_of({"resume_requires": ["otp"]}) == ["otp"]
    for ruim in ("otp", [1], {"otp": True}):
        with pytest.raises(HTTPException) as e:
            cg_main._resume_requires_of({"resume_requires": ruim})
        assert e.value.status_code == 422


def test_modelos_de_delegate_aceitam_o_campo():
    for Model in (cg_main.WebhookDelegateRequest, cg_main.WebhookDelegateConferenceRequest):
        assert "resume_requires" in Model.model_fields
