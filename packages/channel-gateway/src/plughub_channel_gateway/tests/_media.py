"""
_media.py — arquivos de mídia REAIS para teste (ATT-05).

Desde a ATT-05 o commit re-codifica toda imagem de contato: bytes que só têm a assinatura
(`\\xff\\xd8\\xff` + zeros) não decodificam e são recusados, como deveriam. Teste que grava imagem
usa uma destas, geradas pelo Pillow da própria imagem do serviço.
"""
from __future__ import annotations

import io

from PIL import Image


def real_image(fmt: str = "JPEG", *, size=(8, 8), color=(200, 30, 30), exif=None, **save) -> bytes:
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    if exif is not None:
        save["exif"] = exif
    img.save(buf, format=fmt, **save)
    return buf.getvalue()


REAL_JPEG = real_image("JPEG")
REAL_PNG = real_image("PNG")
