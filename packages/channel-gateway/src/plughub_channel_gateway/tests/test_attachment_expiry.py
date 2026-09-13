"""
test_attachment_expiry.py — VOZ-07 (2026-09-13)

O expurgo de anexos estava descrito em três documentos e em nenhum código. Estes
testes cobrem a MECÂNICA (drenagem, contagem de falha, idempotência, a task de
boot); a proposição "a linha vencida some DE VERDADE no Postgres e no S3" é do
probe ao vivo, `infra/test/probe_attachment_expiry.sh`, porque SQL com mock não
prova o SQL.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from plughub_channel_gateway import attachment_expiry as ae
from plughub_channel_gateway.attachment_store import (
    _SQL_EXPIRE_DUE,
    _SQL_MARK_PURGED,
    _SQL_PURGE_CANDIDATES,
    FilesystemAttachmentStore,
    PurgeResult,
    S3AttachmentStore,
    _rowcount,
)


def _pool(fetch_rows=None, execute_status="UPDATE 0"):
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=fetch_rows or [])
    conn.execute = AsyncMock(return_value=execute_status)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=cm)
    return pool, conn


def _fs(tmp_path, **kw):
    pool, conn = _pool(**kw)
    store = FilesystemAttachmentStore(
        storage_root=tmp_path, db_pool=pool,
        serving_base_url="http://h/a", upload_base_url="http://h/u",
    )
    return store, conn


def _marcacoes(conn):
    return [c.args for c in conn.execute.call_args_list if c.args[0] == _SQL_MARK_PURGED]


# ── o contador ────────────────────────────────────────────────────────────────

class TestRowcount:
    def test_status_do_asyncpg(self):
        assert _rowcount("UPDATE 3") == 3

    def test_ausente_e_zero_nao_excecao(self):
        assert _rowcount(None) == 0
        assert _rowcount("") == 0


# ── estágio 1 ─────────────────────────────────────────────────────────────────

class TestExpireDue:
    async def test_devolve_quantas_marcou_e_passa_o_limite(self, tmp_path):
        store, conn = _fs(tmp_path, execute_status="UPDATE 7")
        assert await store.expire_due(limit=500) == 7
        conn.execute.assert_awaited_once_with(_SQL_EXPIRE_DUE, 500)

    def test_sql_so_toca_vencida_e_ainda_nao_marcada(self):
        # Sem o `deleted_at IS NULL`, cada passada remarcaria a mesma linha e
        # empurraria a carência do estágio 2 para sempre.
        assert "deleted_at IS NULL" in _SQL_EXPIRE_DUE
        assert "expires_at < NOW()" in _SQL_EXPIRE_DUE
        assert "SKIP LOCKED" in _SQL_EXPIRE_DUE


# ── estágio 2, filesystem ─────────────────────────────────────────────────────

class TestPurgeFilesystem:
    async def test_apaga_o_arquivo_e_zera_o_path(self, tmp_path):
        fid = uuid.uuid4()
        (tmp_path / "t").mkdir()
        (tmp_path / "t" / "a.jpg").write_bytes(b"\xff\xd8\xff")
        store, conn = _fs(tmp_path, fetch_rows=[{"file_id": fid, "file_path": "t/a.jpg"}])

        r = await store.purge_deleted(grace=timedelta(hours=24), limit=500)

        assert (r.purged, r.failed) == (1, 0)
        assert not (tmp_path / "t" / "a.jpg").exists()
        conn.fetch.assert_awaited_once_with(_SQL_PURGE_CANDIDATES, timedelta(hours=24), 500)
        assert _marcacoes(conn) == [(_SQL_MARK_PURGED, fid, "t/a.jpg")]

    async def test_arquivo_ja_ausente_conta_como_apagado(self, tmp_path):
        fid = uuid.uuid4()
        store, conn = _fs(tmp_path, fetch_rows=[{"file_id": fid, "file_path": "nao/existe.jpg"}])
        r = await store.purge_deleted(grace=timedelta(hours=24), limit=500)
        assert (r.purged, r.failed) == (1, 0)
        assert len(_marcacoes(conn)) == 1

    async def test_blob_que_nao_sai_e_contado_nomeado_e_mantem_o_path(self, tmp_path):
        # Diretório no lugar do arquivo: `remove` levanta, e não é FileNotFoundError.
        fid = uuid.uuid4()
        (tmp_path / "t" / "dir.jpg").mkdir(parents=True)
        store, conn = _fs(tmp_path, fetch_rows=[{"file_id": fid, "file_path": "t/dir.jpg"}])

        r = await store.purge_deleted(grace=timedelta(hours=24), limit=500)

        assert (r.purged, r.failed) == (0, 1)
        assert r.failed_ids == [str(fid)]
        assert _marcacoes(conn) == [], "zerar o path de um blob que ficou o tornaria orfao"


# ── estágio 2, S3 ─────────────────────────────────────────────────────────────

class TestPurgeS3:
    async def test_delete_object_no_bucket_e_na_chave(self):
        fid = uuid.uuid4()
        pool, conn = _pool(fetch_rows=[{"file_id": fid, "file_path": "t/2026/09/13/s/x.jpg"}])
        s3 = AsyncMock()
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=s3)
        cm.__aexit__ = AsyncMock(return_value=False)
        sessao = MagicMock()
        sessao.client = MagicMock(return_value=cm)

        store = object.__new__(S3AttachmentStore)  # sem aioboto3 no teste
        store._db, store._bucket, store._endpoint_url, store._session = pool, "b", None, sessao

        r = await store.purge_deleted(grace=timedelta(hours=24), limit=500)

        assert (r.purged, r.failed) == (1, 0)
        s3.delete_object.assert_awaited_once_with(Bucket="b", Key="t/2026/09/13/s/x.jpg")
        assert len(_marcacoes(conn)) == 1


# ── drenagem ──────────────────────────────────────────────────────────────────

class _Fake:
    def __init__(self, expira=(), purga=()):
        self.expira, self.purga = list(expira), list(purga)
        self.chamadas_expira = 0
        self.chamadas_purga = 0

    async def expire_due(self, *, limit):
        self.chamadas_expira += 1
        return self.expira.pop(0) if self.expira else 0

    async def purge_deleted(self, *, grace, limit):
        self.chamadas_purga += 1
        return self.purga.pop(0) if self.purga else PurgeResult()


class TestDrenagem:
    async def test_estagio1_drena_ate_lote_incompleto(self):
        f = _Fake(expira=[10, 10, 3, 99])
        assert await ae.expire_once(f, batch=10) == 23
        assert f.chamadas_expira == 3

    async def test_estagio1_para_no_teto_e_avisa(self, caplog):
        f = _Fake(expira=[10] * 9)
        assert await ae.expire_once(f, batch=10, max_batches=3) == 30
        assert "teto de 3 lotes" in caplog.text

    async def test_estagio2_conta_falha_por_arquivo_nao_por_tentativa(self):
        # O mesmo blob falha em dois lotes seguidos: é UMA falha.
        f = _Fake(purga=[
            PurgeResult(purged=9, failed=1, failed_ids=["x"]),
            PurgeResult(purged=4, failed=1, failed_ids=["x"]),
        ])
        r = await ae.purge_once(f, batch=10)
        assert (r.purged, r.failed, r.failed_ids) == (13, 1, ["x"])

    async def test_estagio2_para_quando_o_lote_so_tem_falha(self):
        f = _Fake(purga=[PurgeResult(purged=0, failed=10, failed_ids=[str(i) for i in range(10)])] * 5)
        await ae.purge_once(f, batch=10)
        assert f.chamadas_purga == 1


# ── a task de boot ────────────────────────────────────────────────────────────

class TestTaskDeBoot:
    async def test_primeira_passada_roda_na_subida_antes_de_dormir(self, monkeypatch):
        f = _Fake(expira=[2], purga=[PurgeResult(purged=1)])
        dormiu = []

        async def sleep(s):
            dormiu.append(s)
            raise asyncio.CancelledError

        monkeypatch.setattr(ae.asyncio, "sleep", sleep)
        with pytest.raises(asyncio.CancelledError):
            await ae.run_attachment_expiry(f)
        assert f.chamadas_expira >= 1 and f.chamadas_purga == 1
        assert dormiu == [ae.EXPIRE_INTERVAL_S]

    async def test_passada_que_explode_nao_mata_o_laco(self, monkeypatch, caplog):
        class Explode(_Fake):
            async def expire_due(self, *, limit):
                raise RuntimeError("postgres fora")

        voltas = []

        async def sleep(s):
            voltas.append(s)
            if len(voltas) == 2:
                raise asyncio.CancelledError

        monkeypatch.setattr(ae.asyncio, "sleep", sleep)
        with pytest.raises(asyncio.CancelledError):
            await ae.run_attachment_expiry(Explode())
        assert len(voltas) == 2, "o laco morreu na primeira excecao"
        assert "postgres fora" in caplog.text
