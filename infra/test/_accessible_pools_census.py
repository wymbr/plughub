#!/usr/bin/env python3
"""
_accessible_pools_census.py — quem distingue `[]` de `None`, e quem os funde.

AUT-04 do `pending.md`. Pre-requisito do passo 3 (`LEGACY_EMPTY_MEANS_UNRESTRICTED
= False`), que inverte o significado de `accessible_pools: []` de "todos os pools"
para "nenhum pool".

POR QUE AST E NAO GREP. O contrato tem TRES valores e so dois sao distinguiveis por
truthiness:

    None  -> irrestrito           (falsy)
    []    -> NENHUM pool          (falsy)   <-- depois do passo 3
    [...] -> recorte              (truthy)

Logo `if not accessible_pools:` colapsa irrestrito com nenhum-acesso. Antes da
inversao o ramo e inalcancavel (o resolvedor nunca devolve `[]`); depois dela vira
LIBERACAO onde deveria haver RECUSA — sem erro, sem log, sem tela vermelha. `grep`
nao sabe se um `if not x` foi precedido por um `if x is None` na mesma funcao, que e
exatamente o que separa o codigo correto do vazamento.

CLASSES:
  DISTINGUE     — testa `is None` E truthiness (ou so `is None`): correto
  FUNDE         — testa SO truthiness: candidato a vazamento no passo 3
  REPASSA       — menciona, nao decide (parametro, kwarg, anotacao)

Saida: tabela + contagem. Exit 0 sempre — e censo, nao veredicto; quem julga e o
`probe_task_ledger.sh`/humano com a lista na mao.
"""
from __future__ import annotations

import ast
import os
import sys

TARGET = "accessible_pools"
ALIASES = {"pools", "scope", "dominio"}
RESOLVERS = {"resolve_scope", "_resolve_scope", "accessible_pools"}


def _is_target(node: ast.AST, local: set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id == TARGET or node.id in local
    if isinstance(node, ast.Attribute):
        return node.attr == TARGET
    return False


def _bound_from_resolver(fn: ast.AST) -> set[str]:
    """Variaveis locais que recebem o retorno de um resolvedor de escopo."""
    out: set[str] = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call):
            f = n.value.func
            name = getattr(f, "id", None) or getattr(f, "attr", None)
            if name in RESOLVERS:
                for t in n.targets:
                    if isinstance(t, ast.Name) and t.id in ALIASES:
                        out.add(t.id)
    return out


def classify(fn: ast.AST) -> tuple[str, int, int]:
    local = _bound_from_resolver(fn)
    if not any(_is_target(n, local) for n in ast.walk(fn)):
        return ("", 0, 0)
    n_none = 0
    n_truthy = 0
    for n in ast.walk(fn):
        # `x is None` / `x is not None`
        if isinstance(n, ast.Compare) and _is_target(n.left, local):
            for op, cmp in zip(n.ops, n.comparators):
                if isinstance(op, (ast.Is, ast.IsNot)) and isinstance(cmp, ast.Constant) and cmp.value is None:
                    n_none += 1
        # `if not x` / `if x` / `x and ...` / `x or ...`
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not) and _is_target(n.operand, local):
            n_truthy += 1
        elif isinstance(n, ast.If) and _is_target(n.test, local):
            n_truthy += 1
        elif isinstance(n, ast.BoolOp):
            for v in n.values:
                if _is_target(v, local):
                    n_truthy += 1
    if n_none == 0 and n_truthy == 0:
        return ("REPASSA", 0, 0)
    if n_none > 0:
        return ("DISTINGUE", n_none, n_truthy)
    return ("FUNDE", 0, n_truthy)


def main() -> int:
    roots = sys.argv[1:] or ["packages"]
    rows = []
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in {"node_modules", ".venv", "__pycache__", "tests"}]
            for fname in filenames:
                if not fname.endswith(".py"):
                    continue
                path = os.path.join(dirpath, fname)
                try:
                    tree = ast.parse(open(path, encoding="utf-8").read())
                except (SyntaxError, UnicodeDecodeError):
                    continue
                if TARGET not in open(path, encoding="utf-8").read():
                    continue
                for node in ast.walk(tree):
                    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    kind, a, b = classify(node)
                    if kind:
                        rows.append((kind, path.replace("\\", "/"), node.lineno, node.name, a, b))

    order = {"FUNDE": 0, "DISTINGUE": 1, "REPASSA": 2}
    rows.sort(key=lambda r: (order[r[0]], r[1], r[2]))
    counts = {k: sum(1 for r in rows if r[0] == k) for k in order}

    print("== censo `accessible_pools` — quem distingue [] de None ==\n")
    print(f"{'classe':<10} {'arquivo:linha':<62} funcao")
    print("-" * 108)
    for kind, path, lineno, name, a, b in rows:
        if kind == "REPASSA":
            continue
        loc = f"{path.split('packages/')[-1]}:{lineno}"
        print(f"{kind:<10} {loc:<62} {name}")
    print("-" * 108)
    print(f"FUNDE     : {counts['FUNDE']:3d}  <- decidem por truthiness; vazam no passo 3")
    print(f"DISTINGUE : {counts['DISTINGUE']:3d}")
    print(f"REPASSA   : {counts['REPASSA']:3d}  (mencionam, nao decidem)")
    print(f"TOTAL     : {len(rows):3d} funcoes que tocam `accessible_pools`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
