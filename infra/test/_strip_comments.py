#!/usr/bin/env python3
"""
_strip_comments.py — apaga comentarios de um fonte TS/TSX, PRESERVANDO os offsets.

Por que existe (2026-08-29)
---------------------------
Duas varreduras deste repositorio ja acusaram PROSA como codigo:

  · `_ui_raw_analytics_calls.py` casava `fetch(` dentro de comentario — das 15 linhas
    que ele publicava como "nao medidas", pelo menos duas eram comentario;
  · a secao G do `probe_report_surface.sh` acusou `SessionsPage.tsx:629`, uma linha
    dentro de `{/* … */}` que EXPLICA a cascata removida. O gate reprovaria por causa
    da documentacao da propria correcao.

O modo de falha e o mesmo nos dois: um instrumento que conta prosa mede a proposicao
errada, e a evidencia que ele publica (um numero, um arquivo:linha) parece resposta.

Comentario vira ESPACO, nao some: `\n` preservados e cada caractere trocado por um
branco de mesma largura. Assim `grep -n` continua reportando o numero de linha do
arquivo REAL — um stripper que remove linhas transformaria "ignora comentario" em
"reporta a linha errada", que e trocar um defeito por outro mais dificil de ver.

Uso:  _strip_comments.py <arquivo>   → escreve o resultado em stdout
"""
from __future__ import annotations

import pathlib
import re
import sys

# `/* … */` (inclui o `{/* … */}` do JSX, cujas chaves ficam) e `// …` ate o fim da
# linha. Nao tenta entender string literal: um `"//"` dentro de string vira branco, e
# a consequencia (uma linha a menos varrida) e conservadora — este arquivo serve a
# gates que procuram RESIDUO, onde falso negativo custa menos que falso positivo, e a
# alternativa seria um parser de TS dentro de um probe de shell.
COMMENT_RE = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)


def strip(text: str) -> str:
    def branco(m: re.Match) -> str:
        return "".join("\n" if c == "\n" else " " for c in m.group(0))
    return COMMENT_RE.sub(branco, text)


def main() -> int:
    if len(sys.argv) != 2:
        print("uso: _strip_comments.py <arquivo>", file=sys.stderr)
        return 2
    caminho = pathlib.Path(sys.argv[1])
    if not caminho.is_file():
        print(f"arquivo inexistente: {caminho}", file=sys.stderr)
        return 2
    sys.stdout.write(strip(caminho.read_text(encoding="utf-8", errors="replace")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
