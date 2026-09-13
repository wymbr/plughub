# -*- coding: utf-8 -*-
"""Exercicio da IDN-06 DENTRO do container do gateway.

Lido por stdin: `docker exec -i <gw> python - <modo> [--mutar-*]`.

  rota     o portao da rota VIVA (8010), com JWTs cunhados pelo segredo do servico e o
           token de servico do proprio settings:
    anonimo_401            as NOVE rotas sem credencial nenhuma
    servico_errado_401     token de servico errado (com Bearer valido junto) nao cai na porta de usuario
    servico_resolve_200    controle POSITIVO da porta de servico
    servico_pending_200    idem, na rota que devolve resume_token
    usuario_interno_403    usuario com os dois campos em rota interna
    usuario_sem_campo_403  busca sem contacts.visualizar nem agent_assist.atender
    usuario_busca_200      controle POSITIVO da porta de usuario (tenant do JWT)
    usuario_outro_tenant_403

  imagem   o mesmo portao no codigo da IMAGEM, em processo (TestClient, sem lifespan):
    vazio_fecha            gateway sem token configurado RECUSA quem manda header vazio
    anonimo_401 · usuario_interno_403 · servico_200 (controle)
  Mutacoes: --mutar-vazio-abre (token vazio aceita) · --mutar-sem-portao (_identity_caller no-op).
"""
import json
import sys
import time

import httpx
import jwt as pyjwt

from plughub_channel_gateway.config import get_settings

MODO = sys.argv[1] if len(sys.argv) > 1 else "rota"
MUT_VAZIO = "--mutar-vazio-abre" in sys.argv
MUT_SEM = "--mutar-sem-portao" in sys.argv
BASE = "http://localhost:8010/v1/channels/webhook"
ANC = [{"kind": "email", "value": "idn06-inexistente@probe.local"}]
VISUALIZAR = {"contacts": {"visualizar": {"access": "read_only"}}}
ATENDER = {"agent_assist": {"atender": {"access": "read_write"}}}


def _tok(s, tenant, mc):
    return pyjwt.encode({"sub": "probe-idn06", "tenant_id": tenant, "module_config": mc,
                         "exp": int(time.time()) + 600}, s.auth_jwt_secret, algorithm="HS256")


def _chamadas(t):
    """(nome, metodo, caminho, json) — as nove rotas do bloco com portao de duas portas."""
    return [
        ("resolve", "POST", "/identity/resolve", {"tenant_id": t, "provision": False, "anchors": ANC}),
        ("pending_by_customer", "GET", "/pending/by-customer/cus_demo_maria?tenant_id=" + t, None),
        ("pending_legado", "GET", "/pending/5511900000000?tenant_id=" + t, None),
        ("otp_challenge", "POST", "/identity/otp/challenge",
         {"tenant_id": t, "customer_id": "cus_x", "kind": "email", "value": "a@b.c"}),
        ("otp_verify", "POST", "/identity/otp/verify",
         {"tenant_id": t, "customer_id": "cus_x", "kind": "email", "value": "a@b.c", "code": "000000"}),
        ("key_attach", "POST", "/identity/key/attach",
         {"tenant_id": t, "customer_id": "cus_x", "kind": "email", "value": "a@b.c"}),
        ("attributes", "POST", "/identity/attributes", {"tenant_id": t, "customer_id": "cus_x", "attributes": {}}),
        ("search", "GET", "/identity/customers/search?q=maria&tenant_id=" + t, None),
        ("customer_get", "GET", "/identity/customers/cus_demo_maria?tenant_id=" + t, None),
    ]


def _faz(http, metodo, caminho, corpo, headers):
    if metodo == "GET":
        return http.get(BASE + caminho, headers=headers)
    return http.post(BASE + caminho, json=corpo, headers=headers)


