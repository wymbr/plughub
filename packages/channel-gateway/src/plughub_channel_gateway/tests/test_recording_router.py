"""
VOZ-36 — ouvir e exportar a gravação da chamada: capacidade, pool que ATENDEU a parte, trilha.

O portão tem três eixos (credencial · `contacts.recording` ≥ nível, recortado ao pool · pool no
domínio de linhas), e cada teste de recusa tem o controle positivo ao lado — um portão que nega
tudo passaria em todos os testes de recusa.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt as pyjwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from .. import main as gw_main           # antes do router: o router importa o main
from .. import recording_router as rr

SECRET = "segredo-de-teste-com-32-caracteres!!"
TENANT = "tenant_t"
POOL_A, POOL_B = "pool_a", "pool_b"
SESSION = "sess-1"


def _meta(file_id: str, *, pools, part=1, cls="call_recording", deleted=False, tenant=TENANT):
    return SimpleNamespace(
        file_id=file_id, tenant_id=tenant, session_id=SESSION, original_name=f"parte-{part}.ogg",
        mime_type="audio/ogg", size_bytes=4, file_path=f"x/{file_id}.ogg", serving_url="",
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        deleted_at=datetime.now(timezone.utc) if deleted else None,
        artifact_class=cls, attrs={"part": part, "pools": list(pools), "duration_ms": 5000,
                                   "started_at": "2026-09-18T12:00:00+00:00",
                                   "ended_at": "2026-09-18T12:00:05+00:00"},
    )


class FakeStore:
    def __init__(self, metas):
        self.metas = {m.file_id: m for m in metas}

    async def resolve(self, *, file_id, tenant_id):
        uuid.UUID(file_id)                               # o store real levanta em id não-UUID
        m = self.metas.get(file_id)
        return m if m and m.tenant_id == tenant_id else None

    async def list_session(self, *, tenant_id, session_id, artifact_class):
        return [m for m in self.metas.values() if m.tenant_id == tenant_id and m.session_id == session_id
                and m.artifact_class == artifact_class and m.deleted_at is None]

    async def stream_bytes(self, *, file_id, tenant_id):
        async def _gen():
            yield b"OggS"
        return _gen()


class FakeProducer:
    def __init__(self):
        self.sent: list[tuple[str, bytes, dict]] = []

    async def send(self, topic, key=None, value=None):
        self.sent.append((topic, key, json.loads(value)))


A1 = str(uuid.uuid4())      # parte atendida pelo pool A
B1 = str(uuid.uuid4())      # parte atendida pelo pool B
WEB = str(uuid.uuid4())     # anexo de webchat — não é gravação
OLD = str(uuid.uuid4())     # gravação expirada (pool A)
NOPOOL = str(uuid.uuid4())  # parte sem `pools`


@pytest.fixture
def ctx(monkeypatch):
    store = FakeStore([
        _meta(A1, pools=[POOL_A], part=1), _meta(B1, pools=[POOL_B], part=2),
        _meta(WEB, pools=[POOL_A], cls="webchat_attachment"),
        _meta(OLD, pools=[POOL_A], deleted=True), _meta(NOPOOL, pools=[], part=3),
    ])
    prod = FakeProducer()
    monkeypatch.setattr(gw_main, "_attachment_store", store)
    monkeypatch.setattr(gw_main, "_producer", prod)
    monkeypatch.setattr(gw_main, "get_settings",
                        lambda: SimpleNamespace(auth_jwt_secret=SECRET, tenant_id="install_tenant"))
    app = FastAPI()
    app.include_router(rr.router)
    return SimpleNamespace(client=TestClient(app), prod=prod, store=store)


def _tok(access: str | None, *, scope=None, pools=None, sub="u-1") -> dict:
    mc = {} if access is None else {"contacts": {"recording": {"access": access, "scope": scope or []}}}
    payload = {"sub": sub, "tenant_id": TENANT, "module_config": mc,
               "accessible_pools": pools if pools is not None else [POOL_A, POOL_B],
               "exp": int(time.time()) + 300}
    return {"Authorization": f"Bearer {pyjwt.encode(payload, SECRET, algorithm='HS256')}"}


def _trilha(ctx):
    return [e for (topic, _k, e) in ctx.prod.sent if topic == rr.AUDIT_TOPIC]


# ── credencial ────────────────────────────────────────────────────────────────

def test_sem_credencial_401_e_vai_a_trilha_como_anonimo(ctx):
    r = ctx.client.get(f"/v1/recordings/{A1}/audio")
    assert r.status_code == 401
    [e] = _trilha(ctx)
    assert (e["actor_kind"], e["result"], e["tenant_id"]) == ("anonymous", "denied", "install_tenant")


def test_sem_segredo_503_nunca_libera(ctx, monkeypatch):
    monkeypatch.setattr(gw_main, "get_settings",
                        lambda: SimpleNamespace(auth_jwt_secret="", tenant_id="t"))
    assert ctx.client.get(f"/v1/recordings/{A1}/audio", headers=_tok("read_write")).status_code == 503


# ── capacidade ────────────────────────────────────────────────────────────────

def test_sem_o_campo_403_mesmo_para_id_inexistente(ctx):
    """403 antes de resolver: 404 × 403 não pode virar oráculo de existência."""
    assert ctx.client.get(f"/v1/recordings/{A1}/audio", headers=_tok(None)).status_code == 403
    assert ctx.client.get(f"/v1/recordings/{uuid.uuid4()}/audio", headers=_tok(None)).status_code == 403
    assert all(e["result"] == "denied" for e in _trilha(ctx)) and len(_trilha(ctx)) == 2


def test_ouvir_com_read_only_e_exportar_nao(ctx):
    h = _tok("read_only")
    r = ctx.client.get(f"/v1/recordings/{A1}/audio", headers=h)
    assert r.status_code == 200 and r.content == b"OggS"
    assert r.headers["content-disposition"].startswith("inline")
    assert r.headers["cache-control"] == "no-store"
    assert ctx.client.get(f"/v1/recordings/{A1}/export", headers=h).status_code == 403
    ok, negado = _trilha(ctx)
    assert (ok["endpoint"], ok["result"], ok["target_id"], ok["row_count"]) == \
        ("channel-gateway:recording.listen", "ok", f"{SESSION}/{A1}", 1)
    assert (negado["endpoint"], negado["result"]) == ("channel-gateway:recording.export", "denied")


def test_exportar_com_read_write(ctx):
    r = ctx.client.get(f"/v1/recordings/{A1}/export", headers=_tok("read_write"))
    assert r.status_code == 200 and r.headers["content-disposition"].startswith("attachment")
    [e] = _trilha(ctx)
    assert (e["endpoint"], e["result"]) == ("channel-gateway:recording.export", "ok")


# ── pool que atendeu a parte ──────────────────────────────────────────────────

def test_escopo_do_grant_recorta_pelo_pool_da_parte(ctx):
    h = _tok("read_only", scope=[f"pool:{POOL_A}"])
    assert ctx.client.get(f"/v1/recordings/{A1}/audio", headers=h).status_code == 200    # controle
    assert ctx.client.get(f"/v1/recordings/{B1}/audio", headers=h).status_code == 403


def test_accessible_pools_recorta_mesmo_com_grant_global(ctx):
    h = _tok("read_only", pools=[POOL_B])
    assert ctx.client.get(f"/v1/recordings/{B1}/audio", headers=h).status_code == 200    # controle
    assert ctx.client.get(f"/v1/recordings/{A1}/audio", headers=h).status_code == 403


def test_accessible_pools_vazio_e_nenhum_pool(ctx):
    assert ctx.client.get(f"/v1/recordings/{A1}/audio", headers=_tok("read_only", pools=[])).status_code == 403


def test_parte_sem_pools_e_recusada(ctx):
    assert ctx.client.get(f"/v1/recordings/{NOPOOL}/audio", headers=_tok("read_write")).status_code == 403


# ── classe, expiração ─────────────────────────────────────────────────────────

def test_anexo_de_webchat_nao_sai_por_aqui(ctx):
    assert ctx.client.get(f"/v1/recordings/{WEB}/audio", headers=_tok("read_write")).status_code == 404
    assert ctx.client.get("/v1/recordings/nao-e-uuid/audio", headers=_tok("read_write")).status_code == 404


def test_expirada_410(ctx):
    assert ctx.client.get(f"/v1/recordings/{OLD}/audio", headers=_tok("read_write")).status_code == 410


# ── lista ─────────────────────────────────────────────────────────────────────

def test_lista_mostra_so_as_partes_do_escopo_e_conta_as_outras(ctx):
    r = ctx.client.get(f"/v1/recordings/sessions/{SESSION}", headers=_tok("read_only", pools=[POOL_A]))
    assert r.status_code == 200
    body = r.json()
    assert [p["file_id"] for p in body["parts"]] == [A1]
    assert body["parts"][0]["can_export"] is False
    assert body["omitted"] == 2                     # B1 (outro pool) + NOPOOL (sem pool)
    assert _trilha(ctx) == []                       # listar metadados não é escuta


def test_lista_can_export_segue_o_nivel(ctx):
    body = ctx.client.get(f"/v1/recordings/sessions/{SESSION}", headers=_tok("read_write")).json()
    assert {p["file_id"]: p["can_export"] for p in body["parts"]} == {A1: True, B1: True}


def test_lista_sem_o_campo_403(ctx):
    assert ctx.client.get(f"/v1/recordings/sessions/{SESSION}", headers=_tok(None)).status_code == 403
    assert ctx.client.get(f"/v1/recordings/sessions/{SESSION}").status_code == 401


# ── trilha que falha não some calada ─────────────────────────────────────────

def test_sem_produtor_a_escuta_segue_e_o_log_nomeia(ctx, monkeypatch, caplog):
    monkeypatch.setattr(gw_main, "_producer", None)
    with caplog.at_level("ERROR"):
        assert ctx.client.get(f"/v1/recordings/{A1}/audio", headers=_tok("read_only")).status_code == 200
    assert any("NAO foi a trilha" in r.message for r in caplog.records)


def test_router_montado_no_app_real(ctx):
    """Pergunta ao app REAL por HTTP — nesta versão do FastAPI o router incluído aparece em
    `app.routes` como uma entrada de path vazio, então contar paths ali mediria a versão, não a
    montagem. Sem o router, o app devolveria 404; montado, o portão responde 401."""
    real = TestClient(gw_main.app)            # sem `with`: o lifespan (Kafka, Redis, PG) não roda
    assert real.get(f"/v1/recordings/sessions/{SESSION}").status_code == 401
    assert real.get(f"/v1/recordings/{A1}/audio").status_code == 401
    assert real.get(f"/v1/recordings/{A1}/export", headers=_tok("read_write")).status_code == 200
