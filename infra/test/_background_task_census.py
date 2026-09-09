#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_background_task_census.py — censo AST das tasks de VIDA LONGA de cada serviço.

Por que AST e não `grep`: a supervisão é ESTRUTURA (a criação da task está dentro
de outra chamada, ou o seu resultado recebe `add_done_callback`), e `grep` conta
linha. Um `create_task` supervisionado e um cru são a mesma linha para o `grep`.

DUAS populações, e elas continuam separadas de propósito (misturá-las daria um
número sem uso, que é o erro que a D14.1 registra):

  BOOT (default)   criada em `lifespan`/`startup`; vive enquanto o processo vive;
                   morrer é incidente. Exige supervisão — envelope ou callback.
  EFÊMERA (`--efemeras`)  criada como STATEMENT dentro de um handler, uma por
                   mensagem; terminar é o normal. Exige DONO: `create_task` solto
                   devolve uma Task que ninguém referencia, e (a) a exceção fica
                   presa nela, (b) o CPython pode coletá-la no meio da execução.

⚠️ O discriminador da efêmera é ser `ast.Expr` — statement cujo valor é descartado.
`t = create_task(...)`, `await create_task(...)` e `[create_task(...), ...]` têm
dono por construção e ficam fora: o eixo é a AUSÊNCIA de referência, não a criação.

Saída: uma linha por task,  `servico<TAB>linha<TAB>corrotina<TAB>OK|CRUA`
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


def censo_efemeras(caminho: str) -> list[tuple[int, str, bool]]:
    """`create_task`/`ensure_future` como STATEMENT — nascidos sem dono.

    Ter dono é o que `disparar()` (channel-gateway `tarefas.py`) dá: ele guarda a
    referência num conjunto de módulo e loga se a corrotina morrer. Um statement
    que passe por ele deixa de ser `ast.Expr` de `create_task` e some daqui — o
    gate mede a AUSÊNCIA do padrão perigoso, não a presença de um nome.
    """
    try:
        arvore = ast.parse(open(caminho, encoding="utf-8").read())
    except (SyntaxError, UnicodeDecodeError):
        return []
    de_boot = {
        id(x)
        for no in ast.walk(arvore)
        if isinstance(no, (ast.AsyncFunctionDef, ast.FunctionDef)) and no.name in NOMES_DE_BOOT
        for x in ast.walk(no)
    }
    achados = []
    for no in ast.walk(arvore):
        if (isinstance(no, ast.Expr) and _e_criacao(no.value)
                and id(no) not in de_boot):     # boot é a outra população
            achados.append((no.value.lineno, _nome_do_alvo(no.value), False))
    return sorted(achados)


if "--efemeras" in sys.argv:
    for arq in sorted(glob.glob(os.path.join(RAIZ, "packages/*/src/**/*.py"), recursive=True)):
        if "/tests/" in arq.replace("\\", "/") or os.sep + "tests" + os.sep in arq:
            continue
        servico = arq.replace("\\", "/").split("/packages/")[1].split("/")[0]
        for ln, nome, _ in censo_efemeras(arq):
            print(f"{servico}\t{ln}\t{nome}\tCRUA")
    sys.exit(0)

for main in sorted(glob.glob(os.path.join(RAIZ, "packages/*/src/*/main.py"))):
    servico = main.replace("\\", "/").split("/packages/")[1].split("/")[0]
    for ln, nome, sup in censo(main):
        print(f"{servico}\t{ln}\t{nome}\t{'SUPERVISIONADA' if sup else 'CRUA'}")
