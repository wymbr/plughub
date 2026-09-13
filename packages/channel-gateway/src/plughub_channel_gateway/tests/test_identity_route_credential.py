"""
test_identity_route_credential.py — IDN-06 (2026-09-13)

As rotas de identidade e pendência não pediam credencial, e o `tenant_id` vinha do
corpo: pelo host da UI, sem login, a busca devolvia o cadastro e o resolve respondia.

Aqui: as duas portas do `identity_auth` (serviço e usuário), a postura de token vazio,
o tenant que vale em cada porta, e — por rota — que o portão roda ANTES do adapter.
Que TODA rota do bloco chama o portão é do censo AST, em
`infra/test/probe_identity_route_credential.sh`; aqui a rota viva não é alcançável.
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import jwt as pyjwt
import pytest
from fastapi import HTTPException

from plughub_channel_gateway import identity_auth
from plughub_channel_gateway import main as cg_main
from plughub_channel_gateway.main import (
    IdentityAttachKeyRequest,
    IdentityAttributesRequest,
    IdentityResolveRequest,
    OtpChallengeRequest,
    OtpVerifyRequest,
)

SECRET = "segredo-de-teste-hs256"
SVC = "token-de-servico-de-teste"


def _req(headers: dict | None = None, path: str = "/v1/channels/webhook/identity/x"):
    return SimpleNamespace(headers={k.lower(): v for k, v in (headers or {}).items()},
                           url=SimpleNamespace(path=path))


def _token(tenant: str = "tenant_a", module_config: dict | None = None) -> str:
    return pyjwt.encode({"sub": "user_x", "tenant_id": tenant, "module_config": module_config or {},
                         "exp": int(time.time()) + 600}, SECRET, algorithm="HS256")


def _bearer(tok: str) -> dict:
    return {"authorization": "Bearer " + tok}


VISUALIZAR = {"contacts": {"visualizar": {"access": "read_only"}}}
ATENDER = {"agent_assist": {"atender": {"access": "read_write"}}}


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    s = cg_main.get_settings()
    monkeypatch.setattr(s, "auth_jwt_secret", SECRET, raising=False)
    monkeypatch.setattr(s, "channel_gateway_service_token", SVC, raising=False)
    fake = MagicMock()
    for m in ("resolve_customer", "find_pending_by_customer", "otp_challenge", "otp_verify",
              "attach_customer_key", "update_customer_attributes", "search_customers",
              "get_customer", "get_pending_workflow"):
        assert hasattr(cg_main.WebhookAdapter, m), m      # um mock não prova que o alvo existe
        setattr(fake, m, AsyncMock(return_value={}))
    monkeypatch.setattr(cg_main, "_webhook_adapter", fake)
    return fake


# ── as duas portas ────────────────────────────────────────────────────────────

class TestPortas:
    def _p(self, headers, grants=(), service_token=SVC):
        return identity_auth.identity_principal(_req(headers), service_token=service_token,
                                                jwt_secret=SECRET, user_grants=grants)

    def test_sem_nada_401(self):
        with pytest.raises(HTTPException) as e:
            self._p({})
        assert e.value.status_code == 401

    def test_servico_certo_passa_e_se_identifica(self):
        p = self._p({"x-service-token": SVC, "x-service-name": "mailing-api"})
        assert (p.kind, p.sub, p.tenant_id) == ("service", "service:mailing-api", None)

    def test_servico_errado_401_mesmo_com_bearer_valido(self):
        # Token de serviço errado NUNCA cai calado na porta de usuário.
        with pytest.raises(HTTPException) as e:
            self._p({"x-service-token": "outro", **_bearer(_token(module_config=VISUALIZAR))},
                    grants=(("contacts", "visualizar", "read_only"),))
        assert e.value.status_code == 401

    def test_token_vazio_no_gateway_nao_abre(self):
        # A postura que se proíbe: "sem token configurado ⇒ aceita qualquer (ou nenhum)".
        for enviado in ("", "qualquer"):
            with pytest.raises(HTTPException) as e:
                self._p({"x-service-token": enviado}, service_token="")
            assert e.value.status_code == 401

    def test_usuario_em_rota_interna_403_mesmo_com_grants(self):
        with pytest.raises(HTTPException) as e:
            self._p(_bearer(_token(module_config={**VISUALIZAR, **ATENDER})))
        assert e.value.status_code == 403

    def test_usuario_sem_o_campo_403(self):
        with pytest.raises(HTTPException) as e:
            self._p(_bearer(_token(module_config={"contacts": {"monitorar": {"access": "read_only"}}})),
                    grants=cg_main._IDENTITY_CADASTRO_READ)
        assert e.value.status_code == 403

    def test_usuario_com_um_dos_campos_passa(self):
        for mc in (VISUALIZAR, ATENDER):
            p = self._p(_bearer(_token(module_config=mc)), grants=cg_main._IDENTITY_CADASTRO_READ)
            assert (p.kind, p.tenant_id) == ("user", "tenant_a")

    def test_bearer_forjado_401(self):
        forjado = pyjwt.encode({"sub": "x", "tenant_id": "tenant_a", "module_config": VISUALIZAR},
                               "outro-segredo", algorithm="HS256")
        with pytest.raises(HTTPException) as e:
            self._p(_bearer(forjado), grants=cg_main._IDENTITY_CADASTRO_READ)
        assert e.value.status_code == 401


class TestTenant:
    def test_usuario_usa_o_do_jwt_e_recusa_divergente(self):
        p = identity_auth.IdentityPrincipal("user", "u", "tenant_a")
        assert identity_auth.tenant_for(p, None) == "tenant_a"
        assert identity_auth.tenant_for(p, "tenant_a") == "tenant_a"
        with pytest.raises(HTTPException) as e:
            identity_auth.tenant_for(p, "tenant_b")
        assert e.value.status_code == 403

    def test_servico_usa_o_pedido_e_exige(self):
        p = identity_auth.IdentityPrincipal("service", "service:x", None)
        assert identity_auth.tenant_for(p, "tenant_b") == "tenant_b"
        with pytest.raises(HTTPException) as e:
            identity_auth.tenant_for(p, None)
        assert e.value.status_code == 422


# ── por rota: o portão roda antes do adapter ──────────────────────────────────

SVC_H = {"x-service-token": SVC, "x-service-name": "teste"}
ANC = [{"kind": "phone", "value": "+5511999990000"}]

ROTAS_INTERNAS = [
    ("resolve_customer", lambda r: cg_main.webhook_identity_resolve(
        IdentityResolveRequest(tenant_id="tenant_a", anchors=ANC), r)),
    ("find_pending_by_customer", lambda r: cg_main.webhook_pending_by_customer("cus_x", "tenant_a", r)),
    ("otp_challenge", lambda r: cg_main.webhook_otp_challenge(
        OtpChallengeRequest(tenant_id="tenant_a", customer_id="cus_x", kind="phone", value="+5511999990000"), r)),
    ("otp_verify", lambda r: cg_main.webhook_otp_verify(
        OtpVerifyRequest(tenant_id="tenant_a", customer_id="cus_x", kind="phone", value="+5511999990000",
                         code="123456"), r)),
    ("attach_customer_key", lambda r: cg_main.webhook_identity_attach_key(
        IdentityAttachKeyRequest(tenant_id="tenant_a", customer_id="cus_x", kind="email", value="a@b.c"), r)),
    ("update_customer_attributes", lambda r: cg_main.webhook_identity_attributes(
        IdentityAttributesRequest(tenant_id="tenant_a", customer_id="cus_x", attributes={"nome": "x"}), r)),
    ("get_pending_workflow", lambda r: cg_main.webhook_pending("5511999990000", "tenant_a", r)),
]


@pytest.mark.parametrize("metodo,chama", ROTAS_INTERNAS, ids=[m for m, _ in ROTAS_INTERNAS])
class TestRotasInternas:
    async def test_anonimo_401_sem_tocar_o_adapter(self, _ambiente, metodo, chama):
        with pytest.raises(HTTPException) as e:
            await chama(_req())
        assert e.value.status_code == 401
        getattr(_ambiente, metodo).assert_not_called()

    async def test_usuario_com_grants_403(self, _ambiente, metodo, chama):
        with pytest.raises(HTTPException) as e:
            await chama(_req(_bearer(_token(module_config={**VISUALIZAR, **ATENDER}))))
        assert e.value.status_code == 403
        getattr(_ambiente, metodo).assert_not_called()

    async def test_servico_passa(self, _ambiente, metodo, chama):
        # Controle POSITIVO: sem ele os negativos passariam por uma rota que recusa todos.
        await chama(_req(SVC_H))
        getattr(_ambiente, metodo).assert_awaited_once()


class TestCadastroLidoPelaUI:
    async def test_busca_anonima_401(self, _ambiente):
        with pytest.raises(HTTPException) as e:
            await cg_main.webhook_identity_customers_search(_req(), q="a", tenant_id="tenant_demo")
        assert e.value.status_code == 401
        _ambiente.search_customers.assert_not_called()

    async def test_busca_do_usuario_no_tenant_do_jwt(self, _ambiente):
        await cg_main.webhook_identity_customers_search(_req(_bearer(_token("tenant_a", VISUALIZAR))), q="a")
        assert _ambiente.search_customers.call_args.kwargs["tenant_id"] == "tenant_a"

    async def test_busca_de_outro_tenant_403(self, _ambiente):
        with pytest.raises(HTTPException) as e:
            await cg_main.webhook_identity_customers_search(
                _req(_bearer(_token("tenant_a", ATENDER))), q="a", tenant_id="tenant_b")
        assert e.value.status_code == 403
        _ambiente.search_customers.assert_not_called()

    async def test_get_por_id_servico_e_usuario(self, _ambiente):
        _ambiente.get_customer.return_value = {"customer_id": "cus_x"}
        await cg_main.webhook_identity_customer_get("cus_x", _req(SVC_H), tenant_id="tenant_b")
        assert _ambiente.get_customer.call_args.args == ("tenant_b", "cus_x")
        await cg_main.webhook_identity_customer_get("cus_x", _req(_bearer(_token("tenant_a", VISUALIZAR))))
        assert _ambiente.get_customer.call_args.args == ("tenant_a", "cus_x")
