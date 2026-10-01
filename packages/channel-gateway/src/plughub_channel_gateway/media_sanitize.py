"""
media_sanitize.py — a imagem que o cliente manda é RE-CODIFICADA antes de ser gravada (ATT-05).

Duas coisas saem de uma vez, pelo mesmo mecanismo:
  * metadado — EXIF com GPS, modelo do aparelho, data, miniatura embutida: dado pessoal que
    ninguém pediu e que o atendente, o avaliador e o replay passariam a ver;
  * carga escondida — um arquivo "poliglota" (JPEG válido que também é outra coisa) deixa de ser
    poliglota quando os pixels são decodificados e codificados de novo.

A orientação do EXIF é APLICADA aos pixels antes de sair (`exif_transpose`): sem isso, foto de
celular apareceria deitada depois que a tag de orientação some.

GIF animado mantém os quadros (`save_all`). Imagem que não decodifica, ou grande demais para
decodificar com segurança (bomba de descompressão), é RECUSADA — não se grava o original "porque
não deu para limpar".
"""
from __future__ import annotations

import io

from PIL import Image, ImageOps

# Teto de pixels decodificados, conferido no CABEÇALHO, antes de decodificar. O
# `Image.MAX_IMAGE_PIXELS` do Pillow só RECUSA no dobro do valor (abaixo disso, avisa), então ele
# fica de rede, não de regra. 50 Mpx cobre a câmera de celular com folga.
MAX_PIXELS = 50_000_000

_FORMATS = {
    "image/jpeg": "JPEG",
    "image/png":  "PNG",
    "image/webp": "WEBP",
    "image/gif":  "GIF",
}


def is_image(mime_type: str) -> bool:
    return mime_type in _FORMATS


def sanitize_image(data: bytes, mime_type: str) -> bytes:
    """Devolve a imagem re-codificada, sem metadado. Levanta ValueError com o motivo."""
    fmt = _FORMATS.get(mime_type)
    if fmt is None:
        raise ValueError(f"tipo de imagem sem re-codificação: {mime_type}")
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.width * img.height > MAX_PIXELS:
                raise ValueError(f"imagem grande demais para decodificar com segurança "
                                 f"({img.width}x{img.height} > {MAX_PIXELS} px)")
            img.load()
            if img.format != fmt:
                raise ValueError(f"o conteúdo é {img.format}, não {fmt}")
            out = io.BytesIO()
            if fmt == "GIF" and getattr(img, "is_animated", False):
                quadros = []
                for i in range(img.n_frames):
                    img.seek(i)
                    quadros.append(img.copy())
                quadros[0].save(out, format="GIF", save_all=True, append_images=quadros[1:],
                                loop=img.info.get("loop", 0), duration=img.info.get("duration"),
                                disposal=2)
            else:
                limpa = ImageOps.exif_transpose(img)
                if fmt == "JPEG" and limpa.mode not in ("RGB", "L"):
                    limpa = limpa.convert("RGB")
                params = {"quality": 90} if fmt in ("JPEG", "WEBP") else {}
                limpa.save(out, format=fmt, **params)
            return out.getvalue()
    except Image.DecompressionBombError as exc:
        raise ValueError(f"imagem grande demais para decodificar com segurança ({exc})") from exc
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 — imagem que não decodifica não é gravada
        raise ValueError(f"imagem não decodifica ({type(exc).__name__}: {exc})") from exc