def rota(s):
    t = s.tenant_id
    svc = {"x-service-token": s.channel_gateway_service_token, "x-service-name": "probe"}
    out = {"token_configurado": bool(s.channel_gateway_service_token), "casos": {}, "status": {}}
    c = out["casos"]
    with httpx.Client(timeout=15) as http:
        anon = {n: _faz(http, m, p, j, {}).status_code for (n, m, p, j) in _chamadas(t)}
        out["status"]["anonimo"] = anon
        c["anonimo_401"] = len(anon) == 9 and all(v == 401 for v in anon.values())

        errado = {"x-service-token": "errado", "authorization": "Bearer " + _tok(s, t, VISUALIZAR)}
        c["servico_errado_401"] = _faz(http, "GET", "/identity/customers/search?q=maria&tenant_id=" + t,
                                       None, errado).status_code == 401

        r = _faz(http, "POST", "/identity/resolve", {"tenant_id": t, "provision": False, "anchors": ANC}, svc)
        c["servico_resolve_200"] = r.status_code == 200 and "customer_id" in r.json()
        r = _faz(http, "GET", "/pending/by-customer/cus_demo_maria?tenant_id=" + t, None, svc)
        c["servico_pending_200"] = r.status_code == 200 and "found" in r.json()

        ambos = {"authorization": "Bearer " + _tok(s, t, {**VISUALIZAR, **ATENDER})}
        internas = {n: _faz(http, m, p, j, ambos).status_code for (n, m, p, j) in _chamadas(t)
                    if n not in ("search", "customer_get")}
        out["status"]["usuario_interno"] = internas
        c["usuario_interno_403"] = len(internas) == 7 and all(v == 403 for v in internas.values())

        sem = {"authorization": "Bearer " + _tok(s, t, {"contacts": {"monitorar": {"access": "read_only"}}})}
        c["usuario_sem_campo_403"] = _faz(http, "GET", "/identity/customers/search?q=maria", None, sem).status_code == 403

        r = _faz(http, "GET", "/identity/customers/search?q=maria", None,
                 {"authorization": "Bearer " + _tok(s, t, VISUALIZAR)})
        c["usuario_busca_200"] = r.status_code == 200 and "results" in r.json()

        r = _faz(http, "GET", "/identity/customers/search?q=maria&tenant_id=tenant_outro", None,
                 {"authorization": "Bearer " + _tok(s, t, VISUALIZAR)})
        c["usuario_outro_tenant_403"] = r.status_code == 403
    return out


def imagem(s):
    from fastapi.testclient import TestClient
    from plughub_channel_gateway import identity_auth
    from plughub_channel_gateway import main as cg_main

    cg_main._webhook_adapter = None          # o portao decide ANTES do adapter
    if MUT_VAZIO:
        original = identity_auth.identity_principal

        def aberto(request, *, service_token, jwt_secret, user_grants=()):
            if not service_token and request.headers.get("x-service-token") is not None:
                return identity_auth.IdentityPrincipal("service", "service:mutante", None)
            return original(request, service_token=service_token, jwt_secret=jwt_secret, user_grants=user_grants)
        cg_main.identity_principal = aberto
    if MUT_SEM:
        cg_main._identity_caller = lambda request, requested_tenant, grants=(): requested_tenant or ""

    t = s.tenant_id
    corpo = {"tenant_id": t, "provision": False, "anchors": ANC}
    cli = TestClient(cg_main.app)
    out = {"mutar": {"vazio_abre": MUT_VAZIO, "sem_portao": MUT_SEM}, "casos": {}}
    c = out["casos"]
    settings = get_settings()
    real = settings.channel_gateway_service_token
    try:
        settings.channel_gateway_service_token = ""
        c["vazio_fecha"] = cli.post("/v1/channels/webhook/identity/resolve", json=corpo,
                                    headers={"x-service-token": ""}).status_code == 401
        settings.channel_gateway_service_token = "tok-imagem"
        c["anonimo_401"] = cli.post("/v1/channels/webhook/identity/resolve", json=corpo).status_code == 401
        amb = {"authorization": "Bearer " + _tok(s, t, {**VISUALIZAR, **ATENDER})}
        c["usuario_interno_403"] = cli.post("/v1/channels/webhook/identity/resolve", json=corpo,
                                            headers=amb).status_code == 403
        c["servico_200"] = cli.post("/v1/channels/webhook/identity/resolve", json=corpo,
                                    headers={"x-service-token": "tok-imagem"}).status_code == 200
    finally:
        settings.channel_gateway_service_token = real
    return out


if __name__ == "__main__":
    s = get_settings()
    print(json.dumps(imagem(s) if MODO == "imagem" else rota(s), default=str))
