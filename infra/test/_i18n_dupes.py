#!/usr/bin/env python3
"""
_i18n_dupes.py — chave repetida no MESMO objeto de um arquivo de locale.

Por que isto existe (2026-08-28)
--------------------------------
`en/dashboards.json` tinha `"catalog"` DUAS vezes. JSON nao proibe: o parser aceita e
a ULTIMA vence. O bloco perdedor levava junto tres rotulos — `volume-by-channel`,
`pools-queue`, `agent-availability` — que passaram a nao existir. Resultado na tela:
o cartao da Home chamado `catalog.volume-by-channel.label`, a propria chave, porque
`t()` devolve a chave quando nao acha traducao.

O defeito e MUDO em toda ferramenta comum: o arquivo e JSON valido, o editor nao
reclama, o build passa, e o `tsc` nao olha JSON de traducao.

E — o ponto que decide o desenho — **um probe de PARIDADE EN x pt-BR nunca pegaria**:
os dois arquivos estavam duplicados do mesmo jeito, entao a paridade estava perfeita.
Comparar as duas linguas responde *"as traducoes cobrem as mesmas chaves?"*; nao
responde *"alguma chave foi perdida em silencio nas duas?"*. Sao proposicoes
diferentes, e so a segunda tinha o defeito.

Saida: uma linha por achado. rc 0 = limpo · 1 = ha duplicata · 2 = nao consegui varrer.
"""
from __future__ import annotations

import collections
import glob
import io
import json
import os
import sys

DEFAULT_ROOT = 'packages/platform-ui/src/i18n/locales'


def duplicates(path: str) -> list[tuple[str, int]]:
    """[(chave, n_ocorrencias)] das chaves repetidas no mesmo objeto, em qualquer nivel."""
    found: list[tuple[str, int]] = []

    def hook(pairs):
        for k, n in collections.Counter(k for k, _ in pairs).items():
            if n > 1:
                found.append((k, n))
        return dict(pairs)

    json.loads(io.open(path, encoding='utf-8').read(), object_pairs_hook=hook)
    return found


def main() -> int:
    root = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ROOT
    files = sorted(glob.glob(os.path.join(root, '*', '*.json')))
    if not files:
        print(f'nenhum arquivo de locale em {root}', file=sys.stderr)
        return 2
    rc = 0
    for path in files:
        rel = os.path.relpath(path, root).replace(os.sep, '/')
        try:
            dups = duplicates(path)
        except Exception as exc:                      # JSON quebrado e defeito, nao ruido
            print(f'{rel}: NAO E JSON VALIDO — {exc}')
            rc = 1
            continue
        for key, n in dups:
            print(f'{rel}: chave "{key}" aparece {n}x no mesmo objeto '
                  f'(a ultima vence; o resto some em silencio)')
            rc = 1
    if rc == 0:
        print(f'{len(files)} arquivos varridos, nenhuma chave duplicada')
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
