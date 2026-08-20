"""
test_supervisor_tenant_guard.py — o guard de tenant do `/supervisor/join`.

POR QUE ESTA POSIÇÃO É A ÚNICA QUE JULGA AUTORIDADE:
`/supervisor/message` e `/supervisor/leave` comparam o `tenant_id` do corpo com o
estado gravado em `supervisor:{sid}:active` — que foi escrito, no join, a partir do
corpo do PRÓPRIO chamador. Eles conferem consistência, não autoridade. O único
confronto contra um fato da plataforma (`session:{id}:meta`) acontece no join, e é
ele que estes testes fixam.

O DEFEITO QUE ELES IMPEDEM DE VOLTAR (2026-08-21):
    meta.get("tenant_id", body.tenant_id) != body.tenant_id
Com o campo ausente, o default É o valor comparado — a igualdade é sempre verdadeira
e o 403 nunca dispara. Escrito assim, parece uma comparação. Nenhum teste existia
nesta posição, e é por isso que sobreviveu.

Alcance medido antes do conserto (`infra/test/probe_session_meta_ownership.sh`):
8 metas vivos, 8 com `tenant_id`, 0 sem, 0 malformados — defeito REAL no código e
LATENTE no dado. 0 em 8 é evidência fraca de "nunca"; daí o fail-closed.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plughub_analytics_api.supervisor import router

TENANT  = "tenant_a"
OUTRO   = "tenant_b"
SESSION = "sess_20260821T120000_01HX5K3MNJP8QVWZ4RABC"


class _FakeRedis:
    """Só o suficiente para o caminho do join: get/set/xadd."""

    def __init__(self, meta_raw: str | None) -> None:
        self._meta = meta_raw
        self.store: dict[str, str] = {}
        self.streams: list[dict] = []

    async def get(self, key: str):
        if key.endswith(":meta"):
            return self._meta
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None):
        self.store[key] = value

    async def xadd(self, key: str, fields: dict):
        self.streams.append({"key": key, **fields})
        return "1-0"


def _client(meta_raw: str | None) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.redis = _FakeRedis(meta_raw)
    return TestClient(app)


def _join(client: TestClient, tenant: str = TENANT):
    return client.post("/supervisor/join", json={
        "tenant_id": tenant, "session_id": SESSION, "operator_id": "op1",
    })


# ── CONTROLE ──────────────────────────────────────────────────────────────────
# Sem esta metade, uma implementação que recusasse TUDO passaria em todos os casos
# de recusa abaixo sem provar nada.

def test_meta_com_tenant_correto_ENTRA():
    r = _join(_client(json.dumps({"tenant_id": TENANT, "channel": "webchat"})))
    assert r.status_code == 200, r.text
    assert r.json()["session_id"] == SESSION


# ── O caminho que o guard antigo deixava passar ───────────────────────────────

def test_meta_SEM_tenant_id_e_RECUSADO():
    # Era exatamente aqui que `meta.get("tenant_id", body.tenant_id)` devolvia o
    # valor comparado e a desigualdade nunca era verdadeira.
    r = _join(_client(json.dumps({"channel": "webchat", "contact_id": "c1"})))
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "tenant_unverifiable"


def test_meta_MALFORMADO_e_RECUSADO():
    # O `except: meta = {}` produzia a mesma ausência por outro caminho.
    r = _join(_client("{isto não é json"))
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "tenant_unverifiable"


def test_meta_que_nao_e_OBJETO_e_RECUSADO():
    # JSON válido e inútil: `json.loads("[]")` não levanta, e `.get` explodiria
    # mais adiante — ou, pior, o meta viraria `{}` e cairia no fail-open.
    r = _join(_client("[1, 2, 3]"))
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "tenant_unverifiable"


# ── A metade que SEMPRE funcionou (e precisa continuar funcionando) ───────────

def test_tenant_DIVERGENTE_e_recusado_com_mismatch():
    r = _join(_client(json.dumps({"tenant_id": OUTRO})), tenant=TENANT)
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "Tenant mismatch"


def test_sessao_sem_meta_e_404_nao_403():
    # Distinção que importa para quem depura: "não existe" ≠ "não posso conferir".
    r = _join(_client(None))
    assert r.status_code == 404, r.text
