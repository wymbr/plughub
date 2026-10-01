"""
antivirus.py — a pergunta "este arquivo tem vírus?" feita ao clamd (ATT-05, 2026-10-01).

Protocolo INSTREAM do clamd, sem dependência nova: `zINSTREAM\\0`, blocos `<len:4 bytes BE><dados>`,
bloco de tamanho 0 para fechar, e uma resposta só:

    stream: OK                       → clean
    stream: <assinatura> FOUND       → infected (a assinatura é o motivo)
    ... ERROR                        → error (inclusive "INSTREAM size limit exceeded")

O veredicto tem TRÊS valores, e o terceiro nunca vira "limpo": antivírus fora do ar, não
configurado ou com erro devolve `error` com o motivo, e quem chama deixa o anexo em QUARENTENA.
Liberar o arquivo porque não deu para perguntar seria a degradação silenciosa que a Postura de
Engenharia proíbe.
"""
from __future__ import annotations

import asyncio
import logging
import struct
from dataclasses import dataclass

logger = logging.getLogger("plughub.channel-gateway.antivirus")

CHUNK = 1024 * 1024
TIMEOUT_S = 120.0


@dataclass(frozen=True)
class ScanResult:
    verdict: str          # "clean" | "infected" | "error"
    reason:  str = ""     # assinatura achada, ou o motivo de não ter dado para perguntar


async def scan_bytes(data: bytes, *, host: str, port: int = 3310,
                     timeout_s: float = TIMEOUT_S) -> ScanResult:
    """Pergunta ao clamd. Nunca levanta: toda falha vira `error` com o motivo."""
    if not host:
        return ScanResult("error", "antivirus_not_configured (PLUGHUB_CLAMAV_HOST vazio)")
    try:
        return await asyncio.wait_for(_instream(data, host, port), timeout=timeout_s)
    except asyncio.TimeoutError:
        return ScanResult("error", f"clamd_timeout ({timeout_s:.0f}s)")
    except OSError as exc:
        return ScanResult("error", f"clamd_unreachable ({host}:{port}: {exc})")
    except Exception as exc:  # noqa: BLE001 — qualquer outra falha também é "não perguntei"
        return ScanResult("error", f"clamd_failed ({type(exc).__name__}: {exc})")


async def _instream(data: bytes, host: str, port: int) -> ScanResult:
    reader, writer = await asyncio.open_connection(host, port)
    try:
        writer.write(b"zINSTREAM\0")
        for i in range(0, len(data), CHUNK):
            bloco = data[i:i + CHUNK]
            writer.write(struct.pack(">I", len(bloco)) + bloco)
            await writer.drain()
        writer.write(struct.pack(">I", 0))
        await writer.drain()
        resposta = (await reader.read(4096)).rstrip(b"\0").decode(errors="replace").strip()
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception as exc:  # noqa: BLE001 — fechar a conexão não muda o veredicto
            logger.debug("clamd: conexão não fechou limpa (%s) — veredicto mantido", exc)
    return parse_reply(resposta)


def parse_reply(resposta: str) -> ScanResult:
    corpo = resposta.split(":", 1)[1].strip() if ":" in resposta else resposta
    if corpo == "OK":
        return ScanResult("clean")
    if corpo.endswith("FOUND"):
        return ScanResult("infected", corpo[: -len("FOUND")].strip())
    return ScanResult("error", f"clamd_reply ({resposta[:200]})")
