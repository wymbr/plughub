"""
test_attachment_store.py
Unit tests for FilesystemAttachmentStore and validate_mime.

Strategy:
  - validate_mime: pure static method, no mocking needed
  - reserve / commit / resolve / soft_expire / stream_bytes: asyncpg pool is
    mocked; file I/O uses pytest's tmp_path fixture (real filesystem)
  - commit writes a real file so stream_bytes can read it back

asyncpg mock structure:
  pool.acquire() → async context manager → conn
  conn.execute / conn.fetchrow are AsyncMocks
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from plughub_channel_gateway.attachment_store import (
    AttachmentMeta,
    FilesystemAttachmentStore,
    MIME_LIMITS,
    MIME_TO_CONTENT_TYPE,
    validate_magic_bytes,
)


# ── asyncpg mock factory ──────────────────────────────────────────────────────

def make_pool(fetchrow_return=None, execute_return=None):
    """
    Builds a minimal asyncpg Pool mock.
    pool.acquire() returns an async context manager that yields conn.
    conn.fetchrow / conn.execute are AsyncMocks.
    """
    conn = AsyncMock()
    conn.execute  = AsyncMock(return_value=execute_return)
    conn.fetchrow = AsyncMock(return_value=fetchrow_return)

    acquire_cm = MagicMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=conn)
    acquire_cm.__aexit__  = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire_cm)

    return pool, conn


def make_store(tmp_path, fetchrow=None, execute=None) -> tuple[FilesystemAttachmentStore, MagicMock]:
    pool, conn = make_pool(fetchrow_return=fetchrow, execute_return=execute)
    store = FilesystemAttachmentStore(
        storage_root     = tmp_path,
        db_pool          = pool,
        serving_base_url = "http://host/webchat/v1/attachments",
        upload_base_url  = "http://host/webchat/v1/upload",
    )
    return store, conn


TENANT  = "tenant_test"
SESSION = "sess-001"
EXPIRES = datetime.now(timezone.utc) + timedelta(days=30)


# ── validate_mime ─────────────────────────────────────────────────────────────

class TestValidateMime:
    def test_valid_jpeg(self):
        assert FilesystemAttachmentStore.validate_mime("image/jpeg", 1024) is None

    def test_valid_png(self):
        assert FilesystemAttachmentStore.validate_mime("image/png", 4096) is None

    def test_valid_pdf(self):
        assert FilesystemAttachmentStore.validate_mime("application/pdf", 1_000_000) is None

    def test_valid_mp4(self):
        assert FilesystemAttachmentStore.validate_mime("video/mp4", 10_000_000) is None

    def test_valid_webm(self):
        assert FilesystemAttachmentStore.validate_mime("video/webm", 10_000_000) is None

    def test_unknown_mime_returns_error(self):
        err = FilesystemAttachmentStore.validate_mime("application/x-msdownload", 512)
        assert err is not None
        assert "não aceito" in err

    def test_size_too_large_returns_error(self):
        # JPEG limit is 16 MB
        too_big = MIME_LIMITS["image/jpeg"] + 1
        err = FilesystemAttachmentStore.validate_mime("image/jpeg", too_big)
        assert err is not None
        assert "grande" in err

    def test_zero_size_returns_error(self):
        err = FilesystemAttachmentStore.validate_mime("image/jpeg", 0)
        assert err is not None
        assert "inválido" in err

    def test_negative_size_returns_error(self):
        err = FilesystemAttachmentStore.validate_mime("image/jpeg", -1)
        assert err is not None

    def test_exact_limit_is_valid(self):
        limit = MIME_LIMITS["application/pdf"]
        assert FilesystemAttachmentStore.validate_mime("application/pdf", limit) is None

    def test_one_over_limit_is_invalid(self):
        limit = MIME_LIMITS["application/pdf"]
        assert FilesystemAttachmentStore.validate_mime("application/pdf", limit + 1) is not None


# ── validate_magic_bytes ──────────────────────────────────────────────────────

# Minimal valid headers for each supported MIME type
_JPEG_HDR  = b"\xff\xd8\xff" + b"\x00" * 16
_PNG_HDR   = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
_WEBP_HDR  = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 16
_GIF87_HDR = b"GIF87a" + b"\x00" * 16
_GIF89_HDR = b"GIF89a" + b"\x00" * 16
_PDF_HDR   = b"%PDF-1.4\n" + b"\x00" * 16
_MP4_HDR   = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 16
_WEBM_HDR  = b"\x1a\x45\xdf\xa3" + b"\x00" * 16


class TestValidateMagicBytes:
    def test_valid_jpeg(self):
        assert validate_magic_bytes(_JPEG_HDR, "image/jpeg") is None

    def test_valid_png(self):
        assert validate_magic_bytes(_PNG_HDR, "image/png") is None

    def test_valid_webp(self):
        assert validate_magic_bytes(_WEBP_HDR, "image/webp") is None

    def test_valid_gif87(self):
        assert validate_magic_bytes(_GIF87_HDR, "image/gif") is None

    def test_valid_gif89(self):
        assert validate_magic_bytes(_GIF89_HDR, "image/gif") is None

    def test_valid_pdf(self):
        assert validate_magic_bytes(_PDF_HDR, "application/pdf") is None

    def test_valid_mp4(self):
        assert validate_magic_bytes(_MP4_HDR, "video/mp4") is None

    def test_valid_webm(self):
        assert validate_magic_bytes(_WEBM_HDR, "video/webm") is None

    def test_jpeg_content_declared_as_png_returns_error(self):
        err = validate_magic_bytes(_JPEG_HDR, "image/png")
        assert err is not None
        assert "image/png" in err

    def test_png_content_declared_as_jpeg_returns_error(self):
        err = validate_magic_bytes(_PNG_HDR, "image/jpeg")
        assert err is not None

    def test_webp_missing_webp_marker_returns_error(self):
        # Has RIFF but not WEBP at offset 8
        bad = b"RIFF\x00\x00\x00\x00XXXX"
        err = validate_magic_bytes(bad, "image/webp")
        assert err is not None

    def test_mp4_without_ftyp_at_offset4_returns_error(self):
        err = validate_magic_bytes(b"\x00\x00\x00\x00XXXX" + b"\x00" * 16, "video/mp4")
        assert err is not None

    def test_unknown_mime_is_accepted_fail_open(self):
        # Types not in _MAGIC_SIGS should pass without validation
        assert validate_magic_bytes(b"anything", "application/octet-stream") is None

    def test_too_short_data_returns_error(self):
        # Only 2 bytes — can't match any signature
        err = validate_magic_bytes(b"\xff\xd8", "image/jpeg")
        assert err is not None

    def test_empty_data_returns_error(self):
        err = validate_magic_bytes(b"", "image/jpeg")
        assert err is not None

    async def test_commit_rejects_mime_mismatch(self, tmp_path):
        """commit() should raise ValueError when magic bytes do not match declared MIME."""
        file_id = str(uuid.uuid4())
        # DB says image/jpeg but data is PNG bytes
        row = {
            "session_id":    SESSION,
            "original_name": "fake.jpg",
            "mime_type":     "image/jpeg",
            "expires_at":    EXPIRES,
        }
        store, _ = make_store(tmp_path, fetchrow=row)
        with pytest.raises(ValueError, match="image/jpeg"):
            await store.commit(file_id=file_id, tenant_id=TENANT, data=_PNG_HDR)


# ── reserve ───────────────────────────────────────────────────────────────────

class TestReserve:
    async def test_returns_file_id_and_upload_url(self, tmp_path):
        store, conn = make_store(tmp_path)
        file_id, upload_url = await store.reserve(
            tenant_id  = TENANT,
            session_id = SESSION,
            file_name  = "photo.jpg",
            mime_type  = "image/jpeg",
            size_bytes = 1024,
            expires_at = EXPIRES,
        )
        assert file_id  # non-empty
        assert upload_url == f"http://host/webchat/v1/upload/{file_id}"

    async def test_inserts_pending_record(self, tmp_path):
        store, conn = make_store(tmp_path)
        file_id, _ = await store.reserve(
            tenant_id  = TENANT,
            session_id = SESSION,
            file_name  = "doc.pdf",
            mime_type  = "application/pdf",
            size_bytes = 2048,
            expires_at = EXPIRES,
        )
        conn.execute.assert_called_once()
        sql, *params = conn.execute.call_args.args
        assert "INSERT INTO session_attachments" in sql
        assert "pending" in sql

    async def test_file_id_is_valid_uuid(self, tmp_path):
        store, _ = make_store(tmp_path)
        file_id, _ = await store.reserve(
            tenant_id  = TENANT,
            session_id = SESSION,
            file_name  = "clip.mp4",
            mime_type  = "video/mp4",
            size_bytes = 1024,
            expires_at = EXPIRES,
        )
        uuid.UUID(file_id)  # raises if not valid UUID

    async def test_upload_url_contains_file_id(self, tmp_path):
        store, _ = make_store(tmp_path)
        file_id, upload_url = await store.reserve(
            tenant_id  = TENANT,
            session_id = SESSION,
            file_name  = "x.jpg",
            mime_type  = "image/jpeg",
            size_bytes = 512,
            expires_at = EXPIRES,
        )
        assert file_id in upload_url


# ── commit ────────────────────────────────────────────────────────────────────

class TestCommit:
    def _pending_row(self, file_id: str) -> dict:
        return {
            "session_id":    SESSION,
            "original_name": "photo.jpg",
            "mime_type":     "image/jpeg",
            "expires_at":    EXPIRES,
        }

    async def test_writes_file_to_disk(self, tmp_path):
        file_id = str(uuid.uuid4())
        store, conn = make_store(tmp_path, fetchrow=self._pending_row(file_id))

        data = _JPEG_HDR + b"\x00" * 100  # valid JPEG magic + padding
        meta = await store.commit(file_id=file_id, tenant_id=TENANT, data=data)

        abs_path = tmp_path / meta.file_path
        assert abs_path.exists()
        assert abs_path.read_bytes() == data

    async def test_commit_returns_meta_with_correct_fields(self, tmp_path):
        file_id = str(uuid.uuid4())
        store, conn = make_store(tmp_path, fetchrow=self._pending_row(file_id))

        data = _JPEG_HDR + b"\x00" * (100 - len(_JPEG_HDR))
        meta = await store.commit(file_id=file_id, tenant_id=TENANT, data=data)

        assert meta.file_id    == file_id
        assert meta.tenant_id  == TENANT
        assert meta.session_id == SESSION
        assert meta.size_bytes == len(data)
        assert meta.mime_type  == "image/jpeg"
        assert meta.serving_url == f"http://host/webchat/v1/attachments/{file_id}"
        assert meta.deleted_at is None

    async def test_commit_updates_status_to_committed(self, tmp_path):
        file_id = str(uuid.uuid4())
        store, conn = make_store(tmp_path, fetchrow=self._pending_row(file_id))
        await store.commit(file_id=file_id, tenant_id=TENANT, data=_JPEG_HDR)

        conn.execute.assert_called_once()
        sql = conn.execute.call_args.args[0]
        assert "committed" in sql
        assert "UPDATE session_attachments" in sql

    async def test_commit_path_is_date_sharded(self, tmp_path):
        file_id = str(uuid.uuid4())
        store, conn = make_store(tmp_path, fetchrow=self._pending_row(file_id))
        meta = await store.commit(file_id=file_id, tenant_id=TENANT, data=_JPEG_HDR)

        now = datetime.now(timezone.utc)
        # Path contains tenant_id / YYYY / MM / DD
        assert TENANT in meta.file_path
        assert str(now.year) in meta.file_path

    async def test_commit_raises_if_no_pending_slot(self, tmp_path):
        file_id = str(uuid.uuid4())
        store, _ = make_store(tmp_path, fetchrow=None)  # fetchrow returns None

        with pytest.raises(FileNotFoundError):
            await store.commit(file_id=file_id, tenant_id=TENANT, data=_JPEG_HDR)

    async def test_commit_file_extension_matches_mime(self, tmp_path):
        """PDF file gets .pdf extension; JPEG gets .jpg etc."""
        pdf_row = {
            "session_id": SESSION, "original_name": "doc.pdf",
            "mime_type": "application/pdf", "expires_at": EXPIRES,
        }
        file_id = str(uuid.uuid4())
        store, _ = make_store(tmp_path, fetchrow=pdf_row)
        meta = await store.commit(file_id=file_id, tenant_id=TENANT, data=_PDF_HDR)
        assert meta.file_path.endswith(".pdf")


# ── resolve ───────────────────────────────────────────────────────────────────

class TestResolve:
    async def test_returns_meta_for_known_file(self, tmp_path):
        file_id = str(uuid.uuid4())
        db_row = {
            "session_id":    SESSION,
            "original_name": "test.jpg",
            "mime_type":     "image/jpeg",
            "size_bytes":    512,
            "file_path":     f"{TENANT}/2024/01/15/{SESSION}/{file_id}.jpg",
            "expires_at":    EXPIRES,
            "deleted_at":    None,
        }
        store, _ = make_store(tmp_path, fetchrow=db_row)
        meta = await store.resolve(file_id=file_id, tenant_id=TENANT)

        assert meta is not None
        assert meta.file_id    == file_id
        assert meta.tenant_id  == TENANT
        assert meta.mime_type  == "image/jpeg"
        assert meta.size_bytes == 512
        assert meta.deleted_at is None

    async def test_returns_none_for_unknown_file(self, tmp_path):
        store, _ = make_store(tmp_path, fetchrow=None)
        result = await store.resolve(file_id=str(uuid.uuid4()), tenant_id=TENANT)
        assert result is None

    async def test_serving_url_in_meta(self, tmp_path):
        file_id = str(uuid.uuid4())
        db_row = {
            "session_id": SESSION, "original_name": "x.jpg",
            "mime_type": "image/jpeg", "size_bytes": 10,
            "file_path": "path/x.jpg", "expires_at": EXPIRES, "deleted_at": None,
        }
        store, _ = make_store(tmp_path, fetchrow=db_row)
        meta = await store.resolve(file_id=file_id, tenant_id=TENANT)
        assert meta.serving_url == f"http://host/webchat/v1/attachments/{file_id}"


# ── soft_expire ───────────────────────────────────────────────────────────────

class TestSoftExpire:
    async def test_soft_expire_calls_update(self, tmp_path):
        file_id = str(uuid.uuid4())
        store, conn = make_store(tmp_path)
        await store.soft_expire(file_id=file_id)

        conn.execute.assert_called_once()
        sql = conn.execute.call_args.args[0]
        assert "deleted_at" in sql
        assert "UPDATE session_attachments" in sql

    async def test_soft_expire_does_not_raise_on_unknown(self, tmp_path):
        """soft_expire is a best-effort operation — no error on unknown file."""
        store, conn = make_store(tmp_path)
        await store.soft_expire(file_id=str(uuid.uuid4()))  # should not raise


# ── stream_bytes ──────────────────────────────────────────────────────────────

class TestStreamBytes:
    async def _committed_file(
        self, tmp_path, content: bytes = b"hello world"
    ) -> tuple[FilesystemAttachmentStore, str, dict]:
        """Creates a real committed file under tmp_path; returns (store, file_id, db_row)."""
        file_id = str(uuid.uuid4())
        # Build the expected date-sharded path
        now = datetime.now(timezone.utc)
        rel = (
            f"{TENANT}/{now.year}/{now.month:02d}/{now.day:02d}"
            f"/{SESSION}/{file_id}.jpg"
        )
        abs_path = tmp_path / rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(content)

        db_row = {
            "session_id": SESSION, "original_name": "photo.jpg",
            "mime_type": "image/jpeg", "size_bytes": len(content),
            "file_path": rel, "expires_at": EXPIRES, "deleted_at": None,
        }
        store, _ = make_store(tmp_path, fetchrow=db_row)
        return store, file_id, db_row

    async def test_stream_bytes_yields_file_content(self, tmp_path):
        content = b"fake image bytes " * 100
        store, file_id, _ = await self._committed_file(tmp_path, content)

        stream = await store.stream_bytes(file_id=file_id, tenant_id=TENANT)
        chunks = []
        async for chunk in stream:
            chunks.append(chunk)

        assert b"".join(chunks) == content

    async def test_stream_bytes_raises_if_not_found(self, tmp_path):
        store, _ = make_store(tmp_path, fetchrow=None)
        with pytest.raises(FileNotFoundError):
            await store.stream_bytes(file_id=str(uuid.uuid4()), tenant_id=TENANT)

    async def test_stream_bytes_raises_if_deleted(self, tmp_path):
        file_id = str(uuid.uuid4())
        deleted_row = {
            "session_id": SESSION, "original_name": "x.jpg",
            "mime_type": "image/jpeg", "size_bytes": 10,
            "file_path": "x.jpg", "expires_at": EXPIRES,
            "deleted_at": datetime.now(timezone.utc),  # soft-deleted
        }
        store, _ = make_store(tmp_path, fetchrow=deleted_row)
        with pytest.raises(FileNotFoundError, match="expirado"):
            await store.stream_bytes(file_id=file_id, tenant_id=TENANT)

    async def test_stream_bytes_chunks_large_file(self, tmp_path):
        """File larger than 64 KB must be streamed in multiple chunks."""
        content = b"X" * (200 * 1024)  # 200 KB
        store, file_id, _ = await self._committed_file(tmp_path, content)

        stream = await store.stream_bytes(file_id=file_id, tenant_id=TENANT)
        chunks = []
        async for chunk in stream:
            chunks.append(chunk)

        assert len(chunks) > 1
        assert b"".join(chunks) == content


# ── S3AttachmentStore ─────────────────────────────────────────────────────────

from plughub_channel_gateway.attachment_store import S3AttachmentStore  # noqa: E402


def _make_s3_client_mock(content: bytes = b"") -> tuple[MagicMock, MagicMock]:
    """
    Returns (client_mock, client_cm_mock) where client_cm_mock is the async
    context manager returned by session.client("s3", ...).
    """
    client = AsyncMock()

    # put_object mock
    client.put_object = AsyncMock(return_value={"ResponseMetadata": {"HTTPStatusCode": 200}})

    # head_bucket mock (used in ensure_schema)
    client.head_bucket = AsyncMock(return_value={})

    # get_object mock — body yields content then b""
    body = AsyncMock()
    body.read = AsyncMock(side_effect=[content, b""])
    client.get_object = AsyncMock(return_value={"Body": body})

    client_cm = MagicMock()
    client_cm.__aenter__ = AsyncMock(return_value=client)
    client_cm.__aexit__  = AsyncMock(return_value=False)

    return client, client_cm


def make_s3_store(
    fetchrow=None,
    execute=None,
    s3_content: bytes = b"",
) -> tuple[S3AttachmentStore, MagicMock, MagicMock]:
    """Returns (store, db_conn, s3_client)."""
    pool, conn = make_pool(fetchrow_return=fetchrow, execute_return=execute)
    s3_client, s3_client_cm = _make_s3_client_mock(content=s3_content)

    store = S3AttachmentStore(
        bucket                = "test-bucket",
        db_pool               = pool,
        serving_base_url      = "http://host/webchat/v1/attachments",
        upload_base_url       = "http://host/webchat/v1/upload",
        endpoint_url          = "http://minio:9000",
        aws_access_key_id     = "test",
        aws_secret_access_key = "testpass",
    )

    # Patch the aioboto3 session.client call
    session_mock = MagicMock()
    session_mock.client = MagicMock(return_value=s3_client_cm)
    store._session = session_mock

    return store, conn, s3_client


class TestS3AttachmentStore:
    def _pending_row(self) -> dict:
        return {
            "session_id":    SESSION,
            "original_name": "photo.jpg",
            "mime_type":     "image/jpeg",
            "expires_at":    EXPIRES,
        }

    # ── reserve ───────────────────────────────────────────────────────────────

    async def test_reserve_returns_file_id_and_upload_url(self):
        store, conn, _ = make_s3_store()
        file_id, upload_url = await store.reserve(
            tenant_id  = TENANT,
            session_id = SESSION,
            file_name  = "photo.jpg",
            mime_type  = "image/jpeg",
            size_bytes = 1024,
            expires_at = EXPIRES,
        )
        assert file_id
        assert upload_url == f"http://host/webchat/v1/upload/{file_id}"

    async def test_reserve_inserts_pending_row(self):
        store, conn, _ = make_s3_store()
        await store.reserve(
            tenant_id  = TENANT,
            session_id = SESSION,
            file_name  = "photo.jpg",
            mime_type  = "image/jpeg",
            size_bytes = 1024,
            expires_at = EXPIRES,
        )
        sql = conn.execute.call_args.args[0]
        assert "INSERT INTO session_attachments" in sql

    # ── commit ────────────────────────────────────────────────────────────────

    async def test_commit_calls_put_object(self):
        file_id = str(uuid.uuid4())
        store, _, s3_client = make_s3_store(fetchrow=self._pending_row())
        await store.commit(file_id=file_id, tenant_id=TENANT, data=_JPEG_HDR)
        s3_client.put_object.assert_called_once()
        call_kwargs = s3_client.put_object.call_args.kwargs
        assert call_kwargs["Bucket"] == "test-bucket"
        assert call_kwargs["Body"] == _JPEG_HDR
        assert call_kwargs["ContentType"] == "image/jpeg"

    async def test_commit_object_key_is_date_sharded(self):
        file_id = str(uuid.uuid4())
        store, _, s3_client = make_s3_store(fetchrow=self._pending_row())
        meta = await store.commit(file_id=file_id, tenant_id=TENANT, data=_JPEG_HDR)
        now = datetime.now(timezone.utc)
        assert TENANT in meta.file_path
        assert str(now.year) in meta.file_path
        assert meta.file_path.endswith(".jpg")

    async def test_commit_returns_correct_meta(self):
        file_id = str(uuid.uuid4())
        store, _, _ = make_s3_store(fetchrow=self._pending_row())
        meta = await store.commit(file_id=file_id, tenant_id=TENANT, data=_JPEG_HDR)
        assert meta.file_id    == file_id
        assert meta.tenant_id  == TENANT
        assert meta.session_id == SESSION
        assert meta.size_bytes == len(_JPEG_HDR)
        assert meta.serving_url == f"http://host/webchat/v1/attachments/{file_id}"
        assert meta.deleted_at is None

    async def test_commit_raises_if_no_pending_slot(self):
        file_id = str(uuid.uuid4())
        store, _, _ = make_s3_store(fetchrow=None)
        with pytest.raises(FileNotFoundError):
            await store.commit(file_id=file_id, tenant_id=TENANT, data=_JPEG_HDR)

    async def test_commit_rejects_magic_mismatch(self):
        file_id = str(uuid.uuid4())
        store, _, _ = make_s3_store(fetchrow=self._pending_row())
        with pytest.raises(ValueError, match="image/jpeg"):
            await store.commit(file_id=file_id, tenant_id=TENANT, data=_PNG_HDR)

    # ── resolve ───────────────────────────────────────────────────────────────

    async def test_resolve_returns_meta(self):
        file_id = str(uuid.uuid4())
        db_row = {
            "session_id": SESSION, "original_name": "p.jpg",
            "mime_type": "image/jpeg", "size_bytes": 512,
            "file_path": f"{TENANT}/2026/04/15/{SESSION}/{file_id}.jpg",
            "expires_at": EXPIRES, "deleted_at": None,
        }
        store, _, _ = make_s3_store(fetchrow=db_row)
        meta = await store.resolve(file_id=file_id, tenant_id=TENANT)
        assert meta is not None
        assert meta.file_id == file_id
        assert meta.size_bytes == 512

    async def test_resolve_returns_none_for_unknown(self):
        store, _, _ = make_s3_store(fetchrow=None)
        assert await store.resolve(file_id=str(uuid.uuid4()), tenant_id=TENANT) is None

    # ── stream_bytes ──────────────────────────────────────────────────────────

    async def test_stream_bytes_calls_get_object(self):
        file_id = str(uuid.uuid4())
        content = _JPEG_HDR + b"\x00" * 100
        db_row = {
            "session_id": SESSION, "original_name": "p.jpg",
            "mime_type": "image/jpeg", "size_bytes": len(content),
            "file_path": f"key/{file_id}.jpg",
            "expires_at": EXPIRES, "deleted_at": None,
        }
        store, _, s3_client = make_s3_store(fetchrow=db_row, s3_content=content)
        stream = await store.stream_bytes(file_id=file_id, tenant_id=TENANT)
        chunks = []
        async for chunk in stream:
            chunks.append(chunk)
        assert b"".join(chunks) == content
        s3_client.get_object.assert_called_once_with(Bucket="test-bucket", Key=f"key/{file_id}.jpg")

    async def test_stream_bytes_raises_if_not_found(self):
        store, _, _ = make_s3_store(fetchrow=None)
        with pytest.raises(FileNotFoundError):
            await store.stream_bytes(file_id=str(uuid.uuid4()), tenant_id=TENANT)

    async def test_stream_bytes_raises_if_deleted(self):
        file_id = str(uuid.uuid4())
        db_row = {
            "session_id": SESSION, "original_name": "p.jpg",
            "mime_type": "image/jpeg", "size_bytes": 10,
            "file_path": "k.jpg", "expires_at": EXPIRES,
            "deleted_at": datetime.now(timezone.utc),
        }
        store, _, _ = make_s3_store(fetchrow=db_row)
        with pytest.raises(FileNotFoundError, match="expirado"):
            await store.stream_bytes(file_id=file_id, tenant_id=TENANT)

    # ── soft_expire ───────────────────────────────────────────────────────────

    async def test_soft_expire_updates_db(self):
        file_id = str(uuid.uuid4())
        store, conn, _ = make_s3_store()
        await store.soft_expire(file_id=file_id)
        sql = conn.execute.call_args.args[0]
        assert "deleted_at" in sql
        assert "UPDATE session_attachments" in sql
