#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Metade Python do gate de paridade do EXTRATOR (V4/F2).

Lê a fixture única e imprime uma linha por escrita coletada, no MESMO formato do
gêmeo TS (`infra/test/_ctx_extract_runner.ts`):

    <tag>\\t<superficie>\\t<step|->\\t<dyn|lit>

⚠️ **O `stdout` é reconfigurado para `\\n`.** Em bancada Windows o `print()` traduz LF
em CRLF no modo texto, e o `diff` do gate acusaria TODAS as linhas por um byte — a
divergência real sumiria no ruído. Já aconteceu neste arco, e a primeira correção
normalizou a metade ERRADA por presumir a causa em vez de medi-la.

⚠️ **O `step` não vem do extrator do censo.** Ele guarda `(arquivo, superficie)`, não o
id do step — o censo agrega por arquivo, que é o recorte de que ele precisa. Para a
comparação isso não é perda: o gate compara TAG × SUPERFÍCIE × natureza, que é o que o
portão de publish consome. A coluna sai como `-` nos dois lados, e o runner TS a
normaliza igual — comparar uma coluna que um dos lados não tem seria comparar a
implementação, não a proposição.
"""
from __future__ import annotations

import json
import os
import sys

sys.stdout.reconfigure(newline="\n")                       # type: ignore[attr-defined]
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from censo_contextstore_cadastro import extrair_de_doc     # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        sys.stderr.write("uso: runner <fixture.json>\n")
        return 2
    doc = json.load(open(sys.argv[1], encoding="utf-8"))

    escritas, _leituras, dinamicas = extrair_de_doc(doc, "-")

    linhas = []
    for tag, sitios in escritas.items():
        for _arq, superficie in sitios:
            linhas.append("%s\t%s\t-\tlit" % (tag, superficie))
    # Dinâmicas viajam na MESMA lista, marcadas — nunca descartadas. O TS as coleta com
    # `dynamic: true`; o censo as separa num balde. Igualar aqui é o que permite comparar
    # a proposição (*"o que este flow escreve?"*) em vez das duas estruturas internas.
    for _arq, superficie, repr_tag in dinamicas:
        linhas.append("%s\t%s\t-\tdyn" % (repr_tag.strip("'\""), superficie))

    sys.stdout.write("\n".join(sorted(linhas)) + ("\n" if linhas else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
