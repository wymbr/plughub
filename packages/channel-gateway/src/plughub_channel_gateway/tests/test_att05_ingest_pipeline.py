"""
test_att05_ingest_pipeline.py — ATT-05 (2026-10-01)

PROPOSIÇÃO: nenhum anexo de contato é gravado sem passar pela esteira, e nenhum é SERVIDO sem
o antivírus ter dito `clean`. Antes da ATT-05: a imagem era gravada com o EXIF do aparelho (GPS
incluso), não havia hash nem antivírus, e a porta servia todo arquivo commitado.

Cada recusa tem o controle positivo ao lado — uma esteira que recusa tudo passaria nos negativos,
e uma que libera tudo passaria nos positivos. O terceiro valor do antivírus (não deu para
perguntar) é julgado à parte: ele nunca pode virar `clean`.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import struct
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image

from plughub_channel_gateway import attachment_store as st
from plughub_channel_gateway import media_sanitize
from plughub_channel_gateway.antivirus import ScanResult, parse_reply, scan_bytes
from plughub_channel_gateway.attachment_rescan import rescan_once
from plughub_channel_gateway.attachment_store import (
    AttachmentInfected,
    AttachmentMeta,
    prepare_content,
    serve_refusal,
)
from plughub_channel_gateway.media_sanitize import sanitize_image
from plughub_channel_gateway.tests._media import REAL_JPEG, real_image
from plughub_channel_gateway.tests.test_attachment_store import make_store

EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
OGG = b"OggS" + b"\x00" * 16
PDF = b"%PDF-1.4\n"
EXPIRES = datetime.now(timezone.utc) + timedelta(days=30)


def _antivirus(monkeypatch, verdict: str, reason: str = ""):
    chamadas: list[bytes] = []

    async def _scan(data, **kw):
        chamadas.append(data)
        return ScanResult(verdict, reason)
    monkeypatch.setattr(st, "scan_bytes", _scan)
    return chamadas


# ── o protocolo do clamd ──────────────────────────────────────────────────────

class TestParseReply:
    def test_ok_e_limpo(self):
        assert parse_reply("stream: OK") == ScanResult("clean")

    def test_found_traz_a_assinatura(self):
        assert parse_reply("stream: Eicar-Test-Signature FOUND") == ScanResult("infected", "Eicar-Test-Signature")

    def test_erro_nunca_vira_limpo(self):
        r = parse_reply("INSTREAM size limit exceeded. ERROR")
        assert r.verdict == "error" and "size limit" in r.reason


@pytest.fixture
async def clamd_falso():
    """Um clamd de verdade no protocolo (INSTREAM com blocos), de mentira no julgamento."""
    recebido: list[bytes] = []

    async def _h(reader, writer):
        assert await reader.readexactly(10) == b"zINSTREAM\0"
        dados = b""
        while True:
            n = struct.unpack(">I", await reader.readexactly(4))[0]
            if n == 0:
                break
            dados += await reader.readexactly(n)
        recebido.append(dados)
        writer.write(b"stream: Eicar-Test-Signature FOUND\0" if b"EICAR" in dados else b"stream: OK\0")
        await writer.drain()
        writer.close()

    srv = await asyncio.start_server(_h, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    yield SimpleNamespace(port=port, recebido=recebido)
    srv.close()
    await srv.wait_closed()


class TestScanBytes:
    async def test_limpo_controle_positivo(self, clamd_falso):
        r = await scan_bytes(b"ola" * 1000, host="127.0.0.1", port=clamd_falso.port)
        assert r == ScanResult("clean") and clamd_falso.recebido == [b"ola" * 1000]

    async def test_eicar_e_infectado(self, clamd_falso):
        r = await scan_bytes(EICAR, host="127.0.0.1", port=clamd_falso.port)
        assert r.verdict == "infected" and r.reason == "Eicar-Test-Signature"

    async def test_arquivo_maior_que_um_bloco_chega_inteiro(self, clamd_falso):
        grande = b"a" * (2 * 1024 * 1024 + 7)
        await scan_bytes(grande, host="127.0.0.1", port=clamd_falso.port)
        assert clamd_falso.recebido == [grande]

    async def test_nao_configurado_e_erro_nomeado(self):
        r = await scan_bytes(b"x", host="")
        assert r.verdict == "error" and "not_configured" in r.reason

    async def test_fora_do_ar_e_erro_nomeado_nunca_limpo(self):
        srv = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        srv.close()
        await srv.wait_closed()
        r = await scan_bytes(b"x", host="127.0.0.1", port=port)
        assert r.verdict == "error" and "unreachable" in r.reason


# ── a re-codificação da imagem ────────────────────────────────────────────────

def _exif(**tags) -> Image.Exif:
    ex = Image.Exif()
    for k, v in tags.items():
        ex[int(k[1:], 16)] = v
    return ex


class TestSanitizeImage:
    def test_exif_sai_inteiro(self):
        com = real_image("JPEG", exif=_exif(x010F="CanonMarcaUnica", x0110="ModeloUnico"))
        assert b"CanonMarcaUnica" in com, "o controle não tinha EXIF — o teste não mediria nada"
        limpa = sanitize_image(com, "image/jpeg")
        assert b"CanonMarcaUnica" not in limpa and b"ModeloUnico" not in limpa
        assert len(Image.open(io.BytesIO(limpa)).getexif()) == 0

    def test_orientacao_e_aplicada_aos_pixels(self):
        deitada = real_image("JPEG", size=(8, 4), exif=_exif(x0112=6))
        limpa = sanitize_image(deitada, "image/jpeg")
        assert Image.open(io.BytesIO(limpa)).size == (4, 8)

    def test_png_e_webp_saem_no_proprio_formato(self):
        for mime, fmt in (("image/png", "PNG"), ("image/webp", "WEBP")):
            out = sanitize_image(real_image(fmt), mime)
            assert Image.open(io.BytesIO(out)).format == fmt

    def test_gif_animado_mantem_os_quadros(self):
        quadros = [Image.new("RGB", (4, 4), c) for c in ((255, 0, 0), (0, 255, 0), (0, 0, 255))]
        buf = io.BytesIO()
        quadros[0].save(buf, format="GIF", save_all=True, append_images=quadros[1:], duration=50)
        assert Image.open(io.BytesIO(buf.getvalue())).n_frames == 3, "o controle não era animado"
        out = sanitize_image(buf.getvalue(), "image/gif")
        assert Image.open(io.BytesIO(out)).n_frames == 3

    def test_conteudo_de_outro_formato_e_recusado(self):
        with pytest.raises(ValueError, match="PNG"):
            sanitize_image(real_image("PNG"), "image/jpeg")

    def test_imagem_que_nao_decodifica_e_recusada(self):
        with pytest.raises(ValueError, match="não decodifica"):
            sanitize_image(b"\xff\xd8\xff\xe0" + b"\x00" * 64, "image/jpeg")

    def test_bomba_e_recusada_pelo_cabecalho(self, monkeypatch):
        monkeypatch.setattr(media_sanitize, "MAX_PIXELS", 63)
        with pytest.raises(ValueError, match="grande demais"):
            sanitize_image(real_image("PNG", size=(8, 8)), "image/png")
        monkeypatch.setattr(media_sanitize, "MAX_PIXELS", 64)
        assert sanitize_image(real_image("PNG", size=(8, 8)), "image/png")   # no limite, passa


# ── a esteira ─────────────────────────────────────────────────────────────────

class TestPrepareContent:
    async def test_limpo_grava_a_imagem_recodificada_com_o_hash_dela(self, monkeypatch):
        enviados = _antivirus(monkeypatch, "clean")
        com = real_image("JPEG", exif=_exif(x010F="CanonMarcaUnica"))
        p = await prepare_content("webchat_attachment", "image/jpeg", com)
        assert p.scan_status == "clean"
        assert b"CanonMarcaUnica" not in p.data
        assert p.sha256 == hashlib.sha256(p.data).hexdigest()
        assert enviados == [p.data], "o antivírus tem de ver o que será GRAVADO"

    async def test_pdf_passa_sem_recodificar(self, monkeypatch):
        _antivirus(monkeypatch, "clean")
        p = await prepare_content("webchat_attachment", "application/pdf", PDF)
        assert p.data == PDF and p.scan_status == "clean"

    async def test_infectado_levanta(self, monkeypatch):
        _antivirus(monkeypatch, "infected", "Eicar-Test-Signature")
        with pytest.raises(AttachmentInfected, match="Eicar"):
            await prepare_content("webchat_attachment", "application/pdf", PDF)

    async def test_antivirus_fora_do_ar_e_quarentena_nunca_limpo(self, monkeypatch):
        _antivirus(monkeypatch, "error", "clamd_unreachable (x)")
        p = await prepare_content("webchat_attachment", "application/pdf", PDF)
        assert p.scan_status == "quarantined" and "unreachable" in p.scan_reason

    async def test_gravacao_e_isenta_dita_e_nao_pergunta(self, monkeypatch):
        chamadas = _antivirus(monkeypatch, "clean")
        p = await prepare_content("call_recording", "audio/ogg", OGG)
        assert p.scan_status == "exempt" and p.scan_reason and chamadas == []

    async def test_allowlist_continua_antes_de_tudo(self, monkeypatch):
        chamadas = _antivirus(monkeypatch, "clean")
        with pytest.raises(ValueError, match="text/html"):
            await prepare_content("webchat_attachment", "text/html", b"<html></html>")
        assert chamadas == []


class TestCommit:
    def _row(self, mime="application/pdf"):
        return {"session_id": "s", "original_name": "x", "mime_type": mime,
                "expires_at": EXPIRES, "artifact_class": "webchat_attachment"}

    async def test_infectado_nao_vai_a_disco_e_a_linha_fica_rejected(self, tmp_path, monkeypatch):
        _antivirus(monkeypatch, "infected", "Eicar-Test-Signature")
        store, conn = make_store(tmp_path, fetchrow=self._row())
        with pytest.raises(AttachmentInfected):
            await store.commit(file_id=str(uuid.uuid4()), tenant_id="t", data=PDF)
        assert not any(tmp_path.rglob("*.*"))
        sql, *args = conn.execute.call_args.args
        assert "status = 'rejected'" in sql and "Eicar" in args[1]

    async def test_quarentena_vai_a_disco_marcada(self, tmp_path, monkeypatch):
        _antivirus(monkeypatch, "error", "clamd_timeout (120s)")
        store, conn = make_store(tmp_path, fetchrow=self._row())
        await store.commit(file_id=str(uuid.uuid4()), tenant_id="t", data=PDF)
        args = conn.execute.call_args.args
        assert "quarantined" in args and "clamd_timeout (120s)" in args

    async def test_limpo_grava_sha256_controle_positivo(self, tmp_path, monkeypatch):
        _antivirus(monkeypatch, "clean")
        store, conn = make_store(tmp_path, fetchrow=self._row("image/jpeg"))
        meta = await store.commit(file_id=str(uuid.uuid4()), tenant_id="t", data=REAL_JPEG)
        gravado = (tmp_path / meta.file_path).read_bytes()
        args = conn.execute.call_args.args
        assert "clean" in args and hashlib.sha256(gravado).hexdigest() in args


# ── a regra de servir ─────────────────────────────────────────────────────────

def _meta(scan_status, klass="webchat_attachment"):
    return AttachmentMeta(artifact_class=klass, scan_status=scan_status)


class TestServeRefusal:
    def test_limpo_sai(self):
        assert serve_refusal(_meta("clean")) is None

    @pytest.mark.parametrize("status", [None, "", "quarantined"])
    def test_nao_verificado_espera_423(self, status):
        assert serve_refusal(_meta(status)) == (423, "attachment_pending_scan")

    def test_infectado_nao_existe(self):
        assert serve_refusal(_meta("infected"))[0] == 404

    def test_gravacao_isenta_sai(self):
        assert serve_refusal(_meta("exempt", "call_recording")) is None


async def test_porta_publica_recusa_quarentena(monkeypatch):
    from plughub_channel_gateway import main as _main  # noqa: F401 — main antes: o router o importa
    from plughub_channel_gateway import upload_router as up
    from fastapi import HTTPException
    from urllib.parse import parse_qs, urlparse
    from plughub_channel_gateway.attachment_store import sign_attachment_url

    meta = AttachmentMeta(file_id="f", tenant_id="t", session_id="s", original_name="x.pdf",
                          mime_type="application/pdf", size_bytes=9, file_path="p", serving_url="",
                          expires_at=EXPIRES, deleted_at=None, artifact_class="webchat_attachment",
                          attrs={}, scan_status="quarantined", sha256=None)
    store = MagicMock()
    store.resolve = AsyncMock(return_value=meta)
    monkeypatch.setattr(up._main_module, "_attachment_store", store, raising=False)
    monkeypatch.setattr(up._main_module, "get_settings",
                        lambda: SimpleNamespace(tenant_id="t", jwt_secret="k" * 32,
                                                webchat_serving_base_url="http://h"))
    q = parse_qs(urlparse(sign_attachment_url("http://h", "f", "s", secret="k" * 32)).query)
    with pytest.raises(HTTPException) as e:
        await up.serve_attachment("f", exp=q["exp"][0], sig=q["sig"][0])
    assert e.value.status_code == 423
    store.stream_bytes.assert_not_called()


# ── a nova varredura ──────────────────────────────────────────────────────────

class _Db:
    def __init__(self, rows):
        self.rows, self.executados = rows, []

    @asynccontextmanager
    async def acquire(self):
        conn = SimpleNamespace(fetch=AsyncMock(return_value=self.rows), execute=AsyncMock())
        yield conn
        self.executados.extend(c.args for c in conn.execute.call_args_list)


def _store_com(dados: bytes):
    store = MagicMock()

    async def _stream(**kw):
        async def _g():
            yield dados
        return _g()
    store.stream_bytes = _stream
    return store


class TestRescan:
    def _rows(self):
        return [{"file_id": uuid.uuid4(), "tenant_id": "t"}]

    async def test_limpo_libera(self, monkeypatch):
        _antivirus(monkeypatch, "clean")
        db = _Db(self._rows())
        conta = await rescan_once(_store_com(PDF), db)
        assert conta == {"clean": 1, "infected": 0, "quarantined": 0}
        sql, _fid, digest = db.executados[0]
        assert "scan_status = 'clean'" in sql and digest == hashlib.sha256(PDF).hexdigest()

    async def test_infectado_expira_ja(self, monkeypatch):
        _antivirus(monkeypatch, "infected", "Eicar-Test-Signature")
        db = _Db(self._rows())
        await rescan_once(_store_com(EICAR), db)
        sql = db.executados[0][0]
        assert "scan_status = 'infected'" in sql and "deleted_at = NOW()" in sql

    async def test_fora_do_ar_segue_em_quarentena(self, monkeypatch):
        _antivirus(monkeypatch, "error", "clamd_unreachable (x)")
        db = _Db(self._rows())
        conta = await rescan_once(_store_com(PDF), db)
        assert conta["quarantined"] == 1
        assert "scan_status = 'quarantined'" in db.executados[0][0]

    async def test_a_busca_so_pega_classe_varrida_e_nao_verificada(self, monkeypatch):
        db = _Db([])
        capturado = {}

        @asynccontextmanager
        async def _acq():
            conn = SimpleNamespace(execute=AsyncMock())

            async def _fetch(sql, classes, lote):
                capturado.update(sql=sql, classes=classes)
                return []
            conn.fetch = _fetch
            yield conn
        db.acquire = _acq
        await rescan_once(_store_com(PDF), db)
        assert capturado["classes"] == ["webchat_attachment"]
        assert "scan_status IS NULL OR scan_status = 'quarantined'" in capturado["sql"]
