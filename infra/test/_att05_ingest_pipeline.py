"""
_att05_ingest_pipeline.py — corpo do `probe_att05_ingest_pipeline.sh` (ATT-05). Roda DENTRO do
container do channel-gateway, com o store REAL (mesma fábrica, mesmo banco), o clamd REAL do
compose e as portas REAIS em localhost:8010.

PROPOSIÇÃO: nenhum anexo de contato é gravado sem a esteira (imagem re-codificada, sha256,
antivírus) e nenhum é SERVIDO sem o antivírus ter dito `clean`.

  A0 CONTROLE: o gateway tem antivírus configurado e o clamd responde `clean` a um PDF limpo
  W0 CONTROLE: PDF limpo gravado → `clean`, sha256 = hash do que está no store, porta 200
  V1 PDF com EICAR num stream comprimido (tipo da allowlist; o clamd o extrai) → commit RECUSA,
     linha `rejected`/`infected`, sem arquivo; o link assinado não abre (404)
  X1 JPEG com EXIF (marca do aparelho) → o que está no store e o que a porta serve não têm o EXIF
  X2 PNG com carga anexada depois do fim da imagem (poliglota) → a carga não chega ao store
  Q1 antivírus inalcançável → gravado em QUARENTENA → porta pública 423, porta interna 423
  Q2 a nova varredura (o mesmo `rescan_once` da task de boot) com o clamd de pé → `clean` → 200
  C1 CENSO: nenhum anexo de contato SERVÍVEL está `infected`; o não verificado (NULL) é contado

⚠️ Q1 pode, raramente, ver 200 se a task de boot do serviço varrer a linha entre o commit e o GET
(janela de milissegundos, passada a cada 60 s): o vermelho diz o desfecho — repita antes de culpar.

A amostra é do próprio probe e sai ao final (gravados → `soft_expire`, o resto sai do banco).
Saída: 0 verde · 1 alguma falha · 2 não mediu.
"""
import asyncio
import hashlib
import io
import sys
import time
import urllib.error
import urllib.request
import zlib
from datetime import datetime, timedelta, timezone

import asyncpg
from PIL import Image

from plughub_channel_gateway import attachment_store as st
from plughub_channel_gateway.antivirus import scan_bytes
from plughub_channel_gateway.attachment_rescan import rescan_once
from plughub_channel_gateway.config import get_settings
from plughub_channel_gateway.main import _create_attachment_store

PUB = "http://localhost:8010/webchat/v1/attachments"
INT = "http://localhost:8010/v1/attachments"
EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
CARGA = b"ATT05-CARGA-ESCONDIDA-" * 4
MARCA = "ATT05MarcaDoAparelho"

falhas: list[str] = []


def ok(ramo: str, cond: bool, detalhe: str) -> None:
    print(f"  {'✓' if cond else '✗'} {ramo} {detalhe}")
    if not cond:
        falhas.append(ramo)


def get(url: str, headers: dict | None = None):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


def pdf(stream: bytes = b"ola", flate: bool = False) -> bytes:
    corpo = zlib.compress(stream) if flate else stream
    filtro = b"/Filter /FlateDecode " if flate else b""
    return (b"%PDF-1.4\n1 0 obj\n<< /Length " + str(len(corpo)).encode() + b" " + filtro
            + b">>\nstream\n" + corpo
            + b"\nendstream\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n")


def imagem(fmt: str, exif=None) -> bytes:
    buf = io.BytesIO()
    kw = {"exif": exif} if exif is not None else {}
    Image.new("RGB", (16, 8), (10, 120, 200)).save(buf, format=fmt, **kw)
    return buf.getvalue()


