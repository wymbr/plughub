"""
test_identity_provenance.py — PID-12 (2026-09-13)

A importação com credencial é a ÚNICA porta que carimba procedência `authoritative`
na âncora do cliente (decisão do dono, 2026-09-12).

Aqui: a trava do índice (nenhum outro escritor alcança `authoritative`), a
procedência que o adapter declara ao extrair âncoras, e as quatro recusas da rota.
O que só o Postgres responde — o upsert que zera a procedência na reatribuição, a
recusa por conflito, a reimportação idempotente — é do probe ao vivo,
`infra/test/probe_identity_provenance.sh`: SQL com mock não prova o SQL.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import jwt as pyjwt
import pytest
from fastapi import HTTPException

from plughub_channel_gateway import main as cg_main
from plughub_channel_gateway.adapters.webhook import WebhookAdapter
from plughub_channel_gateway.identity.index import (
    PROVENANCE_AUTHORITATIVE,
    IdentityIndex,
    _writer_provenance,
)
from plughub_channel_gateway.main import IdentityImportRequest

SECRET = "segredo-de-teste-hs256"


# ── a trava do índice ─────────────────────────────────────────────────────────

class TestTravaDoIndice:
    def test_escritor_comum_nao_carimba_authoritative(self):
        with pytest.raises(ValueError, match="import_customers"):
            _writer_provenance(PROVENANCE_AUTHORITATIVE)

    def test_valor_fora_do_dominio_recusa(self):
        with pytest.raises(ValueError, match="desconhecida"):
            _writer_provenance("crm")

    def test_valores_dos_escritores_e_ausente_passam(self):
        for p in ("declared", "channel_origin", "operator", None):
            assert _writer_provenance(p) == p

    async def test_attach_anchor_recusa_antes_de_escrever(self):
        redis = AsyncMock()
        idx = IdentityIndex(redis=redis, salt="s", db_pool=MagicMock(), phone_region="BR")
        with pytest.raises(ValueError):
            await idx.attach_anchor("t", "cus_x", "phone", "+5511999990000",
                                    persist_durable=True, provenance="authoritative")
        redis.set.assert_not_called()

    async def test_promote_recusa_ancora_que_se_diz_autoritativa(self):
        idx = IdentityIndex(redis=AsyncMock(), salt="s", db_pool=MagicMock(), phone_region="BR")
        with pytest.raises(ValueError):
            await idx.promote_to_durable(
                "t", "cus_x",
                [{"kind": "phone", "value": "+5511999990000", "provenance": "authoritative"}],
            )


# ── a LEITURA (IDN-07) ────────────────────────────────────────────────────────

class _RedisDict:
    def __init__(self, kv=None):
        self.kv = dict(kv or {})

    async def get(self, k):
        return self.kv.get(k)

    async def set(self, k, v, ex=None):
        self.kv[k] = v


def _pg_com(linha):
    """Pool cujo `fetchrow` devolve sempre `linha` (dict ou None)."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=linha)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=cm)
    return pool


def _indexado(idx, tenant, kind, value, cid):
    from plughub_channel_gateway.identity.index import _encode_index
    from plughub_channel_gateway.identity.normalize import hash_anchor
    return {idx._identity_key(tenant, kind, hash_anchor("s", kind, value, "BR")): _encode_index(cid, "claimed")}


class TestLeitura:
    async def test_mesmo_cliente_devolve_a_procedencia_do_cadastro(self):
        idx = IdentityIndex(redis=None, salt="s",
                            db_pool=_pg_com({"customer_id": "cus_a", "provenance": "authoritative"}), phone_region="BR")
        idx._redis = _RedisDict(_indexado(idx, "t", "phone", "+5511999990000", "cus_a"))
        ref = await idx.resolve_or_provision("t", [{"kind": "phone", "value": "+5511999990000"}], provision=False)
        assert (ref.customer_id, ref.provenance) == ("cus_a", "authoritative")

    async def test_redis_divergente_nao_empresta_e_avisa(self, caplog):
        # O índice diz `cus_b`; o cadastro diz que a âncora é autoritativa de `cus_a`.
        idx = IdentityIndex(redis=None, salt="s",
                            db_pool=_pg_com({"customer_id": "cus_a", "provenance": "authoritative"}), phone_region="BR")
        idx._redis = _RedisDict(_indexado(idx, "t", "phone", "+5511999990000", "cus_b"))
        ref = await idx.resolve_or_provision("t", [{"kind": "phone", "value": "+5511999990000"}], provision=False)
        assert ref.customer_id == "cus_b"
        assert ref.provenance is None
        assert "IDN-10" in caplog.text

    async def test_prospect_efemero_nao_tem_procedencia(self):
        idx = IdentityIndex(redis=_RedisDict(), salt="s", db_pool=_pg_com(None), phone_region="BR")
        ref = await idx.resolve_or_provision("t", [{"kind": "phone", "value": "+5511999990000"}])
        assert ref.matched_by == "provisioned" and ref.provenance is None

    async def test_anchor_provenance_responde_por_cliente(self):
        idx = IdentityIndex(redis=_RedisDict(), salt="s",
                            db_pool=_pg_com({"customer_id": "cus_a", "provenance": "authoritative"}), phone_region="BR")
        assert await idx.anchor_provenance("t", "cus_a", "phone", "+5511999990000") == "authoritative"
        assert await idx.anchor_provenance("t", "cus_b", "phone", "+5511999990000") is None
        assert await idx.anchor_provenance("t", "", "phone", "+5511999990000") is None


