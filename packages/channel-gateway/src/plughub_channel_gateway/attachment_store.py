"""
attachment_store.py
Abstração de storage de anexos para o canal webchat.

Princípio de evolução: a interface AttachmentStore é estável.
  - Fase 1: FilesystemAttachmentStore (disco local + PostgreSQL para metadados)
  - Fase 2: S3AttachmentStore / MinIOAttachmentStore (troca de implementação,
            interface inalterada, Channel Gateway não sabe da diferença)

Estrutura de diretórios (FilesystemAttachmentStore):
  {STORAGE_ROOT}/{tenant_id}/{YYYY}/{MM}/{DD}/{session_id}/{file_id}.{ext}

  O path date-sharded permite limpeza por data com rm -rf sem consultar o banco.
  O path session como subdiretório permite rm -rf atômico na expiração da sessão.

Metadados (PostgreSQL — tabela session_attachments):
  - Lookup rápido por file_id e session_id
  - TTL por expires_at (calculado no upload = NOW() + política do tenant)
  - Soft delete (deleted_at) com hard delete posterior pelo cron

Cron de expurgo (dois estágios):
  Estágio 1 (horário): SET deleted_at = NOW() WHERE expires_at < NOW()
  Estágio 2 (diário, grace=24h): DELETE arquivo, SET file_path = NULL
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Protocol, runtime_checkable

import aiofiles
import aiofiles.os
import asyncpg

logger = logging.getLogger("plughub.channel-gateway.attachment")

# ─── Allowlist de MIME types aceitos (fase 1) ────────────────────────────────
# TODO: fase 2 — validar também por magic bytes (python-filemagic ou filetype)

MIME_LIMITS: dict[str, int] = {
    "image/jpeg":       16 * 1024 * 1024,   # 16 MB
    "image/png":        16 * 1024 * 1024,
    "image/webp":       16 * 1024 * 1024,
    "image/gif":        16 * 1024 * 1024,
    "application/pdf": 100 * 1024 * 1024,   # 100 MB
    "video/mp4":       512 * 1024 * 1024,   # 512 MB
    "video/webm":      512 * 1024 * 1024,
}

MIME_TO_EXT: dict[str, str] = {
    "image/jpeg":       "jpg",
    "image/png":        "png",
    "image/webp":       "webp",
    "image/gif":        "gif",
    "application/pdf":  "pdf",
    "video/mp4":        "mp4",
    "video/webm":       "webm",
}

MIME_TO_CONTENT_TYPE: dict[str, str] = {
    "image/jpeg":       "image",
    "image/png":        "image",
    "image/webp":       "image",
    "image/gif":        "image",
    "application/pdf":  "document",
    "video/mp4":        "video",
    "video/webm":       "video",
}


# ─── Modelos de dados ─────────────────────────────────────────────────────────

class AttachmentMeta:
    """Metadados de um anexo retornado pelo store."""
    __slots__ = (
        "file_id", "tenant_id", "session_id", "original_name",
        "mime_type", "size_bytes", "file_path", "serving_url",
        "expires_at", "deleted_at",
    )

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


# ─── Interface (Protocol) ─────────────────────────────────────────────────────

@runtime_checkable
class AttachmentStore(Protocol):
    """
    Interface de storage de anexos.
    Implementações devem ser stateless por request — o estado vive no DB/disco.
    """

    async def reserve(
        self,
        *,
        tenant_id:   str,
        session_id:  str,
        file_name:   str,
        mime_type:   str,
        size_bytes:  int,
        expires_at:  datetime,
    ) -> tuple[str, str]:
        """
        Reserva um slot de upload.

        Returns:
            (file_id, upload_url) — file_id é o identificador único do anexo;
            upload_url é o endpoint HTTP para POST do conteúdo binário.
        """
        ...

    async def commit(
        self,
        *,
        file_id:    str,
        tenant_id:  str,
        data:       bytes,
    ) -> AttachmentMeta:
        """
        Persiste o conteúdo binário e atualiza status=committed.
        Retorna os metadados completos do anexo (incluindo serving_url).
        """
        ...

    async def resolve(
        self,
        *,
        file_id:   str,
        tenant_id: str,
    ) -> AttachmentMeta | None:
        """Busca metadados por file_id. Retorna None se não encontrado ou expirado."""
        ...

    async def stream_bytes(
        self,
        *,
        file_id:   str,
        tenant_id: str,
    ) -> AsyncIterator[bytes]:
        """Stream dos bytes do arquivo para serving HTTP."""
        ...

    async def soft_expire(
        self,
        *,
        file_id: str,
    ) -> None:
        """Marca deleted_at = NOW() sem deletar o arquivo do disco."""
        ...


# ─── Implementação: sistema de arquivos + PostgreSQL ──────────────────────────

class FilesystemAttachmentStore:
    """
    AttachmentStore para fase 1: arquivos no sistema de arquivos local,
    metadados no PostgreSQL.

    Args:
        storage_root:   diretório raiz para os arquivos (ex: /var/plughub/attachments)
        db_pool:        asyncpg pool do PostgreSQL
        serving_base_url: prefixo da URL pública (ex: https://host/webchat/v1/attachments)
        upload_base_url:  prefixo do endpoint de upload (ex: https://host/webchat/v1/upload)
    """

    DDL = """
    CREATE TABLE IF NOT EXISTS session_attachments (
        file_id          UUID         PRIMARY KEY,
        tenant_id        TEXT         NOT NULL,
        session_id       TEXT         NOT NULL,
        original_name    TEXT         NOT NULL,
        mime_type        TEXT         NOT NULL,
        size_bytes       BIGINT       NOT NULL DEFAULT 0,
        file_path        TEXT,
        status           TEXT         NOT NULL DEFAULT 'pending',
        created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
        expires_at       TIMESTAMPTZ  NOT NULL,
        deleted_at       TIMESTAMPTZ
    );
    CREATE INDEX IF NOT EXISTS idx_attach_session
        ON session_attachments (tenant_id, session_id);
    CREATE INDEX IF NOT EXISTS idx_attach_expires
        ON session_attachments (expires_at)
        WHERE deleted_at IS NULL;
    """

    def __init__(
        self,
        storage_root:     str | Path,
        db_pool:          asyncpg.Pool,
        serving_base_url: str,
        upload_base_url:  str,
    ) -> None:
        self._root        = Path(storage_root)
        self._db          = db_pool
        self._serving_url = serving_base_url.rstrip("/")
        self._upload_url  = upload_base_url.rstrip("/")

    async def ensure_schema(self) -> None:
        """Cria tabela e índices se não existirem. Chamado no startup."""
        async with self._db.acquire() as conn:
            await conn.execute(self.DDL)
        logger.info("AttachmentStore: schema ensured")

    # ── reserve ───────────────────────────────────────────────────────────────

    async def reserve(
        self,
        *,
        tenant_id:   str,
        session_id:  str,
        file_name:   str,
        mime_type:   str,
        size_bytes:  int,
        expires_at:  datetime,
    ) -> tuple[str, str]:
        file_id = str(uuid.uuid4())

        async with self._db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO session_attachments
                    (file_id, tenant_id, session_id, original_name,
                     mime_type, size_bytes, status, expires_at)
                VALUES ($1, $2, $3, $4, $5, $6, 'pending', $7)
                """,
                uuid.UUID(file_id),
                tenant_id,
                session_id,
                file_name,
                mime_type,
                size_bytes,
                expires_at,
            )

        upload_url = f"{self._upload_url}/{file_id}"
        logger.debug("AttachmentStore.reserve: file_id=%s session=%s", file_id, session_id)
        return file_id, upload_url

    # ── commit ────────────────────────────────────────────────────────────────

    async def commit(
        self,
        *,
        file_id:   str,
        tenant_id: str,
        data:      bytes,
    ) -> AttachmentMeta:
        # Carrega registro pendente
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT session_id, original_name, mime_type, expires_at
                FROM   session_attachments
                WHERE  file_id = $1 AND tenant_id = $2 AND status = 'pending'
                """,
                uuid.UUID(file_id), tenant_id,
            )

        if row is None:
            raise FileNotFoundError(f"slot não encontrado ou já committed: {file_id}")

        session_id    = row["session_id"]
        original_name = row["original_name"]
        mime_type     = row["mime_type"]
        expires_at    = row["expires_at"]

        # Calcula path date-sharded
        now       = datetime.now(timezone.utc)
        ext       = MIME_TO_EXT.get(mime_type, "bin")
        rel_path  = Path(tenant_id) / str(now.year) / f"{now.month:02d}" / f"{now.day:02d}" / session_id / f"{file_id}.{ext}"
        abs_path  = self._root / rel_path

        # Cria diretório e salva arquivo
        await aiofiles.os.makedirs(abs_path.parent, exist_ok=True)
        async with aiofiles.open(abs_path, "wb") as f:
            await f.write(data)

        actual_size = len(data)

        # Atualiza registro → committed
        async with self._db.acquire() as conn:
            await conn.execute(
                """
                UPDATE session_attachments
                SET    file_path  = $1,
                       size_bytes = $2,
                       status     = 'committed'
                WHERE  file_id = $3
                """,
                str(rel_path),
                actual_size,
                uuid.UUID(file_id),
            )

        serving_url = f"{self._serving_url}/{file_id}"
        logger.info(
            "AttachmentStore.commit: file_id=%s size=%d path=%s",
            file_id, actual_size, rel_path,
        )

        return AttachmentMeta(
            file_id       = file_id,
            tenant_id     = tenant_id,
            session_id    = session_id,
            original_name = original_name,
            mime_type     = mime_type,
            size_bytes    = actual_size,
            file_path     = str(rel_path),
            serving_url   = serving_url,
            expires_at    = expires_at,
            deleted_at    = None,
        )

    # ── resolve ───────────────────────────────────────────────────────────────

    async def resolve(
        self,
        *,
        file_id:   str,
        tenant_id: str,
    ) -> AttachmentMeta | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT session_id, original_name, mime_type, size_bytes,
                       file_path, expires_at, deleted_at
                FROM   session_attachments
                WHERE  file_id = $1 AND tenant_id = $2
                """,
                uuid.UUID(file_id), tenant_id,
            )

        if row is None:
            return None

        return AttachmentMeta(
            file_id       = file_id,
            tenant_id     = tenant_id,
            session_id    = row["session_id"],
            original_name = row["original_name"],
            mime_type     = row["mime_type"],
            size_bytes    = row["size_bytes"],
            file_path     = row["file_path"],
            serving_url   = f"{self._serving_url}/{file_id}",
            expires_at    = row["expires_at"],
            deleted_at    = row["deleted_at"],
        )

    # ── stream_bytes ──────────────────────────────────────────────────────────

    async def stream_bytes(
        self,
        *,
        file_id:   str,
        tenant_id: str,
    ) -> AsyncIterator[bytes]:
        meta = await self.resolve(file_id=file_id, tenant_id=tenant_id)
        if meta is None or meta.file_path is None:
            raise FileNotFoundError(file_id)
        if meta.deleted_at is not None:
            raise FileNotFoundError(f"arquivo expirado: {file_id}")

        abs_path = self._root / meta.file_path

        async def _gen():
            async with aiofiles.open(abs_path, "rb") as f:
                while chunk := await f.read(64 * 1024):
                    yield chunk

        return _gen()

    # ── soft_expire ───────────────────────────────────────────────────────────

    async def soft_expire(self, *, file_id: str) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                """
                UPDATE session_attachments
                SET    deleted_at = NOW()
                WHERE  file_id = $1 AND deleted_at IS NULL
                """,
                uuid.UUID(file_id),
            )
        logger.debug("AttachmentStore.soft_expire: file_id=%s", file_id)

    # ── Helpers utilitários ───────────────────────────────────────────────────

    @staticmethod
    def validate_mime(mime_type: str, size_bytes: int) -> str | None:
        """
        Valida MIME type e tamanho.
        Retorna None se válido, ou mensagem de erro.
        """
        limit = MIME_LIMITS.get(mime_type)
        if limit is None:
            return f"mime_type não aceito: {mime_type}"
        if size_bytes > limit:
            return f"arquivo muito grande: {size_bytes} > {limit} bytes"
        if size_bytes <= 0:
            return "tamanho inválido"
        return None
