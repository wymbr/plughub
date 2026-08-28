#!/usr/bin/env python3
"""
Censo do resolvedor de ESCOPO de pool — usado pelo C4 do
`probe_authz_single_verifier.sh`.

POR QUE NAO E UM GREP
=====================
O criterio ingenuo seria `grep 'unrestricted'`. Ele mede a proposicao ERRADA: ha
consumidores legitimos do claim que NAO reimplementam o resolvedor. O caso concreto
que motivou este arquivo e `evaluation-api/router.py:_compute_result_scope`, que le
`unrestricted` para decidir o eixo de PESSOA (de quem eu vejo avaliacoes) e ja delega
o eixo de POOL para o canonico na linha de cima. Um grep o marcaria como violacao e o
relatorio publicaria um defeito que nao existe.

A proposicao que importa e mais estreita:

    "existe exatamente UM lugar que decide o que uma lista `accessible_pools` VAZIA
     significa"

e o que caracteriza esse lugar e a COMBINACAO, dentro da MESMA funcao, de:
  (a) referenciar `accessible_pools` no CORPO, e
  (b) **RAMIFICAR** em `unrestricted` (o identificador dentro da CONDICAO de um `if`,
      `if`-expressao ou operador booleano).

As duas restricoes foram aprendidas MEDINDO, cada uma contra um falso positivo real:

  · sem (a)-sem-docstring, `evaluation-api/_compute_result_scope` era acusado — ele
    documenta a convencao de `accessible_pools` em prosa e ja delega o eixo de pool ao
    canonico. Prosa nao decide nada.
  · sem (b)-como-ramo, SETE funcoes do auth-api eram acusadas (`create_access_token`,
    `_user_to_response`, `_make_token_response`, e os pares `create_user`/`update_user`
    de `db.py` e `router.py`). Elas ESCREVEM os dois campos no token; escrever nao e
    decidir o que a ausencia significa. O emissor nunca foi copia do resolvedor.
  · com (b) mas contando `ast.Name`, sobrava UMA: `db.py:update_user`, cujo
    `if unrestricted is not None` e patch parcial ("este campo foi enviado?"). Dai a
    restricao a literal — ver `_chaves_lidas`.

Este arquivo e, ele proprio, um exemplo da regra de metodo do CLAUDE.md: um
instrumento pode ser falseavel, ramificado e honesto e ainda medir a proposicao
ADJACENTE a que se fez.

Saida: uma linha `arquivo:linha:funcao` por reimplementacao encontrada. Sem saida =
ninguem reimplementou. Codigo de saida sempre 0; quem julga e o shell.
"""
from __future__ import annotations

import ast
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[2]
ALVO_LISTA = "accessible_pools"
ALVO_RAMO = "unrestricted"


def _sem_docstring(no: ast.AST) -> list[ast.AST]:
    corpo = list(ast.iter_child_nodes(no))
    if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)) and no.body:
        primeiro = no.body[0]
        if isinstance(primeiro, ast.Expr) and isinstance(primeiro.value, ast.Constant) \
                and isinstance(primeiro.value.value, str):
            corpo = [c for c in corpo if c is not primeiro]
    return corpo


def _chaves_lidas(nos: list[ast.AST]) -> set[str]:
    """Literais string alcancaveis — as CHAVES de claim lidas do token.

    So literal, nunca `ast.Name`. E o discriminador que separa quem LE do token de
    quem ESCREVE no token: o resolvedor faz `claims.get("accessible_pools")`, enquanto
    `auth-api/db.py:update_user` tem `accessible_pools` como PARAMETRO e ramifica em
    `if unrestricted is not None` — patch parcial ("este campo foi enviado?"), que nao
    decide o que a ausencia significa.
    """
    achados: set[str] = set()
    for filho in nos:
        for sub in ast.walk(filho):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                achados.add(sub.value)
    return achados


def _ramifica_em(nos: list[ast.AST], alvo: str) -> bool:
    """True se `alvo` aparece na CONDICAO de um if / if-expressao / operador booleano."""
    for filho in nos:
        for sub in ast.walk(filho):
            condicoes: list[ast.AST] = []
            if isinstance(sub, (ast.If, ast.IfExp, ast.While)):
                condicoes.append(sub.test)
            elif isinstance(sub, ast.BoolOp):
                condicoes.extend(sub.values)
            if condicoes and alvo in _chaves_lidas(condicoes):
                return True
    return False


def main() -> int:
    alvos = sorted(RAIZ.glob("packages/*/src/**/*.py"))
    for caminho in alvos:
        rel = caminho.relative_to(RAIZ).as_posix()
        if "/py-authz/" in rel or "/tests/" in rel:
            continue
        try:
            arvore = ast.parse(caminho.read_text(encoding="utf-8"), filename=rel)
        except (SyntaxError, UnicodeDecodeError):
            continue
        for no in ast.walk(arvore):
            if not isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            corpo = _sem_docstring(no)
            if ALVO_LISTA not in _chaves_lidas(corpo):
                continue
            if not _ramifica_em(corpo, ALVO_RAMO):
                continue
            print(f"{rel}:{no.lineno}:{no.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
