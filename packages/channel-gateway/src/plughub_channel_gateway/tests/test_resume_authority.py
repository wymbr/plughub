"""
test_resume_authority.py — APR-11 (2026-09-14)

O que um resume pode afirmar sobre quem decide. Vermelho ao vivo antes do conserto:

  1. a operadora RECUSOU a portabilidade pela porta externa e o processo seguiu APROVADO
     (a porta descartava `decision`, e o bridge assume `input`);
  2. a promoção de deploy — 401 na rota interna sem credencial — foi APROVADA pela externa;
  3. o carimbo de confiança só existia com aprovador, por `setdefault`: resume anônimo
     passava sem carimbo e o chamador podia declarar `possessed`.

Cada recusa tem o seu controle positivo: uma porta que recusasse tudo passaria nelas.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from plughub_channel_gateway import main as cg_main
from plughub_channel_gateway.adapters.webhook import WebhookAdapter
from plughub_channel_gateway.config import Settings
from plughub_channel_gateway.resume_authority import judge_external_decision, server_trust_stamp


# ── as duas funções puras ─────────────────────────────────────────────────────

class TestJudgeExternalDecision:
    @pytest.mark.parametrize("decisao", ["approved", "rejected"])
    def test_aprovacao_de_sistema_aceita_as_duas_decisoes(self, decisao):
        assert judge_external_decision(decisao, "approval") == ("accept", decisao)

    @pytest.mark.parametrize("decisao", [None, "", "input", "timeout", "APPROVED", 1])
    def test_aprovacao_sem_decisao_valida_e_recusada_nunca_vira_aprovacao(self, decisao):
        veredito, msg = judge_external_decision(decisao, "approval")
        assert veredito == "refuse" and "approved" in msg and "rejected" in msg

    @pytest.mark.parametrize("motivo", ["input", "webhook", "timer", None, ""])
    @pytest.mark.parametrize("decisao", ["approved", "rejected", "timeout", None])
    def test_fora_da_aprovacao_o_campo_segue_descartado(self, motivo, decisao):
        assert judge_external_decision(decisao, motivo) == ("drop", None)


class TestServerTrustStamp:
    def test_sem_principal_e_claimed_de_sistema(self):
        assert server_trust_stamp(None) == {"verification_class": "claimed", "principal_type": "system"}

    def test_principal_verificado_carimba_o_dele(self):
        assert server_trust_stamp({"decided_by": "u"}) == {"verification_class": "possessed", "principal_type": "human"}
        assert server_trust_stamp({"verification_class": "claimed", "principal_type": "human"})["verification_class"] == "claimed"


# ── o carimbo dentro do handle_resume ─────────────────────────────────────────

TENANT = "tenant_test"
TOKEN = "t" * 43
TOKEN_VALUE = "sid-apr11:aprovar:2026-09-20T12:00:00+00:00"


def _adapter() -> tuple[WebhookAdapter, AsyncMock]:
    redis = AsyncMock()
    redis.hget = AsyncMock(return_value=TOKEN_VALUE)
    redis.hdel = AsyncMock(return_value=1)
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    redis.delete = AsyncMock(return_value=1)
    redis.xadd = AsyncMock(return_value=b"1-0")
    s = Settings(tenant_id=TENANT)
    return WebhookAdapter(producer=AsyncMock(), redis=redis, settings=s), redis


def _payload_publicado(redis: AsyncMock) -> dict:
    for c in redis.xadd.call_args_list:
        campos = c.args[1]
        if "payload" in campos:
            return json.loads(campos["payload"]).get("payload") or {}
    raise AssertionError("session_resumed não foi escrito no stream")


class TestCarimboNoResume:
    async def test_resume_anonimo_sai_claimed_mesmo_sem_field_edits(self):
        a, redis = _adapter()
        await a.handle_resume(resume_token=TOKEN, tenant_id=TENANT, payload={"choice": "aprovar"})
        p = _payload_publicado(redis)
        assert (p["verification_class"], p["principal_type"]) == ("claimed", "system")

    async def test_chamador_nao_declara_a_propria_confianca(self, caplog):
        a, redis = _adapter()
        await a.handle_resume(resume_token=TOKEN, tenant_id=TENANT,
                              payload={"choice": "aprovar", "verification_class": "possessed", "principal_type": "human"})
        p = _payload_publicado(redis)
        assert (p["verification_class"], p["principal_type"]) == ("claimed", "system")
        assert "APR-11" in caplog.text

    async def test_controle_aprovador_verificado_carimba_possessed(self):
        a, redis = _adapter()
        await a.handle_resume(resume_token=TOKEN, tenant_id=TENANT, payload={"choice": "aprovar"},
                              approver={"decided_by": "alice", "verification_class": "possessed"})
        assert _payload_publicado(redis)["verification_class"] == "possessed"


# ── a porta externa ───────────────────────────────────────────────────────────

class _FakeAdapter:
    def __init__(self, required_abac=None, suspend_reason=None):
        self.required_abac, self.suspend_reason = required_abac, suspend_reason
        self.chamadas: list[dict] = []

    async def resume_required_abac(self, tenant_id, token):
        return self.required_abac

    async def resume_suspend_reason(self, tenant_id, token):
        return self.suspend_reason

    async def handle_resume(self, **kw):
        self.chamadas.append(kw)
        return "sid-x"


async def _externo(monkeypatch, fake, payload):
    monkeypatch.setattr(cg_main, "_webhook_adapter", fake)
    return await cg_main.external_webhook_resume(TOKEN, cg_main.ExternalResumeRequest(tenant_id=TENANT, payload=payload))


class TestPortaExterna:
    async def test_operadora_recusa_e_a_recusa_chega(self, monkeypatch):
        fake = _FakeAdapter(suspend_reason="approval")
        await _externo(monkeypatch, fake, {"decision": "rejected", "motivo": "numero bloqueado"})
        enviado = fake.chamadas[0]["payload"]
        assert enviado["decision"] == "rejected" and enviado["source"] == "external"
        assert enviado["motivo"] == "numero bloqueado"

    async def test_controle_operadora_aprova(self, monkeypatch):
        fake = _FakeAdapter(suspend_reason="approval")
        await _externo(monkeypatch, fake, {"decision": "approved"})
        assert fake.chamadas[0]["payload"]["decision"] == "approved"

    async def test_aprovacao_sem_decisao_422_e_nada_retoma(self, monkeypatch):
        fake = _FakeAdapter(suspend_reason="approval")
        with pytest.raises(HTTPException) as e:
            await _externo(monkeypatch, fake, {})
        assert e.value.status_code == 422 and fake.chamadas == []
        with pytest.raises(HTTPException):
            await _externo(monkeypatch, fake, {"decision": "timeout"})
        assert fake.chamadas == []

    async def test_fora_da_aprovacao_decision_e_source_seguem_descartados(self, monkeypatch):
        fake = _FakeAdapter(suspend_reason="input")
        await _externo(monkeypatch, fake, {"decision": "rejected", "source": "supervisor:x", "resposta": 3})
        enviado = fake.chamadas[0]["payload"]
        assert "decision" not in enviado and enviado["source"] == "external" and enviado["resposta"] == 3

    async def test_tarefa_que_declara_capacidade_401_nesta_porta(self, monkeypatch):
        fake = _FakeAdapter(required_abac=("approvals", "decide"), suspend_reason="input")
        with pytest.raises(HTTPException) as e:
            await _externo(monkeypatch, fake, {"choice": "aprovar"})
        assert e.value.status_code == 401 and "approvals.decide" in e.value.detail
        assert fake.chamadas == []
