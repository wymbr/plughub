# -*- coding: utf-8 -*-
"""_masking_types_seed_census.py — os tipos que o seed.py declara.

Par do lado TS (`DEFAULT_DATA_TYPE_CATALOG` em `@plughub/schemas/audit.ts`).
Le o seed por AST, nunca por regex: `grep '"id":'` acha tambem os ids de
`agent_activity.pause_reasons`, `survey.*` e afins, que estao no mesmo arquivo e
nao sao tipos de masking. Contar o balde errado e o defeito que este portao existe
para impedir, entao ele nao pode cometê-lo no proprio instrumento.
"""
import ast
import io
import json
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SEED = os.path.join(RAIZ, "packages", "config-api", "src",
                    "plughub_config_api", "seed.py")


def literal(no):
    try:
        return ast.literal_eval(no)
    except (ValueError, TypeError, SyntaxError):
        return None


def acha_tipos(arvore):
    """A tupla ("masking", "types", {...}) — casada pelos DOIS primeiros elementos."""
    for no in ast.walk(arvore):
        if not isinstance(no, ast.Tuple) or len(no.elts) < 3:
            continue
        ns, key = literal(no.elts[0]), literal(no.elts[1])
        if ns != "masking" or key != "types":
            continue
        valor = literal(no.elts[2])
        if isinstance(valor, dict) and isinstance(valor.get("types"), list):
            return valor["types"]
    return None


tipos = acha_tipos(ast.parse(io.open(SEED, encoding="utf-8").read()))
if tipos is None:
    sys.exit("nao achei a tupla (masking, types, ...) no seed.py")

print(json.dumps({
    t["id"]: {
        "by_role": (t.get("mascara") or {}).get("by_role") or {},
        "lgpd": t.get("lgpd"),
    }
    for t in tipos if isinstance(t, dict) and isinstance(t.get("id"), str)
}, sort_keys=True, ensure_ascii=False))
