"""
test_wch16_upload_cors.py — WCH-16 (2026-10-02)

PROPOSIÇÃO: o navegador sobe o binário do anexo a partir de OUTRA origem (o widget mora no site do
cliente). Medido antes: o preflight `OPTIONS /webchat/v1/upload/{id}` respondia 405 sem cabeçalho
de CORS, e todo upload pelo navegador morria em "Failed to fetch" — o gate da VOZ-28 subia de dentro
do container, onde não há CORS, e ficava verde.

Controles: rota fora do prefixo continua SEM CORS (o gateway não abre a porta toda), e a RECUSA do
upload também leva o cabeçalho (senão o navegador esconde o motivo).
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from plughub_channel_gateway import main as _main
from plughub_channel_gateway.upload_router import upload_cors

ORIGEM = {"Origin": "http://site-do-cliente.example"}


def _app() -> TestClient:
    app = FastAPI()

    @app.post("/webchat/v1/upload/{file_id}", status_code=204)
    async def up(file_id: str):
        if file_id == "falso":
            raise HTTPException(415, "o conteudo nao e o tipo declarado")

    @app.get("/v1/interno")
    async def interno():
        return {"ok": True}

    app.middleware("http")(upload_cors)
    return TestClient(app)


def test_preflight_do_navegador_e_aceito():
    r = _app().options("/webchat/v1/upload/abc", headers={
        **ORIGEM, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type"})
    assert r.status_code == 204
    assert r.headers["access-control-allow-origin"] == "*"
    assert "POST" in r.headers["access-control-allow-methods"]
    assert "content-type" in r.headers["access-control-allow-headers"]
    assert "access-control-allow-credentials" not in r.headers, "o slot autoriza, nunca cookie"


def test_o_post_e_a_recusa_levam_o_cabecalho():
    c = _app()
    assert c.post("/webchat/v1/upload/abc", content=b"x", headers=ORIGEM).headers[
        "access-control-allow-origin"] == "*"
    recusa = c.post("/webchat/v1/upload/falso", content=b"x", headers=ORIGEM)
    assert recusa.status_code == 415 and recusa.headers["access-control-allow-origin"] == "*"


def test_controle_fora_do_prefixo_nao_ganha_cors():
    r = _app().get("/v1/interno", headers=ORIGEM)
    assert r.status_code == 200 and "access-control-allow-origin" not in r.headers


def test_o_app_do_gateway_registra_o_middleware():
    """Sem este, o middleware poderia existir e não estar no app que roda."""
    assert any(getattr(m, "kwargs", {}).get("dispatch") is upload_cors for m in _main.app.user_middleware)