async def main() -> int:
    s = get_settings()
    tenant = s.tenant_id
    sid = f"att05-probe-{int(time.time())}"
    if not s.clamav_host:
        print("  ✗ A0 PLUGHUB_CLAMAV_HOST vazio no gateway — a esteira põe tudo em quarentena")
        return 1
    st.configure_scanner(s.clamav_host, s.clamav_port)   # o processo do probe, como o boot faz
    r = await scan_bytes(pdf(), host=s.clamav_host, port=s.clamav_port)
    if r.verdict != "clean":
        print(f"  INCONCL A0 o clamd não respondeu `clean` a um PDF limpo ({r}) — ainda carregando a base?")
        return 2
    ok("A0", True, f"antivírus {s.clamav_host}:{s.clamav_port} responde (controle)")

    try:
        pool = await asyncpg.create_pool(s.database_url, min_size=1, max_size=2)
    except Exception as exc:  # noqa: BLE001
        print(f"  INCONCL banco inalcançável: {exc}")
        return 2
    store = _create_attachment_store(s, pool)
    expira = datetime.now(timezone.utc) + timedelta(hours=1)
    gravados: list[str] = []

    async def gravar(nome, mime, dados):
        fid, _ = await store.reserve(tenant_id=tenant, session_id=sid, file_name=nome,
                                     mime_type=mime, size_bytes=len(dados), expires_at=expira)
        try:
            await store.commit(file_id=fid, tenant_id=tenant, data=dados)
        except ValueError as exc:
            return fid, exc
        gravados.append(fid)
        return fid, None

    async def linha(fid):
        async with pool.acquire() as c:
            return await c.fetchrow("SELECT status, scan_status, sha256, file_path, attrs "
                                    "FROM session_attachments WHERE file_id = $1::uuid", fid)

    async def no_store(fid) -> bytes:
        return b"".join([c async for c in await store.stream_bytes(file_id=fid, tenant_id=tenant)])

    def assinada(fid):
        return st.sign_attachment_url(PUB, fid, sid, secret=s.jwt_secret)

    servico = {"x-service-token": s.channel_gateway_service_token}

    try:
        # ── W0 controle ──
        limpo, err = await gravar("limpo.pdf", "application/pdf", pdf())
        if err is not None:
            print(f"  INCONCL W0 o controle não gravou ({err}) — as recusas não provariam nada")
            return 2
        lin = await linha(limpo)
        dados = await no_store(limpo)
        cod, corpo = get(assinada(limpo))
        ok("W0", lin["scan_status"] == "clean" and lin["sha256"] == hashlib.sha256(dados).hexdigest()
           and cod == 200 and corpo == dados,
           f"PDF limpo: scan={lin['scan_status']} · sha256 confere={lin['sha256'] == hashlib.sha256(dados).hexdigest()} · porta {cod}")

        # ── V1 infectado ──
        inf, err = await gravar("fatura.pdf", "application/pdf", pdf(EICAR, flate=True))
        lin = await linha(inf)
        cod, _ = get(assinada(inf))
        ok("V1", isinstance(err, st.AttachmentInfected) and lin["status"] == "rejected"
           and lin["scan_status"] == "infected" and lin["file_path"] is None and cod == 404,
           f"PDF com EICAR: {type(err).__name__ if err else 'GRAVADO'} · linha {lin['status']}/"
           f"{lin['scan_status']} · arquivo={lin['file_path']} · porta {cod}")

        # ── X1 EXIF ──
        ex = Image.Exif()
        ex[0x010F] = MARCA
        ex[0x0112] = 6
        bruto = imagem("JPEG", ex)
        assert MARCA.encode() in bruto, "o controle não tinha EXIF"
        foto, err = await gravar("foto.jpg", "image/jpeg", bruto)
        if err is None:
            dados = await no_store(foto)
            cod, corpo = get(assinada(foto))
            tam = Image.open(io.BytesIO(dados)).size
            ok("X1", MARCA.encode() not in dados and MARCA.encode() not in corpo and cod == 200
               and tam == (8, 16),
               f"EXIF no store={MARCA.encode() in dados} · na porta={MARCA.encode() in corpo} · "
               f"orientação aplicada={tam == (8, 16)} · porta {cod}")
        else:
            ok("X1", False, f"a foto com EXIF foi recusada: {err}")

        # ── X2 poliglota ──
        png, err = await gravar("img.png", "image/png", imagem("PNG") + CARGA)
        ok("X2", err is None and CARGA not in await no_store(png),
           f"carga depois da imagem chega ao store: {err or (CARGA in await no_store(png))}")

        # ── Q1/Q2 quarentena ──
        st.configure_scanner("antivirus-que-nao-existe.invalid", 3310)
        qua, err = await gravar("q.pdf", "application/pdf", pdf(b"quarentena"))
        st.configure_scanner(s.clamav_host, s.clamav_port)
        lin = await linha(qua)
        cod_pub, _ = get(assinada(qua))
        cod_int, _ = get(f"{INT}/{qua}/content?tenant_id={tenant}", servico)
        ok("Q1", err is None and lin["scan_status"] == "quarantined" and cod_pub == 423 and cod_int == 423,
           f"clamd fora: scan={lin['scan_status']} · motivo={(lin['attrs'] or '')[:60]!r} · "
           f"pública {cod_pub} · interna {cod_int}")
        await rescan_once(store, pool)
        lin = await linha(qua)
        cod_pub, _ = get(assinada(qua))
        ok("Q2", lin["scan_status"] == "clean" and cod_pub == 200,
           f"nova varredura: scan={lin['scan_status']} · pública {cod_pub}")

        # ── censo ──
        async with pool.acquire() as c:
            rows = await c.fetch(
                "SELECT coalesce(scan_status, 'NULL') AS s, count(*) AS n FROM session_attachments "
                "WHERE status = 'committed' AND deleted_at IS NULL AND artifact_class = 'webchat_attachment' "
                "GROUP BY 1")
        censo = {r["s"]: r["n"] for r in rows}
        ok("C1", censo.get("infected", 0) == 0,
           f"anexos de contato servíveis por estado: {censo} (NULL/quarantined = 423 até a varredura)")
    finally:
        for fid in gravados:
            await store.soft_expire(file_id=fid)
        async with pool.acquire() as c:
            await c.execute("DELETE FROM session_attachments WHERE tenant_id = $1 AND session_id = $2 "
                            "AND status IN ('pending', 'rejected')", tenant, sid)
        await pool.close()
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
