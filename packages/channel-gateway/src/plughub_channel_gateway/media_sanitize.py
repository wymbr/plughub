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

from PIL import Image, ImageFilter, ImageOps

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


# ATT-06 — a prévia de quem NÃO atende o contato (supervisor, avaliador, replay). Borrar só com
# GaussianBlur é parcialmente reversível (deconvolução); a imagem é primeiro REDUZIDA a poucos
# pixels — o detalhe deixa de existir — e só então ampliada e suavizada para ficar legível como
# forma e cor. O original nunca sai por este caminho.
PREVIEW_DETAIL_PX = 24     # lado maior depois da redução: o que sobra de informação
PREVIEW_SIZE_PX = 320      # lado maior da prévia entregue


def blurred_preview(data: bytes, mime_type: str) -> bytes:
    """JPEG da prévia borrada. Levanta ValueError se a imagem não decodifica."""
    if not is_image(mime_type):
        raise ValueError(f"sem prévia borrada para {mime_type}")
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.width * img.height > MAX_PIXELS:
                raise ValueError("imagem grande demais para decodificar com segurança")
            img.seek(0)
            base = ImageOps.exif_transpose(img).convert("RGB")
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"imagem não decodifica ({type(exc).__name__}: {exc})") from exc
    w, h = base.size
    escala = PREVIEW_DETAIL_PX / max(w, h, 1)
    pequena = base.resize((max(1, round(w * escala)), max(1, round(h * escala))), Image.BILINEAR)
    escala = PREVIEW_SIZE_PX / max(pequena.size)
    saida = pequena.resize((max(1, round(pequena.width * escala)), max(1, round(pequena.height * escala))),
                           Image.BICUBIC).filter(ImageFilter.GaussianBlur(radius=6))
    out = io.BytesIO()
    saida.save(out, format="JPEG", quality=70)
    return out.getvalue()


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