# ── a procedência que o adapter declara ───────────────────────────────────────

class TestExtracaoDeclaraProcedencia:
    def test_contexto_e_declared_e_identificador_do_canal_e_channel_origin(self):
        anchors = WebhookAdapter._anchors_from_context({
            "session.cpf": "12345678909",
            "contact_identifier": "+5511988887777",
        })
        por_tipo = {(a["kind"], a["provenance"]) for a in anchors}
        assert ("cpf", "declared") in por_tipo
        assert ("phone", "channel_origin") in por_tipo
        assert all(a["provenance"] != PROVENANCE_AUTHORITATIVE for a in anchors)


# ── a rota ────────────────────────────────────────────────────────────────────

class _Req:
    def __init__(self, token: str | None = None):
        self.headers = {"authorization": "Bearer %s" % token} if token else {}


def _token(**claims) -> str:
    base = {"sub": "user_admin", "tenant_id": "tenant_demo",
            "module_config": {"contacts": {"importar_cadastro": {"access": "read_write"}}}}
    base.update(claims)
    return pyjwt.encode(base, SECRET, algorithm="HS256")


def _body(n: int = 1) -> IdentityImportRequest:
    return IdentityImportRequest(system="crm", customers=[
        {"external_id": "e%d" % i, "anchors": [{"kind": "phone", "value": "+55119999%05d" % i}]}
        for i in range(n)
    ])


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    monkeypatch.setattr(cg_main.get_settings(), "auth_jwt_secret", SECRET, raising=False)
    fake = MagicMock()
    fake.import_customers = AsyncMock(return_value={"created": 1, "updated": 0, "refused": 0, "results": []})
    monkeypatch.setattr(cg_main, "_webhook_adapter", fake)
    return fake


class TestRota:
    async def test_sem_credencial_401(self):
        with pytest.raises(HTTPException) as e:
            await cg_main.webhook_identity_import(_body(), _Req())
        assert e.value.status_code == 401

    async def test_sem_o_campo_403_mesmo_sendo_admin_de_papel(self, _ambiente):
        # Papel não é portão (Arc 7): `roles: [admin]` sem o grant não importa.
        tok = _token(roles=["admin"], module_config={"contacts": {"exportar": {"access": "read_write"}}})
        with pytest.raises(HTTPException) as e:
            await cg_main.webhook_identity_import(_body(), _Req(tok))
        assert e.value.status_code == 403
        _ambiente.import_customers.assert_not_called()

    async def test_so_leitura_do_campo_nao_basta(self, _ambiente):
        tok = _token(module_config={"contacts": {"importar_cadastro": {"access": "read_only"}}})
        with pytest.raises(HTTPException) as e:
            await cg_main.webhook_identity_import(_body(), _Req(tok))
        assert e.value.status_code == 403

    async def test_teto_de_linhas_413(self, _ambiente):
        with pytest.raises(HTTPException) as e:
            await cg_main.webhook_identity_import(
                _body(cg_main.IDENTITY_IMPORT_MAX_ROWS + 1), _Req(_token()))
        assert e.value.status_code == 413
        _ambiente.import_customers.assert_not_called()

    async def test_com_o_campo_importa_no_tenant_do_JWT(self, _ambiente):
        # Controle POSITIVO: sem ele, os negativos acima passariam por uma rota que
        # recusa todo mundo.
        await cg_main.webhook_identity_import(_body(2), _Req(_token(tenant_id="tenant_x")))
        kw = _ambiente.import_customers.call_args.kwargs
        assert kw["tenant_id"] == "tenant_x"
        assert kw["imported_by"] == "user_admin"
        assert len(kw["rows"]) == 2

    def test_o_corpo_nao_tem_tenant(self):
        assert "tenant_id" not in IdentityImportRequest.model_fields
