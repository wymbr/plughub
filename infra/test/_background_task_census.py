#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_background_task_census.py — censo AST das tasks de VIDA LONGA de cada serviço.

Por que AST e não `grep`: a supervisão é ESTRUTURA (a criação da task está dentro
de outra chamada, ou o seu resultado recebe `add_done_callback`), e `grep` conta
linha. Um `create_task` supervisionado e um cru são a mesma linha para o `grep`.

Definição da população — task de VIDA LONGA é a criada no BOOT do serviço
(`lifespan` / `startup`), que é onde nascem os consumidores e scanners que devem
viver enquanto o processo viver. Task efêmera de trabalho (uma por mensagem,
criada dentro de um handler) é outro fenômeno, com outro conserto, e está
declarada como dívida em `pending.md` (RET-15) — misturar as duas daria um número
sem uso, que é o erro que a D14.1 registra.

Saída: uma linha por task,  `servico<TAB>linha<TAB>corrotina<TAB>SUPERVISIONADA|CRUA`
"""
from __future__ import annotations

import ast
import glob
import os
import sys

RAIZ = sys.argv[1] if len(sys.argv) > 1 else "."
NOMES_DE_BOOT = {"lifespan", "startup", "on_startup", "_startup"}
CRIADORAS     = {"create_task", "ensure_future"}


def _e_criacao(no: ast.AST) -> bool:
    return (isinstance(no, ast.Call) and isinstance(no.func, ast.Attribute)
            and no.func.attr in CRIADORAS)


def _nome_do_alvo(chamada: ast.Call) -> str:
    if not chamada.args:
        return "?"
    a = chamada.args[0]
    f = a.func if isinstance(a, ast.Call) else a
    return getattr(f, "attr", getattr(f, "id", "?"))


def censo(caminho: str) -> list[tuple[int, str, bool]]:
    try:
        arvore = ast.parse(open(caminho, encoding="utf-8").read())
    except (SyntaxError, UnicodeDecodeError):
        return []
    achados: list[tuple[int, str, bool]] = []
    for no in ast.walk(arvore):
        if not (isinstance(no, (ast.AsyncFunctionDef, ast.FunctionDef))
                and no.name in NOMES_DE_BOOT):
            continue
        for filho in ast.walk(no):
            # (a) supervisão por ENVELOPE: `_supervise(nome, create_task(...))`
            if isinstance(filho, ast.Call) and not _e_criacao(filho):
                for arg in filho.args:
                    if _e_criacao(arg):
                        achados.append((arg.lineno, _nome_do_alvo(arg), True))
            # (b) supervisão por CALLBACK: `t = create_task(...)` + `t.add_done_callback`
            elif isinstance(filho, ast.Assign) and _e_criacao(filho.value):
                alvos = {ast.unparse(t) for t in filho.targets}
                tem_cb = any(
                    isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute)
                    and x.func.attr == "add_done_callback"
                    and ast.unparse(x.func.value) in alvos
                    for x in ast.walk(no)
                )
                achados.append((filho.value.lineno, _nome_do_alvo(filho.value), tem_cb))
            # (c) crua como STATEMENT — nem envelope, nem nome, nem callback possível
            elif isinstance(filho, ast.Expr) and _e_criacao(filho.value):
                achados.append((filho.value.lineno, _nome_do_alvo(filho.value), False))
    # dedup: `ast.walk` do envelope e do Assign podem ver a mesma criação
    vistos: dict[int, tuple[int, str, bool]] = {}
    for ln, nome, sup in achados:
        if ln not in vistos or sup:          # supervisionada vence o duplicado
            vistos[ln] = (ln, nome, sup)
    return sorted(vistos.values())


for main in sorted(glob.glob(os.path.join(RAIZ, "packages/*/src/*/main.py"))):
    servico = main.replace("\\", "/").split("/packages/")[1].split("/")[0]
    for ln, nome, sup in censo(main):
        print(f"{servico}\t{ln}\t{nome}\t{'SUPERVISIONADA' if sup else 'CRUA'}")
