"""
_analytics_tenant_census.py — de onde cada rota da analytics-api tira o TENANT, e quem a guarda.

TNT-01 (2026-09-29). Medido: a analytics-api recebia `tenant_id` pela query e não o
comparava com o do JWT — o token do `tenant_demo` leu histórico e visão 360 de um cliente
que só existe em outro tenant. O conserto mora numa casa só (`pool_auth.
optional_pool_principal`, por onde passam as três dependências de principal de pool); este
censo diz quais rotas essa casa COBRE e quais não.

Classes (uma por rota que declara `tenant_id`):
  coberta      — tenant na QUERY e principal de pool (optional/require/sse) na assinatura
  sistema      — guardada por `require_principal`/`require_dashboard_principal`, que já
                 aplicam `effective_tenant` (usuário preso ao próprio tenant)
  fora         — tenant por outra origem (path/corpo) ou sem principal reconhecido: a
                 casa única NÃO a cobre, e o probe exige que a lista seja declarada

Não julga — imprime JSON. Uso: python3 _analytics_tenant_census.py [raiz_do_pacote]
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2] / \
    "packages/analytics-api/src/plughub_analytics_api"

POOL = {"optional_pool_principal", "require_pool_principal", "sse_pool_principal"}
SYSTEM = {"require_principal", "require_dashboard_principal"}
METHODS = {"get", "post", "put", "patch", "delete"}


def _names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} | \
           {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}


def _route(dec: ast.expr) -> tuple[str, str] | None:
    if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr in METHODS:
        if dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
            return dec.func.attr.upper(), dec.args[0].value
    return None


rows = []
for f in sorted(ROOT.glob("*.py")):
    tree = ast.parse(f.read_text(encoding="utf-8"))
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        routes = [r for r in (_route(d) for d in fn.decorator_list) if r]
        if not routes:
            continue
        args = fn.args.args + fn.args.kwonlyargs
        defaults = dict(zip([a.arg for a in fn.args.args][-len(fn.args.defaults):], fn.args.defaults)) \
            if fn.args.defaults else {}
        defaults.update({a.arg: d for a, d in zip(fn.args.kwonlyargs, fn.args.kw_defaults) if d is not None})
        deps: set[str] = set()
        for d in defaults.values():
            deps |= _names(d)
        for d in fn.decorator_list:
            deps |= _names(d)
        arg_names = {a.arg for a in args}
        path_has_tenant = any("{tenant_id}" in p for _, p in routes)
        tenant_origin = None
        if "tenant_id" in arg_names:
            dflt = defaults.get("tenant_id")
            src = ast.unparse(dflt) if dflt is not None else ""
            tenant_origin = "path" if path_has_tenant else ("query" if ("Query" in src or dflt is None or isinstance(dflt, ast.Constant)) else src[:30])
        else:
            # corpo: algum parâmetro anotado com um modelo — o tenant pode viajar lá
            body_models = [ast.unparse(a.annotation) for a in args if a.annotation is not None
                           and ast.unparse(a.annotation)[:1].isupper()
                           and ast.unparse(a.annotation) not in ("Request", "Response", "PoolPrincipal", "Principal")]
            if body_models and "tenant_id" in f.read_text(encoding="utf-8"):
                tenant_origin = None  # não afirmamos: sem argumento, não é tenant desta rota
        if tenant_origin is None:
            continue
        if deps & POOL:
            cls = "coberta" if tenant_origin == "query" else "fora"
        elif deps & SYSTEM:
            cls = "sistema"
        else:
            cls = "fora"
        for m, p in routes:
            rows.append({"route": f"{m} {p}", "file": f.name, "line": fn.lineno,
                         "tenant": tenant_origin, "guard": sorted(deps & (POOL | SYSTEM)), "class": cls})

rows.sort(key=lambda r: (r["class"], r["file"], r["line"]))
summary = {c: sum(1 for r in rows if r["class"] == c) for c in ("coberta", "sistema", "fora")}
print(json.dumps({"summary": summary, "routes": rows}, ensure_ascii=False, indent=1))
