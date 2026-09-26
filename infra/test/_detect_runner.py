# -*- coding: utf-8 -*-
"""Runner Python da paridade do validador de DETECÇÃO (CTX-12). Par: `_detect_runner.ts`.

Roda os DOIS motores Python sobre a mesma fixture:
  · `plughub_contextstore.masking.passes_detect_validator` — o que o channel-gateway usa;
  · a CÓPIA do quality-ingest (`passes_detect_validator` e `mask_text`), que não depende
    do py-contextstore.
Se os dois Python discordarem num caso, a linha sai com `DIVERGE` — e aí nunca casa com a
linha do TS, então o gate reprova. Categorias do texto: as DUAS redes Python — o
`mask_free_text` do py-contextstore (bridge e channel-gateway, MSK-06) e o `mask_text` do
quality-ingest —, e `DIVERGE` se discordarem.
"""
import io
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(newline="\n")   # type: ignore[union-attr]

_AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(os.path.dirname(_AQUI))
sys.path.insert(0, os.path.join(RAIZ, "packages", "py-contextstore", "src"))
sys.path.insert(0, os.path.join(RAIZ, "packages", "quality-ingest", "src"))
from plughub_contextstore.masking import (  # noqa: E402
    mask_free_text, passes_detect_validator as pcs,
)
from plughub_quality_ingest.masking import (  # noqa: E402
    mask_text, passes_detect_validator as qi,
)

FIXTURE = (sys.argv[1] if len(sys.argv) > 1
           else os.path.join(_AQUI, "fixtures", "detect_validator_cases.json"))


def linha(o):
    print(json.dumps(o, sort_keys=True, ensure_ascii=False, separators=(",", ":")))


def main() -> int:
    fx = json.load(io.open(FIXTURE, encoding="utf-8"))
    for c in fx["validator_cases"]:
        a, b = pcs(c["validator"], c["match"]), qi(c["validator"], c["match"])
        linha({"name": c["name"], "passa": a if a == b else "DIVERGE"})
    for c in fx["text_cases"]:
        _, a = mask_text(c["text"])
        _, b = mask_free_text(c["text"], {})
        ca, cb = sorted(set(a)), sorted(set(b))
        linha({"name": c["name"], "categorias": ca if ca == cb else "DIVERGE"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
