"""
_att01_attachment_door.py — corpo do `probe_att01_attachment_door.sh` (ATT-01). Roda DENTRO do
container do channel-gateway, com o store REAL que o serviço usa (mesma fábrica, mesmo backend,
mesmo banco) e a porta pública REAL em localhost:8010.

PROPOSIÇÃO: nenhum arquivo cujo tipo o remetente escolheu é gravado, e a porta pública não
serve nada como página.

  W0 CONTROLE: uma imagem de verdade é gravada (o portão deixa passar)
  W1 slot declarado `text/html` → o commit RECUSA
  W2 slot `image/jpeg` com bytes de HTML → o commit RECUSA
  W3 CONTROLE: um PDF de verdade é gravado
  S1 a imagem é servida inline, com `nosniff` e CSP `sandbox`
  S2 o PDF é servido como DOWNLOAD (`attachment`), com `nosniff`
  S3 nome hostil (aspas, `;`, CRLF) não reescreve o cabeçalho nem injeta outro
  C1 CENSO: nenhuma linha SERVÍVEL (committed, não expirada) tem tipo fora da allowlist da
     sua classe. Expirada responde 410 — e a própria bateria de mutação deixa uma (M1 grava o
     HTML que a regra desligada não barra), que o probe expira na limpeza.

A amostra é criada pelo próprio probe (não depende de tráfego) e é apagada ao final: as linhas
gravadas vão a `soft_expire` (o expurgo leva o blob), as recusadas saem do banco.

Saída: 0 verde · 1 alguma falha · 2 não mediu.
"""
import asyncio
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import asyncpg

from plughub_channel_gateway.attachment_store import CLASS_MIME_LIMITS
from plughub_channel_gateway.config import get_settings
from plughub_channel_gateway.main import _create_attachment_store

HTTP = "http://localhost:8010/webchat/v1/attachments"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
HTML = b"<html><body><script>alert(document.domain)</script></body></html>"
HOSTIL = 'nota".html; filename=evil.html\r\nX-Att01-Injected: 1.pdf'

falhas: list[str] = []


def ok(ramo: str, cond: bool, detalhe: str) -> None:
    print(f"  {'✓' if cond else '✗'} {ramo} {detalhe}")
    if not cond:
        falhas.append(ramo)


def get(file_id: str):
    try:
        with urllib.request.urlopen(f"{HTTP}/{file_id}", timeout=10) as r:
            return r.status, r.headers, r.read()  # HTTPMessage: busca sem caixa
    except urllib.error.HTTPError as e:
        return e.code, e.headers, b""


async def main() -> int:
    settings = get_settings()
    tenant = settings.tenant_id
    sid = f"att01-probe-{int(time.time())}"
    try:
        pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=2)
    except Exception as exc:  # noqa: BLE001
        print(f"  INCONCL banco inalcançável: {exc}")
        return 2
    store = _create_attachment_store(settings, pool)
    print(f"  backend: {type(store).__name__} · tenant {tenant} · sessão {sid}")
    expira = datetime.now(timezone.utc) + timedelta(hours=1)
    gravados: list[str] = []

    async def gravar(nome, mime, dados):
        fid, _ = await store.reserve(tenant_id=tenant, session_id=sid, file_name=nome,
                                     mime_type=mime, size_bytes=len(dados), expires_at=expira)
        try:
            await store.commit(file_id=fid, tenant_id=tenant, data=dados)
        except ValueError as exc:
            return fid, str(exc)
        gravados.append(fid)
        return fid, None

    try:
        # ── escrita ──
        img, err = await gravar("foto.jpg", "image/jpeg", JPEG)
        ok("W0", err is None, f"imagem gravada (controle){'' if err is None else ': ' + err}")
        if err is not None:
            print("  INCONCL o controle positivo não gravou — as recusas abaixo não provariam nada")
            return 2
        _, err = await gravar("pagina.html", "text/html", HTML)
        ok("W1", err is not None and "text/html" in err, f"text/html recusado ({err})")
        _, err = await gravar("falsa.jpg", "image/jpeg", HTML)
        ok("W2", err is not None, f"JPEG com bytes de HTML recusado ({err})")
        pdf, err = await gravar(HOSTIL, "application/pdf", PDF)
        ok("W3", err is None, f"PDF gravado (controle){'' if err is None else ': ' + err}")

        # ── porta ──
        st, h, corpo = get(img)
        ok("S1", st == 200 and h.get("X-Content-Type-Options") == "nosniff"
           and "sandbox" in (h.get("Content-Security-Policy") or "")
           and (h.get("Content-Disposition") or "").startswith("inline;") and corpo == JPEG,
           f"imagem: {st} · nosniff={h.get('X-Content-Type-Options')} · "
           f"csp={h.get('Content-Security-Policy')!r} · cd={h.get('Content-Disposition')!r}")
        if pdf and pdf in gravados:
            st, h, corpo = get(pdf)
            cd = h.get("Content-Disposition") or ""
            ok("S2", st == 200 and cd.startswith("attachment;")
               and h.get("X-Content-Type-Options") == "nosniff" and corpo == PDF,
               f"pdf: {st} · cd={cd!r}")
            ok("S3", h.get("X-Att01-Injected") is None and "\r" not in cd and "\n" not in cd
               and cd.count('"') == 2,
               f"nome hostil contido · cabeçalho injetado: {h.get('X-Att01-Injected') is not None}")

        # ── censo ──
        async with pool.acquire() as conn:
            linhas = await conn.fetch(
                "SELECT artifact_class, mime_type, count(*) AS n FROM session_attachments "
                "WHERE status = 'committed' AND deleted_at IS NULL GROUP BY 1, 2")
        fora = [(r["artifact_class"], r["mime_type"], r["n"]) for r in linhas
                if r["mime_type"] not in CLASS_MIME_LIMITS.get(r["artifact_class"], {})]
        total = sum(r["n"] for r in linhas)
        ok("C1", not fora, f"{total} servíveis · fora da allowlist da classe: {fora or 'nenhuma'}")
    finally:
        for fid in gravados:
            await store.soft_expire(file_id=fid)
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM session_attachments WHERE tenant_id = $1 AND session_id = $2 "
                "AND status = 'pending'", tenant, sid)
        await pool.close()
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
