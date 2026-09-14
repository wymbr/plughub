"""
test_arrival_evidence.py — PID-09 (2026-09-14)

A chegada pelo WhatsApp do telefone AUTORITATIVO de um cliente vira evidência de posse.
Três camadas, cada recusa com o seu controle:

  1. `IdentityIndex.authoritative_owner` — só a procedência `authoritative` responde.
  2. `ArrivalEvidenceRecorder` — o que a chegada afirma (not_run · failed · verified), e que
     erro nosso nunca apaga prova.
  3. o adapter — só a assinatura CONFERIDA autentica, e a evidência sai ANTES da mensagem.

O que só o Postgres, o mcp-server e o Redis de verdade respondem é do probe ao vivo,
`infra/test/probe_arrival_evidence.sh`.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from plughub_channel_gateway.adapters.whatsapp import WhatsAppAdapter
from plughub_channel_gateway.arrival_evidence import ArrivalEvidenceRecorder, whatsapp_from_as_phone
from plughub_channel_gateway.config import Settings
from plughub_channel_gateway.identity.index import IdentityIndex


def _pg_com(linha):
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=linha)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=cm)
    return pool


# ── 1 · o dono autoritativo ───────────────────────────────────────────────────

class TestAuthoritativeOwner:
    async def test_procedencia_authoritative_responde_o_cliente(self):
        idx = IdentityIndex(redis=AsyncMock(), salt="s", phone_region="BR",
                            db_pool=_pg_com({"customer_id": "cus_a", "provenance": "authoritative"}))
        assert await idx.authoritative_owner("t", "phone", "+5511999990000") == ("cus_a", "authoritative")

    @pytest.mark.parametrize("prov", ["declared", "channel_origin", "operator", None])
    async def test_outra_procedencia_nao_prova(self, prov):
        idx = IdentityIndex(redis=AsyncMock(), salt="s", phone_region="BR",
                            db_pool=_pg_com({"customer_id": "cus_a", "provenance": prov}))
        assert await idx.authoritative_owner("t", "phone", "+5511999990000") is None

    async def test_sem_linha_sem_cadastro_ou_ancora_invalida(self):
        sem_linha = IdentityIndex(redis=AsyncMock(), salt="s", phone_region="BR", db_pool=_pg_com(None))
        assert await sem_linha.authoritative_owner("t", "phone", "+5511999990000") is None
        sem_pg = IdentityIndex(redis=AsyncMock(), salt="s", phone_region="BR", db_pool=None)
        assert await sem_pg.authoritative_owner("t", "phone", "+5511999990000") is None
        pg = _pg_com({"customer_id": "cus_a", "provenance": "authoritative"})
        invalida = IdentityIndex(redis=AsyncMock(), salt="s", phone_region="BR", db_pool=pg)
        assert await invalida.authoritative_owner("t", "phone", "abc") is None

    def test_from_da_meta_vira_e164(self):
        assert whatsapp_from_as_phone("5511999990000") == "+5511999990000"
        assert whatsapp_from_as_phone("+5511999990000") == "+5511999990000"


# ── 2 · o registrador ─────────────────────────────────────────────────────────

class _Dono:
    def __init__(self, resposta=None, erro: Exception | None = None):
        self.resposta, self.erro, self.perguntas = resposta, erro, []

    async def authoritative_owner(self, tenant_id, kind, value):
        self.perguntas.append((tenant_id, kind, value))
        if self.erro:
            raise self.erro
        return self.resposta


def _mcp(status=200):
    pedidos: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        pedidos.append({"url": str(req.url), "token": req.headers.get("x-service-token"),
                        "corpo": json.loads(req.content)})
        return httpx.Response(status, json={"status": "ok"})
    return httpx.MockTransport(handler), pedidos


def _rec(dono, transport, url="http://mcp:3100", token="tk"):
    return ArrivalEvidenceRecorder(identity=dono, mcp_url=url, service_token=token, transport=transport)


class TestRecorder:
    async def test_dono_autoritativo_grava_verified_com_cliente(self):
        t, pedidos = _mcp()
        dono = _Dono(("cus_a", "authoritative"))
        r = await _rec(dono, t).record_whatsapp(tenant_id="t", session_id="S1", from_field="5511999990000", authenticated=True)
        assert r == {"status": "verified"}
        assert dono.perguntas == [("t", "phone", "+5511999990000")]
        assert pedidos == [{
            "url": "http://mcp:3100/internal/identity-evidence", "token": "tk",
            "corpo": {"tenant_id": "t", "session_id": "S1", "mechanism": "whatsapp", "anchor_kind": "phone",
                      "status": "verified", "customer_id": "cus_a", "source": "authoritative"},
        }]

    async def test_numero_sem_dono_autoritativo_grava_failed_sem_cliente(self):
        t, pedidos = _mcp()
        r = await _rec(_Dono(None), t).record_whatsapp(tenant_id="t", session_id="S1", from_field="5511", authenticated=True)
        assert r == {"status": "failed"}
        assert pedidos[0]["corpo"]["status"] == "failed" and "customer_id" not in pedidos[0]["corpo"]

    async def test_assinatura_nao_conferida_nao_prova_nem_pergunta_ao_cadastro(self, caplog):
        t, pedidos = _mcp()
        dono = _Dono(("cus_a", "authoritative"))
        r = await _rec(dono, t).record_whatsapp(tenant_id="t", session_id="S1", from_field="5511999990000", authenticated=False)
        assert r == {"status": "not_run"} and dono.perguntas == []
        assert pedidos[0]["corpo"]["status"] == "not_run" and "customer_id" not in pedidos[0]["corpo"]
        assert "NÃO conferida" in caplog.text

    async def test_cadastro_inacessivel_nao_grava_nada(self, caplog):
        t, pedidos = _mcp()
        r = await _rec(_Dono(erro=RuntimeError("pg caiu")), t).record_whatsapp(
            tenant_id="t", session_id="S1", from_field="5511999990000", authenticated=True)
        assert r == {"skipped": "lookup_failed"} and pedidos == []
        assert "cadastro inacessível" in caplog.text

    async def test_sem_url_ou_token_nao_chama_e_diz_o_que_falta(self, caplog):
        t, pedidos = _mcp()
        for url, token, falta in (("", "tk", "PLUGHUB_MCP_SERVER_URL"), ("http://mcp", "", "PLUGHUB_MCP_INTERNAL_SERVICE_TOKEN")):
            rec = _rec(_Dono(("cus_a", "authoritative")), t, url=url, token=token)
            assert falta in (rec.configured() or "")
            assert await rec.record_whatsapp(tenant_id="t", session_id="S1", from_field="1", authenticated=True) == {"skipped": "not_configured"}
        assert pedidos == []

    async def test_recusa_do_mcp_e_nomeada(self, caplog):
        t, _ = _mcp(status=401)
        r = await _rec(_Dono(("cus_a", "authoritative")), t).record_whatsapp(tenant_id="t", session_id="S1", from_field="1", authenticated=True)
        assert r == {"skipped": "http_401"} and "RECUSOU" in caplog.text


# ── 3 · o adapter ─────────────────────────────────────────────────────────────

SECRET = "segredo_do_app_de_teste"


def _settings(secret=SECRET):
    return Settings(tenant_id="tenant_x", whatsapp_app_secret=secret, whatsapp_phone_number_id="123")


def _corpo(frm="5511999990000"):
    return json.dumps({"entry": [{"changes": [{"value": {"messages": [
        {"from": frm, "id": "wamid.1", "type": "text", "text": {"body": "oi"}}]}}]}]}).encode()


class TestAdapter:
    def test_veredito_da_assinatura_tem_tres_valores(self):
        adp = WhatsAppAdapter(producer=AsyncMock(), redis=AsyncMock(), settings=_settings())
        body = _corpo()
        sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        assert adp.signature_verdict(body, sig) == "authenticated"
        assert adp.signature_verdict(body, "sha256=errado") == "invalid"
        sem = WhatsAppAdapter(producer=AsyncMock(), redis=AsyncMock(), settings=_settings(""))
        assert sem.signature_verdict(body, "") == "unchecked"
        # compatibilidade: "passou" continua verdadeiro sem segredo — só não AUTENTICA
        assert sem.verify_signature(body, "") is True and adp.verify_signature(body, "sha256=errado") is False

    @pytest.mark.parametrize("autenticado", [True, False])
    async def test_evidencia_sai_antes_da_mensagem_com_o_veredito(self, autenticado):
        ordem: list[str] = []
        redis = AsyncMock()
        redis.get = AsyncMock(side_effect=lambda k: "S-wa" if k.endswith(":session") else None)
        producer = AsyncMock()
        producer.send = AsyncMock(side_effect=lambda *a, **kw: ordem.append("kafka"))
        adp = WhatsAppAdapter(producer=producer, redis=redis, settings=_settings())
        rec = MagicMock()
        rec.configured = MagicMock(return_value=None)

        async def grava(**kw):
            ordem.append(f"evidencia:{kw['authenticated']}:{kw['session_id']}:{kw['from_field']}")
            return {"status": "x"}
        rec.record_whatsapp = grava
        adp.attach_arrival_evidence(rec)
        await adp._process_inbound(_corpo(), authenticated=autenticado)
        assert ordem[0] == f"evidencia:{autenticado}:S-wa:5511999990000"
        assert "kafka" in ordem[1:]

    async def test_falha_do_registrador_nao_derruba_a_mensagem(self, caplog):
        redis = AsyncMock()
        redis.get = AsyncMock(side_effect=lambda k: "S-wa" if k.endswith(":session") else None)
        producer = AsyncMock()
        adp = WhatsAppAdapter(producer=producer, redis=redis, settings=_settings())
        rec = MagicMock()
        rec.configured = MagicMock(return_value=None)
        rec.record_whatsapp = AsyncMock(side_effect=RuntimeError("boom"))
        adp.attach_arrival_evidence(rec)
        await adp._process_inbound(_corpo(), authenticated=True)
        assert producer.send.await_count >= 1
        assert "evidência de chegada falhou" in caplog.text
