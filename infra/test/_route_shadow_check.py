#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rota LITERAL registrada depois de uma PARAMÉTRICA que a cobre nunca é alcançada.

── Por que existe (2026-09-02) ──────────────────────────────────────────────────

`GET /config/{namespace}/raw` viveu no repositório com docstring prometendo
*"mostra o que está sobrescrevendo o default global"*, zero chamadores e zero
testes — e era **inalcançável**: estava declarada 200 linhas depois de
`GET /config/{namespace}/{key}`, então `/config/masking/raw` casava a paramétrica
com `key="raw"` e respondia `404 No config found for masking.raw`.

O modo de falha é o que torna isto caro: num endpoint de LEITURA, um 404 parece
*"não há dado"* e não *"não há rota"*. Ninguém investiga. A rota podia ficar morta
para sempre, e ficou.

Este verificador é ESTRUTURAL, não de tráfego: ele lê a ordem de registro (que o
FastAPI preserva em `/openapi.json`) e acusa a classe inteira do defeito, não a
ocorrência que já conhecemos. Bater só em `/raw` seria lista de exceção.

Uso:  python3 _route_shadow_check.py <url-do-openapi.json>
Saída: linhas `SHADOWED <literal> <por> ` e código 1 se houver alguma.
"""
from __future__ import annotations

import json
import sys
import urllib.request


def segmentos(caminho: str) -> list[str]:
    return [s for s in caminho.split("/") if s != ""]


def e_parametrico(seg: str) -> bool:
    return seg.startswith("{") and seg.endswith("}")


def cobre(padrao: list[str], alvo: list[str]) -> bool:
    """`padrao` (registrado antes) casaria a requisição literal de `alvo`?

    Mesmo número de segmentos e, em cada posição, ou o segmento é idêntico ou o do
    padrão é paramétrico. Um paramétrico casa QUALQUER texto — inclusive um que
    pareça nome de rota.
    """
    if len(padrao) != len(alvo):
        return False
    return all(p == a or e_parametrico(p) for p, a in zip(padrao, alvo))


def main() -> int:
    if len(sys.argv) != 2:
        print("uso: _route_shadow_check.py <url-do-openapi.json>", file=sys.stderr)
        return 2

    with urllib.request.urlopen(sys.argv[1], timeout=10) as r:
        doc = json.load(r)

    caminhos = list(doc.get("paths", {}).keys())
    if not caminhos:
        print("INCONCLUSIVO: openapi sem paths", file=sys.stderr)
        return 2

    achados = 0
    for i, alvo in enumerate(caminhos):
        segs_alvo = segmentos(alvo)
        # Só rotas com ao menos um segmento LITERAL podem ser sombreadas; uma rota
        # inteiramente paramétrica não tem literal a proteger.
        if not any(not e_parametrico(s) for s in segs_alvo):
            continue
        for anterior in caminhos[:i]:
            segs_ant = segmentos(anterior)
            if segs_ant == segs_alvo:
                continue
            if cobre(segs_ant, segs_alvo):
                print("SHADOWED %s <- %s" % (alvo, anterior))
                achados += 1
                break

    print("rotas=%d sombreadas=%d" % (len(caminhos), achados))
    return 1 if achados else 0


if __name__ == "__main__":
    sys.exit(main())
