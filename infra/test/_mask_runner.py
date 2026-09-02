# -*- coding: utf-8 -*-
"""Runner Python da fixture de paridade da APLICACAO de mascara (decisao #6, passo 2).

Par: `_mask_runner.ts`. Os dois tem de imprimir exatamente as mesmas linhas.
Ver o cabecalho de `_stamp_runner.py` para as razoes de forma (chaves ordenadas,
caminho por argumento, LF forcado).
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
from plughub_contextstore.masking import apply_masking_type_to_value  # noqa: E402

FIXTURE = (sys.argv[1] if len(sys.argv) > 1
           else os.path.join(_AQUI, "fixtures", "masking_apply_cases.json"))


def main() -> int:
    fx = json.load(io.open(FIXTURE, encoding="utf-8"))
    for c in fx["cases"]:
        print(json.dumps(
            {"name": c["name"], "out": apply_masking_type_to_value(c["raw"], c["mask"])},
            sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
