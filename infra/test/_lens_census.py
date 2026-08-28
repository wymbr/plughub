#!/usr/bin/env python3
"""
_lens_census.py — censo do contrato de lente (`platform-ui/src/modules/analise/lens-contract.ts`).

POR QUE UM PARSER, E NÃO `grep`
-------------------------------
A seção D do `probe_report_surface.sh` nasceu com `grep -oE "id: '...'"` sobre o
arquivo inteiro, e isso bastava enquanto TODA lente vinha do mesmo endpoint. A F2
acrescentou a superfície A, cujas lentes são servidas por `/reports/contacts/series`
e **não são, nem devem ser, conhecidas** pelo `_COMPARE_LENSES` da mesa — com o grep
antigo o gate ficaria VERMELHO por elas, que é reprovar pelo motivo errado.

O discriminador é o campo `source`, e ele vive DEPOIS do `id` no mesmo bloco. Casar
os dois exige acompanhar o bloco, não a linha: é o mesmo motivo pelo qual o censo de
órfãos virou `_ui_orphans.py` (resolução de import) em vez de `grep` por basename.

SAÍDA
-----
Uma linha `<<id>> <<source>>` por lente declarada em `REPORT_LENSES`, mais
`<<id>> backend_only` para cada entrada de `BACKEND_ONLY_LENSES`. Uso:

    python3 _lens_census.py <caminho do lens-contract.ts>

Segundo modo — as MÉTRICAS da superfície A, dos dois lados:

    python3 _lens_census.py --metrics <lens-contract.ts> <contacts_series.py>

Ele imprime `ts <<chave>>` e `py <<chave>>`. Mora aqui, e não numa linha do shell,
porque a primeira versão o escreveu como heredoc Python dentro do `.sh` — e o `\\n`
das strings foi consumido pela camada de cima, quebrando o parser em silêncio (o
probe reportou "0 métricas" e seguiu). Aninhar linguagem dentro de linguagem é o
tipo de coisa que falha por formatação, não por lógica.

Sai com 3 (INCONCLUSIVO) se o parser deixar de casar com a forma do arquivo — nunca
com uma lista vazia, que o chamador leria como "não há lente".
"""
from __future__ import annotations

import re
import sys

ID_RE = re.compile(r"\bid:\s*'([a-z_]+)'")
SRC_RE = re.compile(r"\bsource:\s*'([a-z_]+)'")


def census(text: str) -> list[tuple[str, str]]:
    # Recorta só o array das lentes plotáveis: fora dele há `id:` em comentário e
    # em outros tipos, e contá-los inventaria lente que ninguém declarou.
    start = text.find("export const REPORT_LENSES = [")
    end = text.find("] as const satisfies readonly ReportLens[]", start)
    if start < 0 or end < 0:
        return []
    body = text[start:end]

    out: list[tuple[str, str]] = []
    # Um bloco por lente: começa num `id:` e vai até o próximo.
    marks = [m.start() for m in ID_RE.finditer(body)] + [len(body)]
    for i in range(len(marks) - 1):
        chunk = body[marks[i]:marks[i + 1]]
        lid = ID_RE.search(chunk)
        src = SRC_RE.search(chunk)
        if not lid:
            continue
        # `source` ausente é ERRO, não default: o tipo o exige, e inventá-lo aqui
        # faria o gate passar sobre um contrato incompleto.
        out.append((lid.group(1), src.group(1) if src else "MISSING"))

    bo = re.search(r"BACKEND_ONLY_LENSES = \[(.*?)\]", text, re.S)
    if bo:
        for m in re.finditer(r"'([a-z_]+)'", bo.group(1)):
            out.append((m.group(1), "backend_only"))
    return out


KEY_RE = re.compile(r"\bkey:\s*'([a-z_]+)'")
PY_KEY_RE = re.compile(r'"key":\s*"([a-z_]+)"')


def contract_metrics(text: str) -> set[str]:
    """Chaves de métrica que a UI PLOTA nas lentes de `source: 'contacts_series'`."""
    start = text.find("export const REPORT_LENSES = [")
    end = text.find("] as const satisfies readonly ReportLens[]", start)
    if start < 0 or end < 0:
        return set()
    body = text[start:end]
    marks = [m.start() for m in ID_RE.finditer(body)] + [len(body)]
    out: set[str] = set()
    for i in range(len(marks) - 1):
        chunk = body[marks[i]:marks[i + 1]]
        if "source: 'contacts_series'" not in chunk:
            continue
        out |= {m.group(1) for m in KEY_RE.finditer(chunk)}
    return out


def backend_metrics(text: str) -> set[str]:
    """Chaves que `contacts_series._SERIES` declara servir."""
    start = text.find("_SERIES: dict[str, list[dict]] = {")
    end = text.find("\n}", start)
    if start < 0 or end < 0:
        return set()
    return {m.group(1) for m in PY_KEY_RE.finditer(text[start:end])}


def _metrics_mode(argv: list[str]) -> int:
    if len(argv) < 4:
        print("uso: _lens_census.py --metrics <lens-contract.ts> <contacts_series.py>",
              file=sys.stderr)
        return 3
    try:
        ts = contract_metrics(open(argv[2], encoding="utf-8").read())
        py = backend_metrics(open(argv[3], encoding="utf-8").read())
    except OSError as exc:
        print(f"INCONCLUSIVO: {exc}", file=sys.stderr)
        return 3
    if len(ts) < 3 or len(py) < 3:
        print(
            f"INCONCLUSIVO: {len(ts)} metricas no TS, {len(py)} no Python (esperado >= 3). "
            "Os parsers deixaram de casar com a forma dos arquivos.",
            file=sys.stderr,
        )
        return 3
    for k in sorted(ts):
        print(f"ts {k}")
    for k in sorted(py):
        print(f"py {k}")
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--metrics":
        return _metrics_mode(sys.argv)
    if len(sys.argv) < 2:
        print("uso: _lens_census.py <lens-contract.ts>", file=sys.stderr)
        return 3
    try:
        text = open(sys.argv[1], encoding="utf-8").read()
    except OSError as exc:
        print(f"INCONCLUSIVO: {exc}", file=sys.stderr)
        return 3

    rows = census(text)
    if len(rows) < 5:
        print(
            f"INCONCLUSIVO: o parser extraiu {len(rows)} lentes (esperado >= 5). "
            "A forma do arquivo mudou; um verde aqui nao significaria nada.",
            file=sys.stderr,
        )
        return 3
    for lid, src in rows:
        print(f"{lid} {src}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
