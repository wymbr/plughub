#!/usr/bin/env python3
"""
_report_scope_census.py — censo AST: toda rota de `reports.py` cai em UMA classe.

Chamado pela metade A do `probe_report_row_scope.sh`.

POR QUE AST E NAO `grep`. A pergunta e "esta rota passa `accessible_pools` a alguma
query?", e ela e sintatica: `grep accessible_pools reports.py` acusa tambem o
docstring, o comentario que explica a regra e o proprio nome do parametro do
`PoolPrincipal` — todos presentes em rota que NAO recorta nada. Foi assim que 13 rotas
passaram meses parecendo cobertas.

AS TRES CLASSES, e por que "nenhuma" tem de ser vermelho:

  ESCOPADA  passa `accessible_pools=` a uma chamada dentro do corpo
  ISENTA    esta em `_SCOPE_EXEMPT` — decisao tomada, sem gatilho
  DIVIDA    esta em `_SCOPE_DEBT`   — recorte nao expressavel, COM gatilho nomeado

Rota em NENHUMA classe e o estado que este censo existe para tornar impossivel: e
indistinguivel, olhando o arquivo, de uma rota que decidiu nao recortar. A diferenca
entre as duas ultimas classes tambem e proposital — juntar divida com decisao faria a
divida herdar a tranquilidade da decisao, e nada voltaria a olha-la.

Saida: linhas `CLASSE<TAB>ROTA`, e `FALTA<TAB>ROTA` para as sem classe.
"""
from __future__ import annotations

import ast
import pathlib
import sys

ALVO = pathlib.Path(
    "packages/analytics-api/src/plughub_analytics_api/reports.py"
)


def _dict_literal(tree: ast.Module, nome: str) -> set[str]:
    """Le a tabela declarada no modulo. Ausente => conjunto vazio (nunca 'tudo')."""
    for node in tree.body:
        alvo = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            alvo, valor = node.target.id, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            alvo, valor = node.targets[0].id, node.value
        if alvo == nome and isinstance(valor, ast.Dict):
            return {
                ast.literal_eval(k) for k in valor.keys if isinstance(k, ast.Constant)
            }
    return set()


def main() -> int:
    if not ALVO.exists():
        print(f"INCONCLUSIVO: {ALVO} ausente", file=sys.stderr)
        return 2

    tree = ast.parse(ALVO.read_text(encoding="utf-8"))
    isentas = _dict_literal(tree, "_SCOPE_EXEMPT")
    dividas = _dict_literal(tree, "_SCOPE_DEBT")

    sobreposicao = isentas & dividas
    if sobreposicao:
        # Uma rota nas duas tabelas e uma divida que se declara decidida.
        for r in sorted(sobreposicao):
            print(f"AMBIGUA\t{r}")

    faltando = False
    for node in tree.body:
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        rota = None
        for d in node.decorator_list:
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.args:
                arg = d.args[0]
                if isinstance(arg, ast.Constant):
                    rota = arg.value
        if rota is None:
            continue

        # ESCOPADA = passa a keyword a ALGUMA chamada do corpo. Nao basta o parametro
        # existir na assinatura: o `pool_principal` esta em 39 rotas e recortava em 26.
        escopada = any(
            isinstance(x, ast.keyword) and x.arg == "accessible_pools"
            for x in ast.walk(node)
        )
        if escopada:
            print(f"ESCOPADA\t{rota}")
        elif rota in isentas:
            print(f"ISENTA\t{rota}")
        elif rota in dividas:
            print(f"DIVIDA\t{rota}")
        else:
            print(f"FALTA\t{rota}")
            faltando = True

    return 1 if (faltando or sobreposicao) else 0


if __name__ == "__main__":
    raise SystemExit(main())
